"""Privileged, finite supervisor for only this installer's dependency group.

Only reserved exit codes 80/81/82 establish cleaned success/failure/timeout.
Every other exit is unproven and forbids retry. EOF from Node cancels the attempt.
"""

from __future__ import annotations

import ctypes
import os
import selectors
import signal
import subprocess
import sys
import time

CLEANUP_SECONDS = 2
CLEANED_SUCCESS = 80
CLEANED_FAILURE = 81
CLEANED_TIMEOUT = 82
UNPROVEN = 125


def main() -> int:
    # Adopt orphaned grandchildren. This supervisor never launches other work.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        print("Cannot establish dependency cleanup ownership", file=sys.stderr)
        return UNPROVEN
    term_seconds, kill_seconds = (float(value) / 1000 for value in sys.argv[1:3])
    cancelled = False

    def cancel(*_args: object) -> None:
        nonlocal cancelled
        cancelled = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, cancel)

    child = None
    selector = None
    outcome = UNPROVEN
    cleaned = False
    started = time.monotonic()
    try:
        # Lifetime protection starts before Popen, including every setup step.
        # Preserve the locked Node path without forwarding the caller's env.
        node_directory = os.path.dirname(sys.argv[3])
        child = subprocess.Popen(
            [sys.argv[4], "install-deps", "chromium"],
            start_new_session=True,
            env={**os.environ, "PATH": node_directory + os.pathsep + os.environ["PATH"]},
        )
        selector = selectors.DefaultSelector()
        selector.register(sys.stdin, selectors.EVENT_READ)
        termed = False
        while True:
            # WNOWAIT reserves even an exited leader's PID/PGID. Never poll or
            # wait before the final killpg: root must not signal a reused group.
            status = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if status is not None and not termed:
                outcome = (
                    CLEANED_SUCCESS
                    if status.si_code == os.CLD_EXITED and status.si_status == 0
                    else CLEANED_FAILURE
                )
                break
            elapsed = time.monotonic() - started
            if cancelled or elapsed >= kill_seconds:
                outcome = CLEANED_TIMEOUT
                break
            if not termed and elapsed >= term_seconds:
                termed = True
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass  # The finalizer must still establish actual cleanup.
            if selector.select(0.02):
                os.read(sys.stdin.fileno(), 4096)
                cancelled = True
    except Exception as error:
        # Generic supervisor/setup failure never authorizes retry, even if its
        # finalizer succeeds. Do not include arbitrary child/OS error values.
        print(f"Playwright dependency supervisor failed: {type(error).__name__}", file=sys.stderr)
    finally:
        if child is not None:
            group = child.pid
            try:
                os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            # Only now release the reserved leader and reap adopted descendants.
            deadline = time.monotonic() + CLEANUP_SECONDS
            while True:
                child.poll()
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    cleaned = True
                    break
                if pid == 0:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
        if selector is not None:
            selector.close()
    if not cleaned or outcome == UNPROVEN:
        print(
            "Playwright dependency cleanup could not be confirmed; refusing retry", file=sys.stderr
        )
        return UNPROVEN
    return outcome


if __name__ == "__main__":
    raise SystemExit(main())
