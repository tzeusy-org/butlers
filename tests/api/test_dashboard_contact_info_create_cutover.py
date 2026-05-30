"""Regression test for the dashboard contact-info create endpoint cut-over.

Write-path cut-over (Migration bead 8, bu-k9ylx): ``POST
/api/relationship/contacts/{id}/contact-info`` no longer INSERTs into
``public.contact_info`` (read-only). It resolves the contact's ``entity_id`` and
asserts the channel fact via the central writer ``relationship_assert_fact()``.

Covers:
- create_contact_info calls relationship_assert_fact (no contact_info INSERT).
- Secured rows are rejected (HTTP 422) — credentials carve-out.
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


def _make_pool(*, entity_id):
    """Pool whose contact-exists check returns a row with the given entity_id."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"id": uuid.uuid4(), "entity_id": entity_id})
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


async def test_create_contact_info_secured_rejected(router_mod, monkeypatch):
    """Secured rows are rejected with HTTP 422 (credentials carve-out)."""
    pool = _make_pool(entity_id=uuid.uuid4())
    monkeypatch.setattr(router_mod, "_pool", lambda db: pool)

    mock_assert = AsyncMock()
    monkeypatch.setattr(_raf_module(), "relationship_assert_fact", mock_assert)

    req = router_mod.CreateContactInfoRequest(
        type="telegram", value="secret-token", is_primary=True, secured=True
    )
    with pytest.raises(HTTPException) as exc:
        await router_mod.create_contact_info(uuid.uuid4(), MagicMock(), req, MagicMock())
    assert exc.value.status_code == 422
    mock_assert.assert_not_awaited()


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
