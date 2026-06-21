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
        "branch_name": None,
        "pr_url": None,
        "pr_number": None,
        "created_at": _NOW,
        "closed_at": None,
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
    fetchval_result: Any | None = None,
    fetchval_side_effect: list[Any] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchrow_side_effect: list[dict[str, Any] | None] | None = None,
    fetch_side_effect: list[list[dict[str, Any]]] | None = None,
) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(
            return_value=total if fetchval_result is None else fetchval_result
        )
    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(
            side_effect=[_r(row) if row is not None else None for row in fetchrow_side_effect]
        )
    elif fetchrow_result is not None:

        async def _fetchrow(sql: str, *_args: Any) -> _MockRecord | None:
            if "FROM public.qa_dismissals" in sql:
                return None
            return _r(fetchrow_result)

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=None)
    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(
            side_effect=[[_r(row) for row in page] for page in fetch_side_effect]
        )
    else:
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


async def test_cases_since_all_omits_created_at_cutoff() -> None:
    app, pool = _build_app(rows=[_make_case_row()], total=1)

    response = await _call(app, "/api/qa/cases", params={"since": "all"})

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == str(_make_case_row()["id"])
    count_args = pool.fetchval.await_args.args
    fetch_args = pool.fetch.await_args.args
    assert "a.created_at >=" not in count_args[0]
    assert "a.created_at >=" not in fetch_args[0]
    assert count_args[1:] == ()
    assert fetch_args[-2:] == (0, 25)


async def test_cases_filter_combinations() -> None:
    app, pool = _build_app(rows=[_make_case_row()], total=1)

    response = await _call(app, "/api/qa/cases", params={"sev": "high", "since": "24h"})

    assert response.status_code == 200
    assert response.json()["data"][0]["sev"] == "high"
    count_sql = pool.fetchval.await_args.args[0]
    fetch_sql = pool.fetch.await_args.args[0]
    assert "COALESCE(f.severity, a.severity) IN (0, 1)" in count_sql
    assert "COALESCE(f.severity, a.severity) IN (0, 1)" in fetch_sql
    cutoff = pool.fetchval.await_args.args[1]
    assert isinstance(cutoff, datetime)
    assert (
        timedelta(hours=23, minutes=59)
        <= datetime.now(UTC) - cutoff
        <= timedelta(hours=24, minutes=1)
    )

    invalid_app, invalid_pool = _build_app(rows=[], total=0)
    invalid_response = await _call(invalid_app, "/api/qa/cases", params={"sev": "invalid"})

    assert invalid_response.status_code == 422
    invalid_pool.fetchval.assert_not_awaited()
    invalid_pool.fetch.assert_not_awaited()


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


async def test_cases_state_filter_applies_before_pagination() -> None:
    app, pool = _build_app(
        rows=[_make_case_row(status="investigating")],
        total=12,
    )

    body = (
        await _call(
            app,
            "/api/qa/cases",
            params={"state": "diagnose", "since": "all", "offset": 10, "limit": 5},
        )
    ).json()

    assert body["meta"] == {"total": 12, "offset": 10, "limit": 5, "has_more": False}
    count_sql = pool.fetchval.await_args.args[0]
    fetch_sql = pool.fetch.await_args.args[0]
    assert "a.status = 'investigating'" in count_sql
    assert "a.status = 'investigating'" in fetch_sql
    assert fetch_sql.index("a.status = 'investigating'") < fetch_sql.index("OFFSET")
    assert pool.fetch.await_args.args[-2:] == (10, 5)


async def test_cases_butler_filter_applies_before_pagination() -> None:
    app, pool = _build_app(
        rows=[_make_case_row(butler_name="health", finding_source_butler="health")],
        total=1,
    )

    body = (
        await _call(
            app,
            "/api/qa/cases",
            params=[
                ("butler", "health"),
                ("butler", "finance"),
                ("since", "all"),
                ("offset", "50"),
                ("limit", "10"),
            ],
        )
    ).json()

    assert body["meta"] == {"total": 1, "offset": 50, "limit": 10, "has_more": False}
    count_args = pool.fetchval.await_args.args
    fetch_args = pool.fetch.await_args.args
    assert "a.butler_name = ANY($1::text[])" in count_args[0]
    assert "a.butler_name = ANY($1::text[])" in fetch_args[0]
    assert count_args[1] == ["health", "finance"]
    assert fetch_args[1] == ["health", "finance"]
    assert fetch_args[0].index("a.butler_name = ANY($1::text[])") < fetch_args[0].index("OFFSET")
    assert fetch_args[-2:] == (50, 10)


