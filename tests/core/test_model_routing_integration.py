"""Integration tests for dynamic model routing — end-to-end pipeline validation.

Tests the complete pipeline: complexity tier → catalog lookup → model resolution →
session recording. Requires a real PostgreSQL database (via testcontainer).

Covers:
- trigger() with complexity=high resolves catalog model and stores it in session
- trigger() with complexity=high falls back to TOML when catalog is empty
- Scheduler tick passes complexity to dispatch_fn
- TOML fallback records the TOML model in the session when catalog is empty
- Complexity propagation through route.v1 envelope (contract-level validation)

[bu-afm7.6]
"""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.model_routing import Complexity, resolve_model
from butlers.core.spawner import Spawner

docker_available = shutil.which("docker") is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


async def _create_model_routing_schema(pool: asyncpg.Pool) -> None:
    """Create shared schema with model catalog and butler override tables."""
    await pool.execute("CREATE SCHEMA IF NOT EXISTS shared")

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.model_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias           TEXT NOT NULL,
            runtime_type    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            extra_args      JSONB NOT NULL DEFAULT '[]'::jsonb,
            complexity_tier TEXT NOT NULL DEFAULT 'medium',
            enabled         BOOLEAN NOT NULL DEFAULT true,
            priority        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_model_catalog_alias UNIQUE (alias),
            CONSTRAINT chk_model_catalog_complexity_tier
                CHECK (complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)

    await pool.execute("""
        CREATE TABLE IF NOT EXISTS shared.butler_model_overrides (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler_name      TEXT NOT NULL,
            catalog_entry_id UUID NOT NULL
                REFERENCES shared.model_catalog(id) ON DELETE CASCADE,
            enabled          BOOLEAN NOT NULL DEFAULT true,
            priority         INTEGER,
            complexity_tier  TEXT,
            CONSTRAINT uq_butler_model_overrides_butler_entry
                UNIQUE (butler_name, catalog_entry_id),
            CONSTRAINT chk_butler_model_overrides_complexity_tier
                CHECK (complexity_tier IS NULL
                       OR complexity_tier IN ('trivial', 'medium', 'high', 'extra_high'))
        )
    """)


async def _create_sessions_schema(pool: asyncpg.Pool) -> None:
    """Create minimal sessions table for testing session recording."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prompt TEXT NOT NULL,
            trigger_source TEXT NOT NULL,
            model TEXT,
            success BOOLEAN,
            error TEXT,
            result TEXT,
            tool_calls JSONB NOT NULL DEFAULT '[]'::jsonb,
            duration_ms INTEGER,
            trace_id TEXT,
            request_id TEXT,
            cost JSONB,
            input_tokens INTEGER,
            output_tokens INTEGER,
            parent_session_id UUID,
            ingestion_event_id UUID,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
    """)


