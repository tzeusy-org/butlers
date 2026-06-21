"""Tests for CoreSessionsAdapter._resolve_contacts entity_facts migration (bu-hjo3i).

Verifies that _resolve_contacts uses relationship.entity_facts instead of
public.contact_info for sender display-name resolution.

Covers:
- SQL query references entity_facts, not contact_info.
- SQL uses CASE expression to map source_channel → has-* predicate.
- Known entity → display_name populated from public.entities.canonical_name.
- Unknown sender (no entity) → (None, channel) fallback.
- Non-route sessions skipped (no DB call needed).
- Sessions without ingestion_event_id skipped.
- PostgresError → empty dict (graceful degradation).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.chronicler.adapters.sessions import CoreSessionsAdapter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncCtx:
    def __init__(self, obj: object) -> None:
        self._obj = obj

    async def __aenter__(self) -> object:
        return self._obj

    async def __aexit__(self, *_: object) -> None:
        pass


def _make_row(**kwargs) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k: kwargs[k])
    for k, v in kwargs.items():
        setattr(m, k, v)
    return m


def _session_row(
    session_id: int = 1,
    trigger_source: str | None = "route",
    ingestion_event_id: UUID | None = None,
) -> dict:
    return {
        "id": session_id,
        "trigger_source": trigger_source,
        "ingestion_event_id": ingestion_event_id,
    }


def _make_conn(*, contact_rows: list) -> AsyncMock:
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=contact_rows)
    return conn


def _make_pool(conn: AsyncMock) -> AsyncMock:
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=_AsyncCtx(conn))
    return pool


_adapter = CoreSessionsAdapter(butler_schemas=("test",))


# ---------------------------------------------------------------------------
# SQL shape tests
# ---------------------------------------------------------------------------


async def test_resolve_contacts_reads_entity_facts_not_contact_info() -> None:
    """Architectural invariant (bu-hjo3i): identity resolution reads
    relationship.entity_facts and NEVER public.contact_info. The query maps the
    source channel to a has-* predicate via a CASE expression (has-email/has-handle)
    and includes the telegram_user_client 'telegram:' prefix fallback branch."""
    event_id = uuid4()
    rows = [_make_row(**_session_row(ingestion_event_id=event_id))]

    conn = _make_conn(contact_rows=[])
    pool = _make_pool(conn)

    await _adapter._resolve_contacts(pool, rows)

    sql: str = conn.fetch.call_args[0][0]
    assert "relationship.entity_facts" in sql
    assert "contact_info" not in sql
    assert "CASE" in sql
    assert "has-email" in sql
    assert "has-handle" in sql
    assert "telegram_user_client" in sql
    assert "telegram:" in sql
    assert "NOT LIKE" in sql


async def test_resolve_contacts_returns_display_name_from_entity() -> None:
    """Entity canonical_name becomes the display_name in the result map."""
    event_id = uuid4()
    session_id = 42

    rows = [_make_row(**_session_row(session_id=session_id, ingestion_event_id=event_id))]

    contact_row = _make_row(event_id=event_id, channel="telegram", display_name="Alice")

    conn = _make_conn(contact_rows=[contact_row])
    pool = _make_pool(conn)

    result = await _adapter._resolve_contacts(pool, rows)

    assert session_id in result
    assert result[session_id] == ("Alice", "telegram")


async def test_resolve_contacts_unknown_sender_returns_none_display_name() -> None:
    """No matching entity → (None, channel) in result map."""
    event_id = uuid4()
    session_id = 43

    rows = [_make_row(**_session_row(session_id=session_id, ingestion_event_id=event_id))]

    # Row with NULL display_name (no entity matched)
    contact_row = _make_row(event_id=event_id, channel="telegram", display_name=None)

    conn = _make_conn(contact_rows=[contact_row])
    pool = _make_pool(conn)

    result = await _adapter._resolve_contacts(pool, rows)

    assert session_id in result
    assert result[session_id] == (None, "telegram")


async def test_resolve_contacts_skips_non_route_and_missing_event_id() -> None:
    """No DB lookup happens for non-route sessions (trigger/tick/None) NOR for route
    sessions missing an ingestion_event_id — both skip branches return {} with no fetch."""
    rows = [
        _make_row(
            **_session_row(session_id=1, trigger_source="trigger", ingestion_event_id=uuid4())
        ),
        _make_row(**_session_row(session_id=2, trigger_source="tick", ingestion_event_id=uuid4())),
        _make_row(**_session_row(session_id=3, trigger_source=None, ingestion_event_id=uuid4())),
        _make_row(**_session_row(session_id=4, trigger_source="route", ingestion_event_id=None)),
    ]

    conn = _make_conn(contact_rows=[])
    pool = _make_pool(conn)

    result = await _adapter._resolve_contacts(pool, rows)

    assert result == {}
    conn.fetch.assert_not_called()


async def test_resolve_contacts_postgres_error_returns_empty() -> None:
    """PostgresError → returns empty dict (degraded gracefully)."""
    event_id = uuid4()
    rows = [_make_row(**_session_row(ingestion_event_id=event_id))]

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError())
    pool = _make_pool(conn)

    result = await _adapter._resolve_contacts(pool, rows)
    assert result == {}


async def test_resolve_contacts_uuid_coercion() -> None:
    """event_id returned as string (not UUID) is coerced correctly."""
    event_id = uuid4()
    session_id = 99

    rows = [_make_row(**_session_row(session_id=session_id, ingestion_event_id=event_id))]

    # Return the event_id as a string to exercise the isinstance/UUID(...) path
    contact_row = _make_row(event_id=str(event_id), channel="email", display_name="Bob")

    conn = _make_conn(contact_rows=[contact_row])
    pool = _make_pool(conn)

    result = await _adapter._resolve_contacts(pool, rows)
    assert session_id in result
    assert result[session_id] == ("Bob", "email")