def _notes_payload(headline: str = "Agent found the real failure") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "headline": headline,
        "hypothesis": "A scheduler guard is rejecting the job.",
        "blurb_segments": ["The failure is reproducible from the latest patrol."],
        "claims": {
            "c1": {
                "evidence_ids": ["e1"],
                "note": "The stack trace points to the scheduler guard.",
            }
        },
        "evidence_lines": [
            {
                "id": "e1",
                "ts": "2026-05-15T07:58:00Z",
                "lvl": "ERROR",
                "butler": "finance",
                "msg": "raw scheduler failure",
            }
        ],
        "counter_evidence": [
            {
                "hypothesis": "The DB was unavailable",
                "verdict": "rejected",
                "reason": "The health check stayed green.",
            }
        ],
        "why_this_fix": "It moves the guard after state hydration.",
        "diff_snapshot": [{"kind": "+", "text": "hydrate before guard"}],
    }


def _make_journal_row(index: int, *, attempt_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "attempt_id": attempt_id or uuid.uuid4(),
        "ts": _NOW + timedelta(minutes=index),
        "step": "flagged" if index == 0 else "tick",
        "text": f"journal event {index}",
        "detail": f"detail {index}" if index % 2 == 0 else None,
        "data": {"index": index},
    }


async def test_case_detail_full_notes() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_890)
    row = _make_case_row(
        id=attempt_id,
        status="pr_open",
        branch_name="agent/bu-z34mk",
        pr_url="https://github.com/Tzeusy/butlers/pull/1653",
        pr_number=1653,
        finding_structured_evidence={"investigation_notes": _notes_payload()},
    )
    journal_rows = [
        _make_journal_row(0, attempt_id=attempt_id),
        _make_journal_row(1, attempt_id=attempt_id),
    ]
    app, pool = _build_app(fetchrow_result=row, rows=journal_rows)

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"] == {}
    assert body["data"]["case"]["id"] == str(attempt_id)
    assert body["data"]["case"]["headline"] == "Agent found the real failure"
    assert body["data"]["state_track_stage"] == "pr"
    assert body["data"]["investigation_notes"]["hypothesis"] == (
        "A scheduler guard is rejecting the job."
    )
    assert body["data"]["pr"] == {
        "number": 1653,
        "state": "open",
        "title": "PR #1653",
        "branch": "agent/bu-z34mk",
        "ci_status": None,
        "additions": None,
        "deletions": None,
        "opened_at": row["created_at"].isoformat().replace("+00:00", "Z"),
        "merged_at": None,
        "url": "https://github.com/Tzeusy/butlers/pull/1653",
    }
    assert [event["text"] for event in body["data"]["journal"]] == [
        "journal event 0",
        "journal event 1",
    ]
    assert "ORDER BY ts DESC" in pool.fetch.await_args.args[0]


