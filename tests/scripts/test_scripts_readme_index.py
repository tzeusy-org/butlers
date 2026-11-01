"""Keep ``scripts/README.md`` an honest index of ``scripts/`` (bu-99t5s3.2).

A script with no row in the index has no named caller, which is how dead one-shot
scripts accumulated. These two checks pin the index in both directions: a new
script needs a row (with its caller), and a deleted script cannot leave a stale row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
SCRIPT_SUFFIXES = {".py", ".sh", ".mjs", ".sql", ".c", ".sudoers"}
INDEX_ROW = re.compile(r"^\| \[`([^`]+)`\]\(([^)]+)\)", re.MULTILINE)


def _index_targets() -> set[str]:
    text = (SCRIPTS / "README.md").read_text()
    index = text[text.index("## Script index") :]
    return {target for _, target in INDEX_ROW.findall(index)}


def test_every_script_has_an_index_row() -> None:
    present = {
        path.name
        for path in SCRIPTS.iterdir()
        if path.is_file() and path.suffix in SCRIPT_SUFFIXES and path.name != "README.md"
    }
    present.add("k8s/")
    missing = sorted(present - _index_targets())
    assert not missing, f"add a row naming the caller in scripts/README.md for: {missing}"


def test_every_index_row_names_an_existing_script() -> None:
    stale = sorted(
        target
        for target in _index_targets()
        if not target.startswith("..") and not (SCRIPTS / target).exists()
    )
    assert not stale, f"scripts/README.md indexes scripts that no longer exist: {stale}"
