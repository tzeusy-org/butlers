"""Regression test for the dashboard contact-info create endpoint cut-over.

Write-path cut-over (Migration bead 8, bu-k9ylx): ``POST
/api/relationship/contacts/{id}/contact-info`` no longer INSERTs into
``public.contact_info`` (read-only). It resolves the contact's ``entity_id`` and
asserts the channel fact via the central writer ``relationship_assert_fact()``.

Covers:
- create_contact_info calls relationship_assert_fact (no contact_info INSERT).
- Secured rows (secured=True) are written to public.entity_info (bu-pl8fy) —
  RFC 0004 Amendment 2 credential carve-out (NOT relationship.entity_facts).
- Contact with no linked entity is rejected (HTTP 409).

The pool/asyncpg layer is mocked; no DB or Docker required.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit


def _raf_module():
    """Return the central-writer SUBMODULE object.

    ``butlers.tools.relationship.__init__`` re-exports the *function*
    ``relationship_assert_fact``, which shadows the submodule of the same name as
    a package attribute. ``import ... as x`` therefore binds the function, not
    the module. The import machinery still registers the real submodule in
    ``sys.modules``, so we fetch it from there to patch its attribute — the same
    object the router's deferred ``from ...relationship_assert_fact import ...``
    resolves against.
    """
    import importlib
    import sys

    name = "butlers.tools.relationship.relationship_assert_fact"
    importlib.import_module(name)
    return sys.modules[name]


def _load_router():
    """Load the relationship router by file path.

    The router self-loads its Pydantic models internally and exposes
    ``CreateContactInfoRequest`` as a module-level attribute.
    """
    import importlib.util
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    router_path = repo_root / "roster" / "relationship" / "api" / "router.py"

    mod_name = "relationship_api_router_cutover_test"
    router_spec = importlib.util.spec_from_file_location(mod_name, router_path)
    router_mod = importlib.util.module_from_spec(router_spec)
    sys.modules[mod_name] = router_mod
    router_spec.loader.exec_module(router_mod)
    return router_mod


def _make_pool(*, entity_id, entity_info_row=None):
    """Pool whose contact-exists check returns a row with the given entity_id.

    ``entity_info_row`` simulates the INSERT ... ON CONFLICT DO NOTHING ...
    RETURNING result for the public.entity_info write path.  Pass a dict to
    represent a newly inserted row; pass ``None`` to simulate an ON CONFLICT
    no-op (idempotent second run).
    """
    pool = MagicMock()

    # contact-exists check always returns the contact row
    _contact_row = {"id": uuid.uuid4(), "entity_id": entity_id}
    _ei_row = (
        {
            "id": entity_info_row["id"],
            "entity_id": entity_id,
            "type": entity_info_row["type"],
            "value": entity_info_row["value"],
            "label": None,
            "is_primary": entity_info_row.get("is_primary", False),
            "secured": True,
        }
        if entity_info_row is not None
        else None
    )

    _fetchrow_calls = [0]

    async def _fetchrow(sql, *args):
        call_idx = _fetchrow_calls[0]
        _fetchrow_calls[0] += 1
        if call_idx == 0:
            # First call: contact-exists check
            return _contact_row
        # Subsequent calls: entity_info INSERT ... RETURNING
        return _ei_row

    pool.fetchrow = _fetchrow
    pool.execute = AsyncMock()
    return pool


def _result(outcome: str, fact_id=None):
    r = MagicMock()
    r.outcome.value = outcome
    r.fact_id = fact_id
    return r


@pytest.fixture
def router_mod():
    return _load_router()


async def test_create_contact_info_asserts_triple(router_mod, monkeypatch):
    """create_contact_info calls relationship_assert_fact and issues no contact_info INSERT."""
    contact_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    pool = _make_pool(entity_id=entity_id)
    monkeypatch.setattr(router_mod, "_pool", lambda db: pool)

    async def _noop_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(router_mod, "emit_dashboard_audit", _noop_audit)

    mock_assert = AsyncMock(return_value=_result("inserted", fact_id=uuid.uuid4()))
    monkeypatch.setattr(_raf_module(), "relationship_assert_fact", mock_assert)

    req = router_mod.CreateContactInfoRequest(
        type="email", value="alice@example.com", is_primary=False, secured=False
    )
    result = await router_mod.create_contact_info(contact_id, MagicMock(), req, MagicMock())

    mock_assert.assert_awaited_once()
    call = mock_assert.call_args
    assert call.args[1] == entity_id  # subject
    assert call.args[2] == "has-email"  # predicate
    assert call.args[3] == "alice@example.com"
    # No write DML to contact_info anywhere.
    pool.execute.assert_not_called()
    assert result.type == "email"
    assert result.value == "alice@example.com"


async def test_create_contact_info_secured_writes_to_entity_info(router_mod, monkeypatch):
    """Secured rows (bu-pl8fy) are written to public.entity_info, not rejected.

    RFC 0004 Amendment 2: secured=True credential rows must go to public.entity_info
    (NOT relationship.entity_facts).  The endpoint returns HTTP 201 with the
    credential entry reflected in the response.  relationship_assert_fact is NOT
    called for secured rows.
    """
    entity_id = uuid.uuid4()
    info_id = uuid.uuid4()
    pool = _make_pool(
        entity_id=entity_id,
        entity_info_row={"id": info_id, "type": "telegram_session", "value": "secret-token"},
    )
    monkeypatch.setattr(router_mod, "_pool", lambda db: pool)

    async def _noop_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(router_mod, "emit_dashboard_audit", _noop_audit)

    mock_assert = AsyncMock()
    monkeypatch.setattr(_raf_module(), "relationship_assert_fact", mock_assert)

    req = router_mod.CreateContactInfoRequest(
        type="telegram_session", value="secret-token", is_primary=True, secured=True
    )
    result = await router_mod.create_contact_info(uuid.uuid4(), MagicMock(), req, MagicMock())

    # relationship_assert_fact must NOT be called for secured rows
    mock_assert.assert_not_awaited()
    # Result reflects the credential entry
    assert result.secured is True
    assert result.type == "telegram_session"
    assert result.value == "secret-token"


async def test_create_contact_info_secured_idempotent_conflict(router_mod, monkeypatch):
    """ON CONFLICT DO NOTHING on second run: returns 201 with a synthesised id.

    When public.entity_info already has a row for (entity_id, type) the INSERT
    returns no row (ON CONFLICT DO NOTHING).  The endpoint must still return 201
    rather than raising an error.
    """
    entity_id = uuid.uuid4()
    pool = _make_pool(
        entity_id=entity_id,
        entity_info_row=None,  # simulate ON CONFLICT DO NOTHING (no row returned)
    )
    monkeypatch.setattr(router_mod, "_pool", lambda db: pool)

    async def _noop_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(router_mod, "emit_dashboard_audit", _noop_audit)

    monkeypatch.setattr(_raf_module(), "relationship_assert_fact", AsyncMock())

    req = router_mod.CreateContactInfoRequest(
        type="google_oauth_refresh", value="refresh-token", is_primary=False, secured=True
    )
    # Must not raise — idempotent second-run path
    result = await router_mod.create_contact_info(uuid.uuid4(), MagicMock(), req, MagicMock())
    assert result.secured is True
    assert result.type == "google_oauth_refresh"
    # id is synthesised (uuid4) when INSERT returns no row
    assert result.id is not None


async def test_create_contact_info_no_entity_rejected(router_mod, monkeypatch):
    """A contact with no linked entity is rejected with HTTP 409."""
    pool = _make_pool(entity_id=None)
    monkeypatch.setattr(router_mod, "_pool", lambda db: pool)

    req = router_mod.CreateContactInfoRequest(
        type="email", value="a@b.com", is_primary=False, secured=False
    )
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_contact_info(uuid.uuid4(), MagicMock(), req, MagicMock())
    assert exc.value.status_code == 409
