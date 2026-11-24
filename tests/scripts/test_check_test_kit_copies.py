"""The shared test-kit copy ratchet (bu-rvgzmf.2)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_test_kit_copies as guard  # noqa: E402

pytestmark = pytest.mark.unit

MAKE_ROW = "def _make_row(**cols):\n    return cols\n"


def _module_level_class(name: str) -> str:
    """Source declaring a module-level class *name*, for the class-copy probes.

    Built from the name so this file never spells a frozen copy itself (the AC1
    grep over tests/ stays empty).
    """
    return f"class {name}:\n    pass\n"


FAKE_POOL = "_Fake" + "Pool"


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    return tmp_path


def _run(root: Path, baseline: dict, *extra: str) -> tuple[int, dict]:
    path = root / "baseline.json"
    path.write_text(json.dumps(baseline))
    code = guard.main(["--root", str(root), "--baseline", str(path), *extra])
    return code, json.loads(path.read_text())


@pytest.mark.parametrize(
    "path",
    ["tests/core/test_new.py", "roster/finance/tests/test_new.py", "tests/api/conftest.py"],
)
def test_a_new_per_file_make_row_fails(tmp_path: Path, path: str) -> None:
    assert _run(_repo(tmp_path, {path: MAKE_ROW}), {})[0] == 1


def test_frozen_copies_pass_and_the_baseline_only_shrinks(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/test_a.py": MAKE_ROW})
    frozen = {"tests/test_a.py": ["_load_migration", "_make_row"]}
    code, written = _run(root, frozen, "--update-baseline")
    assert code == 0
    assert written == {"tests/test_a.py": ["_make_row"]}
    (root / "tests/test_a.py").write_text(MAKE_ROW + _module_level_class(FAKE_POOL))
    assert _run(root, written)[0] == 1


@pytest.mark.parametrize(
    "path, source",
    [
        ("src/butlers/testing/records.py", MAKE_ROW),  # the kit itself
        ("tests/test_b.py", "def test_x():\n    def _make_row():\n        return 1\n"),  # nested
        ("tests/test_c.py", "from butlers.testing.records import row as _make_row\n"),  # import
    ],
)
def test_the_kit_nested_helpers_and_imports_pass(tmp_path: Path, path: str, source: str) -> None:
    assert _run(_repo(tmp_path, {path: source}), {})[0] == 0


def test_the_committed_baseline_holds() -> None:
    assert guard.main([]) == 0
