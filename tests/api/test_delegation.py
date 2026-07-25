"""Tests for GET /api/delegation/ledger (+ /{id}) — bu-gxmfx.

Mirrors the ``_MockDB``/``_Record`` harness style from ``tests/api/test_memory.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.routers.delegation import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


class _Record(dict):
    """Dict subclass standing in for an asyncpg Record (supports ``.get`` with real None)."""


def _make_ledger_row(
    *,
    asking_butler: str = "finance",
    question: str = "Who is Alice's employer?",
    target_butler: str | None = "relationship",
    status: str = "routed",
    answer: str | None = None,
    wake_state: str = "not_applicable",
    wake_task_id: uuid.UUID | None = None,
    wake_task_name: str | None = None,
) -> _Record:
    return _Record(
        {
            "id": uuid.uuid4(),
            "asked_at": _NOW,
            "asking_butler": asking_butler,
            "question": question,
            "target_butler": target_butler,
            "catalog_match_id": uuid.uuid4() if target_butler else None,
            "catalog_score": 0.42 if target_butler else None,
            "status": status,
            "reason": None,
            "answer": answer,
            "answered_at": _NOW if answer else None,
            "answering_butler": target_butler if answer else None,
            "metadata": None,
            "answer_digest": None,
            "wake_key": None,
            "wake_state": wake_state,
            "wake_task_id": wake_task_id,
            "wake_task_name": wake_task_name,
            "wake_updated_at": None,
        }
    )


class _MockDB:
    """Minimal DatabaseManager stand-in — one pool serving public.delegation_ledger."""

    def __init__(self, *, rows: list[dict], total: int | None = None) -> None:
        self.butler_names = ["finance"]
        self.pool_mock = AsyncMock()
        self.pool_mock.fetchval = AsyncMock(return_value=total if total is not None else len(rows))
        self.pool_mock.fetch = AsyncMock(return_value=rows)
        self.pool_mock.fetchrow = AsyncMock(return_value=rows[0] if rows else None)

    def pool(self, name: str):
        if name not in self.butler_names:
            raise KeyError(name)
        return self.pool_mock


def _wire_db(app, *, rows: list[dict], total: int | None = None) -> _MockDB:
    mock_db = _MockDB(rows=rows, total=total)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return mock_db


async def test_list_ledger_returns_rows(app):
    rows = [_make_ledger_row(), _make_ledger_row(status="answered", answer="Acme Corp.")]
    _wire_db(app, rows=rows)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 2
    assert body["meta"]["total"] == 2
    assert body["data"][0]["asking_butler"] == "finance"


async def test_list_ledger_rejects_invalid_status(app):
    _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger", params={"status": "bogus"})

    assert resp.status_code == 400


async def test_list_ledger_passes_filters_through(app):
    mock_db = _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/delegation/ledger",
            params={
                "status": "answered",
                "asking_butler": "finance",
                "target_butler": "relationship",
            },
        )

    assert resp.status_code == 200
    fetch_query, *fetch_args = mock_db.pool_mock.fetch.await_args.args
    assert "status = $1" in fetch_query
    assert "asking_butler = $2" in fetch_query
    assert "target_butler = $3" in fetch_query
    assert fetch_args[:3] == ["answered", "finance", "relationship"]


async def test_list_ledger_exposes_wake_fields(app):
    row = _make_ledger_row(
        status="answered",
        answer="Acme Corp.",
        wake_state="callback_failed",
        wake_task_id=uuid.uuid4(),
        wake_task_name="delegation-wake-task",
    )
    _wire_db(app, rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger")

    assert resp.status_code == 200
    entry = resp.json()["data"][0]
    assert entry["wake_state"] == "callback_failed"
    assert entry["wake_task_id"] == str(row["wake_task_id"])
    assert entry["wake_task_name"] == "delegation-wake-task"


async def test_list_ledger_defaults_wake_state_when_absent(app):
    row = _make_ledger_row()
    _wire_db(app, rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger")

    assert resp.status_code == 200
    assert resp.json()["data"][0]["wake_state"] == "not_applicable"


async def test_list_ledger_passes_wake_stuck_through(app):
    mock_db = _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger", params={"wake_stuck": "true"})

    assert resp.status_code == 200
    fetch_query, *fetch_args = mock_db.pool_mock.fetch.await_args.args
    assert "wake_state = ANY($1)" in fetch_query
    assert fetch_args[0] == ["callback_failed", "task_conflict"]


async def test_get_ledger_entry_by_id(app):
    row = _make_ledger_row()
    _wire_db(app, rows=[row])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/delegation/ledger/{row['id']}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["id"] == str(row["id"])
    assert body["data"]["target_butler"] == "relationship"


async def test_get_ledger_entry_not_found(app):
    _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/delegation/ledger/{uuid.uuid4()}")

    assert resp.status_code == 404


async def test_get_ledger_entry_invalid_uuid(app):
    _wire_db(app, rows=[])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger/not-a-uuid")

    assert resp.status_code == 400


async def test_no_pools_available_returns_503(app):
    mock_db = MagicMock()
    mock_db.butler_names = []
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/delegation/ledger")

    assert resp.status_code == 503
