"""Verdict contract for ``scripts/pytest_gate.py`` (bu-5hp74).

A pytest run can end with **no summary line at all**. The observed shape
(bu-6jv4m.11) is an xdist run whose controller is signal-killed with the agent
tool call's process group: each worker's ``pytest_sessionfinish`` hookwrapper
(``xdist/remote.py:142``) then raises ``OSError: cannot send (already
closed?)`` trying to post ``workerfinished`` back down a dead execnet channel,
and the log simply stops. No ``F``, no ``FAILURES`` section, no ``N passed``.

That truncation is byte-for-byte indistinguishable from a run still in flight,
so anything deciding "did the suite pass?" by the *absence* of a failure line
reads a killed run as clean. These tests pin the opposite rule: a verdict
requires a **positive terminator** — a pytest summary line, or a gate sentinel
carrying the process exit code. Anything else is UNKNOWN, and UNKNOWN is never
a pass.

The two halves under test:

* ``verdict`` — classifies a log. Exit 0 PASS / 1 FAILED / 2 UNKNOWN, so a
  shell ``&&`` chain fails closed on a truncated log.
* ``run`` — spawns pytest in its own session (so a process-group signal aimed
  at the caller cannot reach it) and has the *child* append the sentinel, so
  the receipt survives the parent being killed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE = _REPO_ROOT / "scripts" / "pytest_gate.py"

PASS, FAILED, UNKNOWN = 0, 1, 2


def _verdict(tmp_path: Path, log_text: str) -> subprocess.CompletedProcess[str]:
    log = tmp_path / "pytest.log"
    log.write_text(log_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(_GATE), "verdict", str(log)],
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Real log shapes
# ---------------------------------------------------------------------------

#: The bu-6jv4m.11 truncation, trimmed. Note what it does NOT contain: any
#: ``F``, any ``FAILURES`` header, any summary line. Grepping it for failures
#: returns nothing, which is exactly why it reads as clean.
TRUNCATED_XDIST_LOG = """\
tests/core/test_a.py ......                                              [ 12%]
tests/core/test_b.py ..........                                          [ 31%]
tests/config/test_c.py ....
INTERNALERROR> Traceback (most recent call last):
INTERNALERROR>   File "/repo/.venv/lib/python3.12/site-packages/xdist/remote.py", line 149, in pytest_sessionfinish
INTERNALERROR>     self.sendevent("workerfinished", workeroutput=workeroutput)
INTERNALERROR> OSError: cannot send (already closed?)
/repo/.venv/lib/python3.12/site-packages/_pytest/main.py:365: PluggyTeardownRaisedWarning
"""

PASSING_LOG = """\
tests/core/test_a.py ......                                              [100%]

