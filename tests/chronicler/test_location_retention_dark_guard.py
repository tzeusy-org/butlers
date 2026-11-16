"""Raw-expiry preparation stays uncalled until the owner enables it (bu-s11n0s.7 S4b-2).

``prepare_retention_grant`` irreversibly deletes Chronicler location point events and coarsens
their summaries. Nothing may schedule, expose or call it (no job, scheduled task, MCP tool,
API route or script) before the owner-enable step, and the same holds for the grant-state
writer. This guard fails on any reference outside the module that defines them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from butlers.chronicler import location_retention

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
#: The grant module, the reconciler (S4b-4b), the one other module that appends states, and the
#: retention job (S4b-4c), the one caller of the reconciler.
_DEFINING = {
    _ROOT / "src/butlers/chronicler/location_retention_grant.py",
    _ROOT / "src/butlers/chronicler/location_retention_reconcile.py",
    _ROOT / "src/butlers/chronicler/location_retention_job.py",
}
# The entry points by name, and the modules by import path (the grant module's bare name is
# also the grant tables' prefix, which migrations and docs legitimately mention).
_GUARDED = re.compile(
    r"\bprepare_retention_grant\b|\bappend_grant_state\b|\breconcile_ready_grants\b"
    r"|\brecord_holder_receipt\b"
    r"|\brun_location_retention_job\b"
    r"|\bbutlers\.chronicler\.location_retention_(?:grant|reconcile|job)\b"
    r"|\bfrom\s+butlers\.chronicler\s+import\b[^\n]*\blocation_retention_(?:grant|reconcile|job)\b"
)
_SCANNED = ("src", "roster", "scripts")
_SUFFIXES = {".py", ".toml", ".md", ".yaml", ".yml", ".json", ".sql"}


def _references() -> list[str]:
    found = []
    for top in _SCANNED:
        for path in sorted((_ROOT / top).rglob("*")):
            if path.suffix not in _SUFFIXES or not path.is_file() or path in _DEFINING:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            found += [f"{path.relative_to(_ROOT)}: {m.group(0)}" for m in _GUARDED.finditer(text)]
    return found


@pytest.mark.parametrize(
    "line",
    [
        "from butlers.chronicler.location_retention_grant import prepare_retention_grant",
        "from butlers.chronicler import location_retention_grant",
        'importlib.import_module("butlers.chronicler.location_retention_grant")',
        "await append_grant_state(conn, grant, 'ready')",
        "await reconcile_ready_grants(conn)",
        "from butlers.chronicler import location_retention_reconcile",
        "await run_location_retention_job(pool)",
        "await record_holder_receipt(conn, grant, holder, outcome='complete')",
        'job_name = "location_retention_job"  # butlers.chronicler.location_retention_job',
        "from butlers.chronicler import location_retention_job as job",
    ],
)
def test_the_guard_recognises_a_reference(line: str) -> None:
    assert _GUARDED.search(line)


def test_nothing_schedules_exposes_or_calls_raw_expiry_preparation() -> None:
    assert _references() == []


def test_raw_expiry_is_still_declared_not_enforced() -> None:
    assert location_retention.ENFORCEMENT_STATUS == "not_enforced"
