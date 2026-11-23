"""Apply the 600-line budget to Core paths outside the inherited baseline.

Each listed path remains eligible while oversized. Split scheduler paths always
use the budget.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CORE = Path(__file__).resolve().parents[2] / "src" / "butlers" / "core"
_BASELINE = Path(__file__).with_name("core_module_size_baseline.txt")
_MAX_LINES = 600


def test_core_module_size_budget():
    modules = [path for path in _CORE.rglob("*.py") if path.is_file()]
    assert modules, f"No Core Python modules found under {_CORE}"

    oversized = {
        path.relative_to(_CORE).as_posix(): line_count
        for path in modules
        if (line_count := len(path.read_bytes().splitlines())) > _MAX_LINES
    }
    entries = _BASELINE.read_text(encoding="utf-8").splitlines()
    baseline = set(entries)
    assert len(entries) == len(baseline), "Core module size baseline contains duplicate paths"

    problems = [
        f"{path} has {oversized[path]} lines (limit {_MAX_LINES})"
        for path in sorted(oversized.keys() - baseline)
    ]
    problems.extend(
        f"Remove stale size exemption: {path}" for path in sorted(baseline - oversized.keys())
    )
    problems.extend(
        f"Split scheduler modules cannot have size exemptions: {path}"
        for path in sorted(baseline)
        if path.startswith("scheduler/")
    )
    assert not problems, "Core module size violations:\n" + "\n".join(problems)
