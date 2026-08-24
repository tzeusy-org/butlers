#!/usr/bin/env python3
"""Run pytest so its outcome is provable, and read that outcome honestly (bu-5hp74).

A pytest run can end with **no summary line at all**. The observed shape is an
xdist run whose controller is signal-killed with the calling process group --
an agent tool call hitting its harness timeout is the reliable way to produce
one. Each worker's ``pytest_sessionfinish`` hookwrapper (``xdist/remote.py``)
then raises ``OSError: cannot send (already closed?)`` posting ``workerfinished``
down a dead execnet channel, and the log stops mid-progress-line. There is no
``F``, no ``FAILURES`` section, no ``N passed``.

The hazard is that this truncation is byte-for-byte indistinguishable from a run
still in flight, and *greps clean*. Deciding "did the suite pass?" from the
absence of a failure line credits a killed run as a green one.

So this tool refuses to infer a verdict from absence. It requires a **positive
terminator**:

* a gate sentinel line carrying the process exit code -- authoritative, because
  the exit status is the only thing that knows the difference between "no
  failures" and "never finished"; or
* a pytest summary line (``N passed ...``, ``N failed ...``, ``no tests ran``).

With neither, the verdict is UNKNOWN, and UNKNOWN is never a pass.

Usage::

    scripts/pytest_gate.py run [--log PATH] [--detach] [--] <pytest args...>
    scripts/pytest_gate.py verdict PATH

``verdict`` exits 0 PASS / 1 FAILED / 2 UNKNOWN, so a shell ``&&`` chain fails
closed on a truncated log.

``run`` spawns pytest in its own session, so a signal aimed at the caller's
process group cannot reach it, and has the *child* append the sentinel -- the
receipt is written even when the runner itself is killed. Same shape as the
EXIT-trap receipt in ``deploy/backup/pg_dump.sh``: a run that publishes no
receipt is a run with no verdict, not a run that went fine.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

PASS, FAILED, UNKNOWN = 0, 1, 2

SENTINEL_PREFIX = "## pytest-gate exit="

_SENTINEL_RE = re.compile(rf"^{re.escape(SENTINEL_PREFIX)}(?P<code>\d+)\b", re.MULTILINE)

#: pytest's terminal counts line, with or without the ``=`` padding it wears
#: outside ``-q``: ``1348 passed, 21 skipped in 612.34s (0:10:12)``.
_SUMMARY_RE = re.compile(
    r"^=*\s*(?P<body>no tests ran|\d+ [a-z]+(?:, \d+ [a-z]+)*)"
    r" in \d+(?:\.\d+)?s(?: \(\d+:\d+:\d+\))?\s*=*$",
    re.MULTILINE,
)

#: The tell that a truncated log is a killed controller rather than a stall.
_KILLED_CONTROLLER_MARKER = "cannot send (already closed?)"

#: The child appends this itself, so the receipt outlives the runner.
_INNER_SH = f"""\
interpreter="$1"; shift
"$interpreter" -m pytest "$@"
exit_code=$?
printf '{SENTINEL_PREFIX}%s at %s\\n' "$exit_code" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit "$exit_code"
"""


class Verdict:
    """A classification plus the sentence that justifies it."""

    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason

    @property
    def name(self) -> str:
        return {PASS: "PASS", FAILED: "FAILED", UNKNOWN: "UNKNOWN"}[self.code]


def _exit_code_verdict(code: int) -> Verdict:
    """Classify a pytest process exit status.

    Only 0 is a pass and only 1 is a failure. Everything else -- interrupted,
    internal error, usage error, nothing collected, killed by a signal -- means
    the suite never rendered a verdict, which is precisely what UNKNOWN is for.
    """
    if code == 0:
        return Verdict(PASS, f"pytest exited {code}")
    if code == 1:
        return Verdict(FAILED, f"pytest exited {code} (tests failed)")
    if code == 5:
        return Verdict(UNKNOWN, f"pytest exited {code} (no tests collected); nothing was verified")
    if code > 128:
        signal_number = code - 128
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = f"signal {signal_number}"
        return Verdict(
            UNKNOWN,
            f"pytest exited {code} = 128+{signal_number} ({signal_name}): killed, not failed",
        )
    return Verdict(UNKNOWN, f"pytest exited {code}: the run did not complete")


def _summary_verdict(body: str) -> Verdict:
    quoted = f"summary line reports `{body}`"
    if "failed" in body or "error" in body:
        return Verdict(FAILED, quoted)
    if body == "no tests ran":
        return Verdict(UNKNOWN, f"{quoted}; nothing was verified")
    if "passed" in body:
        return Verdict(PASS, quoted)
    return Verdict(UNKNOWN, f"{quoted}, which counts no passing tests")


def classify(log_text: str) -> Verdict:
    """Classify a pytest log, requiring a positive terminator to call it either way."""
    sentinels = _SENTINEL_RE.findall(log_text)
    if sentinels:
        return _exit_code_verdict(int(sentinels[-1]))

    summaries = _SUMMARY_RE.findall(log_text)
    if summaries:
        return _summary_verdict(summaries[-1])

    reason = "no gate sentinel and no pytest summary line: the log carries no verdict"
    if _KILLED_CONTROLLER_MARKER in log_text:
        reason += (
            f"\n  an xdist worker reports `{_KILLED_CONTROLLER_MARKER}`, the signature of the"
            "\n  controller being signal-killed mid-run (AGENTS.md) -- not a red suite"
        )
    else:
        reason += "\n  it is either still in flight or it was killed; those look identical here"
    return Verdict(UNKNOWN, reason)


def _default_log_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(".tmp/test-logs") / f"pytest-{Path.cwd().name}-{stamp}-{os.getpid()}.log"


def _run(args: argparse.Namespace) -> int:
    log_path = Path(args.log) if args.log else _default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            ["sh", "-c", _INNER_SH, "sh", args.python, *args.pytest_args],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    print(f"log:     {log_path}", file=sys.stderr)
    print(f"verdict: {Path(__file__).name} verdict {log_path}", file=sys.stderr)

    if args.detach:
        print(
            f"pid:     {process.pid} (own session; group signals cannot reach it)",
            file=sys.stderr,
        )
        return 0
    return process.wait()


def _verdict(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        verdict = Verdict(UNKNOWN, f"cannot read the log: {exc}")
    else:
        verdict = classify(log_text)

    print(f"{verdict.name}  {log_path}")
    print(f"  {verdict.reason}")
    return verdict.code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run pytest and record a sentinel receipt")
    run_parser.add_argument("--log", help="log path (default: .tmp/test-logs/pytest-*.log)")
    run_parser.add_argument(
        "--python",
        default=sys.executable,
        help="interpreter that runs `-m pytest` (default: this one)",
    )
    run_parser.add_argument(
        "--detach",
        action="store_true",
        help="return immediately; poll the log and read the verdict later",
    )
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    run_parser.set_defaults(handler=_run)

    verdict_parser = subparsers.add_parser("verdict", help="classify a pytest log")
    verdict_parser.add_argument("log")
    verdict_parser.set_defaults(handler=_verdict)

    args = parser.parse_args(argv)
    if args.command == "run" and args.pytest_args and args.pytest_args[0] == "--":
        args.pytest_args = args.pytest_args[1:]
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
