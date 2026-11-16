"""Switchboard's location forgetting has one caller: its own local relay (bu-s11n0s.7 S4c-2/3).

``forget_location_copies`` irreversibly withholds the coordinates in Switchboard's inbox copies
of OwnTracks fixes. Only the ``location_retention.forget_copies`` local relay reader in the
service-plane wiring may call it (no MCP tool, job, route or script). This guard fails on any
reference outside that reader and the module that defines it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_DEFINING = _ROOT / "roster/switchboard/tools/location_retention.py"
#: The one caller: Switchboard's ``location_retention.forget_copies`` local relay (S4c-3),
#: which only Chronicler may call.
_WIRING = _ROOT / "src/butlers/core/service_plane/wiring.py"
_GUARDED = re.compile(
    r"\bforget_location_copies\b"
    r"|\bbutlers\.tools\.switchboard\.location_retention\b"
    r"|\bfrom\s+butlers\.tools\.switchboard\s+import\b[^\n]*\blocation_retention\b"
)
_SUFFIXES = {".py", ".toml", ".md", ".yaml", ".yml", ".json", ".sql"}


def _references() -> list[str]:
    found = []
    for top in ("src", "roster", "scripts"):
        for path in sorted((_ROOT / top).rglob("*")):
            if path.suffix not in _SUFFIXES or not path.is_file() or path in (_DEFINING, _WIRING):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found += [f"{path.relative_to(_ROOT)}: {m.group(0)}" for m in _GUARDED.finditer(text)]
    return found


@pytest.mark.parametrize(
    "line",
    [
        "await forget_location_copies(conn, grant, keys)",
        "from butlers.tools.switchboard.location_retention import WITHHELD_TEXT",
        "from butlers.tools.switchboard import location_retention",
    ],
)
def test_the_guard_recognises_a_reference(line: str) -> None:
    assert _GUARDED.search(line)


def test_only_the_relay_reader_calls_switchboard_location_forgetting() -> None:
    assert _references() == []
    assert _GUARDED.search(_WIRING.read_text(encoding="utf-8"))


def test_the_insert_trigger_withholds_with_the_same_text() -> None:
    from butlers.tools.switchboard.location_retention import WITHHELD_TEXT

    migration = _ROOT / "roster/switchboard/migrations/082_location_retention_forgetting.py"
    assert f'_WITHHELD = "{WITHHELD_TEXT}"' in migration.read_text(encoding="utf-8")
