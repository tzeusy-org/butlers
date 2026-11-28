"""The RSVP agreement ledger is informational and narrowly read (REQ-calendar-invitation-docket-006).

Only the ledger writer and the dashboard read path may name it; no runtime role holds a privilege on
the table; and nothing the docket does depends on a ledger value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.db_authority.grants import PUBLIC_RELATIONS
from butlers.db_authority.roles import runtime_roles

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_NAME = "calendar_rsvp_ledger"
_ALLOWED = {
    "src/butlers/db_authority/grants.py",
    "src/butlers/jobs/calendar_rsvp_ledger.py",  # the writer
    "src/butlers/api/owner_auth/service.py",  # the dashboard read
    "src/butlers/api/router_registry.py",
    "src/butlers/jobs/calendar_rsvp_docket.py",  # imports the writer module by name only
}


def test_only_the_writer_and_the_dashboard_read_name_the_ledger() -> None:
    offenders = []
    for base in ("src", "roster"):
        for path in (_ROOT / base).rglob("*"):
            if path.suffix not in {".py", ".md", ".toml", ".sql"} or not path.is_file():
                continue
            relative = path.relative_to(_ROOT).as_posix()
            if relative not in _ALLOWED and _NAME in path.read_text(errors="ignore"):
                offenders.append(relative)
    assert offenders == []


def test_the_docket_job_does_not_read_the_ledger_table_or_its_counts() -> None:
    source = (_ROOT / "src/butlers/jobs/calendar_rsvp_docket.py").read_text()
    assert "calendar_rsvp_ledger_dashboard" not in source
    assert "FROM public.calendar_rsvp_ledger" not in source
    assert "ledger_counts" not in source


def test_no_runtime_role_holds_a_privilege_on_the_ledger_table() -> None:
    grant = PUBLIC_RELATIONS[_NAME]
    assert grant.fleet == frozenset()
    for role in runtime_roles():
        assert set(grant.privileges(role)) == set(), role
