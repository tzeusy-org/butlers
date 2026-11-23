"""Start-up gate for a QA investigator pod: no work until the egress policy is in force.

``python -I -m butlers.core.qa.egress_gate [options] -- <command> ...`` is what every investigator
container runs first (``build_investigator_job`` wraps its command with it). A NetworkPolicy is
programmed by the CNI after the pod starts, so a fresh pod can reach the network for a moment even
though a default-deny policy existed beforehand (the k3d isolation verifier measured exactly this:
the API server was reachable at start and refused ~20 seconds later). The investigator must not
touch the network, or read an untrusted checkout, in that window.

The gate dials one address the policy must deny, the cluster's own API server (the pod always
carries ``KUBERNETES_SERVICE_HOST``/``PORT``), until it has been refused ``--consecutive`` times in
a row, within ``--deadline`` seconds. Only then does it ``exec`` the real command, so there is no
extra process and no change to PID 1. It fails closed: when the deadline passes with the address
still reachable, or no target can be found, it exits ``EXIT_NOT_ENFORCED`` and the command never
runs. The reason is a fixed token on stderr, never an address or an error message.

Standard library only: it runs with ``-I`` before the investigator's own tooling is trusted.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from collections.abc import Callable, Sequence
from typing import Final, NoReturn

#: Not 0/1/2 (success, generic failure, usage) and not a signal: the launcher maps it to a
#: containment failure instead of an investigation that failed.
EXIT_NOT_ENFORCED: Final[int] = 70
DEFAULT_DEADLINE_S: Final[int] = 120
DEFAULT_CONSECUTIVE: Final[int] = 3
DEFAULT_INTERVAL_S: Final[float] = 1.0
_CONNECT_TIMEOUT_S: Final[float] = 2.0
REASON_NOT_ENFORCED: Final[str] = "egress_gate:policy_not_enforced"
REASON_NO_TARGET: Final[str] = "egress_gate:no_target"


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S):
            return True
    except OSError:
        return False


def target_from_env(env: dict[str, str] | os._Environ[str] = os.environ) -> tuple[str, int] | None:
    host = env.get("KUBERNETES_SERVICE_HOST", "")
    port = env.get("KUBERNETES_SERVICE_PORT", "")
    if not host or not port.isdigit():
        return None
    return host, int(port)


def wait_until_enforced(
    target: tuple[str, int],
    *,
    deadline_s: float,
    consecutive: int,
    interval_s: float,
    reachable: Callable[[str, int], bool] = _reachable,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once ``target`` was unreachable ``consecutive`` times in a row before the deadline."""
    give_up = clock() + deadline_s
    refused = 0
    while True:
        refused = 0 if reachable(*target) else refused + 1
        if refused >= consecutive:
            return True
        if clock() >= give_up:
            return False
        sleep(interval_s)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="egress_gate")
    parser.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE_S)
    parser.add_argument("--consecutive", type=int, default=DEFAULT_CONSECUTIVE)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> NoReturn:
    args = _parser().parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        sys.stderr.write("egress_gate: a command is required\n")
        raise SystemExit(2)
    target = target_from_env()
    if target is None:
        sys.stderr.write(f"{REASON_NO_TARGET}\n")
        raise SystemExit(EXIT_NOT_ENFORCED)
    if not wait_until_enforced(
        target,
        deadline_s=args.deadline,
        consecutive=max(1, args.consecutive),
        interval_s=args.interval,
    ):
        sys.stderr.write(f"{REASON_NOT_ENFORCED}\n")
        raise SystemExit(EXIT_NOT_ENFORCED)
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
