"""Tests for priority_contacts entity_facts migration (bu-hjo3i).

Verifies that the list endpoint fetches channel identifiers from
relationship.entity_facts instead of public.contact_info.

Covers:
- _ef_display_value strips telegram: prefix correctly.
- _entity_facts_values_by_contact returns display-ready strings.
- _entity_facts_values_by_contact.has_email tracks has-email facts only.
- list endpoint: entity_facts values appear in contact_info_values.
- list endpoint: contact with no entity_id → empty contact_info_values.
- list endpoint: entity_facts DB error → empty contact_info_values (graceful).
- list endpoint: is_inert=True when contact has no entity_id.
- list endpoint: is_inert=True when entity exists but has no has-email fact.
- list endpoint: is_inert=False when entity has an active has-email fact.

priority_contacts is butler-agnostic (bu-gx13h): the sole runtime consumer is the
Gmail policy evaluator (resolves via has-email), so a contact with no has-email
fact is inert regardless of any other channel facts it carries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.priority_contacts import (
    _ef_display_value,
    _entity_facts_values_by_contact,
    _get_db_manager,
)

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_ef_display_value_strips_telegram_prefix() -> None:
    assert _ef_display_value("has-handle", "telegram:123456789") == "123456789"


async def test_entity_facts_values_by_contact_empty_input() -> None:
    pool = AsyncMock()
    result = await _entity_facts_values_by_contact(pool, [])
    assert result.values == {}
    assert result.has_email == set()
    pool.fetch.assert_not_called()


async def test_entity_facts_values_by_contact_returns_display_values() -> None:
    contact_id = uuid4()
    entity_id = uuid4()

    ef_row = MagicMock()
    ef_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "entity_id": entity_id,
            "predicate": "has-email",
            "object": "alice@example.com",
        }[k]
    )

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[ef_row])

    result = await _entity_facts_values_by_contact(pool, [(contact_id, entity_id)])
    assert result.values == {contact_id: ["alice@example.com"]}
    assert contact_id in result.has_email


async def test_entity_facts_values_strips_telegram_prefix_in_batch() -> None:
    contact_id = uuid4()
    entity_id = uuid4()

    ef_row = MagicMock()
    ef_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "entity_id": entity_id,
            "predicate": "has-handle",
            "object": "telegram:987654321",
        }[k]
    )

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[ef_row])

    result = await _entity_facts_values_by_contact(pool, [(contact_id, entity_id)])
    assert result.values == {contact_id: ["987654321"]}
    # has-handle is NOT has-email: contact should NOT be in has_email
    assert contact_id not in result.has_email


async def test_entity_facts_values_multiple_contacts_share_entity() -> None:
    """Two contacts sharing the same entity_id both receive the entity's facts."""
    contact_id_a = uuid4()
    contact_id_b = uuid4()
    entity_id = uuid4()

    ef_row = MagicMock()
    ef_row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "entity_id": entity_id,
            "predicate": "has-email",
            "object": "shared@example.com",
        }[k]
    )

    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[ef_row])

    result = await _entity_facts_values_by_contact(
        pool, [(contact_id_a, entity_id), (contact_id_b, entity_id)]
    )
    assert result.values == {
        contact_id_a: ["shared@example.com"],
        contact_id_b: ["shared@example.com"],
    }
    assert contact_id_a in result.has_email
    assert contact_id_b in result.has_email


# ---------------------------------------------------------------------------
# Integration tests for list endpoint
# ---------------------------------------------------------------------------


def _make_record(row: dict) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _wire_app(app: FastAPI, *, pool: AsyncMock) -> None:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool
    app.dependency_overrides[_get_db_manager] = lambda: mock_db


