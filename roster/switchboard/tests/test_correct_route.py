"""Unit and integration tests for the correct_route Switchboard tool.

Tests cover:
- Successful re-dispatch to the correct butler
- Ingestion event not found (invalid request_id)
- Expired ingestion event (older than 1-month retention window)
- Message inbox row not found (pruned)
- Dispatch failure (butler unreachable / not registered)
- message_inbox lifecycle update to 'corrected'
- operator_audit_log recording
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.routing.correct_route import (
    _RETENTION_WINDOW,
    correct_route,
)

# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# DB fixture — real core + Switchboard chains, ordinary migration login
# ---------------------------------------------------------------------------


class _CorrectionPool(asyncpg.Pool):
    """A real asyncpg pool carrying its disposable Alembic configuration."""

    migration_config: Config


@pytest.fixture(scope="module")
def correction_db_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(correction_db_url):
    """Reuse migrated structure; isolate every node's committed rows.

    The supported factory runs bootstrap separately from the ordinary
    NOCREATEDB migration login. No Template8 cache or copied DDL is used.
    """
    p = await _CorrectionPool(
        correction_db_url,
        min_size=2,
        max_size=3,
        max_queries=50000,
        max_inactive_connection_lifetime=300.0,
        loop=None,
        connection_class=asyncpg.Connection,
        record_class=asyncpg.Record,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard, public"},
    )
    p.migration_config = _build_alembic_config(
        correction_db_url, ["switchboard"], target_schema="switchboard"
    )
    try:
        # Reset only rows in this module's disposable DB. DELETE avoids
        # TRUNCATE's privilege requirements on unrelated FK descendants.
        for table in (
            "switchboard.routing_log",
            "switchboard.operator_audit_log",
            "switchboard.message_inbox",
            "public.ingestion_events",
            "switchboard.butler_registry_eligibility_log",
            "switchboard.butler_boot_registrations",
            "switchboard.butler_registry_control_plane",
            "switchboard.butler_registry",
        ):
            await p.execute(f"DELETE FROM {table}")
        yield p
    finally:
        await p.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _audit_revision(pool: asyncpg.Pool, *, legacy: bool) -> None:
    """Run the actual bounded revision, never hand-install a test CHECK."""
    assert isinstance(pool, _CorrectionPool)
    operation = command.downgrade if legacy else command.upgrade
    # pinned-revision: this species owns only the sw_042 -> sw_043 boundary.
    target = "switchboard@sw_042" if legacy else "switchboard@sw_043"
    await asyncio.to_thread(operation, pool.migration_config, target)


@asynccontextmanager
async def _audit_runtime(pool: asyncpg.Pool):
    async with pool.acquire() as writer:
        await writer.execute("SET ROLE butler_switchboard_rw")
        try:
            role = await writer.fetchrow(
                "SELECT current_user AS name, rolsuper, rolcreaterole, rolcreatedb, "
                "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert role["name"] == "butler_switchboard_rw"
            assert not any(role[key] for key in role.keys() if key != "name")
            yield writer
        finally:
            await writer.execute("RESET ROLE")


async def _legacy_domains(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Plant every old action/outcome combination under the real old CHECKs."""
    async with _audit_runtime(pool) as writer:
        for action in (
            "manual_reroute",
            "cancel_request",
            "abort_request",
            "controlled_replay",
            "controlled_retry",
            "force_complete",
        ):
            for outcome in ("success", "failed", "rejected", "partial"):
                await writer.execute(
                    "INSERT INTO operator_audit_log "
                    "(action_type, target_request_id, target_table, operator_identity, "
                    "reason, outcome) VALUES ($1, $2, 'message_inbox', 'legacy-domain', "
                    "'legacy upgrade survivor', $3)",
                    action,
                    uuid.uuid4(),
                    outcome,
                )
    return [
        dict(row)
        for row in await pool.fetch(
            "SELECT * FROM operator_audit_log WHERE operator_identity = 'legacy-domain' ORDER BY id"
        )
    ]