1348 passed, 21 skipped in 612.34s (0:10:12)
"""

FAILING_LOG = """\
tests/core/test_a.py .F....                                              [100%]
=========================== short test summary info ============================
FAILED tests/core/test_a.py::test_b - assert 1 == 2
1 failed, 5 passed in 3.21s
"""


# ---------------------------------------------------------------------------
# verdict: the fail-open case
# ---------------------------------------------------------------------------


def test_truncated_xdist_log_is_unknown_not_pass(tmp_path: Path) -> None:
    """The defect itself: no summary line must never be credited as a pass."""
    result = _verdict(tmp_path, TRUNCATED_XDIST_LOG)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout
    assert "PASS" not in result.stdout


def test_truncated_log_names_the_killed_controller_signature(tmp_path: Path) -> None:
    """UNKNOWN is more useful when it says which UNKNOWN this is."""
    result = _verdict(tmp_path, TRUNCATED_XDIST_LOG)
    assert "already closed" in result.stdout


def test_log_with_no_failure_markers_at_all_is_unknown(tmp_path: Path) -> None:
    """A log truncated before any test failed still has no verdict."""
    result = _verdict(tmp_path, "tests/core/test_a.py ......   [ 12%]\n")
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


def test_empty_log_is_unknown(tmp_path: Path) -> None:
    result = _verdict(tmp_path, "")
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


def test_missing_log_is_unknown(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_GATE), "verdict", str(tmp_path / "nope.log")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# verdict: positive terminators
# ---------------------------------------------------------------------------


def test_summary_line_with_passes_is_pass(tmp_path: Path) -> None:
    result = _verdict(tmp_path, PASSING_LOG)
    assert result.returncode == PASS, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_summary_line_with_failures_is_failed(tmp_path: Path) -> None:
    result = _verdict(tmp_path, FAILING_LOG)
    assert result.returncode == FAILED, result.stdout + result.stderr
    assert "FAILED" in result.stdout


def test_decorated_summary_line_is_recognised(tmp_path: Path) -> None:
    """Non-quiet pytest pads the summary with ``=``; still a terminator."""
    log = "======================== 2 passed, 1 skipped in 0.42s =========================\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == PASS, result.stdout + result.stderr


def test_errors_without_the_word_failed_are_not_a_pass(tmp_path: Path) -> None:
    """Docker contention yields ``N errors`` and no ``N failed`` (AGENTS.md)."""
    log = "13727 passed, 21 skipped, 10 errors in 1502.11s (0:25:02)\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == FAILED, result.stdout + result.stderr


def test_no_tests_ran_is_unknown(tmp_path: Path) -> None:
    """Nothing was verified, so there is nothing to call green."""
    result = _verdict(tmp_path, "no tests ran in 0.01s\n")
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


def test_last_summary_line_wins(tmp_path: Path) -> None:
    """A rerun appends; the trailing summary is the one that describes the run."""
    result = _verdict(tmp_path, FAILING_LOG + "\n" + PASSING_LOG)
    assert result.returncode == PASS, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# verdict: the sentinel outranks the prose
# ---------------------------------------------------------------------------


def test_sentinel_exit_zero_is_pass(tmp_path: Path) -> None:
    log = "tests/core/test_a.py ...\n## pytest-gate exit=0 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == PASS, result.stdout + result.stderr


def test_sentinel_exit_one_is_failed_even_without_a_summary_line(tmp_path: Path) -> None:
    log = "tests/core/test_a.py ...\n## pytest-gate exit=1 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == FAILED, result.stdout + result.stderr


def test_sentinel_signal_exit_is_unknown(tmp_path: Path) -> None:
    """Exit 144 = 128+16 (SIGUSR1): the run was killed, it did not fail."""
    log = PASSING_LOG + "## pytest-gate exit=144 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "144" in result.stdout


def test_sentinel_outranks_a_stale_summary_line(tmp_path: Path) -> None:
    """A green summary from an earlier phase cannot outvote a nonzero exit."""
    log = PASSING_LOG + "## pytest-gate exit=1 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == FAILED, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# run: the sentinel is written, and by a process that survives the caller
# ---------------------------------------------------------------------------


def _fake_python(tmp_path: Path, exit_code: int, *, sleep_seconds: float = 0.0) -> Path:
    """A stand-in for the ``-m pytest`` interpreter, so no real suite is run."""
    path = tmp_path / "fake_python"
    path.write_text(
        f"#!/bin/sh\necho 'fake pytest args:' \"$@\"\nsleep {sleep_seconds}\nexit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_run_appends_the_sentinel_with_the_real_exit_code(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    result = subprocess.run(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 1)),
            "--",
            "tests/",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "## pytest-gate exit=1 " in log.read_text(encoding="utf-8")


def test_run_output_is_readable_by_verdict(tmp_path: Path) -> None:
    """The two halves agree: what ``run`` writes, ``verdict`` classifies."""
    log = tmp_path / "run.log"
    subprocess.run(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 0)),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    verdict = subprocess.run(
        [sys.executable, str(_GATE), "verdict", str(log)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verdict.returncode == PASS, verdict.stdout + verdict.stderr


def test_run_child_survives_a_signal_to_the_callers_process_group(tmp_path: Path) -> None:
    """The AGENTS.md failure mode, reproduced and closed.

    An agent tool call that times out signals its whole process group. If the
    pytest child shares that group it dies mid-run and the log truncates with
    no verdict. ``run`` puts the child in its own session, and the child — not
    the runner — appends the sentinel, so the receipt is written even though
    the runner is gone.
    """
    log = tmp_path / "run.log"
    runner = subprocess.Popen(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 0, sleep_seconds=2)),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        time.sleep(0.75)
        os.killpg(os.getpgid(runner.pid), signal.SIGTERM)
        runner.wait(timeout=10)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if "## pytest-gate exit=" in log.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        else:  # pragma: no cover - only on regression
            pytest.fail(
                "no sentinel after the caller's process group was signalled; "
                f"log was:\n{log.read_text(encoding='utf-8')}"
            )
    finally:
        if runner.poll() is None:  # pragma: no cover - defensive
            runner.kill()

    assert "## pytest-gate exit=0 " in log.read_text(encoding="utf-8")
