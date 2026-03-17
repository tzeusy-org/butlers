"""Tests for healing dashboard API routes.

Covers:
- GET /api/healing/attempts — paginated list with optional status filter
- GET /api/healing/attempts/{id} — full attempt detail
- POST /api/healing/attempts/{id}/retry — create new attempt; reject non-terminal with 409;
  dispatch function called when wired; no dispatch when no hook is configured
- GET /api/healing/circuit-breaker — circuit breaker status
- POST /api/healing/circuit-breaker/reset — reset circuit breaker
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.healing import _get_db_manager, _get_dispatch_fn

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attempt_row(
    *,
    attempt_id: uuid.UUID | None = None,
    fingerprint: str = "a" * 64,
    butler_name: str = "general",
    status: str = "investigating",
    severity: int = 2,
    exception_type: str = "KeyError",
    call_site: str = "src/foo.py:bar",
    sanitized_msg: str | None = "Something went wrong",
    branch_name: str | None = None,
    worktree_path: str | None = None,
    pr_url: str | None = None,
    pr_number: int | None = None,
    session_ids: list[str] | None = None,
    healing_session_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    closed_at: datetime | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    """Build a fake healing attempt row dict."""
    now = datetime.now(tz=UTC)
    return {
        "id": attempt_id or uuid.uuid4(),
        "fingerprint": fingerprint,
        "butler_name": butler_name,
        "status": status,
        "severity": severity,
        "exception_type": exception_type,
        "call_site": call_site,
        "sanitized_msg": sanitized_msg,
        "branch_name": branch_name,
        "worktree_path": worktree_path,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "session_ids": session_ids or [],
        "healing_session_id": healing_session_id,
        "created_at": created_at or now,
        "updated_at": updated_at or now,
        "closed_at": closed_at,
        "error_detail": error_detail,
    }


class _MockRecord(dict):
    """A dict subclass that mimics asyncpg Record access patterns.

    Supports both dict() conversion (used by _decode_row) and attribute
    access (used by some route helpers).
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _mock_record(row: dict[str, Any]) -> _MockRecord:
    """Create a dict-backed mock record compatible with asyncpg Record usage."""
    return _MockRecord(row)


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    execute_result: str = "INSERT 0 1",
) -> tuple[Any, MagicMock]:
    """Build a test FastAPI app with a mocked database pool."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])
    mock_pool.fetchrow = AsyncMock(
        return_value=_mock_record(fetchrow_result) if fetchrow_result else None
    )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# GET /api/healing/attempts — list
# ---------------------------------------------------------------------------


class TestListHealingAttempts:
    async def test_returns_empty_list_when_no_attempts(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["offset"] == 0
        assert body["meta"]["limit"] == 20

    async def test_returns_attempts_with_default_pagination(self) -> None:
        attempt_id = uuid.uuid4()
        row = _make_attempt_row(attempt_id=attempt_id, status="failed")
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == str(attempt_id)
        assert body["data"][0]["status"] == "failed"
        assert body["meta"]["total"] == 1

    async def test_accepts_valid_status_filter(self) -> None:
        row = _make_attempt_row(status="investigating")
        app, mock_pool = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts", params={"status": "investigating"})

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "investigating"

    async def test_rejects_invalid_status_filter(self) -> None:
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/healing/attempts", params={"status": "not_a_real_status"}
            )

        assert response.status_code == 422
        assert "not_a_real_status" in response.json()["detail"]

    async def test_pagination_parameters_accepted(self) -> None:
        app, mock_pool = _build_app(fetch_rows=[], fetchval_result=50)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts", params={"limit": 10, "offset": 30})

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["limit"] == 10
        assert body["meta"]["offset"] == 30
        assert body["meta"]["total"] == 50

    async def test_has_more_computed_correctly(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=50)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts", params={"limit": 20, "offset": 0})

        body = response.json()
        assert body["meta"]["has_more"] is True  # 0 + 20 < 50

    async def test_all_valid_statuses_accepted(self) -> None:
        valid_statuses = [
            "dispatch_pending",
            "investigating",
            "pr_open",
            "pr_merged",
            "failed",
            "unfixable",
            "anonymization_failed",
            "timeout",
        ]
        for status_val in valid_statuses:
            app, _ = _build_app(fetch_rows=[], fetchval_result=0)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/healing/attempts", params={"status": status_val})
            assert response.status_code == 200, f"Status {status_val!r} should be accepted"


# ---------------------------------------------------------------------------
# GET /api/healing/attempts/{id} — detail
# ---------------------------------------------------------------------------


class TestGetHealingAttempt:
    async def test_returns_full_attempt_detail(self) -> None:
        attempt_id = uuid.uuid4()
        healing_session = uuid.uuid4()
        row = _make_attempt_row(
            attempt_id=attempt_id,
            status="pr_open",
            pr_url="https://github.com/org/repo/pull/42",
            pr_number=42,
            healing_session_id=healing_session,
            session_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        )
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/healing/attempts/{attempt_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(attempt_id)
        assert data["status"] == "pr_open"
        assert data["pr_url"] == "https://github.com/org/repo/pull/42"
        assert data["pr_number"] == 42
        assert data["healing_session_id"] == str(healing_session)
        assert len(data["session_ids"]) == 2

    async def test_returns_404_when_not_found(self) -> None:
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/healing/attempts/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_returns_attempt_with_null_optional_fields(self) -> None:
        attempt_id = uuid.uuid4()
        row = _make_attempt_row(
            attempt_id=attempt_id,
            status="investigating",
            branch_name=None,
            worktree_path=None,
            pr_url=None,
            pr_number=None,
            healing_session_id=None,
        )
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/healing/attempts/{attempt_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["pr_url"] is None
        assert data["pr_number"] is None
        assert data["healing_session_id"] is None


# ---------------------------------------------------------------------------
# POST /api/healing/attempts/{id}/retry
# ---------------------------------------------------------------------------


class TestRetryHealingAttempt:
    async def test_creates_new_attempt_for_terminal_status(self) -> None:
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "b" * 64

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status="failed",
        )
        new_attempt_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "investigating",
        }

        # First fetchrow call returns original, second returns the new row
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_attempt_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 201
        data = response.json()
        assert data["attempt_id"] == str(new_id)
        assert data["fingerprint"] == fingerprint
        assert data["status"] == "investigating"

    async def test_rejects_retry_on_investigating_status(self) -> None:
        original_id = uuid.uuid4()
        original_row = _make_attempt_row(
            attempt_id=original_id,
            status="investigating",
        )
        app, _ = _build_app(fetchrow_result=original_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 409
        assert "investigating" in response.json()["detail"]

    async def test_rejects_retry_on_pr_open_status(self) -> None:
        original_id = uuid.uuid4()
        original_row = _make_attempt_row(
            attempt_id=original_id,
            status="pr_open",
            pr_url="https://github.com/org/repo/pull/1",
        )
        app, _ = _build_app(fetchrow_result=original_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 409

    async def test_returns_404_when_original_not_found(self) -> None:
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{uuid.uuid4()}/retry")

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "terminal_status",
        ["failed", "unfixable", "anonymization_failed", "timeout", "pr_merged"],
    )
    async def test_accepts_all_terminal_statuses(self, terminal_status: str) -> None:
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "c" * 64

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status=terminal_status,
        )
        new_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "investigating",
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 201, (
            f"Expected 201 for terminal status {terminal_status!r}, got {response.status_code}"
        )

    async def test_dispatch_fn_called_when_wired(self) -> None:
        """When a dispatch callable is injected, it is invoked as a background task."""
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "d" * 64

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status="failed",
            butler_name="test-butler",
            severity=2,
            exception_type="builtins.ValueError",
            call_site="src/butlers/foo.py:bar",
            sanitized_msg="Something broke",
        )
        new_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "investigating",
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        # Track dispatch calls
        dispatch_calls: list[dict] = []

        async def _mock_dispatch(**kwargs: Any) -> None:
            dispatch_calls.append(kwargs)

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[_get_dispatch_fn] = lambda: _mock_dispatch

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 201
        data = response.json()
        assert data["attempt_id"] == str(new_id)

        # The dispatch callable must have been called with the attempt metadata
        assert len(dispatch_calls) == 1
        call = dispatch_calls[0]
        assert call["attempt_id"] == new_id
        assert call["fingerprint"] == fingerprint
        assert call["butler_name"] == "test-butler"
        assert call["severity"] == 2
        assert call["exception_type"] == "builtins.ValueError"
        assert call["call_site"] == "src/butlers/foo.py:bar"
        assert call["sanitized_msg"] == "Something broke"

    async def test_no_dispatch_creates_dispatch_pending_row(self) -> None:
        """When no dispatch callable is wired, the endpoint creates a dispatch_pending row."""
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "e" * 64

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status="failed",
        )
        # Simulate the INSERT returning dispatch_pending (the new initial status)
        new_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "dispatch_pending",
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        # Explicitly return None — no dispatch available
        app.dependency_overrides[_get_dispatch_fn] = lambda: None

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        # Row is created successfully with dispatch_pending status
        assert response.status_code == 201
        data = response.json()
        assert data["attempt_id"] == str(new_id)
        assert data["status"] == "dispatch_pending"

        # Verify the INSERT used 'dispatch_pending' as the status
        insert_call = mock_pool.fetchrow.call_args_list[1]
        insert_args = insert_call[0][1:]
        assert "dispatch_pending" in insert_args

    async def test_dispatch_fn_creates_investigating_row(self) -> None:
        """When a dispatch callable IS wired, the row is created with investigating status."""
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "e2" * 32

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status="failed",
        )
        new_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "investigating",
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        async def _noop_dispatch(**kwargs: Any) -> None:
            pass

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[_get_dispatch_fn] = lambda: _noop_dispatch

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "investigating"

        # Verify the INSERT used 'investigating' (not 'dispatch_pending')
        insert_call = mock_pool.fetchrow.call_args_list[1]
        insert_args = insert_call[0][1:]
        assert "investigating" in insert_args
        assert "dispatch_pending" not in insert_args

    async def test_retry_rejects_dispatch_pending_status(self) -> None:
        """A dispatch_pending row is non-terminal — retry should return 409."""
        original_id = uuid.uuid4()
        original_row = _make_attempt_row(
            attempt_id=original_id,
            status="dispatch_pending",
        )
        app, _ = _build_app(fetchrow_result=original_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 409
        assert "dispatch_pending" in response.json()["detail"]

    async def test_dispatch_receives_correct_metadata_from_original_attempt(self) -> None:
        """Dispatch callable receives all metadata fields from the original attempt row."""
        original_id = uuid.uuid4()
        new_id = uuid.uuid4()
        fingerprint = "f" * 64

        original_row = _make_attempt_row(
            attempt_id=original_id,
            fingerprint=fingerprint,
            status="timeout",
            butler_name="finance",
            severity=0,
            exception_type="asyncpg.exceptions.UndefinedTableError",
            call_site="src/butlers/modules/finance/tools.py:get_transactions",
            sanitized_msg="relation <ID> does not exist",
        )
        new_row = {
            "id": new_id,
            "fingerprint": fingerprint,
            "status": "investigating",
        }

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(
            side_effect=[
                _mock_record(original_row),
                _mock_record(new_row),
            ]
        )

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        captured: list[dict] = []

        async def _capture_dispatch(**kwargs: Any) -> None:
            captured.append(kwargs)

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[_get_dispatch_fn] = lambda: _capture_dispatch

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(f"/api/healing/attempts/{original_id}/retry")

        assert response.status_code == 201
        assert len(captured) == 1
        call = captured[0]
        assert call["fingerprint"] == fingerprint
        assert call["butler_name"] == "finance"
        assert call["severity"] == 0
        assert call["exception_type"] == "asyncpg.exceptions.UndefinedTableError"
        assert call["call_site"] == "src/butlers/modules/finance/tools.py:get_transactions"
        assert call["sanitized_msg"] == "relation <ID> does not exist"


# ---------------------------------------------------------------------------
# GET /api/healing/circuit-breaker
# ---------------------------------------------------------------------------


class TestGetCircuitBreakerStatus:
    async def test_returns_not_tripped_when_no_failures(self) -> None:
        # Empty terminal statuses
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/circuit-breaker")

        assert response.status_code == 200
        data = response.json()
        assert data["tripped"] is False
        assert data["consecutive_failures"] == 0

    async def test_returns_tripped_when_all_failures(self) -> None:
        failure_statuses = [{"status": "failed"}] * 5

        mock_pool = AsyncMock()
        # fetch is called once via get_recent_terminal_statuses
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in failure_statuses])
        # fetchrow is called once to retrieve last_failure_at
        last_failure_row = {"closed_at": datetime.now(tz=UTC)}
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(last_failure_row))

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/circuit-breaker", params={"threshold": 5})

        assert response.status_code == 200
        data = response.json()
        assert data["tripped"] is True
        assert data["consecutive_failures"] == 5
        assert data["threshold"] == 5

    async def test_not_tripped_when_success_breaks_streak(self) -> None:
        # 3 failures, then a pr_merged (success)
        statuses = [
            {"status": "failed"},
            {"status": "failed"},
            {"status": "failed"},
            {"status": "pr_merged"},
        ]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in statuses])
        last_failure_row = {"closed_at": datetime.now(tz=UTC)}
        mock_pool.fetchrow = AsyncMock(return_value=_mock_record(last_failure_row))

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/circuit-breaker", params={"threshold": 5})

        assert response.status_code == 200
        data = response.json()
        assert data["tripped"] is False
        # Still counts 3 consecutive failures at the start
        assert data["consecutive_failures"] == 3

    async def test_default_threshold_is_five(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/circuit-breaker")

        assert response.status_code == 200
        data = response.json()
        assert data["threshold"] == 5


# ---------------------------------------------------------------------------
# POST /api/healing/circuit-breaker/reset
# ---------------------------------------------------------------------------


class TestResetCircuitBreaker:
    async def test_reset_inserts_sentinel_row(self) -> None:
        # After reset, fetch returns a pr_merged (the sentinel)
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
        # After reset, get_recent_terminal_statuses returns the sentinel
        mock_pool.fetch = AsyncMock(return_value=[_mock_record({"status": "pr_merged"})])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/healing/circuit-breaker/reset")

        assert response.status_code == 200
        mock_pool.execute.assert_called_once()
        # The INSERT must target healing_attempts
        call_sql = mock_pool.execute.call_args[0][0]
        assert "healing_attempts" in call_sql
        assert "pr_merged" in call_sql

    async def test_reset_returns_tripped_false_after_success(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
        # After reset, the sentinel breaks the failure streak
        mock_pool.fetch = AsyncMock(return_value=[_mock_record({"status": "pr_merged"})])

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/healing/circuit-breaker/reset")

        assert response.status_code == 200
        data = response.json()
        assert data["tripped"] is False
        assert data["consecutive_failures"] == 0