async def _invalid_domains(pool: asyncpg.Pool) -> None:
    async with _audit_runtime(pool) as writer:
        for action, outcome in (
            ("invalid-action", "success"),
            ("manual_reroute", "invalid-outcome"),
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await writer.execute(
                    "INSERT INTO operator_audit_log "
                    "(action_type, target_request_id, target_table, operator_identity, "
                    "reason, outcome) VALUES ($1, $2, 'message_inbox', 'invalid-domain', "
                    "'must refuse', $3)",
                    action,
                    uuid.uuid4(),
                    outcome,
                )
        assert (
            await writer.fetchval(
                "SELECT count(*) FROM operator_audit_log WHERE operator_identity = 'invalid-domain'"
            )
            == 0
        )


def _make_request_id() -> uuid.UUID:
    return uuid.uuid4()


def _make_correction_id() -> uuid.UUID:
    return uuid.uuid4()


async def _seed_ingestion_event(
    pool: asyncpg.Pool,
    *,
    request_id: uuid.UUID,
    received_at: datetime | None = None,
    source_channel: str = "telegram_bot",
    triage_target: str | None = "assistant",
) -> None:
    """Insert a minimal public.ingestion_events row for testing."""
    if received_at is None:
        received_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO public.ingestion_events (
            id, received_at, source_channel, source_provider,
            source_endpoint_identity, source_sender_identity,
            external_event_id, dedupe_key, dedupe_strategy,
            ingestion_tier, policy_tier, triage_target
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        """,
        request_id,
        received_at,
        source_channel,
        "telegram",
        "bot_test",
        "user_123",
        f"evt_{request_id}",
        f"dedupe_{request_id}",
        "connector_api",
        "full",
        "default",
        triage_target,
    )


async def _seed_message_inbox(
    pool: asyncpg.Pool,
    *,
    request_id: uuid.UUID,
    received_at: datetime | None = None,
    lifecycle_state: str = "accepted",
    triage_target: str | None = "assistant",
) -> None:
    """Insert a minimal message_inbox row for testing."""
    if received_at is None:
        received_at = datetime.now(UTC)
    request_context: dict[str, Any] = {
        "request_id": str(request_id),
        "received_at": received_at.isoformat(),
        "source_channel": "telegram_bot",
        "triage_decision": "route_to",
        "triage_target": triage_target,
    }
    raw_payload: dict[str, Any] = {
        "source": {
            "channel": "telegram_bot",
            "provider": "telegram",
            "endpoint_identity": "bot_test",
        },
        "event": {
            "external_event_id": f"evt_{request_id}",
            "observed_at": received_at.isoformat(),
        },
        "sender": {"identity": "user_123"},
        "payload": {"normalized_text": "Hello from wrong butler"},
    }
    await pool.execute(
        """
        INSERT INTO message_inbox (
            id, received_at, request_context, raw_payload, normalized_text,
            lifecycle_state, processing_metadata
        ) VALUES ($1, $2, $3, $4, $5, $6, '{}'::jsonb)
        """,
        request_id,
        received_at,
        request_context,
        raw_payload,
        "Hello from wrong butler",
        lifecycle_state,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorrectRouteNotFound:
    """Tests for missing ingestion events."""

    async def test_unknown_request_id_returns_error(self, pool: asyncpg.Pool) -> None:
        """Returns ingestion_event_not_found when request_id doesn't exist."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "ingestion_event_not_found"
        assert str(request_id) in result["message"]

    async def test_error_message_is_actionable(self, pool: asyncpg.Pool) -> None:
        """Error message tells the LLM how to recover."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        # Message must contain actionable hints
        msg = result["message"].lower()
        assert "request_id" in msg or "ingestion events" in msg


class TestCorrectRouteExpired:
    """Tests for ingestion events past the 1-month retention window."""

    async def test_expired_event_returns_error(self, pool: asyncpg.Pool) -> None:
        """Returns ingestion_event_expired when event is older than 1 month."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        old_ts = datetime.now(UTC) - _RETENTION_WINDOW - timedelta(days=1)

        await _seed_ingestion_event(pool, request_id=request_id, received_at=old_ts)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "ingestion_event_expired"

    async def test_expired_message_includes_age(self, pool: asyncpg.Pool) -> None:
        """Expired error message includes the event age and alternative."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        old_ts = datetime.now(UTC) - _RETENTION_WINDOW - timedelta(days=5)

        await _seed_ingestion_event(pool, request_id=request_id, received_at=old_ts)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        # Must mention data_correction alternative
        assert "data_correction" in result["message"]

    async def test_recent_event_not_expired(self, pool: asyncpg.Pool) -> None:
        """A recent event does NOT return expired error (continues to next stage)."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        # 1 day ago — well within retention window
        recent_ts = datetime.now(UTC) - timedelta(days=1)

        await _seed_ingestion_event(pool, request_id=request_id, received_at=recent_ts)

        # No message_inbox row — should fail with a different error, not expired
        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert result["error"] != "ingestion_event_expired"

    async def test_event_exactly_at_retention_boundary_is_expired(self, pool: asyncpg.Pool) -> None:
        """Event at exactly the retention boundary is considered expired."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        # Exactly at boundary
        boundary_ts = datetime.now(UTC) - _RETENTION_WINDOW - timedelta(seconds=1)

        await _seed_ingestion_event(pool, request_id=request_id, received_at=boundary_ts)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "ingestion_event_expired"


class TestCorrectRouteMessageInboxMissing:
    """Tests for missing message_inbox rows (pruned after partition expiry)."""

    async def test_missing_inbox_row_returns_error(self, pool: asyncpg.Pool) -> None:
        """Returns message_inbox_not_found when inbox row is missing."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        # Seed ingestion event but NO message_inbox row
        await _seed_ingestion_event(pool, request_id=request_id)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "message_inbox_not_found"

    async def test_missing_inbox_message_is_actionable(self, pool: asyncpg.Pool) -> None:
        """Error message suggests data_correction as alternative."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        assert "data_correction" in result["message"]


async def _register_butler(
    pool: asyncpg.Pool,
    name: str,
    *,
    endpoint_url: str = "http://localhost:8099/mcp/sse",
    agent_type: str = "butler",
) -> None:
    """Register an active agent in butler_registry for routing tests."""
    await pool.execute(
        """
        INSERT INTO butler_registry (name, endpoint_url, last_seen_at, agent_type)
        VALUES ($1, $2, now(), $3)
        ON CONFLICT (name) DO NOTHING
        """,
        name,
        endpoint_url,
        agent_type,
    )


def _failing_call_fn() -> Any:
    """Return a call_fn that simulates an unreachable but registered butler."""
    return AsyncMock(side_effect=RuntimeError("connection refused"))


class TestCorrectRouteUnregistered:
    """Tests for re-dispatch to a butler that is not in the registry.

    Per the butler-switchboard spec ("Re-dispatch to unregistered butler
    rejected"), the tool must fail with the list of available butlers so the
    caller can pick a valid routing target.
    """

    @pytest.mark.pg_clock
    async def test_unregistered_butler_returns_butler_not_registered(
        self, pool: asyncpg.Pool
    ) -> None:
        """Returns butler_not_registered when target is not in the registry."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        # Register some valid butlers (and a staffer, which must be excluded).
        await _register_butler(pool, "personal_assistant")
        await _register_butler(pool, "finance")
        await _register_butler(pool, "messenger", agent_type="staffer")

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="nonexistent_butler",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "butler_not_registered"
        # The available_butlers list must be populated from the real registry,
        # contain only routable butler-typed agents, and exclude the staffer.
        assert set(result["available_butlers"]) == {"personal_assistant", "finance"}
        assert "messenger" not in result["available_butlers"]
        # The human-readable message must name the rejected butler and the options.
        assert "nonexistent_butler" in result["message"]
        assert "personal_assistant" in result["message"]
        assert "finance" in result["message"]

    async def test_unregistered_butler_empty_registry(self, pool: asyncpg.Pool) -> None:
        """Returns an empty available_butlers list when none are registered."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="ghost_butler",
            correction_id=correction_id,
        )

        assert result["success"] is False
        assert result["error"] == "butler_not_registered"
        assert result["available_butlers"] == []


class TestCorrectRouteDispatchFailure:
    """Tests for routing failures (registered butler unreachable)."""

    async def test_registered_butler_unreachable_returns_dispatch_failed(
        self, pool: asyncpg.Pool
    ) -> None:
        """Returns dispatch_failed when a registered butler cannot be reached."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)
        await _register_butler(pool, "nonexistent_butler")

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="nonexistent_butler",
            correction_id=correction_id,
            call_fn=_failing_call_fn(),
        )

        assert result["success"] is False
        assert result["error"] == "dispatch_failed"
        # Error message must say which butler failed
        assert "nonexistent_butler" in result["message"]

    async def test_dispatch_failed_message_is_actionable(self, pool: asyncpg.Pool) -> None:
        """dispatch_failed error message tells LLM to check list_butlers()."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)
        await _register_butler(pool, "ghost_butler")

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="ghost_butler",
            correction_id=correction_id,
            call_fn=_failing_call_fn(),
        )

        assert "list_butlers" in result["message"]

    async def test_dispatch_failed_writes_audit_log(self, pool: asyncpg.Pool) -> None:
        """dispatch_failed records a failure entry in operator_audit_log."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)
        await _register_butler(pool, "missing_butler")

        await _audit_revision(pool, legacy=True)
        legacy_rows = await _legacy_domains(pool)
        await _invalid_domains(pool)
        try:
            async with _audit_runtime(pool) as writer:
                old_result = await correct_route(
                    writer,
                    request_id=request_id,
                    correct_butler="missing_butler",
                    correction_id=correction_id,
                    call_fn=_failing_call_fn(),
                )
                assert old_result["error"] == "dispatch_failed"
                assert (
                    await pool.fetchval(
                        "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1",
                        request_id,
                    )
                    == 0
                )
        finally:
            await _audit_revision(pool, legacy=False)
        assert [
            dict(row)
            for row in await pool.fetch(
                "SELECT * FROM operator_audit_log WHERE operator_identity = 'legacy-domain' ORDER BY id"
            )
        ] == legacy_rows
        await _invalid_domains(pool)

        async with _audit_runtime(pool) as writer:
            result = await correct_route(
                writer,
                request_id=request_id,
                correct_butler="missing_butler",
                correction_id=correction_id,
                call_fn=_failing_call_fn(),
            )

            async with pool.acquire() as reader:
                assert await writer.fetchval("SELECT pg_backend_pid()") != await reader.fetchval(
                    "SELECT pg_backend_pid()"
                )
                assert (
                    await reader.fetchval(
                        "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1",
                        request_id,
                    )
                    == 1
                )

        assert result["success"] is False
        assert result["error"] == "dispatch_failed"

        audit_row = await pool.fetchrow(
            """
            SELECT action_type, target_request_id, outcome, outcome_details
            FROM operator_audit_log
            WHERE action_type = 'correct_route' AND target_request_id = $1
            """,
            request_id,
        )
        assert audit_row is not None, "dispatch_failed must write an audit log entry"
        assert audit_row["outcome"] == "failure"
        outcome_details_raw = audit_row["outcome_details"]
        outcome_details = (
            json.loads(outcome_details_raw)
            if isinstance(outcome_details_raw, str)
            else outcome_details_raw
        )
        assert outcome_details["error"] == "dispatch_failed"

        # An actual downgrade refuses new-domain rows without deleting history.
        with pytest.raises(IntegrityError) as refused:
            await _audit_revision(pool, legacy=True)
        assert getattr(refused.value.orig, "pgcode", None) == "23514"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1", request_id
            )
            == 1
        )
        await pool.execute(
            "DELETE FROM operator_audit_log WHERE target_request_id = $1", request_id
        )
        await _audit_revision(pool, legacy=True)
        try:
            async with _audit_runtime(pool) as writer:
                await correct_route(
                    writer,
                    request_id=request_id,
                    correct_butler="missing_butler",
                    correction_id=correction_id,
                    call_fn=_failing_call_fn(),
                )
            assert (
                await pool.fetchval(
                    "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1",
                    request_id,
                )
                == 0
            )
        finally:
            await _audit_revision(pool, legacy=False)
        async with _audit_runtime(pool) as writer:
            await correct_route(
                writer,
                request_id=request_id,
                correct_butler="missing_butler",
                correction_id=correction_id,
                call_fn=_failing_call_fn(),
            )
        assert (
            await pool.fetchval(
                "SELECT outcome FROM operator_audit_log WHERE target_request_id = $1", request_id
            )
            == "failure"
        )