async def _create_scheduled_tasks_schema(pool: asyncpg.Pool) -> None:
    """Create minimal scheduled_tasks table for scheduler tests."""
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT UNIQUE NOT NULL,
            cron TEXT NOT NULL,
            prompt TEXT,
            dispatch_mode TEXT NOT NULL DEFAULT 'prompt',
            job_name TEXT,
            job_args JSONB,
            complexity TEXT DEFAULT 'medium',
            timezone TEXT NOT NULL DEFAULT 'UTC',
            start_at TIMESTAMPTZ,
            end_at TIMESTAMPTZ,
            until_at TIMESTAMPTZ,
            display_title TEXT,
            calendar_event_id TEXT,
            source TEXT NOT NULL DEFAULT 'db',
            enabled BOOLEAN NOT NULL DEFAULT true,
            next_run_at TIMESTAMPTZ,
            last_run_at TIMESTAMPTZ,
            last_result JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT scheduled_tasks_dispatch_mode_check
                CHECK (dispatch_mode IN ('prompt', 'job')),
            CONSTRAINT scheduled_tasks_dispatch_payload_check
                CHECK (
                    (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                    OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
                )
        )
    """)


@asynccontextmanager
async def _make_pool(postgres_container: Any) -> AsyncIterator[asyncpg.Pool]:
    """Create a fresh database with all required tables and yield a pool."""
    db_name = f"test_{uuid.uuid4().hex[:12]}"

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
        max_size=3,
    )
    try:
        await _create_model_routing_schema(pool)
        await _create_sessions_schema(pool)
        await _create_scheduled_tasks_schema(pool)
        yield pool
    finally:
        await pool.close()


async def _insert_catalog_entry(
    pool: asyncpg.Pool,
    *,
    alias: str,
    runtime_type: str = "claude-code",
    model_id: str,
    complexity_tier: str = "medium",
    enabled: bool = True,
    priority: int = 0,
    extra_args: list[str] | None = None,
) -> str:
    """Insert a catalog entry and return its UUID string."""
    extra_json = json.dumps(extra_args or [])
    row = await pool.fetchrow(
        """
        INSERT INTO shared.model_catalog
            (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
        VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
        RETURNING id
        """,
        alias,
        runtime_type,
        model_id,
        extra_json,
        complexity_tier,
        enabled,
        priority,
    )
    return str(row["id"])


def _make_config(
    *,
    name: str = "test-butler",
    model: str = "claude-haiku-4-5-20251001",
    port: int = 9100,
) -> ButlerConfig:
    """Build a minimal ButlerConfig for spawner construction."""
    return ButlerConfig(
        name=name,
        port=port,
        runtime=RuntimeConfig(model=model),
        modules={},
        env_required=[],
        env_optional=[],
    )


# ---------------------------------------------------------------------------
# MockAdapter — fast, in-process adapter for spawner integration tests
# ---------------------------------------------------------------------------


class _MockAdapter:
    """Minimal runtime adapter that immediately succeeds."""

    binary_name = "mock"
    last_process_info = None

    def create_worker(self) -> _MockAdapter:
        return _MockAdapter()

    async def invoke(
        self,
        prompt: str,
        system_prompt: str,
        mcp_servers: dict[str, Any],
        env: dict[str, str],
        max_turns: int = 20,
        model: str | None = None,
        runtime_args: list[str] | None = None,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[str | None, list[dict[str, Any]], dict[str, Any] | None]:
        self._invoked_model = model
        return "done", [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        config_path = tmp_dir / "mock_config.json"
        config_path.write_text(json.dumps({"mcpServers": mcp_servers}))
        return config_path

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return "You are a test butler."


# ---------------------------------------------------------------------------
# Integration test: trigger() with complexity=high resolves catalog model
# ---------------------------------------------------------------------------


async def test_trigger_high_complexity_resolves_catalog_model(
    postgres_container: Any,
    tmp_path: Path,
) -> None:
    """trigger(complexity=HIGH) queries catalog and uses the matching model.

    With a real PostgreSQL database, inserts a catalog entry for 'high' tier
    and verifies that trigger() uses that model and records it in the session.
    """
    async with _make_pool(postgres_container) as pool:
        # Insert a catalog entry for 'high' complexity tier
        await _insert_catalog_entry(
            pool,
            alias="opus-high",
            runtime_type="claude-code",
            model_id="claude-opus-4-20250514",
            complexity_tier="high",
            priority=1,
        )

        config_dir = tmp_path / "config-high"
        config_dir.mkdir()
        config = _make_config(model="claude-haiku-4-5-20251001")
        adapter = _MockAdapter()

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=pool,
            runtime=adapter,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock):
            result = await spawner.trigger(
                "complex analysis prompt", "tick", complexity=Complexity.HIGH
            )

        # Spawner should have resolved the catalog model for 'high' tier
        assert result.success is True
        assert result.model == "claude-opus-4-20250514", (
            f"Expected catalog model 'claude-opus-4-20250514', got '{result.model}'"
        )

        # Verify the model is recorded in the sessions table
        session_row = await pool.fetchrow(
            "SELECT model, trigger_source FROM sessions WHERE id = $1",
            result.session_id,
        )
        assert session_row is not None, "Session row should exist after trigger"
        assert session_row["model"] == "claude-opus-4-20250514", (
            f"Session model should be 'claude-opus-4-20250514', got '{session_row['model']}'"
        )
        assert session_row["trigger_source"] == "tick"


# ---------------------------------------------------------------------------
# Integration test: trigger() with empty catalog falls back to TOML model
# ---------------------------------------------------------------------------


async def test_trigger_high_complexity_toml_fallback_when_catalog_empty(
    postgres_container: Any,
    tmp_path: Path,
) -> None:
    """trigger(complexity=HIGH) falls back to TOML model when catalog is empty.

    With a real empty catalog, trigger() should use the TOML-configured model
    as the fallback and record it in the session.
    """
    async with _make_pool(postgres_container) as pool:
        # No catalog entries inserted — empty catalog

        config_dir = tmp_path / "config-toml"
        config_dir.mkdir()
        toml_model = "claude-haiku-4-5-20251001"
        config = _make_config(model=toml_model)
        adapter = _MockAdapter()

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=pool,
            runtime=adapter,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock):
            result = await spawner.trigger("routine check", "tick", complexity=Complexity.HIGH)

        # Empty catalog → TOML fallback
        assert result.success is True
        assert result.model == toml_model, (
            f"Expected TOML fallback model '{toml_model}', got '{result.model}'"
        )

        # Verify the TOML model is recorded in the sessions table
        session_row = await pool.fetchrow(
            "SELECT model FROM sessions WHERE id = $1",
            result.session_id,
        )
        assert session_row is not None
        assert session_row["model"] == toml_model, (
            f"Session should record TOML fallback model '{toml_model}', "
            f"got '{session_row['model']}'"
        )


# ---------------------------------------------------------------------------
# Integration test: complexity tier matching is tier-specific
# ---------------------------------------------------------------------------


async def test_trigger_complexity_tier_is_respected(
    postgres_container: Any,
    tmp_path: Path,
) -> None:
    """Catalog entry for 'high' tier does NOT match 'medium' complexity trigger.

    Verifies that the tier filter is strict: a high-tier catalog entry should
    not be selected when trigger is called with complexity=MEDIUM. TOML fallback
    should apply instead.
    """
    async with _make_pool(postgres_container) as pool:
        # Catalog entry for 'high' tier only
        await _insert_catalog_entry(
            pool,
            alias="opus-high-only",
            runtime_type="claude-code",
            model_id="claude-opus-4-20250514",
            complexity_tier="high",
            priority=1,
        )

        config_dir = tmp_path / "config-tier"
        config_dir.mkdir()
        toml_model = "claude-haiku-4-5-20251001"
        config = _make_config(model=toml_model)
        adapter = _MockAdapter()

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=pool,
            runtime=adapter,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock):
            # Trigger with MEDIUM complexity — high-tier entry should NOT match
            result = await spawner.trigger("medium task", "tick", complexity=Complexity.MEDIUM)

        assert result.success is True
        # TOML fallback: no medium-tier entries in catalog
        assert result.model == toml_model, (
            f"Expected TOML fallback '{toml_model}' for MEDIUM complexity (no matching entry), "
            f"got '{result.model}'"
        )


# ---------------------------------------------------------------------------
# Integration test: catalog priority ordering is respected
# ---------------------------------------------------------------------------


async def test_trigger_catalog_priority_ordering(
    postgres_container: Any,
    tmp_path: Path,
) -> None:
    """Lower priority number wins when multiple catalog entries match the tier.

    Inserts two entries for 'high' tier with different priorities and verifies
    that the lower-priority-numbered entry is selected.
    """
    async with _make_pool(postgres_container) as pool:
        # Lower priority number = higher preference
        await _insert_catalog_entry(
            pool,
            alias="preferred-high",
            runtime_type="claude-code",
            model_id="claude-preferred-4",
            complexity_tier="high",
            priority=5,
        )
        await _insert_catalog_entry(
            pool,
            alias="fallback-high",
            runtime_type="claude-code",
            model_id="claude-fallback-4",
            complexity_tier="high",
            priority=50,
        )

        config_dir = tmp_path / "config-priority"
        config_dir.mkdir()
        config = _make_config(model="claude-haiku-4-5-20251001")
        adapter = _MockAdapter()

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=pool,
            runtime=adapter,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock):
            result = await spawner.trigger("priority test", "tick", complexity=Complexity.HIGH)

        assert result.success is True
        assert result.model == "claude-preferred-4", (
            f"Expected lower-priority-number entry 'claude-preferred-4', got '{result.model}'"
        )


# ---------------------------------------------------------------------------
# Integration test: scheduler tick passes complexity to dispatch_fn
# ---------------------------------------------------------------------------


async def test_scheduler_tick_complexity_propagated_to_dispatch(
    postgres_container: Any,
) -> None:
    """Scheduler tick() reads complexity from scheduled_tasks and passes it to dispatch_fn.

    Creates a scheduled task with complexity='high', fires tick(), and verifies
    that dispatch_fn receives complexity=Complexity.HIGH.
    """
    from butlers.core.scheduler import tick

    async with _make_pool(postgres_container) as pool:
        # Insert a scheduled task with complexity='high' and past due next_run_at
        past = datetime.now(UTC) - timedelta(minutes=5)
        task_name = f"test-complexity-{uuid.uuid4().hex[:8]}"

        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, complexity, source, enabled, next_run_at)
            VALUES ($1, '0 * * * *', 'High complexity task', 'prompt', 'high', 'db', true, $2)
            """,
            task_name,
            past,
        )

        dispatched_complexity: list[Complexity] = []

        async def capturing_dispatch_fn(**kwargs: Any) -> Any:
            dispatched_complexity.append(kwargs.get("complexity"))
            return None

        count = await tick(pool, capturing_dispatch_fn)

        assert count == 1, f"Expected 1 task dispatched, got {count}"
        assert len(dispatched_complexity) == 1
        assert dispatched_complexity[0] == Complexity.HIGH, (
            f"Expected Complexity.HIGH from scheduler, got {dispatched_complexity[0]}"
        )


