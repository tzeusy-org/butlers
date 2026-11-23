"""Callers of the role-checked ``dashboard_attention`` functions (bu-dss7he.24).

``dashboard_attention.remember`` and ``final_claim`` return false, and ``evaluate`` is
only executable, for a caller whose ``current_setting('role')`` (else ``session_user``) is
the butler role or the configured migration role. After the migration role is rotated
off the shared runtime login (docs/data_and_storage/migrator-login.md), a shared-login
caller that has not done ``SET ROLE`` is refused silently: dropped work, not an error.

The audit found every caller runs on a butler daemon pool, and ``Database`` does
``SET ROLE butler_<schema>_rw`` on every acquire of a schema-bound pool. The dashboard
API (a role-less pool) reaches these only through MCP tools on the butler daemons. This
static scan pins that: a new call site, or any call from the dashboard API tree, fails
here until its role is reviewed and the allowlist below is updated.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_CHECKED = re.compile(r"dashboard_attention\s*\.\s*(remember|evaluate|final_claim)\s*\(")

# Call sites run on a schema-bound butler ``Database`` pool (SET ROLE on every acquire).
_ALLOWED = {
    "src/butlers/core/attention_urgency.py",
    "src/butlers/core/break_through.py",
    "src/butlers/core/scheduler/dispatch.py",
    "src/butlers/core/scheduler/update.py",
}


def _call_sites() -> dict[str, list[str]]:
    sites: dict[str, list[str]] = {}
    for root in ("src", "roster", "scripts"):
        for path in (_REPO_ROOT / root).rglob("*.py"):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            parts = rel.split("/")
            if "tests" in parts or "migrations" in parts:
                continue
            if _CHECKED.search(path.read_text(encoding="utf-8")):
                sites[rel] = _CHECKED.findall(path.read_text(encoding="utf-8"))
    return sites


def test_checked_attention_functions_are_called_only_from_reviewed_butler_pools() -> None:
    assert set(_call_sites()) == _ALLOWED


def test_dashboard_api_never_calls_the_checked_attention_functions() -> None:
    offenders = [
        rel
        for rel in _call_sites()
        if rel.startswith("src/butlers/api/") or (rel.startswith("roster/") and "/api/" in rel)
    ]
    assert offenders == []


def test_schema_bound_pools_set_role_on_every_acquire() -> None:
    source = (_REPO_ROOT / "src/butlers/db.py").read_text(encoding="utf-8")
    assert 'pool_kwargs["setup"] = self._setup_connection' in source
    assert 'await conn.execute(f"SET ROLE {quoted_role}")' in source
    assert "return self.schema is not None or self.role is not None" in source