class TestCorrectRouteSuccess:
    """Tests for the happy path — successful re-dispatch."""

    async def _make_mock_call_fn(self) -> Any:
        """Return a call_fn mock that simulates a successful route."""
        from unittest.mock import AsyncMock

        call_fn = AsyncMock(return_value={"ok": True})
        return call_fn

    @pytest.mark.pg_clock
    async def test_success_registers_butler_and_dispatches(self, pool: asyncpg.Pool) -> None:
        """Successful re-dispatch returns success=True with expected fields."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        # Register the target butler (set last_seen_at to mark as active)
        await pool.execute(
            """
            INSERT INTO butler_registry (name, endpoint_url, last_seen_at)
            VALUES ('personal_assistant', 'http://localhost:8001/mcp/sse', now())
            ON CONFLICT (name) DO NOTHING
            """,
        )

        call_fn = await self._make_mock_call_fn()

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
            description="Sent to wrong butler; should go to personal_assistant",
            call_fn=call_fn,
        )

        assert result["success"] is True
        assert result["request_id"] == str(request_id)
        assert result["correction_id"] == str(correction_id)
        assert result["correct_butler"] == "personal_assistant"
        assert result["lifecycle_state"] == "corrected"

    @pytest.mark.pg_clock
    async def test_success_updates_lifecycle_state(self, pool: asyncpg.Pool) -> None:
        """Successful re-dispatch marks message_inbox as 'corrected'."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        call_fn = await self._make_mock_call_fn()

        await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            call_fn=call_fn,
        )

        row = await pool.fetchrow(
            "SELECT lifecycle_state, processing_metadata FROM message_inbox WHERE id = $1",
            request_id,
        )
        assert row is not None
        assert row["lifecycle_state"] == "corrected"

    @pytest.mark.pg_clock
    async def test_success_embeds_correction_metadata_in_inbox(self, pool: asyncpg.Pool) -> None:
        """Successful re-dispatch stores correction_id in processing_metadata."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        call_fn = await self._make_mock_call_fn()

        await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            description="Test correction",
            call_fn=call_fn,
        )

        row = await pool.fetchrow(
            "SELECT processing_metadata FROM message_inbox WHERE id = $1",
            request_id,
        )
        metadata_raw = row["processing_metadata"]
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        correction_section = metadata.get("correction", {})

        assert correction_section["correction_id"] == str(correction_id)
        assert correction_section["correction_type"] == "misroute"
        assert correction_section["correct_butler"] == "correct_butler"
        assert correction_section["description"] == "Test correction"

    @pytest.mark.pg_clock
    async def test_success_writes_operator_audit_log(self, pool: asyncpg.Pool) -> None:
        """Successful re-dispatch records an entry in operator_audit_log."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        call_fn = await self._make_mock_call_fn()

        await _audit_revision(pool, legacy=True)
        legacy_rows = await _legacy_domains(pool)
        await _invalid_domains(pool)
        try:
            async with _audit_runtime(pool) as writer:
                with pytest.raises(asyncpg.CheckViolationError):
                    await correct_route(
                        writer,
                        request_id=request_id,
                        correct_butler="correct_butler",
                        correction_id=correction_id,
                        call_fn=call_fn,
                    )
        finally:
            await _audit_revision(pool, legacy=False)
        assert [
            dict(row)
            for row in await pool.fetch(
                "SELECT * FROM operator_audit_log WHERE operator_identity = 'legacy-domain' ORDER BY id"
            )
        ] == legacy_rows
        await _invalid_domains(pool)

        async with _audit_runtime(pool) as writer:
            await correct_route(
                writer,
                request_id=request_id,
                correct_butler="correct_butler",
                correction_id=correction_id,
                call_fn=call_fn,
            )

            async with pool.acquire() as reader:
                assert await writer.fetchval("SELECT pg_backend_pid()") != await reader.fetchval(
                    "SELECT pg_backend_pid()"
                )
                assert (
                    await reader.fetchval(
                        "SELECT lifecycle_state FROM message_inbox WHERE id = $1", request_id
                    )
                    == "corrected"
                )
                assert (
                    await reader.fetchval(
                        "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1",
                        request_id,
                    )
                    == 1
                )

        audit_row = await pool.fetchrow(
            """
            SELECT action_type, target_request_id, operator_identity, outcome
            FROM operator_audit_log
            WHERE action_type = 'correct_route' AND target_request_id = $1
            """,
            request_id,
        )
        assert audit_row is not None
        assert audit_row["action_type"] == "correct_route"
        assert audit_row["target_request_id"] == request_id
        assert audit_row["outcome"] == "success"
        assert str(correction_id) in audit_row["operator_identity"]

        with pytest.raises(IntegrityError) as refused:
            await _audit_revision(pool, legacy=True)
        assert getattr(refused.value.orig, "pgcode", None) == "23514"
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM operator_audit_log WHERE target_request_id = $1", request_id
            )
            == 1
        )
        await pool.execute(
            "DELETE FROM operator_audit_log WHERE target_request_id = $1", request_id
        )
        await _audit_revision(pool, legacy=True)
        try:
            async with _audit_runtime(pool) as writer:
                with pytest.raises(asyncpg.CheckViolationError):
                    await correct_route(
                        writer,
                        request_id=request_id,
                        correct_butler="correct_butler",
                        correction_id=correction_id,
                        call_fn=call_fn,
                    )
        finally:
            await _audit_revision(pool, legacy=False)
        async with _audit_runtime(pool) as writer:
            await correct_route(
                writer,
                request_id=request_id,
                correct_butler="correct_butler",
                correction_id=correction_id,
                call_fn=call_fn,
            )
        assert (
            await pool.fetchval(
                "SELECT outcome FROM operator_audit_log WHERE target_request_id = $1", request_id
            )
            == "success"
        )

    @pytest.mark.pg_clock
    async def test_success_calls_route_with_original_context(self, pool: asyncpg.Pool) -> None:
        """The call_fn receives routing args containing the original context."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        captured_args: list[dict[str, Any]] = []

        async def _capture_call_fn(endpoint_url: str, tool_name: str, args: dict[str, Any]) -> Any:
            captured_args.append(args)
            return {"ok": True}

        await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            call_fn=_capture_call_fn,
        )

        assert len(captured_args) == 1
        args = captured_args[0]
        # trigger contract: must have prompt (str) and context (JSON string or None)
        assert "prompt" in args, "trigger tool requires a 'prompt' key"
        assert args["prompt"] == "Hello from wrong butler"
        # correction metadata is in the context JSON
        ctx_raw = args["context"]
        context = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        assert context["correction_id"] == str(correction_id)
        assert context["original_request_id"] == str(request_id)
        assert context["correction_type"] == "misroute"

    @pytest.mark.pg_clock
    async def test_success_with_correcting_session_id(self, pool: asyncpg.Pool) -> None:
        """correcting_session_id is included in routing args and correction metadata."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        correcting_session_id = uuid.uuid4()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        captured_args: list[dict[str, Any]] = []

        async def _capture_call_fn(endpoint_url: str, tool_name: str, args: dict[str, Any]) -> Any:
            captured_args.append(args)
            return {"ok": True}

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            correcting_session_id=correcting_session_id,
            call_fn=_capture_call_fn,
        )

        assert result["success"] is True
        args = captured_args[0]
        # correcting_session_id is embedded in the trigger context JSON, not at top level
        ctx_raw = args["context"]
        context = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        assert context["correcting_session_id"] == str(correcting_session_id)

        # Check metadata
        row = await pool.fetchrow(
            "SELECT processing_metadata FROM message_inbox WHERE id = $1",
            request_id,
        )
        metadata_raw = row["processing_metadata"]
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
        assert metadata["correction"]["correcting_session_id"] == str(correcting_session_id)

    @pytest.mark.pg_clock
    async def test_string_uuids_are_accepted(self, pool: asyncpg.Pool) -> None:
        """correct_route accepts string UUIDs for all UUID parameters."""
        request_id = str(uuid.uuid4())
        correction_id = str(uuid.uuid4())
        correcting_session_id = str(uuid.uuid4())

        await _seed_ingestion_event(pool, request_id=uuid.UUID(request_id))
        await _seed_message_inbox(pool, request_id=uuid.UUID(request_id))

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('correct_butler', 'http://localhost:8002/mcp/sse', now())"
            " ON CONFLICT DO NOTHING"
        )

        call_fn = AsyncMock(return_value={"ok": True})

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            correcting_session_id=correcting_session_id,
            call_fn=call_fn,
        )

        assert result["success"] is True


