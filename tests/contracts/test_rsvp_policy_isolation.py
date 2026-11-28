"""No model-facing code reads or writes the RSVP policy table (REQ-calendar-invitation-docket-001).

The table is written only by the dashboard definer and, from the docket job slice on, read by that
one job. Until then nothing in ``src`` or ``roster`` may name it except the grants manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.db_authority.grants import PUBLIC_RELATIONS
from butlers.db_authority.roles import runtime_roles

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_TABLE = "calendar_rsvp_policies"
_ALLOWED = {
    "src/butlers/db_authority/grants.py",
    "src/butlers/core/temporal/rsvp_policy.py",
    "src/butlers/api/router_registry.py",  # the route module shares the table's name
}


def test_only_the_grants_manifest_and_the_policy_module_name_the_table() -> None:
    offenders = []
    for base in ("src", "roster"):
        for path in (_ROOT / base).rglob("*"):
            if path.suffix not in {".py", ".md", ".toml", ".sql"} or not path.is_file():
                continue
            relative = path.relative_to(_ROOT).as_posix()
            if relative not in _ALLOWED and _TABLE in path.read_text(errors="ignore"):
                offenders.append(relative)
    assert offenders == []


def test_no_runtime_role_holds_a_privilege_on_the_table() -> None:
    grant = PUBLIC_RELATIONS[_TABLE]
    assert grant.fleet == frozenset()
    assert all(not grant.privileges(role) for role in runtime_roles())
