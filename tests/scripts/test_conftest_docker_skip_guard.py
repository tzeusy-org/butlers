"""Pins the root conftest's Docker-skip guard (bu-u5euwb.37).

Real-PG tests skip with ``"Docker not available"`` when the docker CLI is absent.
With ``BUTLERS_REQUIRE_DOCKER=1`` (set by ``scripts/ci_lanes.py`` for lanes that
guarantee Docker) that exact skip must fail the run; unrelated skips and xfails
must not, and with the flag unset the skip stays a skip.

The run is a real pytest subprocess with the root conftest loaded as a plugin,
so the hook is exercised through pytest's own skip and report machinery.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_SAMPLE = """\
import pytest

@pytest.mark.skipif(True, reason="Docker not available")
def test_marker_skip():
    pass

def test_runtime_skip():
    pytest.skip("Docker not available")

@pytest.mark.skip(reason="unrelated")
def test_unrelated_skip():
    pass

@pytest.mark.xfail(reason="Docker not available")
def test_xfail():
    assert False
"""


def _run(tmp_path: Path, *, require: str | None) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_sample.py").write_text(_SAMPLE)
    env = dict(os.environ)
    env.pop("BUTLERS_REQUIRE_DOCKER", None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    if require is not None:
        env["BUTLERS_REQUIRE_DOCKER"] = require
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "conftest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-rA",
            "test_sample.py",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_required_docker_turns_only_the_docker_skip_into_failures(tmp_path: Path) -> None:
    result = _run(tmp_path, require="1")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR test_sample.py::test_marker_skip" in result.stdout
    assert "FAILED test_sample.py::test_runtime_skip" in result.stdout
    assert "1 failed, 1 skipped, 1 xfailed, 1 error" in result.stdout
    assert ": unrelated" in result.stdout
    assert "XFAIL test_sample.py::test_xfail" in result.stdout


def test_without_the_flag_docker_skips_stay_skips(tmp_path: Path) -> None:
    result = _run(tmp_path, require=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 skipped, 1 xfailed" in result.stdout
