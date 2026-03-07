from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_tests_conftest_loads_outside_repo_root(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tests_conftest = repo_root / "tests" / "conftest.py"
    script = f"""
import importlib.util
import pathlib

path = pathlib.Path({str(tests_conftest)!r})
spec = importlib.util.spec_from_file_location("tests.conftest", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.__all__ == ["MockSpawner", "SpawnerResult", "mock_spawner"]
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        cwd=tmp_path,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
