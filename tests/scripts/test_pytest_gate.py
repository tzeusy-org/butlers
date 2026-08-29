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
import shutil
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

#: What `--maxfail=1` actually produces (bu-17myd). The run is *interrupted*
#: rather than completed, so pytest exits 2 -- but it still printed its counts,
#: and those counts say a test failed.
MAXFAIL_INTERRUPTED_LOG = """\
tests/core/test_a.py .F                                                  [ 33%]
=========================== short test summary info ============================
FAILED tests/core/test_a.py::test_b - assert 1 == 2
!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!
1 failed, 6482 passed in 1502.11s (0:25:02)
## pytest-gate exit=2 at 2026-08-25T00:00:00Z
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
# verdict: exit 2 is interrupted, and --maxfail interrupts on an ordinary failure
# ---------------------------------------------------------------------------


def test_maxfail_interrupted_run_is_failed_not_unknown(tmp_path: Path) -> None:
    """The bu-17myd defect: the gate's own `--maxfail=1` makes red runs exit 2.

    Labelling an ordinary test failure UNKNOWN teaches readers that UNKNOWN
    means "probably just a real failure", which is exactly the reflex UNKNOWN
    exists to prevent. UNKNOWN has to stay rare to stay meaningful.
    """
    result = _verdict(tmp_path, MAXFAIL_INTERRUPTED_LOG)
    assert result.returncode == FAILED, result.stdout + result.stderr
    assert "FAILED" in result.stdout
    assert "UNKNOWN" not in result.stdout


def test_exit_two_with_an_error_summary_is_failed(tmp_path: Path) -> None:
    """`-x` on a collection error interrupts too, and errors are failures here."""
    log = "10 passed, 2 errors in 4.20s\n## pytest-gate exit=2 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == FAILED, result.stdout + result.stderr


def test_exit_two_with_no_summary_line_stays_unknown(tmp_path: Path) -> None:
    """Interrupted before pytest printed its counts: nothing was established."""
    log = "tests/core/test_a.py ...\n## pytest-gate exit=2 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "UNKNOWN" in result.stdout


def test_exit_two_with_a_green_summary_is_unknown_not_pass(tmp_path: Path) -> None:
    """A Ctrl-C mid-run prints counts with no failures; exit 2 still means unfinished.

    The summary may only take exit 2 *down* to FAILED. It may never take it up
    to PASS -- an interrupted run has not verified the tests it never reached.
    """
    log = PASSING_LOG + "## pytest-gate exit=2 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "PASS" not in result.stdout


def test_exit_two_reads_the_last_summary_line(tmp_path: Path) -> None:
    """A rerun appends; the trailing counts describe the run that exited 2."""
    log = FAILING_LOG + PASSING_LOG + "## pytest-gate exit=2 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


# ---------------------------------------------------------------------------
# verdict: the other nonzero exits are not softened by a summary line
# ---------------------------------------------------------------------------


def test_sentinel_exit_five_is_unknown_not_pass(tmp_path: Path) -> None:
    """Exit 5 = nothing collected. A typo'd path verified no code at all."""
    log = "no tests ran in 0.01s\n## pytest-gate exit=5 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "PASS" not in result.stdout


def test_sentinel_exit_five_with_a_stale_green_summary_is_unknown(tmp_path: Path) -> None:
    """Only exit 2 consults the summary; a green line cannot rescue exit 5."""
    log = PASSING_LOG + "## pytest-gate exit=5 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


def test_sentinel_exit_four_with_a_failure_summary_is_unknown(tmp_path: Path) -> None:
    """Exit 4 is a usage error: the gate misfired, so no suite verdict exists."""
    log = FAILING_LOG + "## pytest-gate exit=4 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr


def test_signal_exit_with_a_failure_summary_is_unknown_not_failed(tmp_path: Path) -> None:
    """Killed, not failed: the counts describe the part of the run that happened."""
    log = FAILING_LOG + "## pytest-gate exit=143 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == UNKNOWN, result.stdout + result.stderr
    assert "143" in result.stdout


