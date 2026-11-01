#!/usr/bin/env python3
"""Install uv with finite retries and ownership of every installer process group."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

ATTEMPT_SECONDS = 120
CLEANUP_SECONDS = 1
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 10
INSTALL_COMMAND = "set -o pipefail; curl -LsSf https://astral.sh/uv/install.sh | sh"


def install_attempt() -> int:
    """Reserve the leader PID until all members of its owned group are killed."""
    deadline = time.monotonic() + ATTEMPT_SECONDS
    process = None
    timed_out = False
    interrupted = []

    def record_signal(signum: int, _frame: object) -> None:
        # Defer interruption until Popen has returned its owned PID. Raising
        # between child creation and assignment could otherwise lose ownership.
        interrupted.append(signum)

    previous = {sig: signal.signal(sig, record_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        process = subprocess.Popen(["bash", "-c", INSTALL_COMMAND], start_new_session=True)
        while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            if interrupted:
                break
            remaining = deadline - CLEANUP_SECONDS - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            time.sleep(min(0.05, remaining))
    finally:
        # Do not poll/wait before killpg: even an exited leader must retain its
        # PID so no unrelated process group can reuse that ownership identity.
        # Successful/failed shell exits can also leave descendants holding pipes.
        try:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=max(0.001, min(CLEANUP_SECONDS, deadline - time.monotonic())))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if interrupted:
        raise SystemExit(128 + interrupted[0])
    return 124 if timed_out else process.returncode


def main() -> int:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if install_attempt() == 0:
            print(f"uv installed on attempt {attempt}", flush=True)
            with Path(os.environ["GITHUB_PATH"]).open("a") as destination:
                destination.write(str(Path.home() / ".local/bin") + "\n")
            return 0
        if attempt < MAX_ATTEMPTS:
            print(f"uv install attempt {attempt} failed; retrying in 10s...", flush=True)
            time.sleep(BACKOFF_SECONDS)
    print(f"::error::uv install failed after {MAX_ATTEMPTS} attempts", flush=True)
    return 1


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)
    raise SystemExit(main())