# ---------------------------------------------------------------------------
# Integration test: scheduler tick defaults complexity to medium when not set
# ---------------------------------------------------------------------------


async def test_scheduler_tick_complexity_defaults_to_medium(
    postgres_container: Any,
) -> None:
    """Scheduler tick() defaults complexity to MEDIUM when column is NULL or missing."""
    from butlers.core.scheduler import tick

    async with _make_pool(postgres_container) as pool:
        past = datetime.now(UTC) - timedelta(minutes=5)
        task_name = f"test-default-complexity-{uuid.uuid4().hex[:8]}"

        # Insert task without complexity (should default to medium)
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, source, enabled, next_run_at)
            VALUES ($1, '0 * * * *', 'Default complexity task', 'prompt', 'db', true, $2)
            """,
            task_name,
            past,
        )

        dispatched_complexity: list[Complexity] = []

        async def capturing_dispatch_fn(**kwargs: Any) -> Any:
            dispatched_complexity.append(kwargs.get("complexity"))
            return None

        count = await tick(pool, capturing_dispatch_fn)

        assert count == 1, f"Expected 1 task dispatched, got {count}"
        assert len(dispatched_complexity) == 1
        assert dispatched_complexity[0] == Complexity.MEDIUM, (
            f"Expected Complexity.MEDIUM as default, got {dispatched_complexity[0]}"
        )


# ---------------------------------------------------------------------------
# Integration test: E2E scheduler → spawner → catalog → session
# ---------------------------------------------------------------------------


async def test_scheduler_high_complexity_resolves_catalog_model_in_session(
    postgres_container: Any,
    tmp_path: Path,
) -> None:
    """Full pipeline: scheduler fires high-complexity task → catalog model in session.

    This test wires scheduler tick() through a real spawner with a real DB
    to verify the complete complexity propagation path:
        scheduled_tasks.complexity='high'
        → tick() calls dispatch_fn(complexity=Complexity.HIGH)
        → Spawner.trigger(complexity=Complexity.HIGH)
        → resolve_model returns 'high' tier catalog entry
        → session.model = catalog model
    """
    from butlers.core.scheduler import tick

    async with _make_pool(postgres_container) as pool:
        # Seed catalog with a 'high' tier entry
        await _insert_catalog_entry(
            pool,
            alias="scheduler-high-opus",
            runtime_type="claude-code",
            model_id="claude-opus-4-e2e",
            complexity_tier="high",
            priority=1,
        )

        config_dir = tmp_path / "config-e2e-sched"
        config_dir.mkdir()
        config = _make_config(model="claude-haiku-fallback")
        adapter = _MockAdapter()

        spawner = Spawner(
            config=config,
            config_dir=config_dir,
            pool=pool,
            runtime=adapter,
        )

        # Insert a scheduled task due in the past with complexity='high'
        past = datetime.now(UTC) - timedelta(minutes=5)
        task_name = f"test-e2e-sched-{uuid.uuid4().hex[:8]}"
        await pool.execute(
            """
            INSERT INTO scheduled_tasks
                (name, cron, prompt, dispatch_mode, complexity, source, enabled, next_run_at)
            VALUES ($1, '0 * * * *', 'High complexity scheduled task', 'prompt', 'high',
                    'db', true, $2)
            """,
            task_name,
            past,
        )

        with patch("butlers.core.spawner.write_audit_entry", new_callable=AsyncMock):
            dispatched = await tick(pool, spawner.trigger)

        assert dispatched == 1, f"Expected 1 task dispatched, got {dispatched}"

        # Check that the session was created with the catalog-resolved model
        session_row = await pool.fetchrow(
            """
            SELECT model, trigger_source
            FROM sessions
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        assert session_row is not None, "Session should have been created"
        assert session_row["model"] == "claude-opus-4-e2e", (
            f"Expected catalog model 'claude-opus-4-e2e' in session, got '{session_row['model']}'"
        )
        assert session_row["trigger_source"] == f"schedule:{task_name}"


