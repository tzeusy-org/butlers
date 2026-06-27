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

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import asyncpg
import pytest

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
# DB fixture — minimal schema needed by correct_route
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with the tables needed by correct_route."""
    async with provisioned_postgres_pool() as p:
        # public.ingestion_events (from core_019 + core_032)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS public.ingestion_events (
                id                       UUID PRIMARY KEY,
                received_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
                source_channel           TEXT NOT NULL,
                source_provider          TEXT NOT NULL,
                source_endpoint_identity TEXT NOT NULL,
                source_sender_identity   TEXT,
                source_thread_identity   TEXT,
                external_event_id        TEXT NOT NULL,
                dedupe_key               TEXT NOT NULL,
                dedupe_strategy          TEXT NOT NULL,
                ingestion_tier           TEXT NOT NULL,
                policy_tier              TEXT NOT NULL,
                triage_decision          TEXT,
                triage_target            TEXT,
                status                   TEXT NOT NULL DEFAULT 'ingested',
                error_detail             TEXT
            )
        """)

        # message_inbox — partitioned table (simplified: single non-partitioned table for tests)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS message_inbox (
                id                   UUID NOT NULL,
                received_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                request_context      JSONB NOT NULL DEFAULT '{}'::jsonb,
                raw_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
                normalized_text      TEXT NOT NULL DEFAULT '',
                lifecycle_state      TEXT NOT NULL DEFAULT 'accepted',
                processing_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb,
                schema_version       TEXT NOT NULL DEFAULT 'message_inbox.v2',
                attachments          JSONB,
                direction            TEXT NOT NULL DEFAULT 'inbound',
                ingestion_tier       TEXT NOT NULL DEFAULT 'full',
                created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (id)
            )
        """)

        # operator_audit_log (from switchboard migration 012)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS operator_audit_log (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                action_type      TEXT NOT NULL,
                target_request_id UUID,
                target_table     TEXT,
                operator_identity TEXT NOT NULL,
                reason           TEXT NOT NULL,
                action_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
                outcome          TEXT NOT NULL,
                outcome_details  JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        # butler_registry (needed by route() inside correct_route)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS butler_registry (
                name             TEXT PRIMARY KEY,
                endpoint_url     TEXT NOT NULL,
                description      TEXT,
                modules          JSONB NOT NULL DEFAULT '[]',
                last_seen_at     TIMESTAMPTZ,
                eligibility_state TEXT NOT NULL DEFAULT 'active',
                liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
                quarantined_at   TIMESTAMPTZ,
                quarantine_reason TEXT,
                route_contract_min INTEGER NOT NULL DEFAULT 1,
                route_contract_max INTEGER NOT NULL DEFAULT 1,
                capabilities     JSONB NOT NULL DEFAULT '[]',
                eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_type       TEXT NOT NULL DEFAULT 'butler'
            )
        """)

        # routing_log (needed by _log_routing inside route())
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler  TEXT NOT NULL,
                target_butler  TEXT NOT NULL,
                tool_name      TEXT NOT NULL,
                success        BOOLEAN NOT NULL,
                duration_ms    INTEGER,
                error          TEXT,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                thread_id      TEXT,
                source_channel TEXT,
                contact_id     UUID,
                entity_id      UUID,
                sender_roles   TEXT[]
            )
        """)

        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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

        result = await correct_route(
            pool,
            request_id=request_id,
            correct_butler="missing_butler",
            correction_id=correction_id,
            call_fn=_failing_call_fn(),
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


class TestCorrectRouteSuccess:
    """Tests for the happy path — successful re-dispatch."""

    async def _make_mock_call_fn(self) -> Any:
        """Return a call_fn mock that simulates a successful route."""
        from unittest.mock import AsyncMock

        call_fn = AsyncMock(return_value={"ok": True})
        return call_fn

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

        await correct_route(
            pool,
            request_id=request_id,
            correct_butler="correct_butler",
            correction_id=correction_id,
            call_fn=call_fn,
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
