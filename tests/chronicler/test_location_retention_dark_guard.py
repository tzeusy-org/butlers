"""Raw-expiry preparation has exactly one caller: the owner-switched job (bu-s11n0s.7, .75).

``prepare_retention_grant`` irreversibly deletes Chronicler location point events and coarsens
their summaries. Nothing may expose or call it (no MCP tool, API route or script) except the
hourly ``chronicler_location_retention`` schedule, whose handler reads the owner's switch each
run; the same holds for the grant-state and holder-receipt writers. This guard fails on any
reference outside the modules that define them, other than that one registration.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from butlers.chronicler import location_retention
from butlers.chronicler.location_retention import LocationRetentionPolicy

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
#: The grant module, the reconciler (S4b-4b) and the holder collector (S4c-3), the other modules
#: that append states, and the retention job (S4b-4c), the one caller of both.
_DEFINING = {
    _ROOT / "src/butlers/chronicler/location_retention_grant.py",
    _ROOT / "src/butlers/chronicler/location_retention_reconcile.py",
    _ROOT / "src/butlers/chronicler/location_retention_holders.py",
    _ROOT / "src/butlers/chronicler/location_retention_job.py",
}
# The entry points by name, and the modules by import path (the grant module's bare name is
# also the grant tables' prefix, which migrations and docs legitimately mention).
_GUARDED = re.compile(
    r"\bprepare_retention_grant\b|\bappend_grant_state\b|\breconcile_ready_grants\b"
    r"|\brecord_holder_receipt\b|\bcollect_holder_receipts\b|\brelay_forgetter\b"
    r"|\brun_location_retention_job\b"
    r"|\bbutlers\.chronicler\.location_retention_(?:grant|reconcile|holders|job)\b"
    r"|\bfrom\s+butlers\.chronicler\s+import\b[^\n]*\blocation_retention_(?:grant|reconcile|holders|job)\b"
)
#: The one admitted reference: the scheduled-job registry imports the switched handler.
_ADMITTED = ["src/butlers/scheduled_jobs.py: butlers.chronicler.location_retention_job"]
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
        "await collect_holder_receipts(conn, forget)",
        "forget = relay_forgetter(lambda: url)",
        "from butlers.chronicler import location_retention_holders",
    ],
)
def test_the_guard_recognises_a_reference(line: str) -> None:
    assert _GUARDED.search(line)


def test_only_the_switched_schedule_reaches_raw_expiry_preparation() -> None:
    assert _references() == _ADMITTED


def test_the_registered_handler_is_the_switched_one() -> None:
    from butlers.chronicler import location_retention_job as job
    from butlers.scheduled_jobs import _DETERMINISTIC_SCHEDULE_JOB_REGISTRY

    text = (_ROOT / "src/butlers/scheduled_jobs.py").read_text(encoding="utf-8")
    assert "import run_scheduled_location_retention" in text
    assert callable(job.run_scheduled_location_retention)
    assert "chronicler_location_retention" in _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["chronicler"]


@pytest.mark.parametrize(("enforced", "status"), [(False, "not_enforced"), (True, "enforcing")])
def test_the_status_follows_the_owner_switch(enforced: bool, status: str) -> None:
    policy = LocationRetentionPolicy(
        days=30, version=1, updated_at=datetime.now(UTC), updated_by="owner", enforced=enforced
    )
    assert location_retention.enforcement_status(policy) == status