class TestCorrectRouteNewSessionId:
    """Tests for new_session_id propagation (butler-switchboard spec).

    Per spec.md, a successful re-dispatch SHALL return ``new_session_id`` — the
    UUID of the session created by the re-dispatch on the correct butler. The
    real path routes through ``route()`` to the target butler's ``trigger``
    tool, whose return surfaces the spawned session UUID as ``session_id``.
    """

    @pytest.mark.pg_clock
    async def test_success_returns_new_session_id_from_re_dispatch(
        self, pool: asyncpg.Pool
    ) -> None:
        """The real session id from the re-dispatch is surfaced as new_session_id."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        new_session_id = uuid.uuid4()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('personal_assistant', 'http://localhost:8001/mcp/sse', now())"
            " ON CONFLICT (name) DO NOTHING"
        )

        # call_fn returns the real `trigger` tool shape, which includes the
        # spawned session's UUID under "session_id".
        async def _trigger_call_fn(endpoint_url: str, tool_name: str, args: dict[str, Any]) -> Any:
            assert tool_name == "trigger"
            return {
                "output": "handled",
                "success": True,
                "error": None,
                "duration_ms": 42,
                "session_id": str(new_session_id),
            }

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
            call_fn=_trigger_call_fn,
        )

        assert result["success"] is True
        assert result["new_session_id"] == str(new_session_id)

    @pytest.mark.pg_clock
    async def test_success_new_session_id_none_when_absent(self, pool: asyncpg.Pool) -> None:
        """new_session_id is None when the re-dispatch return omits session_id."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()

        await _seed_ingestion_event(pool, request_id=request_id)
        await _seed_message_inbox(pool, request_id=request_id)

        await pool.execute(
            "INSERT INTO butler_registry (name, endpoint_url, last_seen_at)"
            " VALUES ('personal_assistant', 'http://localhost:8001/mcp/sse', now())"
            " ON CONFLICT (name) DO NOTHING"
        )

        call_fn = AsyncMock(return_value={"output": "ok", "success": True})

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
            call_fn=call_fn,
        )

        assert result["success"] is True
        assert result["new_session_id"] is None


class TestCorrectRouteRetentionWindow:
    """Tests for the retention window boundary logic."""

    async def test_retention_window_constant_is_31_days(self) -> None:
        """_RETENTION_WINDOW is set to 31 days."""
        assert _RETENTION_WINDOW == timedelta(days=31)

    async def test_event_just_within_window_proceeds(self, pool: asyncpg.Pool) -> None:
        """Event 30 days old is within the retention window."""
        request_id = _make_request_id()
        correction_id = _make_correction_id()
        # 30 days ago — within 31-day window
        recent_ts = datetime.now(UTC) - timedelta(days=30)

        await _seed_ingestion_event(pool, request_id=request_id, received_at=recent_ts)
        # No message_inbox — should fail with message_inbox_not_found, NOT expired

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="personal_assistant",
            correction_id=correction_id,
        )

        # Should have progressed past expiry check
        assert result["error"] != "ingestion_event_expired"
        assert result["error"] == "message_inbox_not_found"
