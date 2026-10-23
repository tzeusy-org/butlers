"""Privileged, finite supervisor for only this installer's dependency group.

Run under sudo before starting Playwright so apt never changes privilege or
escapes through a nested sudo session. EOF from the Node parent cancels the
attempt. Exit 124 means a cleaned timeout; 125 forbids retry (cleanup unproven).
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


def main() -> int:
    # Adopt orphaned grandchildren, so their exit is reaped here rather than
    # delegated to the runner's init. This supervisor never launches other work.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        print("Cannot establish dependency cleanup ownership", file=sys.stderr)
        return 125
    term_seconds, kill_seconds = (float(value) / 1000 for value in sys.argv[1:3])
    cancelled = False

    def cancel(*_args: object) -> None:
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    signal.signal(signal.SIGHUP, cancel)
    # Preserve the caller's locked Node executable without forwarding its env
    # through sudo. Playwright's executable uses /usr/bin/env node.
    node_directory = os.path.dirname(sys.argv[3])
    child = subprocess.Popen(
        [sys.argv[4], "install-deps", "chromium"],
        start_new_session=True,
        env={**os.environ, "PATH": node_directory + os.pathsep + os.environ["PATH"]},
    )
    group = child.pid

    def send(sig: signal.Signals) -> None:
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # A denied signal is never proof of cleanup, even under sudo.
            pass

    def reaped() -> bool:
        # poll() owns the direct child's status. waitpid then reaps any adopted
        # descendants, and ECHILD proves none of our processes remain alive.
        child.poll()
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return True
            if pid == 0:
                return False

    selector = selectors.DefaultSelector()
    selector.register(sys.stdin, selectors.EVENT_READ)
    started = time.monotonic()
    termed = False
    result = 124
    try:
        while True:
            elapsed = time.monotonic() - started
            code = child.poll()
            if code is not None and not termed:
                result = code
                break
            if cancelled or elapsed >= kill_seconds:
                break
            if not termed and elapsed >= term_seconds:
                termed = True
                send(signal.SIGTERM)
            if selector.select(0.02):
                # EOF or any cancellation message means the parent is leaving.
                os.read(sys.stdin.fileno(), 4096)
                cancelled = True
    finally:
        selector.close()
        send(signal.SIGKILL)
        deadline = time.monotonic() + CLEANUP_SECONDS
        while not reaped():
            if time.monotonic() >= deadline:
                print(
                    "Playwright dependency cleanup could not be confirmed; refusing retry",
                    file=sys.stderr,
                )
                return 125
            time.sleep(0.01)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