def test_sentinel_exit_zero_with_a_stale_failure_summary_is_pass(tmp_path: Path) -> None:
    """A rerun that went green exits 0; the earlier red summary does not outvote it."""
    log = FAILING_LOG + "## pytest-gate exit=0 at 2026-08-25T00:00:00Z\n"
    result = _verdict(tmp_path, log)
    assert result.returncode == PASS, result.stdout + result.stderr


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


# ---------------------------------------------------------------------------
# The premise: a real xdist --maxfail run really does exit 2
# ---------------------------------------------------------------------------


def test_a_real_maxfail_run_exits_two_and_reads_as_failed(tmp_path: Path) -> None:
    """End-to-end, against real pytest: the exit-2 classification is not theoretical.

    Under xdist the controller answers ``--maxfail`` by raising ``Interrupted``
    (``xdist/dsession.py``), which pytest reports as exit 2 — where a serial run
    would raise ``Failed`` and exit 1. The default ``make test-qg`` path is
    xdist (it passes ``-n auto``), while ``make test-qg-serial`` explicitly uses
    ``-n 0``. Every quality-gate invocation passes ``--maxfail=1``, so this is
    the shape of an ordinary red parallel gate run.

    Pinning it here means that if pytest or xdist ever changes that status, the
    premise fails loudly instead of the classification quietly going stale.
    """
    (tmp_path / "test_red.py").write_text("def test_red():\n    assert 1 == 2\n", encoding="utf-8")
    (tmp_path / "test_green.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('i', range(6))\ndef test_ok(i):\n"
        "    assert True\n",
        encoding="utf-8",
    )
    log = tmp_path / "run.log"
    subprocess.run(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--log",
            str(log),
            "--",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "--no-header",
            "-q",
            "--maxfail=1",
            "--tb=no",
            "-n",
            "2",
            "--dist",
            "loadfile",
            ".",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    log_text = log.read_text(encoding="utf-8")
    assert "## pytest-gate exit=2 " in log_text, log_text

    verdict = subprocess.run(
        [sys.executable, str(_GATE), "verdict", str(log)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verdict.returncode == FAILED, verdict.stdout + verdict.stderr + log_text
    assert "UNKNOWN" not in verdict.stdout


# ---------------------------------------------------------------------------
# run --tee: the log is mirrored live, without a pipe between caller and pytest
# ---------------------------------------------------------------------------


def _run_gate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_GATE), "run", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_tee_mirrors_the_run_to_stdout(tmp_path: Path) -> None:
    """`make test-qg` stays watchable: what lands in the log also lands on stdout."""
    log = tmp_path / "run.log"
    result = _run_gate(
        "--tee", "--log", str(log), "--python", str(_fake_python(tmp_path, 0)), "--", "tests/"
    )
    assert result.returncode == PASS, result.stdout + result.stderr
    assert "fake pytest args:" in result.stdout
    assert "## pytest-gate exit=0 " in result.stdout
    assert result.stdout.count("## pytest-gate exit=") == 1


def test_without_tee_stdout_stays_quiet(tmp_path: Path) -> None:
    """The low-context path is unchanged: the log holds the output, not the terminal."""
    log = tmp_path / "run.log"
    result = _run_gate("--log", str(log), "--python", str(_fake_python(tmp_path, 0)))
    assert "fake pytest args:" not in result.stdout
    assert "fake pytest args:" in log.read_text(encoding="utf-8")


def test_tee_does_not_replay_an_existing_log(tmp_path: Path) -> None:
    """`run` appends. Only this run's output is mirrored, not the previous run's."""
    log = tmp_path / "run.log"
    log.write_text("output from an earlier run\n", encoding="utf-8")
    result = _run_gate("--tee", "--log", str(log), "--python", str(_fake_python(tmp_path, 0)))
    assert "earlier run" not in result.stdout
    assert "fake pytest args:" in result.stdout


def test_tee_shows_output_while_the_run_is_still_going(tmp_path: Path) -> None:
    """Mirrored, not buffered to the end -- a 30-minute gate has to show progress."""
    log = tmp_path / "run.log"
    process = subprocess.Popen(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--tee",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 0, sleep_seconds=8)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    started = time.monotonic()
    try:
        assert process.stdout is not None
        line = process.stdout.readline()
        elapsed = time.monotonic() - started
        assert "fake pytest args:" in line, line
        # The child runs for 8s. A mirror that only flushed at the end would
        # deliver this line at ~8s, and would still be a regression.
        assert elapsed < 5, f"the first line took {elapsed:.1f}s: nothing was mirrored live"
        assert process.poll() is None, "the run had already finished; this proves nothing"
    finally:
        process.kill()
        process.wait(timeout=10)


def test_tee_leaves_no_pipe_between_the_caller_and_pytest(tmp_path: Path) -> None:
    """The caller going away must not take the run -- or its receipt -- with it.

    Teeing through a pipe would hand pytest a broken one the moment the caller
    died. ``--tee`` follows the file instead, so killing the runner leaves the
    child writing happily to the log in its own session.
    """
    log = tmp_path / "run.log"
    runner = subprocess.Popen(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--tee",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 0, sleep_seconds=2)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        time.sleep(0.75)
        os.killpg(os.getpgid(runner.pid), signal.SIGKILL)
        runner.wait(timeout=10)

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if "## pytest-gate exit=0 " in log.read_text(encoding="utf-8"):
                return
            time.sleep(0.1)
        pytest.fail(  # pragma: no cover - only on regression
            f"no sentinel after the tee'ing runner was killed; log was:\n"
            f"{log.read_text(encoding='utf-8')}"
        )
    finally:
        if runner.poll() is None:  # pragma: no cover - defensive
            runner.kill()


def test_tee_survives_its_reader_hanging_up(tmp_path: Path) -> None:
    """`make test-qg | head` closes the pipe early; the run must not care."""
    log = tmp_path / "run.log"
    runner = subprocess.Popen(
        [
            sys.executable,
            str(_GATE),
            "run",
            "--tee",
            "--log",
            str(log),
            "--python",
            str(_fake_python(tmp_path, 0, sleep_seconds=3)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert runner.stdout is not None and runner.stderr is not None
    try:
        time.sleep(0.5)
        runner.stdout.close()
        assert runner.wait(timeout=30) == PASS
        assert "## pytest-gate exit=0 " in log.read_text(encoding="utf-8")
        assert b"BrokenPipeError" not in runner.stderr.read()
    finally:
        if runner.poll() is None:  # pragma: no cover - defensive
            runner.kill()
        runner.stderr.close()


# ---------------------------------------------------------------------------
# The quality-gate targets are wired to it (bu-ecizp)
# ---------------------------------------------------------------------------


def _make_recipe(target: str) -> str:
    """What `make <target>` would actually run, make variables already expanded."""
    if shutil.which("make") is None:  # pragma: no cover - make is a dev/CI prerequisite
        pytest.skip("make is not installed")
    result = subprocess.run(
        ["make", "--dry-run", target],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.mark.parametrize("target", ["test-qg", "test-qg-serial"])
def test_quality_gate_targets_run_through_the_gate(target: str) -> None:
    """A raw `uv run pytest` here is the fail-open hole this tool closes (bu-ecizp)."""
    recipe = _make_recipe(target)
    assert "pytest_gate.py run" in recipe
    assert "pytest_gate.py verdict" in recipe, "without a verdict step nothing reads the receipt"
    assert "uv run pytest" not in recipe
    assert ".tmp/test-logs/" in recipe, "the log has to land somewhere a verdict can be read from"


@pytest.mark.parametrize("target", ["test-qg", "test-qg-serial"])
def test_quality_gate_targets_use_the_project_interpreter(target: str) -> None:
    """`python3` is the venv-less interpreter: ModuleNotFoundError, exit 4, UNKNOWN (adb0261bc)."""
    recipe = _make_recipe(target)
    assert "uv run python scripts/pytest_gate.py" in recipe
    assert "python3 scripts/pytest_gate.py" not in recipe
