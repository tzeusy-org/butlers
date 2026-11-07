"""Pytest plugin: let ``scripts/ci_lanes.py`` read every pytest process's stacks if it hangs.

Loaded by ``scripts/ci_lanes.py run`` with ``-p ci_hang_evidence`` (bu-dhloi1.137). Each
process, controller and xdist workers alike, registers a SIGUSR1 handler that writes all its
threads' stacks to ``stacks-<process>-<pid>.txt`` in ``CI_HANG_STATE_DIR``. ``faulthandler``
does that at C level, so it works while a thread is stuck in a blocking C call or holds the
GIL, and outside the test call where pytest-timeout's timer applies. Each worker also records
the test it last started. The lane runner reads the file names to know which pids to signal.
"""

from __future__ import annotations

import faulthandler
import os
import signal
from pathlib import Path

_process = "controller"
_stream = None  # Kept open for the process's life: faulthandler writes to its descriptor.


def pytest_configure(config) -> None:
    global _process, _stream
    state = os.environ.get("CI_HANG_STATE_DIR")
    if not state:
        return
    _process = getattr(config, "workerinput", {}).get("workerid", "controller")
    path = Path(state) / f"stacks-{_process}-{os.getpid()}.txt"
    _stream = path.open("w", encoding="utf-8")
    faulthandler.register(signal.SIGUSR1, file=_stream, all_threads=True, chain=False)


def pytest_runtest_logstart(nodeid: str, location) -> None:
    state = os.environ.get("CI_HANG_STATE_DIR")
    if state and _process != "controller":
        (Path(state) / f"last-started-{_process}.txt").write_text(nodeid + "\n", encoding="utf-8")