async def test_list_contact_info_values_from_entity_facts(app: FastAPI) -> None:
    """list endpoint populates contact_info_values from entity_facts rows."""
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Alice",
            "entity_id": entity_id,
        }
    )
    ef_row = _make_record(
        {
            "entity_id": entity_id,
            "predicate": "has-email",
            "object": "alice@example.com",
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            return [ef_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["contact_info_values"] == ["alice@example.com"]


async def test_list_contact_no_entity_id_empty_values(app: FastAPI) -> None:
    """Contact with no entity_id → contact_info_values is []."""
    contact_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Unknown",
            "entity_id": None,
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["contact_info_values"] == []


async def test_list_entity_facts_error_returns_empty_values(app: FastAPI) -> None:
    """entity_facts fetch error → contact_info_values=[] (graceful degradation)."""
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Bob",
            "entity_id": entity_id,
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            raise RuntimeError("db gone")
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["contact_info_values"] == []


async def test_list_contact_info_values_telegram_prefix_stripped(app: FastAPI) -> None:
    """Telegram handle with telegram: prefix is displayed without the prefix."""
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Charlie",
            "entity_id": entity_id,
        }
    )
    ef_row = _make_record(
        {
            "entity_id": entity_id,
            "predicate": "has-handle",
            "object": "telegram:555000111",
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            return [ef_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["contact_info_values"] == ["555000111"]


# ---------------------------------------------------------------------------
# is_inert detection tests (bu-bt61v)
# ---------------------------------------------------------------------------


async def test_list_is_inert_true_when_no_entity_id(app: FastAPI) -> None:
    """Contact with entity_id=None → is_inert=True (3-hop join breaks at hop 1)."""
    contact_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "No Entity",
            "entity_id": None,
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["is_inert"] is True


async def test_list_is_inert_true_when_entity_has_no_email_fact(app: FastAPI) -> None:
    """Entity exists but has only non-email facts → is_inert=True.

    The Gmail policy evaluator resolves priority senders via has-email only;
    a has-handle (telegram) fact does not enable matching.
    """
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Telegram Only",
            "entity_id": entity_id,
        }
    )
    ef_row = _make_record(
        {
            "entity_id": entity_id,
            "predicate": "has-handle",
            "object": "telegram:99887766",
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            return [ef_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["is_inert"] is True


async def test_list_is_inert_false_when_entity_has_email_fact(app: FastAPI) -> None:
    """Entity with an active has-email fact → is_inert=False (Gmail join succeeds)."""
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Active Alice",
            "entity_id": entity_id,
        }
    )
    ef_row = _make_record(
        {
            "entity_id": entity_id,
            "predicate": "has-email",
            "object": "active@example.com",
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            return [ef_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["is_inert"] is False


async def test_list_is_inert_true_for_entity_with_only_handle_fact(app: FastAPI) -> None:
    """Contact with entity + has-handle fact but no has-email → is_inert=True.

    priority_contacts is butler-agnostic (bu-gx13h): the sole runtime consumer
    is the Gmail policy evaluator, which resolves senders via has-email only.
    A contact whose entity carries only a has-handle fact matches nothing at
    runtime, so it is inert regardless of the (now-removed) butler dimension.
    """
    contact_id = uuid4()
    entity_id = uuid4()

    pc_row = _make_record(
        {
            "contact_id": contact_id,
            "added_at": _NOW,
            "added_by": "dashboard",
            "contact_name": "Telegram VIP",
            "entity_id": entity_id,
        }
    )
    ef_row = _make_record(
        {
            "entity_id": entity_id,
            "predicate": "has-handle",
            "object": "telegram:99887766",
        }
    )

    pool = AsyncMock()

    async def _fetch(sql, *args, **kwargs):
        if "priority_contacts" in sql:
            return [pc_row]
        if "entity_facts" in sql:
            return [ef_row]
        return []

    pool.fetchval = AsyncMock(return_value=1)
    pool.fetch = AsyncMock(side_effect=_fetch)
    _wire_app(app, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/ingestion/priority-contacts")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["is_inert"] is True, (
        "A priority contact with no has-email fact is inert — the Gmail "
        "policy evaluator (sole consumer) matches senders via has-email only."
    )
