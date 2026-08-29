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

One exit status needs both terminators read together. pytest exits **2** when a
run is *interrupted*, and under xdist ``--maxfail`` interrupts on an ordinary
test failure: the controller sets ``shouldstop`` and raises ``Interrupted``
(``xdist/dsession.py``) where a serial run would raise ``Failed`` and exit 1.
Nearly every run here is an xdist run -- ``addopts`` carries ``-n 3``, and only
``make test-qg-serial`` overrides it with an explicit ``-n 0`` (bu-bcujm) -- and
every quality-gate invocation passes ``--maxfail``. Reading the code alone would
label the common red run UNKNOWN, teaching readers that UNKNOWN means "probably
just a real failure": exactly the reflex this tool exists to prevent. So exit 2
consults the log's last summary line, which is itself a positive terminator. If
those counts report failures the verdict is FAILED; otherwise the run really did
stop before establishing anything, and it stays UNKNOWN. The summary may only
take exit 2 *down* to FAILED, never up to PASS.

Usage::

    scripts/pytest_gate.py run [--log PATH] [--tee] [--detach] [--] <pytest args...>
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
from typing import BinaryIO

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

#: The child appends this itself, so the receipt outlives the runner. ``-u`` keeps
#: the log current: block-buffered output would leave a followed log seconds stale
#: and would drop the last few KB whenever the run is killed.
_INNER_SH = f"""\
interpreter="$1"; shift
"$interpreter" -u -m pytest "$@"
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


def _reports_failures(body: str) -> bool:
    """Whether a pytest counts line reports anything that went wrong.

    ``error`` matters as much as ``failed``: Docker contention yields
    ``N errors`` with no ``N failed`` at all (AGENTS.md).
    """
    return "failed" in body or "error" in body


def _interrupted_verdict(summary: str | None) -> Verdict:
    """Classify pytest's exit 2, the *interrupted* run, against its own counts.

    The default ``make test-qg`` path runs xdist with ``-n auto``. There,
    ``--maxfail`` stops the session by raising ``Interrupted``, so an ordinary
    red parallel gate exits 2. ``make test-qg-serial`` deliberately passes
    ``-n 0`` for order-dependent debugging, and its ordinary red run exits 1
    instead. Pytest still printed its counts on the way out, and those counts are
    a positive terminator. When they report failures, the run is FAILED and
    calling it UNKNOWN would only blunt the word.

    The counts cannot rescue the run, only convict it. A Ctrl-C partway through
    a green run prints a summary with no failures in it, and that run still
    never reached the tests it was interrupted before.
    """
    interrupted = "pytest exited 2 (run interrupted, e.g. by --maxfail)"
    if summary is None:
        return Verdict(UNKNOWN, f"{interrupted} before printing a summary line")
    if _reports_failures(summary):
        return Verdict(FAILED, f"{interrupted}; summary line reports `{summary}`")
    return Verdict(
        UNKNOWN,
        f"{interrupted}; summary line reports `{summary}`, which counts no failures,"
        "\n  so the run stopped without establishing anything",
    )


def _exit_code_verdict(code: int, summary: str | None) -> Verdict:
    """Classify a pytest process exit status, with the log's last counts line.

    Only 0 is a pass and only 1 is a plain failure. Exit 2 means interrupted,
    which ``--maxfail`` makes routine, so it is decided against ``summary``.
    Everything else -- internal error, usage error, nothing collected, killed by
    a signal -- means the suite never rendered a verdict, which is precisely
    what UNKNOWN is for, and no summary line softens that.
    """
    if code == 0:
        return Verdict(PASS, f"pytest exited {code}")
    if code == 1:
        return Verdict(FAILED, f"pytest exited {code} (tests failed)")
    if code == 2:
        return _interrupted_verdict(summary)
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
    if _reports_failures(body):
        return Verdict(FAILED, quoted)
    if body == "no tests ran":
        return Verdict(UNKNOWN, f"{quoted}; nothing was verified")
    if "passed" in body:
        return Verdict(PASS, quoted)
    return Verdict(UNKNOWN, f"{quoted}, which counts no passing tests")


def classify(log_text: str) -> Verdict:
    """Classify a pytest log, requiring a positive terminator to call it either way."""
    summaries = _SUMMARY_RE.findall(log_text)

    sentinels = _SENTINEL_RE.findall(log_text)
    if sentinels:
        return _exit_code_verdict(int(sentinels[-1]), summaries[-1] if summaries else None)

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


#: How often ``--tee`` looks for new bytes in the log, and how much it takes at a
#: time. Progress output is sparse, so a fifth of a second reads as live.
_MIRROR_POLL_SECONDS = 0.2
_MIRROR_CHUNK_BYTES = 65536


def _mirror(reader: BinaryIO, destination: BinaryIO) -> None:
    """Copy everything the log has gained since the last call."""
    while chunk := reader.read(_MIRROR_CHUNK_BYTES):
        destination.write(chunk)
        destination.flush()


def _tee(process: subprocess.Popen[bytes], log_path: Path, start_offset: int) -> int:
    """Mirror the growing log to this process's stdout until pytest exits.

    Deliberately *not* a pipe. Teeing through one would put the caller between
    pytest and its output: a closed pipe (the harness that launched `make` going
    away) would take the run down with it, and with it the receipt this whole
    tool exists to write. Following the file instead leaves the child writing to
    a plain file in its own session -- if this runner dies, pytest does not
    notice, and the sentinel still lands.
    """
    try:
        with log_path.open("rb") as reader:
            reader.seek(start_offset)
            while process.poll() is None:
                _mirror(reader, sys.stdout.buffer)
                time.sleep(_MIRROR_POLL_SECONDS)
            _mirror(reader, sys.stdout.buffer)
    except BrokenPipeError:
        # Whoever was reading this stdout hung up (``make test-qg | head``). The
        # mirror is a convenience; the run and its receipt are not, so keep
        # waiting for it. Point stdout at /dev/null so the interpreter's
        # exit-time flush does not raise the same error again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        process.wait()
    return process.returncode


def _run(args: argparse.Namespace) -> int:
    log_path = Path(args.log) if args.log else _default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start_offset = log_path.stat().st_size if log_path.exists() else 0

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
    if args.tee:
        return _tee(process, log_path, start_offset)
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
    run_parser.add_argument(
        "--tee",
        action="store_true",
        help="also mirror the log to stdout while it runs (ignored with --detach)",
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