async def test_case_dossier_includes_active_dismissal() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_894)
    fingerprint = "d" * 64
    expires_at = _NOW + timedelta(hours=4)
    row = _make_case_row(id=attempt_id, finding_fingerprint=fingerprint)
    dismissal_row = {
        "fingerprint": fingerprint,
        "expires_at": expires_at,
        "reason": None,
    }
    app, pool = _build_app(fetchrow_side_effect=[row, dismissal_row], rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    dismissal = response.json()["data"]["dismissal"]
    assert dismissal == {
        "fingerprint": fingerprint,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "reason": None,
    }
    dismissal_sql, dismissal_fp = pool.fetchrow.await_args_list[1].args
    assert "FROM public.qa_dismissals" in dismissal_sql
    assert "dismissed_until > now()" in dismissal_sql
    assert dismissal_fp == fingerprint


async def test_case_dossier_dismissal_is_null_when_expired() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_895)
    fingerprint = "e" * 64
    row = _make_case_row(id=attempt_id, finding_fingerprint=fingerprint)
    app, _pool = _build_app(fetchrow_side_effect=[row, None], rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    assert response.json()["data"]["dismissal"] is None


async def test_case_dossier_dismissal_is_null_when_no_dismissal() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_896)
    row = _make_case_row(id=attempt_id)
    app, _pool = _build_app(fetchrow_side_effect=[row, None], rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    assert response.json()["data"]["dismissal"] is None


async def test_cases_returns_raw_evidence_lines() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_892)
    raw_token = "RAW-EVIDENCE-TOKEN-bu-do4op"
    notes = _notes_payload()
    notes["evidence_lines"][0]["msg"] = f"log line includes {raw_token}"
    row = _make_case_row(
        id=attempt_id,
        finding_structured_evidence={"investigation_notes": notes},
    )
    app, _pool = _build_app(fetchrow_result=row, rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    evidence_lines = response.json()["data"]["investigation_notes"]["evidence_lines"]
    assert evidence_lines[0]["msg"] == f"log line includes {raw_token}"


async def test_case_detail_missing_notes() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_891)
    row = _make_case_row(
        id=attempt_id,
        finding_event_summary="Fallback event summary",
        finding_structured_evidence=None,
    )
    app, _pool = _build_app(fetchrow_result=row, rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["case"]["headline"] == "Fallback event summary"
    assert body["investigation_notes"] is None
    assert body["pr"] is None
    assert body["journal"] == []


async def test_cases_short_id_stable_per_attempt() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_321)
    row = _make_case_row(id=attempt_id)
    app, _pool = _build_app(fetchrow_result=row, rows=[])

    first = await _call(app, f"/api/qa/cases/{attempt_id}")
    second = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["case"]["short_id"] == "#321"
    assert second.json()["data"]["case"]["short_id"] == "#321"


async def test_cases_handles_attempt_without_findings() -> None:
    attempt_id = _uuid7_with_timestamp(1_771_234_567_654)
    row = _make_case_row(
        id=attempt_id,
        butler_name="messenger",
        exception_type="ValueError",
        finding_id=None,
        finding_event_summary=None,
        finding_structured_evidence=None,
    )
    app, _pool = _build_app(fetchrow_result=row, rows=[])

    response = await _call(app, f"/api/qa/cases/{attempt_id}")

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["case"]["headline"] == "ValueError in messenger"
    assert body["investigation_notes"] is None


async def test_case_detail_404() -> None:
    missing_id = uuid.uuid4()
    app, _pool = _build_app(fetchrow_result=None)

    response = await _call(app, f"/api/qa/cases/{missing_id}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "QA_CASE_NOT_FOUND",
            "message": f"QA case not found: {missing_id}",
            "butler": None,
            "details": None,
        }
    }


async def test_journal_pagination() -> None:
    attempt_id = uuid.uuid4()
    events = [_make_journal_row(index, attempt_id=attempt_id) for index in range(75)]
    app, pool = _build_app(
        fetchval_side_effect=[True, 75, True, 25],
        fetch_side_effect=[events[:50], events[50:]],
    )

    first_response = await _call(app, f"/api/qa/cases/{attempt_id}/journal")

    assert first_response.status_code == 200
    first_body = first_response.json()
    assert len(first_body["data"]) == 50
    assert first_body["data"][0]["text"] == "journal event 0"
    assert first_body["data"][-1]["text"] == "journal event 49"
    assert first_body["meta"] == {"total": 75, "offset": 0, "limit": 50, "has_more": True}
    first_sql, first_attempt_id, first_limit = pool.fetch.await_args_list[0].args
    assert "ORDER BY ts ASC" in first_sql
    assert first_attempt_id == attempt_id
    assert first_limit == 50

    cursor = events[49]["ts"].isoformat()
    second_response = await _call(
        app,
        f"/api/qa/cases/{attempt_id}/journal",
        params={"cursor": cursor, "limit": 50},
    )

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert len(second_body["data"]) == 25
    assert second_body["data"][0]["text"] == "journal event 50"
    assert second_body["meta"] == {"total": 25, "offset": 0, "limit": 50, "has_more": False}
    second_args = pool.fetch.await_args_list[1].args
    assert "ts > $2" in second_args[0]
    assert second_args[1] == attempt_id
    assert second_args[2] == events[49]["ts"]
    assert second_args[3] == 50


async def test_journal_404() -> None:
    missing_id = uuid.uuid4()
    app, _pool = _build_app(fetchval_result=False)

    response = await _call(app, f"/api/qa/cases/{missing_id}/journal")

    assert response.status_code == 404
    body = response.json()
    assert body == {
        "error": {
            "code": "QA_CASE_NOT_FOUND",
            "message": f"QA case not found: {missing_id}",
            "butler": None,
            "details": None,
        }
    }
