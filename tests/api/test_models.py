"""Tests for shared Dashboard API Pydantic models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from butlers.api.models import (
    ApiResponse,
    ButlerSummary,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    PaginatedResponse,
    PaginationMeta,
    SessionSummary,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# ApiResponse
# ---------------------------------------------------------------------------


class TestApiResponse:
    def test_string_data(self):
        resp = ApiResponse[str](data="hello")
        payload = resp.model_dump()
        assert payload == {"data": "hello", "meta": {}}

    def test_dict_data(self):
        resp = ApiResponse[dict](data={"key": "value"})
        payload = resp.model_dump()
        assert payload["data"] == {"key": "value"}

    def test_list_data(self):
        resp = ApiResponse[list[int]](data=[1, 2, 3])
        payload = resp.model_dump()
        assert payload["data"] == [1, 2, 3]

    def test_nested_model_data(self):
        butler = ButlerSummary(name="atlas", status="running", port=8100, db="butler_atlas")
        resp = ApiResponse[ButlerSummary](data=butler)
        payload = resp.model_dump()
        assert payload["data"]["name"] == "atlas"
        assert payload["meta"] == {}

    def test_json_round_trip(self):
        resp = ApiResponse[int](data=42)
        json_str = resp.model_dump_json()
        restored = json.loads(json_str)
        assert restored == {"data": 42, "meta": {}}


# ---------------------------------------------------------------------------
# ErrorResponse
# ---------------------------------------------------------------------------


class TestErrorResponse:
    def test_basic_error(self):
        err = ErrorResponse(error=ErrorDetail(code="NOT_FOUND", message="Butler not found"))
        payload = err.model_dump()
        assert payload == {
            "error": {
                "code": "NOT_FOUND",
                "message": "Butler not found",
                "butler": None,
                "details": None,
            }
        }

    def test_error_with_details(self):
        err = ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION",
                message="Invalid input",
                details={"field": "name", "reason": "too short"},
            )
        )
        payload = err.model_dump()
        assert payload["error"]["details"] == {"field": "name", "reason": "too short"}

    def test_json_round_trip(self):
        err = ErrorResponse(error=ErrorDetail(code="INTERNAL", message="oops"))
        json_str = err.model_dump_json()
        restored = ErrorResponse.model_validate_json(json_str)
        assert restored.error.code == "INTERNAL"


# ---------------------------------------------------------------------------
# PaginationMeta
# ---------------------------------------------------------------------------


class TestPaginationMeta:
    def test_has_more_true(self):
        meta = PaginationMeta(total=100, offset=0, limit=20)
        assert meta.has_more is True

    def test_has_more_false_exact(self):
        meta = PaginationMeta(total=20, offset=0, limit=20)
        assert meta.has_more is False

    def test_has_more_false_last_page(self):
        meta = PaginationMeta(total=50, offset=40, limit=20)
        assert meta.has_more is False

    def test_has_more_true_middle_page(self):
        meta = PaginationMeta(total=100, offset=20, limit=20)
        assert meta.has_more is True

    def test_has_more_empty(self):
        meta = PaginationMeta(total=0, offset=0, limit=20)
        assert meta.has_more is False


# ---------------------------------------------------------------------------
# PaginatedResponse
# ---------------------------------------------------------------------------


class TestPaginatedResponse:
    def test_with_butler_summaries(self):
        butlers = [
            ButlerSummary(name="atlas", status="running", port=8100, db="butler_atlas"),
            ButlerSummary(name="switchboard", status="idle", port=8101, db="butler_switchboard"),
        ]
        resp = PaginatedResponse[ButlerSummary](
            data=butlers,
            meta=PaginationMeta(total=5, offset=0, limit=2),
        )
        payload = resp.model_dump()
        assert len(payload["data"]) == 2
        assert payload["data"][0]["name"] == "atlas"
        assert payload["meta"]["total"] == 5
        assert payload["meta"]["offset"] == 0
        assert payload["meta"]["limit"] == 2

    def test_empty_page(self):
        resp = PaginatedResponse[str](
            data=[],
            meta=PaginationMeta(total=0, offset=0, limit=20),
        )
        payload = resp.model_dump()
        assert payload["data"] == []
        assert payload["meta"]["total"] == 0

    def test_json_round_trip(self):
        resp = PaginatedResponse[int](
            data=[1, 2, 3],
            meta=PaginationMeta(total=10, offset=0, limit=3),
        )
        json_str = resp.model_dump_json()
        restored = json.loads(json_str)
        assert restored["data"] == [1, 2, 3]
        assert restored["meta"]["total"] == 10


# ---------------------------------------------------------------------------
# ButlerSummary
# ---------------------------------------------------------------------------


class TestButlerSummary:
    def test_valid(self):
        b = ButlerSummary(name="atlas", status="running", port=8100, db="butler_atlas")
        assert b.name == "atlas"
        assert b.status == "running"
        assert b.port == 8100
        assert b.db == "butler_atlas"
        assert b.modules == []
        assert b.schedule_count == 0

    def test_rejects_missing_field(self):
        with pytest.raises(Exception):
            ButlerSummary(name="atlas", status="running")  # type: ignore[call-arg]

    def test_json_round_trip(self):
        b = ButlerSummary(name="atlas", status="idle", port=9000, db="butler_atlas")
        json_str = b.model_dump_json()
        restored = ButlerSummary.model_validate_json(json_str)
        assert restored == b


# ---------------------------------------------------------------------------
# SessionSummary
# ---------------------------------------------------------------------------


class TestSessionSummary:
    def test_minimal(self):
        sid = uuid4()
        now = datetime.now(tz=UTC)
        s = SessionSummary(
            id=sid,
            prompt="do something",
            trigger_source="mcp",
            started_at=now,
        )
        assert s.id == sid
        assert s.success is None
        assert s.completed_at is None
        assert s.duration_ms is None

    def test_full(self):
        sid = uuid4()
        start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        end = datetime(2025, 1, 1, 12, 0, 5, tzinfo=UTC)
        s = SessionSummary(
            id=sid,
            prompt="run backup",
            trigger_source="scheduler",
            success=True,
            started_at=start,
            completed_at=end,
            duration_ms=5000,
        )
        assert s.success is True
        assert s.duration_ms == 5000

    def test_json_round_trip(self):
        sid = uuid4()
        now = datetime.now(tz=UTC)
        s = SessionSummary(
            id=sid,
            prompt="test",
            trigger_source="tick",
            started_at=now,
        )
        json_str = s.model_dump_json()
        restored = SessionSummary.model_validate_json(json_str)
        assert restored.id == sid
        assert restored.started_at == now

    def test_rejects_invalid_uuid(self):
        with pytest.raises(Exception):
            SessionSummary(
                id="not-a-uuid",  # type: ignore[arg-type]
                prompt="x",
                trigger_source="mcp",
                started_at=datetime.now(tz=UTC),
            )


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------


class TestHealthResponse:
    def test_ok(self):
        h = HealthResponse(status="ok")
        assert h.model_dump() == {"status": "ok"}

    def test_json_round_trip(self):
        h = HealthResponse(status="degraded")
        restored = HealthResponse.model_validate_json(h.model_dump_json())
        assert restored.status == "degraded"
