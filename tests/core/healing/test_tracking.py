"""Tests for butlers.core.healing.tracking.

Covers:
- CRUD operations: create_or_join_attempt, update_attempt_status, get_attempt
- Atomic race condition: only one investigating row per fingerprint (INSERT ON CONFLICT)
- Session ID accumulation: joining appends session_id without duplicates
- Fingerprint collision detection: CRITICAL log emitted when (exc_type, call_site) mismatch
- State machine: valid transitions, terminal-state rejection, updated_at / closed_at
- Gate query functions: get_active_attempt, get_recent_attempt, count_active_attempts,
  get_recent_terminal_statuses, list_attempts
- recover_stale_attempts: stale investigating rows → timeout / failed
- session_set_healing_fingerprint in sessions.py (best-effort, no error on missing)
"""

from __future__ import annotations

import logging
import shutil
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None

pytestmark_unit = pytest.mark.unit

# Integration tests require Docker
pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# DB setup helpers (shared schema for integration tests)
# ---------------------------------------------------------------------------

_CREATE_SHARED_SCHEMA = "CREATE SCHEMA IF NOT EXISTS shared"

_CREATE_HEALING_ATTEMPTS_TABLE = """
CREATE TABLE IF NOT EXISTS shared.healing_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     TEXT NOT NULL,
    butler_name     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'investigating',
    severity        INTEGER NOT NULL,
    exception_type  TEXT NOT NULL,
    call_site       TEXT NOT NULL,
    sanitized_msg   TEXT,
    branch_name     TEXT,
    worktree_path   TEXT,
    pr_url          TEXT,
    pr_number       INTEGER,
    session_ids     UUID[] NOT NULL DEFAULT '{}',
    healing_session_id UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    error_detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_healing_fingerprint
    ON shared.healing_attempts(fingerprint);
CREATE INDEX IF NOT EXISTS idx_healing_status
    ON shared.healing_attempts(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_healing_active_fingerprint
    ON shared.healing_attempts(fingerprint)
    WHERE status IN ('investigating', 'pr_open');
"""


async def _setup_db(pool: asyncpg.Pool) -> None:
    """Create the shared schema and healing_attempts table."""
    await pool.execute(_CREATE_SHARED_SCHEMA)
    await pool.execute(_CREATE_HEALING_ATTEMPTS_TABLE)


def _unique_db_name() -> str:
    return f"testdb_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Integration test fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def healing_pool(postgres_container):  # type: ignore[no-untyped-def]
    """Fresh isolated database with shared.healing_attempts for each test module."""
    db_name = _unique_db_name()

    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=5,
    )
    await _setup_db(pool)
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# Helpers shared across integration tests
# ---------------------------------------------------------------------------


def _make_attempt_args(
    *,
    fingerprint: str | None = None,
    butler_name: str = "test-butler",
    severity: int = 2,
    exception_type: str = "builtins.KeyError",
    call_site: str = "src/butlers/core/spawner.py:_run",
    session_id: uuid.UUID | None = None,
    sanitized_msg: str | None = "something went wrong",
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint or uuid.uuid4().hex * 2,  # 64-char hex-ish
        "butler_name": butler_name,
        "severity": severity,
        "exception_type": exception_type,
        "call_site": call_site,
        "session_id": session_id or uuid.uuid4(),
        "sanitized_msg": sanitized_msg,
    }


# ===========================================================================
# Unit tests — no database required
# ===========================================================================


class TestConstants:
    """VALID_STATUSES, TERMINAL_STATUSES, ACTIVE_STATUSES are correctly defined."""

    @pytest.mark.unit
    def test_valid_statuses_complete(self) -> None:
        from butlers.core.healing.tracking import VALID_STATUSES

        expected = {
            "investigating",
            "pr_open",
            "pr_merged",
            "failed",
            "unfixable",
            "anonymization_failed",
            "timeout",
        }
        assert VALID_STATUSES == expected

    @pytest.mark.unit
    def test_terminal_statuses_are_subset_of_valid(self) -> None:
        from butlers.core.healing.tracking import TERMINAL_STATUSES, VALID_STATUSES

        assert TERMINAL_STATUSES.issubset(VALID_STATUSES)

    @pytest.mark.unit
    def test_investigating_not_terminal(self) -> None:
        from butlers.core.healing.tracking import TERMINAL_STATUSES

        assert "investigating" not in TERMINAL_STATUSES

    @pytest.mark.unit
    def test_pr_open_not_terminal(self) -> None:
        from butlers.core.healing.tracking import TERMINAL_STATUSES

        assert "pr_open" not in TERMINAL_STATUSES

    @pytest.mark.unit
    def test_active_statuses(self) -> None:
        from butlers.core.healing.tracking import ACTIVE_STATUSES

        assert "investigating" in ACTIVE_STATUSES
        assert "pr_open" in ACTIVE_STATUSES


