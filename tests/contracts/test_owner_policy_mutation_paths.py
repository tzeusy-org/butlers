"""Every owner-policy mutation path is enumerated and owner-only (bu-dhloi1.10, AC4).

REQ-capability-authorization-020: a non-owner or model tool cannot mutate the
overlay. The inventory below is the complete set; a new writer, import or
route must be added here deliberately (and protected) or this fails.

* Terms, receipts and the generation are written only by
  ``core/owner_policy.py``'s ``OwnerPolicyWriter``, and that writer is reached
  only from the owner-dashboard routes, each behind the verified dashboard
  session (``tests/api/test_owner_policy_api.py``).
* Holds are appended only by the broker ledger (``record_hold``).
* sw_059's RLS keeps the switchboard daemon role (broker, MCP tools) read-only
  on terms and the generation (``tests/switchboard/test_owner_policy_postgres.py``).
* No MCP tool, callback or connector registers a writer: none of them import it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("src", "roster")
_WRITE = re.compile(
    r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE)\s+(?:switchboard\.)?(owner_policy_\w+)",
    re.IGNORECASE,
)
WRITERS = {
    ("src/butlers/core/owner_policy.py", "owner_policy_terms"),
    ("src/butlers/core/owner_policy.py", "owner_policy_state"),
    ("src/butlers/core/owner_policy.py", "owner_policy_receipts"),
    ("src/butlers/capability_broker/ledger.py", "owner_policy_holds"),
}
WRITER_IMPORTERS = {"src/butlers/core/owner_policy.py", "src/butlers/api/routers/owner_policy.py"}
OWNER_ROUTES = {
    ("GET", "/api/owner-policy"),
    ("POST", "/api/owner-policy/terms"),
    ("POST", "/api/owner-policy/terms/{term_id}/tighten"),
    ("GET", "/api/owner-policy/holds"),
}


def _python_files():
    for top in SOURCES:
        for path in sorted((ROOT / top).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if "/migrations/" in relative or "/tests/" in relative:
                continue  # schema owners and tests are not runtime mutation paths
            yield relative, path.read_text(encoding="utf-8")


def test_owner_policy_rows_are_written_only_by_the_enumerated_writers():
    found = {
        (relative, match.group(2))
        for relative, text in _python_files()
        for match in _WRITE.finditer(text)
    }
    assert found == WRITERS


def test_the_policy_writer_is_reached_only_from_the_owner_dashboard_routes():
    importers = {relative for relative, text in _python_files() if "OwnerPolicyWriter" in text}
    assert importers == WRITER_IMPORTERS


def test_the_owner_routes_are_the_whole_dashboard_surface_and_none_is_an_mcp_tool():
    from butlers.api.routers.owner_policy import router

    routes = {(method, route.path) for route in router.routes for method in route.methods}
    assert routes == OWNER_ROUTES
    for relative, text in _python_files():
        if "owner_policy" in text and ("@mcp.tool" in text or "register_tool" in text):
            pytest.fail(f"{relative} registers a tool next to owner-policy code")
