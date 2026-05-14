"""Tests for the QA cases list endpoint."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.qa import _get_db_manager

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 15, 8, 0, 0, tzinfo=UTC)


class _MockRecord(dict):
    """A dict subclass that mimics asyncpg Record access patterns used by the router."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _r(row: dict[str, Any]) -> _MockRecord:
    return _MockRecord(row)


def _uuid7_with_timestamp(timestamp_ms: int) -> uuid.UUID:
    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= 0b10 << 62
    return uuid.UUID(int=value)


def _make_case_row(**overrides: Any) -> dict[str, Any]:
    attempt_id = overrides.pop("id", _uuid7_with_timestamp(1_771_234_567_890))
    detected = overrides.pop("detected", _NOW - timedelta(hours=2))
    row = {
        "id": attempt_id,
        "butler_name": "finance",
        "status": "investigating",
        "severity": 1,
        "exception_type": "RuntimeError",
        "call_site": "finance.jobs:run",
        "sanitized_msg": "job failed",
        "pr_url": None,
        "created_at": _NOW,
        "error_detail": None,
        "case_severity": 1,
        "detected": detected,
        "age_seconds": 7200,
        "finding_id": uuid.uuid4(),
        "finding_fingerprint": "a" * 64,
        "finding_source_type": "log_scanner",
        "finding_source_butler": "finance",
        "finding_severity": 1,
        "finding_exception_type": "RuntimeError",
        "finding_event_summary": "Finance job failed",
        "finding_call_site": "finance.jobs:run",
        "finding_occurrence_count": 1,
        "finding_first_seen": detected,
        "finding_last_seen": _NOW,
        "finding_source_session_trigger_source": None,
        "finding_structured_evidence": None,
    }
    row.update(overrides)
    return row


def _build_app(
    *,
    rows: list[dict[str, Any]] | None = None,
    total: int = 0,
) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    mock_pool.fetchval = AsyncMock(return_value=total)
    mock_pool.fetch = AsyncMock(return_value=[_r(row) for row in rows or []])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


async def _call(app: Any, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, **kwargs)


async def test_cases_empty_db_uses_standard_pagination_defaults() -> None:
    app, pool = _build_app(rows=[], total=0)

    response = await _call(app, "/api/qa/cases")

    assert response.status_code == 200
    assert response.json() == {
        "data": [],
        "meta": {"total": 0, "offset": 0, "limit": 25, "has_more": False},
    }
    fetch_args = pool.fetch.await_args.args
    assert fetch_args[-2:] == (0, 25)
    default_cutoff = pool.fetchval.await_args.args[1]
    assert (
        timedelta(days=6, hours=23, minutes=59)
        <= datetime.now(UTC) - default_cutoff
        <= timedelta(days=7, minutes=1)
    )


async def test_cases_return_descending_attempt_order_and_shape() -> None:
    newest = _make_case_row(
        id=_uuid7_with_timestamp(1_771_234_567_999),
        created_at=_NOW,
        status="pr_open",
        pr_url="https://github.com/Tzeusy/butlers/pull/1651",
    )
    older = _make_case_row(
        id=_uuid7_with_timestamp(1_771_234_567_111),
        created_at=_NOW - timedelta(hours=1),
        status="pr_merged",
        case_severity=2,
        finding_severity=2,
        finding_event_summary="General issue",
        finding_source_butler="general",
        butler_name="general",
        pr_url="https://github.com/Tzeusy/butlers/pull/1648",
    )
    app, pool = _build_app(rows=[newest, older], total=2)

    body = (await _call(app, "/api/qa/cases")).json()

    assert [case["id"] for case in body["data"]] == [str(newest["id"]), str(older["id"])]
    assert body["data"][0] == {
        "id": str(newest["id"]),
        "short_id": "#999",
        "sev": "high",
        "butler": "finance",
        "headline": "Finance job failed",
        "detected": newest["detected"].isoformat().replace("+00:00", "Z"),
        "age_seconds": 7200,
        "state": "pr",
        "pr_state": "open",
        "pr_url": "https://github.com/Tzeusy/butlers/pull/1651",
    }
    assert body["data"][1]["sev"] == "medium"
    assert body["data"][1]["state"] == "landed"
    assert body["data"][1]["pr_state"] == "merged"
    fetch_sql = pool.fetch.await_args.args[0]
    assert "ORDER BY a.created_at DESC" in fetch_sql
    assert "MIN(first_seen) OVER () AS detected_at" in fetch_sql


async def test_cases_severity_filter_maps_labels_to_stored_integer_ranges() -> None:
    app, pool = _build_app(rows=[_make_case_row()], total=1)

    response = await _call(app, "/api/qa/cases", params={"sev": "high"})

    assert response.status_code == 200
    count_sql = pool.fetchval.await_args.args[0]
    fetch_sql = pool.fetch.await_args.args[0]
    assert "COALESCE(f.severity, a.severity) IN (0, 1)" in count_sql
    assert "COALESCE(f.severity, a.severity) IN (0, 1)" in fetch_sql
    assert response.json()["data"][0]["sev"] == "high"


async def test_cases_since_filter_builds_requested_cutoff() -> None:
    app, pool = _build_app(rows=[], total=0)

    response = await _call(app, "/api/qa/cases", params={"since": "24h"})

    assert response.status_code == 200
    cutoff = pool.fetchval.await_args.args[1]
    assert isinstance(cutoff, datetime)
    assert (
        timedelta(hours=23, minutes=59)
        <= datetime.now(UTC) - cutoff
        <= timedelta(hours=24, minutes=1)
    )
    assert "a.created_at >= $1" in pool.fetchval.await_args.args[0]


async def test_cases_pagination_passes_offset_limit_and_reports_has_more() -> None:
    app, pool = _build_app(rows=[_make_case_row()], total=40)

    body = (
        await _call(app, "/api/qa/cases", params={"offset": 10, "limit": 5, "sev": "all"})
    ).json()

    assert body["meta"] == {"total": 40, "offset": 10, "limit": 5, "has_more": True}
    fetch_args = pool.fetch.await_args.args
    assert fetch_args[-2:] == (10, 5)
    assert "COALESCE(f.severity, a.severity) IN" not in pool.fetch.await_args.args[0]
    assert "public.qa_findings" not in pool.fetchval.await_args.args[0]
