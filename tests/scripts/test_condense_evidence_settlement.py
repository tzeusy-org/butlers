"""REQ-testing-053: cancelled proof sides settle with complete observations."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_condensation_ledger as consumer  # noqa: E402
import condense_evidence as evidence  # noqa: E402

pytestmark = pytest.mark.unit

SLEEPER = (
    "import os, sys, time\n"
    "open(sys.argv[1] + '.tmp', 'w').write(f'{os.getppid()} {os.getpid()}')\n"
    "os.rename(sys.argv[1] + '.tmp', sys.argv[1])\n"
    "time.sleep(120)\n"
)
ROOT = (
    "import subprocess, sys, time\n"
    "subprocess.run([sys.executable, '-I', '-c', 'pass'], check=True)\n"
    f"subprocess.Popen([sys.executable, '-I', '-c', {SLEEPER!r}, sys.argv[1]])\n"
    "time.sleep(120)\n"
)


def test_killed_tracees_record_their_exits_without_signal_records(tmp_path):
    """REQ-testing-053: an owned cancellation's SIGKILLed tracees still record exits."""
    tool = shutil.which("strace")
    assert tool is not None, "the request-access observer requires installed strace"
    trace, marker = tmp_path / "trace", tmp_path / "ready"
    observer = subprocess.Popen(
        [tool, *consumer.REQUEST_TRACE_OPTIONS, "-o", str(trace)]
        + [sys.executable, "-I", "-c", ROOT, str(marker)],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 60
        while not marker.exists():
            assert observer.poll() is None and time.monotonic() < deadline
            time.sleep(0.05)
        root, child = (int(pid) for pid in marker.read_text().split())
        for pid in (root, child):
            os.kill(pid, signal.SIGKILL)
        observer.wait(timeout=60)
    finally:
        try:
            os.killpg(observer.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        observer.wait(timeout=60)
    lines = [line for line in trace.read_bytes().splitlines() if line]
    stream = evidence.RequestAccessStream(
        tmp_path, {"absent.json"}, tmp_path / "run.request.json", tmp_path / "run.json", "0" * 64
    )
    for line in lines:
        stream.consume(line)
    # A delivered-signal record would refuse the stream; the reaped ordinary
    # child still records its normal exit beside both killed tracees.
    assert not stream.failed and not stream.pending
    assert {root, child} < stream.actors and stream.actors == stream.exits
    assert {line.split()[0] for line in lines if line.endswith(b"+++ killed by SIGKILL +++")} == {
        str(root).encode(),
        str(child).encode(),
    }