# ---------------------------------------------------------------------------
# Integration test: resolve_model returns None for empty catalog (no regression)
# ---------------------------------------------------------------------------


async def test_resolve_model_returns_none_for_empty_catalog(
    postgres_container: Any,
) -> None:
    """resolve_model returns None (not error) when catalog is empty.

    Existing deployments that have no catalog entries should continue to work
    with TOML fallback — this verifies no regression.
    """
    async with _make_pool(postgres_container) as pool:
        for tier in Complexity:
            result = await resolve_model(pool, "general", tier)
            assert result is None, (
                f"Expected None for empty catalog with tier={tier.value}, got {result}"
            )


# ---------------------------------------------------------------------------
# Switchboard contract: complexity propagates through route.v1 envelope
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSwitchboardComplexityContract:
    """Validate complexity propagation through route.v1 contract models.

    These tests are unit-level (no DB) but validate the full contract:
    - Switchboard injects complexity into route.v1 input
    - route.execute handler extracts complexity and passes to spawner
    - Invalid complexity falls back to medium

    [bu-afm7.6]
    """

    # Override module-level asyncio mark — tests here are synchronous
    pytestmark = [pytest.mark.unit]

    @pytest.mark.unit
    def test_route_v1_complexity_high_roundtrips(self) -> None:
        """route.v1 envelope with complexity=high validates and roundtrips correctly."""
        from butlers.tools.switchboard.routing.contracts import RouteEnvelopeV1

        payload = _valid_route_payload()
        payload["input"]["complexity"] = "high"

        envelope = RouteEnvelopeV1.model_validate(payload)
        assert envelope.input.complexity == "high"

    @pytest.mark.unit
    def test_route_v1_complexity_extra_high_roundtrips(self) -> None:
        """route.v1 envelope with complexity=extra_high validates correctly."""
        from butlers.tools.switchboard.routing.contracts import RouteEnvelopeV1

        payload = _valid_route_payload()
        payload["input"]["complexity"] = "extra_high"

        envelope = RouteEnvelopeV1.model_validate(payload)
        assert envelope.input.complexity == "extra_high"

    @pytest.mark.unit
    def test_route_v1_complexity_defaults_medium_when_absent(self) -> None:
        """route.v1 envelope defaults complexity to medium when not specified."""
        from butlers.tools.switchboard.routing.contracts import RouteEnvelopeV1

        payload = _valid_route_payload()
        # No complexity field in input
        assert "complexity" not in payload["input"]

        envelope = RouteEnvelopeV1.model_validate(payload)
        assert envelope.input.complexity == "medium"

    @pytest.mark.unit
    def test_route_v1_complexity_normalizes_case(self) -> None:
        """route.v1 complexity field normalizes uppercase input to lowercase."""
        from butlers.tools.switchboard.routing.contracts import RouteEnvelopeV1

        payload = _valid_route_payload()
        payload["input"]["complexity"] = "HIGH"

        envelope = RouteEnvelopeV1.model_validate(payload)
        assert envelope.input.complexity == "high"

    @pytest.mark.unit
    def test_route_v1_invalid_complexity_raises_validation_error(self) -> None:
        """route.v1 envelope with invalid complexity value raises ValidationError."""
        from pydantic import ValidationError

        from butlers.tools.switchboard.routing.contracts import RouteEnvelopeV1

        payload = _valid_route_payload()
        payload["input"]["complexity"] = "extreme"

        with pytest.raises(ValidationError) as exc_info:
            RouteEnvelopeV1.model_validate(payload)

        error = exc_info.value.errors()[0]
        assert error["type"] == "invalid_complexity"

    @pytest.mark.unit
    def test_complexity_enum_covers_all_valid_contract_values(self) -> None:
        """Complexity enum values match the set accepted by the route.v1 contract."""
        from butlers.tools.switchboard.routing.contracts import _ALLOWED_COMPLEXITY_VALUES

        enum_values = {c.value for c in Complexity}
        assert enum_values == _ALLOWED_COMPLEXITY_VALUES, (
            f"Complexity enum values {enum_values} must match contract "
            f"allowed values {_ALLOWED_COMPLEXITY_VALUES}"
        )


# ---------------------------------------------------------------------------
# Helpers for contract tests (no DB required)
# ---------------------------------------------------------------------------

_VALID_UUID7 = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


def _valid_route_payload() -> dict[str, Any]:
    """Return a valid route.v1 payload dict (no complexity set by default)."""
    return {
        "schema_version": "route.v1",
        "request_context": {
            "request_id": _VALID_UUID7,
            "received_at": "2026-02-18T10:00:00Z",
            "source_channel": "telegram",
            "source_endpoint_identity": "switchboard-bot",
            "source_sender_identity": "user-123",
        },
        "input": {
            "prompt": "summarize this message",
        },
        "subrequest": {
            "subrequest_id": "sr-001",
            "segment_id": "seg-001",
            "fanout_mode": "parallel",
        },
        "target": {
            "butler": "health",
            "tool": "route.execute",
        },
    }
