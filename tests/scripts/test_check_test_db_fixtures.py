"""The hand-built test database ratchet (bu-rvgzmf.1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_test_db_fixtures as guard  # noqa: E402

pytestmark = pytest.mark.unit

# Built from parts so this file does not itself hold the literal it plants.
DDL = "CREATE " + "TABLE"
DDL_FILE = f'SCHEMA = """\n{DDL} IF NOT EXISTS notes (id INT);\n"""\n'
FIXTURE_FILE = (
    "import pytest\n\n\n@pytest.fixture\nasync def pool(db):\n    return await db('core')\n"
)


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


def test_a_new_create_table_in_a_test_file_fails(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/api/test_notes.py": DDL_FILE})
    assert _run(root, {})[0] == 1


def test_a_new_pool_or_migrated_db_url_fixture_fails(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "roster/finance/tests/test_a.py": FIXTURE_FILE,
            "tests/test_b.py": FIXTURE_FILE.replace("def pool", "def migrated_db_url"),
        },
    )
    assert _run(root, {})[0] == 1


def test_frozen_instances_pass_and_only_ever_tighten(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/api/test_notes.py": DDL_FILE})
    frozen = {"tests/api/test_notes.py": {"create_table": 2, "fixtures": 1}}
    code, written = _run(root, frozen, "--update-baseline")
    assert code == 0
    assert written == {"tests/api/test_notes.py": {"create_table": 1, "fixtures": 0}}
    (root / "tests/api/test_notes.py").write_text(DDL_FILE * 2)
    assert _run(root, written)[0] == 1


@pytest.mark.parametrize(
    "path, source",
    [
        # Production code and migrations own DDL; only test files are scanned.
        ("src/butlers/migrations/core_999.py", DDL_FILE),
        # A method or a non-fixture function named pool is not a fixture.
        ("tests/test_c.py", "class Manager:\n    def pool(self, name):\n        return name\n"),
        # Prose about the statement is not DDL.
        ("tests/test_d.py", "MESSAGE = 'tables are created by migrations'\n"),
    ],
)
def test_out_of_scope_sources_pass(tmp_path: Path, path: str, source: str) -> None:
    root = _repo(tmp_path, {path: source})
    assert _run(root, {})[0] == 0


def test_the_committed_baseline_holds() -> None:
    assert guard.main([]) == 0