class TestUpdateAttemptStatusUnit:
    """State machine validation tested with a mock pool."""

    @pytest.mark.unit
    async def test_rejects_invalid_status(self) -> None:
        """update_attempt_status returns False for an unknown status."""
        from butlers.core.healing.tracking import update_attempt_status

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"status": "investigating"})
        pool.fetchval = AsyncMock(return_value=uuid.uuid4())

        result = await update_attempt_status(pool, uuid.uuid4(), "not_a_real_status")
        assert result is False
        # fetchrow should NOT have been called since we reject before hitting DB
        pool.fetchrow.assert_not_called()

    @pytest.mark.unit
    async def test_rejects_terminal_state_transition(self) -> None:
        """update_attempt_status returns False and logs warning for terminal state."""
        from butlers.core.healing.tracking import update_attempt_status

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={"status": "failed"})
        pool.fetchval = AsyncMock(return_value=None)

        result = await update_attempt_status(pool, uuid.uuid4(), "pr_open")
        assert result is False

    @pytest.mark.unit
    async def test_returns_false_when_attempt_not_found(self) -> None:
        """update_attempt_status returns False when the attempt row is missing."""
        from butlers.core.healing.tracking import update_attempt_status

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await update_attempt_status(pool, uuid.uuid4(), "failed")
        assert result is False

    @pytest.mark.unit
    async def test_rejects_invalid_transition(self) -> None:
        """update_attempt_status rejects transitions not in the state machine."""
        from butlers.core.healing.tracking import update_attempt_status

        pool = MagicMock()
        # investigating → pr_merged is not a valid direct transition
        pool.fetchrow = AsyncMock(return_value={"status": "investigating"})
        pool.fetchval = AsyncMock(return_value=None)

        result = await update_attempt_status(pool, uuid.uuid4(), "pr_merged")
        assert result is False


