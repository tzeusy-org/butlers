"""The import-linter contracts in pyproject.toml reject a new forbidden import.

CI and ``make lint`` run ``lint-imports`` against the real tree. This runs the same
contracts against a throwaway ``butlers`` package, so a contract that silently stops
matching (a renamed package, a typo in a module path) fails here instead of passing
vacuously.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
# Every package a contract names must exist, or lint-imports rejects the config outright.
_PACKAGES = ("api", "connectors", "core", "core_tools", "modules")


def _lint(tmp_path: Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    for package in ("", *_PACKAGES):
        (tmp_path / "butlers" / package).mkdir(parents=True, exist_ok=True)
        (tmp_path / "butlers" / package / "__init__.py").touch()
    for rel, body in files.items():
        path = tmp_path / "butlers" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        for parent in path.parents:
            if parent == tmp_path:
                break
            (parent / "__init__.py").touch()
    return subprocess.run(
        [
            shutil.which("lint-imports") or str(Path(sys.executable).parent / "lint-imports"),
            "--config",
            str(_REPO_ROOT / "pyproject.toml"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("files", "broken"),
    [
        (
            {"core/a.py": "from butlers.api import b\n", "api/b.py": ""},
            "core does not import api, connectors or modules",
        ),
        (
            {"core/a.py": "def f():\n    from butlers.modules import m\n", "modules/m.py": ""},
            "core does not import api, connectors or modules",
        ),
        (
            {"modules/m.py": "import butlers.core.spawner\n", "core/spawner.py": ""},
            "modules do not import the spawner",
        ),
        (
            {"connectors/c.py": "from butlers.api import b\n", "api/b.py": ""},
            "connectors and modules do not import api",
        ),
        (
            {"modules/m2.py": "from butlers.api import b\n", "api/b.py": ""},
            "connectors and modules do not import api",
        ),
        (
            {"core/a.py": "from butlers.connectors import c\n", "connectors/c.py": ""},
            "core does not import api, connectors or modules",
        ),
        (
            {"core_tools/t.py": "from butlers.modules import m\n", "modules/m.py": ""},
            "core_tools does not import modules",
        ),
    ],
)
def test_a_forbidden_import_breaks_its_contract(
    tmp_path: Path, files: dict[str, str], broken: str
) -> None:
    result = _lint(tmp_path, files)

    assert result.returncode != 0, result.stdout
    assert f"{broken} BROKEN" in result.stdout


def test_a_type_checking_import_is_not_a_dependency_edge(tmp_path: Path) -> None:
    files = {
        "core/a.py": "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from butlers.api import b\n",
        "api/b.py": "",
    }

    assert _lint(tmp_path, files).returncode == 0
