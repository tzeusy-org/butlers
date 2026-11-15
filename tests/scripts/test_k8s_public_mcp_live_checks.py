"""Static contract for scripts/k8s/public-mcp-live-checks.sh (RFC 0039 task 2.5).

The live checks exec Python inside the butlers-app image. That image puts no venv on PATH and
need not ship a `python3` name, so every exec names the image interpreter, and the isolation
probe, which imports asyncpg, runs through the sealed entrypoint that selects the venv.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = (_ROOT / "scripts" / "k8s" / "public-mcp-live-checks.sh").read_text()
_IMAGE_PYTHON = "/usr/local/bin/python -I"
_VENV_PYTHON = f"{_IMAGE_PYTHON} /app/scripts/runtime_clis/python_entrypoint.py python"


def _exec_lines() -> list[str]:
    return [line for line in _SCRIPT.splitlines() if re.search(r"\bexec\b.*--\s", line)]


def test_no_exec_relies_on_a_python_name_on_path():
    lines = _exec_lines()
    assert lines, "the live checks exec into pods"
    for line in lines:
        assert not re.search(r"--\s+python3?\b", line), line


def test_stdlib_probes_use_the_image_interpreter():
    python_execs = [line for line in _exec_lines() if "python" in line]
    assert len(python_execs) >= 3  # canaries count, tcp reachability, isolation probe
    for line in python_execs:
        assert f"-- {_IMAGE_PYTHON}" in line, line


def test_the_isolation_probe_runs_in_the_venv():
    (line,) = [
        line
        for line in _SCRIPT.splitlines()
        if "public-mcp-isolation-probe.py" in line and "exec" in line
    ]
    assert f"-- {_VENV_PYTHON} -" in line
    probe = (_ROOT / "scripts" / "k8s" / "public-mcp-isolation-probe.py").read_text()
    assert "import asyncpg" in probe  # why the venv is needed
