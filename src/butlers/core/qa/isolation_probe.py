"""Content-blind isolation probe run inside a QA investigator pod (bu-ejow5p.11).

``python -I -m butlers.core.qa.isolation_probe [options]`` is the container command of the
isolation verifier's Job (``scripts/verify_qa_isolation.py``); the Job is
``build_investigator_job()`` unchanged except for this command. Standard library only: the probe
must run with ``-I`` (no ``PYTHON*`` environment, no user site, no cwd on ``sys.path``).

Each check is a *negative* (the investigator must NOT be able to do it) or a *positive control*
(it MUST be able to, so a green negative cannot be a broken probe). The receipt carries category
names, outcomes and a digest; it never carries a path's contents, an environment value, an address
resolved from a secret or an error message, so it is safe to log and to attach to a review.

Exit status is 0 only when every check passed. Every check's failure is a normal receipt row, not
an exception: an exception inside one check is recorded as ``error`` and the rest still run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

SERVICE_ACCOUNT_DIR: Final[str] = "/var/run/secrets/kubernetes.io"
EXPECTED_UID: Final[int] = 10001
_CONNECT_TIMEOUT_S: Final[float] = 3.0

NEGATIVE: Final[str] = "negative"
POSITIVE: Final[str] = "positive"


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    passed: bool
    category: str = ""


def _tcp_connects(host: str, port: int, timeout_s: float = _CONNECT_TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _can_read(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            handle.read(1)
        return True
    except OSError:
        return False


def _can_write_file(directory: str) -> bool:
    probe = os.path.join(directory, f".isolation-probe-{os.getpid()}")
    try:
        with open(probe, "wb") as handle:
            handle.write(b"x")
        os.unlink(probe)
        return True
    except OSError:
        return False


def _foreign_processes() -> int:
    """Count visible processes other than this one and its own descendants."""
    own = os.getpid()
    visible = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return 0
    for name in names:
        if name.isdigit():
            visible.append(int(name))
    children = {own}
    parents: dict[int, int] = {}
    for pid in visible:
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                fields = handle.read().rsplit(b")", 1)[1].split()
            parents[pid] = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in children and pid not in children:
                children.add(pid)
                changed = True
    return sum(1 for pid in visible if pid not in children)


def _parse_target(spec: str) -> tuple[str, str, int]:
    name, _, address = spec.partition("=")
    host, _, port = address.rpartition(":")
    return name, host, int(port)


def run_checks(args: argparse.Namespace) -> list[CheckResult]:
    results: list[CheckResult] = []

    def check(name: str, kind: str, probe: Callable[[], bool]) -> None:
        try:
            results.append(CheckResult(name, kind, bool(probe())))
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            results.append(CheckResult(name, kind, False, type(exc).__name__))

    check("uid_not_root", NEGATIVE, lambda: os.getuid() != 0 and os.getuid() == args.expected_uid)
    check("no_foreign_processes", NEGATIVE, lambda: _foreign_processes() == 0)
    check("no_service_account_token", NEGATIVE, lambda: not os.path.exists(SERVICE_ACCOUNT_DIR))
    check("root_is_read_only", NEGATIVE, lambda: not _can_write_file("/"))
    check("app_is_read_only", NEGATIVE, lambda: not _can_write_file("/app"))
    for path in args.forbidden_path:
        label = hashlib.sha256(path.encode()).hexdigest()[:12]
        check(f"forbidden_path_unreadable:{label}", NEGATIVE, lambda p=path: not _can_read(p))
    for name in args.forbidden_env:
        check(f"forbidden_env_absent:{name}", NEGATIVE, lambda n=name: n not in os.environ)
    for spec in args.refused_tcp:
        check(
            f"tcp_refused:{spec.partition('=')[0]}",
            NEGATIVE,
            lambda s=spec: not _tcp_connects(*_parse_target(s)[1:]),
        )

    check("workspace_writable", POSITIVE, lambda: _can_write_file(args.workspace))
    for path in args.readable_path:
        label = hashlib.sha256(path.encode()).hexdigest()[:12]
        check(f"staged_path_readable:{label}", POSITIVE, lambda p=path: _can_read(p))
    for spec in args.reachable_tcp:
        check(
            f"tcp_reachable:{spec.partition('=')[0]}",
            POSITIVE,
            lambda s=spec: _tcp_connects(*_parse_target(s)[1:]),
        )
    return results


def receipt(results: Sequence[CheckResult]) -> dict[str, object]:
    """The content-blind receipt: names, kinds, outcomes and a digest over them."""
    rows = [
        {
            "check": r.name,
            "kind": r.kind,
            "outcome": "pass" if r.passed else "fail",
            **({"error": r.category} if r.category else {}),
        }
        for r in results
    ]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema": 1,
        "passed": all(r.passed for r in results),
        "checks": rows,
        "digest": hashlib.sha256(canonical).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="isolation_probe")
    parser.add_argument("--expected-uid", type=int, default=EXPECTED_UID)
    parser.add_argument("--workspace", default="/workspace")
    parser.add_argument("--forbidden-path", action="append", default=[])
    parser.add_argument("--forbidden-env", action="append", default=[])
    parser.add_argument("--refused-tcp", action="append", default=[], metavar="NAME=HOST:PORT")
    parser.add_argument("--readable-path", action="append", default=[])
    parser.add_argument("--reachable-tcp", action="append", default=[], metavar="NAME=HOST:PORT")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = receipt(run_checks(args))
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
