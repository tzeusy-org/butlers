"""Every core revision id is ``core_<digits>``.

Shared-table migrations (core_240.., core_317, core_323) decide whether another schema's core
chain still depends on their tables with ``version_num ~ '^core_[0-9]+$'``. A suffixed id would
be invisible to those counts and let a downgrade drop tables another schema still uses, so the
assumption is pinned here rather than patched into each already-applied migration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_CORE = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "core"
_REVISION = re.compile(r'^revision(?:\s*:\s*str)?\s*=\s*"([^"]+)"', re.MULTILINE)


def test_every_core_revision_id_is_core_digits():
    ids = {}
    for path in sorted(_CORE.glob("*.py")):
        if path.name == "__init__.py":
            continue
        match = _REVISION.search(path.read_text(encoding="utf-8"))
        assert match, f"{path.name} declares no revision id"
        ids[path.name] = match.group(1)
    assert ids
    bad = {name: rev for name, rev in ids.items() if not re.fullmatch(r"core_[0-9]+", rev)}
    assert not bad, f"core revision ids must be core_<digits>: {bad}"