class TestCollisionDetectionUnit:
    """Fingerprint collision detection emits CRITICAL log."""

    @pytest.mark.unit
    async def test_collision_emits_critical_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """CRITICAL is logged when (exception_type, call_site) differ on join."""
        from butlers.core.healing import tracking

        fingerprint = "a" * 64
        session_id = uuid.uuid4()
        attempt_id = uuid.uuid4()

        # Simulate ON CONFLICT path: xmax != 0, existing fields differ from new
        mock_row = {
            "id": attempt_id,
            "existing_exc_type": "asyncpg.exceptions.UndefinedTableError",
            "existing_call_site": "src/butlers/core/sessions.py:session_create",
            "was_inserted": False,
        }

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)

        with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
            result_id, is_new = await tracking.create_or_join_attempt(
                pool,
                fingerprint=fingerprint,
                butler_name="test-butler",
                severity=0,
                exception_type="builtins.KeyError",  # DIFFERENT from stored
                call_site="src/butlers/core/spawner.py:_run",  # DIFFERENT from stored
                session_id=session_id,
            )

        assert result_id == attempt_id
        assert is_new is False
        assert any(
            "Fingerprint collision detected" in record.message
            for record in caplog.records
            if record.levelno == logging.CRITICAL
        )

    @pytest.mark.unit
    async def test_no_collision_log_on_matching_metadata(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No CRITICAL log when (exception_type, call_site) match on join."""
        from butlers.core.healing import tracking

        fingerprint = "b" * 64
        session_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        exc_type = "builtins.KeyError"
        call_site = "src/butlers/core/spawner.py:_run"

        mock_row = {
            "id": attempt_id,
            "existing_exc_type": exc_type,
            "existing_call_site": call_site,
            "was_inserted": False,
        }

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)

        with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
            result_id, is_new = await tracking.create_or_join_attempt(
                pool,
                fingerprint=fingerprint,
                butler_name="test-butler",
                severity=2,
                exception_type=exc_type,
                call_site=call_site,
                session_id=session_id,
            )

        assert is_new is False
        critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
        assert len(critical_records) == 0

    @pytest.mark.unit
    async def test_new_insert_returns_is_new_true(self) -> None:
        """create_or_join_attempt returns is_new=True when a new row is inserted."""
        from butlers.core.healing import tracking

        attempt_id = uuid.uuid4()
        mock_row = {
            "id": attempt_id,
            "existing_exc_type": "builtins.KeyError",
            "existing_call_site": "src/butlers/core/spawner.py:_run",
            "was_inserted": True,
        }

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=mock_row)

        result_id, is_new = await tracking.create_or_join_attempt(
            pool,
            fingerprint="c" * 64,
            butler_name="test-butler",
            severity=2,
            exception_type="builtins.KeyError",
            call_site="src/butlers/core/spawner.py:_run",
            session_id=uuid.uuid4(),
        )

        assert result_id == attempt_id
        assert is_new is True

    @pytest.mark.unit
    async def test_raises_on_none_result(self) -> None:
        """create_or_join_attempt raises RuntimeError when the query returns None."""
        from butlers.core.healing import tracking

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)

        with pytest.raises(RuntimeError, match="unexpected empty result"):
            await tracking.create_or_join_attempt(
                pool,
                fingerprint="d" * 64,
                butler_name="test-butler",
                severity=2,
                exception_type="builtins.KeyError",
                call_site="src/butlers/core/spawner.py:_run",
                session_id=uuid.uuid4(),
            )


# ===========================================================================
# Integration tests — require Docker
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestCreateOrJoinAttemptIntegration:
    """Integration tests for create_or_join_attempt against a real Postgres."""

    async def test_creates_new_row(self, healing_pool: asyncpg.Pool) -> None:
        """First call creates a new row, is_new=True."""
        from butlers.core.healing.tracking import create_or_join_attempt

        args = _make_attempt_args()
        attempt_id, is_new = await create_or_join_attempt(healing_pool, **args)

        assert isinstance(attempt_id, uuid.UUID)
        assert is_new is True

    async def test_returns_existing_row_on_conflict(self, healing_pool: asyncpg.Pool) -> None:
        """Second call with same fingerprint joins the existing row, is_new=False."""
        from butlers.core.healing.tracking import create_or_join_attempt

        fingerprint = uuid.uuid4().hex * 2
        s1 = uuid.uuid4()
        s2 = uuid.uuid4()

        attempt_id_1, is_new_1 = await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s1)
        )
        attempt_id_2, is_new_2 = await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s2)
        )

        assert is_new_1 is True
        assert is_new_2 is False
        assert attempt_id_1 == attempt_id_2

    async def test_session_ids_accumulated(self, healing_pool: asyncpg.Pool) -> None:
        """Both session IDs appear in the attempt's session_ids array after join."""
        from butlers.core.healing.tracking import create_or_join_attempt, get_attempt

        fingerprint = uuid.uuid4().hex * 2
        s1 = uuid.uuid4()
        s2 = uuid.uuid4()

        attempt_id, _ = await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s1)
        )
        await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s2)
        )

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        session_ids = [str(sid) for sid in row["session_ids"]]
        assert str(s1) in session_ids
        assert str(s2) in session_ids

    async def test_duplicate_session_id_idempotent(self, healing_pool: asyncpg.Pool) -> None:
        """Appending the same session_id twice does not create duplicates."""
        from butlers.core.healing.tracking import create_or_join_attempt, get_attempt

        fingerprint = uuid.uuid4().hex * 2
        s1 = uuid.uuid4()

        attempt_id, _ = await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s1)
        )
        # Join with same session_id — should be idempotent
        await create_or_join_attempt(
            healing_pool, **_make_attempt_args(fingerprint=fingerprint, session_id=s1)
        )

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        session_ids = [str(sid) for sid in row["session_ids"]]
        assert session_ids.count(str(s1)) == 1

    async def test_initial_status_is_investigating(self, healing_pool: asyncpg.Pool) -> None:
        """New attempt has status 'investigating'."""
        from butlers.core.healing.tracking import create_or_join_attempt, get_attempt

        args = _make_attempt_args()
        attempt_id, _ = await create_or_join_attempt(healing_pool, **args)
        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "investigating"

    async def test_row_fields_stored_correctly(self, healing_pool: asyncpg.Pool) -> None:
        """All fields passed to create_or_join_attempt are persisted."""
        from butlers.core.healing.tracking import create_or_join_attempt, get_attempt

        fingerprint = uuid.uuid4().hex * 2
        session_id = uuid.uuid4()
        attempt_id, _ = await create_or_join_attempt(
            healing_pool,
            fingerprint=fingerprint,
            butler_name="finance-butler",
            severity=1,
            exception_type="asyncpg.exceptions.UniqueViolationError",
            call_site="src/butlers/core/sessions.py:session_create",
            session_id=session_id,
            sanitized_msg="duplicate key value violates constraint",
        )

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["fingerprint"] == fingerprint
        assert row["butler_name"] == "finance-butler"
        assert row["severity"] == 1
        assert row["exception_type"] == "asyncpg.exceptions.UniqueViolationError"
        assert row["call_site"] == "src/butlers/core/sessions.py:session_create"
        assert row["sanitized_msg"] == "duplicate key value violates constraint"
        assert str(session_id) in [str(s) for s in row["session_ids"]]

    async def test_collision_detection_critical_log(
        self, healing_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Joining with different (exc_type, call_site) emits CRITICAL log."""
        from butlers.core.healing.tracking import create_or_join_attempt

        fingerprint = uuid.uuid4().hex * 2

        await create_or_join_attempt(
            healing_pool,
            fingerprint=fingerprint,
            butler_name="test-butler",
            severity=2,
            exception_type="builtins.KeyError",
            call_site="src/butlers/core/spawner.py:_run",
            session_id=uuid.uuid4(),
        )

        with caplog.at_level(logging.CRITICAL, logger="butlers.core.healing.tracking"):
            await create_or_join_attempt(
                healing_pool,
                fingerprint=fingerprint,
                butler_name="test-butler",
                severity=0,
                exception_type="asyncpg.exceptions.UndefinedTableError",  # DIFFERENT
                call_site="src/butlers/core/sessions.py:session_create",  # DIFFERENT
                session_id=uuid.uuid4(),
            )

        assert any(
            "Fingerprint collision detected" in r.message
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestUpdateAttemptStatusIntegration:
    """Integration tests for update_attempt_status state machine."""

    async def _create_attempt(self, pool: asyncpg.Pool, **kwargs: Any) -> uuid.UUID:
        from butlers.core.healing.tracking import create_or_join_attempt

        args = _make_attempt_args(**kwargs)
        attempt_id, _ = await create_or_join_attempt(pool, **args)
        return attempt_id

    async def test_valid_transition_investigating_to_pr_open(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """investigating → pr_open is a valid transition."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        result = await update_attempt_status(
            healing_pool,
            attempt_id,
            "pr_open",
            pr_url="https://github.com/test/repo/pull/42",
            pr_number=42,
        )

        assert result is True
        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "pr_open"
        assert row["pr_url"] == "https://github.com/test/repo/pull/42"
        assert row["pr_number"] == 42
        assert row["closed_at"] is None  # pr_open is not terminal

    async def test_valid_transition_investigating_to_failed(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """investigating → failed is a valid transition; sets closed_at."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        result = await update_attempt_status(
            healing_pool,
            attempt_id,
            "failed",
            error_detail="Agent produced no viable fix",
        )

        assert result is True
        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "failed"
        assert row["closed_at"] is not None
        assert row["error_detail"] == "Agent produced no viable fix"

    async def test_valid_transition_pr_open_to_pr_merged(self, healing_pool: asyncpg.Pool) -> None:
        """pr_open → pr_merged is a valid transition; sets closed_at."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "pr_open")
        result = await update_attempt_status(healing_pool, attempt_id, "pr_merged")

        assert result is True
        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "pr_merged"
        assert row["closed_at"] is not None

    async def test_terminal_state_rejects_further_transition(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """A terminal state (failed) rejects any further transition."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "failed")

        # Attempt to transition from terminal state
        result = await update_attempt_status(healing_pool, attempt_id, "pr_open")
        assert result is False

        # Status must be unchanged
        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "failed"

    async def test_terminal_state_rejects_all_statuses(self, healing_pool: asyncpg.Pool) -> None:
        """All terminal states reject further transitions."""
        from butlers.core.healing.tracking import (
            TERMINAL_STATUSES,
            VALID_STATUSES,
            create_or_join_attempt,
            get_attempt,
            update_attempt_status,
        )

        for terminal in TERMINAL_STATUSES:
            # Build a path to each terminal state
            fingerprint = uuid.uuid4().hex * 2
            args = _make_attempt_args(fingerprint=fingerprint)
            attempt_id, _ = await create_or_join_attempt(healing_pool, **args)

            if terminal == "pr_merged":
                # Need to go through pr_open first
                await update_attempt_status(healing_pool, attempt_id, "pr_open")
                await update_attempt_status(healing_pool, attempt_id, terminal)
            else:
                await update_attempt_status(healing_pool, attempt_id, terminal)

            # Now try every valid status — all should be rejected
            for target in VALID_STATUSES:
                result = await update_attempt_status(healing_pool, attempt_id, target)
                assert result is False, (
                    f"Expected rejection of {target!r} from terminal state {terminal!r}"
                )

            row = await get_attempt(healing_pool, attempt_id)
            assert row is not None
            assert row["status"] == terminal

    async def test_updated_at_changes_on_transition(self, healing_pool: asyncpg.Pool) -> None:
        """updated_at is refreshed on every transition."""
        import asyncio

        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        row_before = await get_attempt(healing_pool, attempt_id)
        assert row_before is not None
        updated_at_before = row_before["updated_at"]

        await asyncio.sleep(0.01)  # ensure clock advances
        await update_attempt_status(healing_pool, attempt_id, "failed")

        row_after = await get_attempt(healing_pool, attempt_id)
        assert row_after is not None
        assert row_after["updated_at"] > updated_at_before

    async def test_closed_at_set_for_all_terminal_states(self, healing_pool: asyncpg.Pool) -> None:
        """closed_at is set when transitioning to any terminal state."""
        from butlers.core.healing.tracking import (
            TERMINAL_STATUSES,
            create_or_join_attempt,
            get_attempt,
            update_attempt_status,
        )

        terminal_via_investigating = TERMINAL_STATUSES - {"pr_merged"}
        for terminal in terminal_via_investigating:
            fingerprint = uuid.uuid4().hex * 2
            args = _make_attempt_args(fingerprint=fingerprint)
            attempt_id, _ = await create_or_join_attempt(healing_pool, **args)

            await update_attempt_status(healing_pool, attempt_id, terminal)
            row = await get_attempt(healing_pool, attempt_id)
            assert row is not None
            assert row["closed_at"] is not None, (
                f"closed_at not set for terminal state {terminal!r}"
            )

    async def test_closed_at_null_for_non_terminal_transition(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """closed_at remains NULL when transitioning to non-terminal state (pr_open)."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "pr_open")

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["closed_at"] is None

    async def test_healing_session_id_stored(self, healing_pool: asyncpg.Pool) -> None:
        """healing_session_id is stored when provided."""
        from butlers.core.healing.tracking import get_attempt, update_attempt_status

        attempt_id = await self._create_attempt(healing_pool)
        healing_session_id = uuid.uuid4()

        await update_attempt_status(
            healing_pool,
            attempt_id,
            "pr_open",
            healing_session_id=healing_session_id,
        )

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert str(row["healing_session_id"]) == str(healing_session_id)


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestGateQueriesIntegration:
    """Integration tests for dispatch gate query functions."""

    async def _create_attempt(self, pool: asyncpg.Pool, **kwargs: Any) -> tuple[uuid.UUID, str]:
        """Create an attempt and return (attempt_id, fingerprint)."""
        from butlers.core.healing.tracking import create_or_join_attempt

        fingerprint = kwargs.pop("fingerprint", uuid.uuid4().hex * 2)
        args = _make_attempt_args(fingerprint=fingerprint, **kwargs)
        attempt_id, _ = await create_or_join_attempt(pool, **args)
        return attempt_id, fingerprint

    async def test_get_active_attempt_returns_investigating(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_active_attempt returns the row when status=investigating."""
        from butlers.core.healing.tracking import get_active_attempt

        attempt_id, fingerprint = await self._create_attempt(healing_pool)
        row = await get_active_attempt(healing_pool, fingerprint)

        assert row is not None
        assert str(row["id"]) == str(attempt_id)
        assert row["status"] == "investigating"

    async def test_get_active_attempt_returns_pr_open(self, healing_pool: asyncpg.Pool) -> None:
        """get_active_attempt returns the row when status=pr_open."""
        from butlers.core.healing.tracking import get_active_attempt, update_attempt_status

        attempt_id, fingerprint = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "pr_open")

        row = await get_active_attempt(healing_pool, fingerprint)
        assert row is not None
        assert row["status"] == "pr_open"

    async def test_get_active_attempt_returns_none_for_terminal(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_active_attempt returns None when the attempt is terminal."""
        from butlers.core.healing.tracking import get_active_attempt, update_attempt_status

        attempt_id, fingerprint = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "failed")

        row = await get_active_attempt(healing_pool, fingerprint)
        assert row is None

    async def test_get_active_attempt_returns_none_for_unknown_fingerprint(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_active_attempt returns None for a fingerprint with no row."""
        from butlers.core.healing.tracking import get_active_attempt

        row = await get_active_attempt(healing_pool, "e" * 64)
        assert row is None

    async def test_get_recent_attempt_returns_terminal_within_window(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_recent_attempt returns a recently closed terminal attempt."""
        from butlers.core.healing.tracking import get_recent_attempt, update_attempt_status

        attempt_id, fingerprint = await self._create_attempt(healing_pool)
        await update_attempt_status(healing_pool, attempt_id, "failed")

        row = await get_recent_attempt(healing_pool, fingerprint, window_minutes=60)
        assert row is not None
        assert row["status"] == "failed"

    async def test_get_recent_attempt_returns_none_for_active_attempt(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_recent_attempt returns None when attempt is still active."""
        from butlers.core.healing.tracking import get_recent_attempt

        _, fingerprint = await self._create_attempt(healing_pool)
        # Not yet closed
        row = await get_recent_attempt(healing_pool, fingerprint, window_minutes=60)
        assert row is None

    async def test_get_recent_attempt_returns_none_for_unknown(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_recent_attempt returns None for unknown fingerprint."""
        from butlers.core.healing.tracking import get_recent_attempt

        row = await get_recent_attempt(healing_pool, "f" * 64, window_minutes=60)
        assert row is None

    async def test_count_active_attempts_counts_investigating(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """count_active_attempts returns the number of investigating rows."""
        from butlers.core.healing.tracking import count_active_attempts

        before = await count_active_attempts(healing_pool)

        # Add two new investigating attempts
        fp1 = uuid.uuid4().hex * 2
        fp2 = uuid.uuid4().hex * 2
        await self._create_attempt(healing_pool, fingerprint=fp1)
        await self._create_attempt(healing_pool, fingerprint=fp2)

        after = await count_active_attempts(healing_pool)
        assert after >= before + 2

    async def test_count_active_attempts_excludes_terminal(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """count_active_attempts does not count terminal attempts."""
        from butlers.core.healing.tracking import count_active_attempts, update_attempt_status

        attempt_id, _ = await self._create_attempt(healing_pool)
        before = await count_active_attempts(healing_pool)

        await update_attempt_status(healing_pool, attempt_id, "failed")

        after = await count_active_attempts(healing_pool)
        assert after == before - 1

    async def test_get_recent_terminal_statuses_returns_statuses(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_recent_terminal_statuses returns recent terminal status strings."""
        from butlers.core.healing.tracking import (
            TERMINAL_STATUSES,
            get_recent_terminal_statuses,
            update_attempt_status,
        )

        # Create and close some attempts
        for _ in range(3):
            attempt_id, _ = await self._create_attempt(healing_pool)
            await update_attempt_status(healing_pool, attempt_id, "failed")

        statuses = await get_recent_terminal_statuses(healing_pool, limit=10)
        assert isinstance(statuses, list)
        assert all(s in TERMINAL_STATUSES for s in statuses)
        # We should have at least 3 failures
        assert len([s for s in statuses if s == "failed"]) >= 3

    async def test_get_recent_terminal_statuses_respects_limit(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """get_recent_terminal_statuses respects the limit parameter."""
        from butlers.core.healing.tracking import (
            get_recent_terminal_statuses,
            update_attempt_status,
        )

        for _ in range(5):
            attempt_id, _ = await self._create_attempt(healing_pool)
            await update_attempt_status(healing_pool, attempt_id, "timeout")

        statuses = await get_recent_terminal_statuses(healing_pool, limit=2)
        assert len(statuses) <= 2

    async def test_list_attempts_pagination(self, healing_pool: asyncpg.Pool) -> None:
        """list_attempts returns paginated rows ordered by created_at DESC."""
        from butlers.core.healing.tracking import list_attempts

        # Create several attempts with unique fingerprints
        fps = [uuid.uuid4().hex * 2 for _ in range(4)]
        for fp in fps:
            await self._create_attempt(healing_pool, fingerprint=fp)

        page1 = await list_attempts(healing_pool, limit=2, offset=0)
        page2 = await list_attempts(healing_pool, limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) >= 1  # there are more rows in total

    async def test_list_attempts_status_filter(self, healing_pool: asyncpg.Pool) -> None:
        """list_attempts with status_filter only returns matching rows."""
        from butlers.core.healing.tracking import list_attempts, update_attempt_status

        fp_failed = uuid.uuid4().hex * 2
        fp_timeout = uuid.uuid4().hex * 2

        attempt_f, _ = await self._create_attempt(healing_pool, fingerprint=fp_failed)
        attempt_t, _ = await self._create_attempt(healing_pool, fingerprint=fp_timeout)

        await update_attempt_status(healing_pool, attempt_f, "failed")
        await update_attempt_status(healing_pool, attempt_t, "timeout")

        failed_rows = await list_attempts(healing_pool, status_filter="failed")
        assert all(r["status"] == "failed" for r in failed_rows)
        failed_ids = {str(r["id"]) for r in failed_rows}
        assert str(attempt_f) in failed_ids
        assert str(attempt_t) not in failed_ids


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestRecoverStaleAttemptsIntegration:
    """Integration tests for recover_stale_attempts."""

    async def test_recover_stale_investigating_with_session_to_timeout(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """Stale investigating rows with healing_session_id → timeout."""
        from butlers.core.healing.tracking import (
            create_or_join_attempt,
            get_attempt,
            recover_stale_attempts,
        )

        fingerprint = uuid.uuid4().hex * 2
        args = _make_attempt_args(fingerprint=fingerprint)
        attempt_id, _ = await create_or_join_attempt(healing_pool, **args)

        # Artificially age the row beyond the timeout window
        healing_session_id = uuid.uuid4()
        await healing_pool.execute(
            """
            UPDATE shared.healing_attempts
            SET healing_session_id = $2,
                updated_at = now() - INTERVAL '35 minutes'
            WHERE id = $1
            """,
            attempt_id,
            healing_session_id,
        )

        recovered = await recover_stale_attempts(healing_pool, timeout_minutes=30)
        assert recovered >= 1

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "timeout"
        assert row["error_detail"] is not None
        assert "interrupted" in row["error_detail"].lower()
        assert row["closed_at"] is not None

    async def test_recover_never_spawned_to_failed(self, healing_pool: asyncpg.Pool) -> None:
        """Investigating rows with no healing_session_id older than 5 min → failed."""
        from butlers.core.healing.tracking import (
            create_or_join_attempt,
            get_attempt,
            recover_stale_attempts,
        )

        fingerprint = uuid.uuid4().hex * 2
        args = _make_attempt_args(fingerprint=fingerprint)
        attempt_id, _ = await create_or_join_attempt(healing_pool, **args)

        # Age the row: no healing_session_id, created_at older than 5 minutes
        await healing_pool.execute(
            """
            UPDATE shared.healing_attempts
            SET healing_session_id = NULL,
                created_at = now() - INTERVAL '10 minutes',
                updated_at = now() - INTERVAL '10 minutes'
            WHERE id = $1
            """,
            attempt_id,
        )

        recovered = await recover_stale_attempts(healing_pool, timeout_minutes=30)
        assert recovered >= 1

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "failed"
        assert row["error_detail"] is not None
        assert "never spawned" in row["error_detail"].lower()

    async def test_recent_investigating_row_preserved(self, healing_pool: asyncpg.Pool) -> None:
        """An investigating row updated just now is NOT recovered."""
        from butlers.core.healing.tracking import (
            create_or_join_attempt,
            get_attempt,
            recover_stale_attempts,
            update_attempt_status,
        )

        fingerprint = uuid.uuid4().hex * 2
        args = _make_attempt_args(fingerprint=fingerprint)
        attempt_id, _ = await create_or_join_attempt(healing_pool, **args)

        # Touch updated_at to now (should not be recovered)
        await healing_pool.execute(
            "UPDATE shared.healing_attempts SET updated_at = now() WHERE id = $1",
            attempt_id,
        )

        await recover_stale_attempts(healing_pool, timeout_minutes=30)

        row = await get_attempt(healing_pool, attempt_id)
        assert row is not None
        assert row["status"] == "investigating"

        # Cleanup
        await update_attempt_status(healing_pool, attempt_id, "failed")

    async def test_recover_returns_zero_when_no_stale(self, healing_pool: asyncpg.Pool) -> None:
        """recover_stale_attempts returns 0 when there are no stale rows."""
        from butlers.core.healing.tracking import recover_stale_attempts

        # Use a very short timeout — so nothing newly created counts as stale
        # All existing rows are either already terminal or fresh
        recovered = await recover_stale_attempts(healing_pool, timeout_minutes=9999)
        assert recovered == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestAtomicRaceConditionIntegration:
    """Validate the partial unique index enforces at-most-one active attempt."""

    async def test_concurrent_inserts_same_fingerprint_produce_one_row(
        self, healing_pool: asyncpg.Pool
    ) -> None:
        """Concurrent create_or_join_attempt calls for the same fingerprint produce
        exactly one investigating row and both return the same attempt_id."""
        import asyncio

        from butlers.core.healing.tracking import create_or_join_attempt, list_attempts

        fingerprint = uuid.uuid4().hex * 2
        s1 = uuid.uuid4()
        s2 = uuid.uuid4()
        s3 = uuid.uuid4()

        results = await asyncio.gather(
            create_or_join_attempt(
                healing_pool,
                **_make_attempt_args(fingerprint=fingerprint, session_id=s1),
            ),
            create_or_join_attempt(
                healing_pool,
                **_make_attempt_args(fingerprint=fingerprint, session_id=s2),
            ),
            create_or_join_attempt(
                healing_pool,
                **_make_attempt_args(fingerprint=fingerprint, session_id=s3),
            ),
        )

        attempt_ids = {str(r[0]) for r in results}
        # All three calls must return the same attempt_id
        assert len(attempt_ids) == 1, f"Expected 1 unique attempt_id, got {len(attempt_ids)}"

        # Only one investigating row for this fingerprint
        rows = await list_attempts(healing_pool, status_filter="investigating", limit=1000)
        matching = [r for r in rows if r["fingerprint"] == fingerprint]
        assert len(matching) == 1, f"Expected 1 investigating row, got {len(matching)}"


# ===========================================================================
# session_set_healing_fingerprint — unit tests (already covered by
# test_sessions_healing_trigger.py, but we add targeted checks here)
# ===========================================================================


class TestSessionSetHealingFingerprintUnit:
    """Unit tests for session_set_healing_fingerprint in sessions.py."""

    @pytest.mark.unit
    async def test_issues_update_sql(self) -> None:
        """session_set_healing_fingerprint executes an UPDATE statement."""
        from butlers.core.sessions import session_set_healing_fingerprint

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="UPDATE 1")

        session_id = uuid.uuid4()
        fingerprint = "a" * 64

        await session_set_healing_fingerprint(pool, session_id, fingerprint)

        pool.execute.assert_called_once()
        call_args = pool.execute.call_args
        sql = call_args[0][0]
        assert "healing_fingerprint" in sql
        assert "UPDATE" in sql.upper()

    @pytest.mark.unit
    async def test_no_error_on_zero_rows_affected(self) -> None:
        """session_set_healing_fingerprint is best-effort: no error if row missing."""
        from butlers.core.sessions import session_set_healing_fingerprint

        pool = MagicMock()
        pool.execute = AsyncMock(return_value="UPDATE 0")  # zero rows

        # Must not raise
        await session_set_healing_fingerprint(pool, uuid.uuid4(), "b" * 64)
