"""Sender-authentication verdict on ingestion event summaries and detail (bu-6u90jj).

Reads the verdict bu-s11n0s.20 stores on ``public.ingestion_events``. Every stored value and
NULL round-trips through the list and the detail endpoint; a failed or out-of-vocabulary
lookup degrades only that field.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from tests.api.test_ingestion_events import _app_with_mock_db, _make_event_row

pytestmark = pytest.mark.unit

_VERDICT_SQL = "sender_authentication"


def _pool(stored: dict[str, Any] | None = None, *, detail_row: dict | None = None):
    """Shared pool whose verdict query answers from *stored* (id -> value); all else is empty."""
    pool = AsyncMock()
    calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(sql: str, *args: Any) -> list[dict]:
        if _VERDICT_SQL in sql:
            calls.append((sql, args))
            return [{"id": k, "sender_authentication": v} for k, v in (stored or {}).items()]
        return []

    pool.fetch = AsyncMock(side_effect=fetch)
    pool.fetchrow = AsyncMock(return_value=detail_row)
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value=None)
    pool.verdict_calls = calls
    return pool


async def _list(app, pool, rows: list[dict]) -> list[dict]:
    _app_with_mock_db(app, shared_pool=pool)
    with patch(
        "butlers.api.routers.ingestion_events.ingestion_events_list",
        new_callable=AsyncMock,
        return_value={"items": rows, "next_cursor": None, "has_more": False},
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/ingestion/events")
    assert resp.status_code == 200
    return resp.json()["data"]


async def _detail(app, pool, event_id: str) -> dict:
    _app_with_mock_db(app, shared_pool=pool)
    with patch("butlers.api.routers.ingestion_events.emit_dashboard_audit", new_callable=AsyncMock):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/ingestion/events/{event_id}")
    assert resp.status_code == 200
    return resp.json()["data"] if "data" in resp.json() else resp.json()


@pytest.mark.parametrize("verdict", ["authenticated", "unverified", "failed"])
async def test_each_stored_verdict_round_trips_through_list_and_detail(app, verdict):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id)
    pool = _pool({event_id: verdict}, detail_row=row)

    listed = await _list(app, pool, [row])
    detail = await _detail(app, pool, event_id)

    assert listed[0]["sender_authentication"] == verdict
    assert detail["sender_authentication"] == verdict


async def test_no_stored_verdict_reads_null_in_list_and_detail(app):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id)
    pool = _pool({}, detail_row=row)

    listed = await _list(app, pool, [row])
    detail = await _detail(app, pool, event_id)

    assert listed[0]["sender_authentication"] is None
    assert detail["sender_authentication"] is None


async def test_one_page_reads_each_row_its_own_verdict_with_a_single_query(app):
    ids = [str(uuid4()) for _ in range(3)]
    rows = [_make_event_row(event_id=i) for i in ids]
    pool = _pool({ids[0]: "failed", ids[2]: "authenticated"})

    listed = await _list(app, pool, rows)

    assert [item["sender_authentication"] for item in listed] == ["failed", None, "authenticated"]
    assert len(pool.verdict_calls) == 1
    assert {str(u) for u in pool.verdict_calls[0][1][0]} == set(ids)


async def test_a_value_outside_the_three_verdicts_reads_null(app):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id)
    pool = _pool({event_id: "pass"}, detail_row=row)

    assert (await _list(app, pool, [row]))[0]["sender_authentication"] is None
    assert (await _detail(app, pool, event_id))["sender_authentication"] is None


async def test_a_failed_lookup_degrades_only_the_verdict(app):
    event_id = str(uuid4())
    row = _make_event_row(event_id=event_id)
    pool = _pool({event_id: "failed"}, detail_row=row)
    real_fetch = pool.fetch.side_effect

    async def broken(sql: str, *args: Any):
        if _VERDICT_SQL in sql:
            raise RuntimeError("verdict read failed")
        return await real_fetch(sql, *args)

    pool.fetch = AsyncMock(side_effect=broken)

    listed = await _list(app, pool, [row])
    detail = await _detail(app, pool, event_id)

    assert listed[0]["id"] == event_id and listed[0]["sender_authentication"] is None
    assert detail["id"] == event_id and detail["sender_authentication"] is None


async def test_the_verdict_query_reads_the_stored_column_for_the_requested_ids_only(app):
    event_id = str(uuid4())
    pool = _pool({event_id: "failed"})

    await _list(app, pool, [_make_event_row(event_id=event_id)])

    sql, args = pool.verdict_calls[0]
    assert "FROM public.ingestion_events" in sql
    assert "id = ANY($1::uuid[])" in sql
    assert "sender_authentication IS NOT NULL" in sql
    assert [str(u) for u in args[0]] == [event_id]


@pytest.mark.parametrize("verdict", ["authenticated", "unverified", "failed", None])
def test_the_model_accepts_exactly_the_three_verdicts_and_null(verdict):
    from butlers.api.models.ingestion_event import IngestionEventSummary

    row = _make_event_row()
    assert IngestionEventSummary(**row, sender_authentication=verdict).sender_authentication == (
        verdict
    )


def test_the_model_refuses_any_other_verdict():
    from pydantic import ValidationError

    from butlers.api.models.ingestion_event import IngestionEventSummary

    with pytest.raises(ValidationError):
        IngestionEventSummary(**_make_event_row(), sender_authentication="pass")
