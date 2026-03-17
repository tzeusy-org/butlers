"""Tests for the SelfHealingModule.

Covers:
- Module ABC compliance (name, config_schema, dependencies, migration_revisions)
- Config schema validation (defaults, field overrides, extra fields rejected)
- Tool registration (report_error and get_healing_status are registered)
- report_error response shapes: accepted, already_investigating, gate rejection
- report_error sensitivity metadata (error_message, traceback, context are sensitive)
- get_healing_status: by fingerprint, recent list, empty result
- on_startup: calls recover_stale_attempts + reap_stale_worktrees
- on_shutdown: cancels watchdog tasks
- wire_runtime: wires butler_name, spawner, repo_root
- _serialize_attempt: handles UUID and datetime fields
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.self_healing import SelfHealingConfig, SelfHealingModule, _serialize_attempt

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module() -> SelfHealingModule:
    return SelfHealingModule()


def _make_config(**kwargs) -> SelfHealingConfig:
    return SelfHealingConfig(**kwargs)


def _make_fake_pool(
    active_attempt: dict | None = None,
    recent_attempts: list[dict] | None = None,
) -> MagicMock:
    """Mock asyncpg Pool for module tests."""
    pool = MagicMock()

    async def fetchrow(*args, **kwargs):
        return None

    async def fetch(*args, **kwargs):
        return []

    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    return pool


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    """Verify SelfHealingModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(SelfHealingModule, Module)

    def test_instantiates(self):
        mod = _make_module()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = _make_module()
        assert mod.name == "self_healing"

    def test_config_schema_is_self_healing_config(self):
        mod = _make_module()
        assert mod.config_schema is SelfHealingConfig

    def test_config_schema_is_pydantic_model(self):
        mod = _make_module()
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_is_empty_list(self):
        mod = _make_module()
        assert mod.dependencies == []

    def test_migration_revisions_is_none(self):
        mod = _make_module()
        assert mod.migration_revisions() is None


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class TestSelfHealingConfig:
    """Validate SelfHealingConfig defaults and field handling."""

    def test_defaults(self):
        cfg = SelfHealingConfig()
        assert cfg.enabled is True
        assert cfg.severity_threshold == 2
        assert cfg.max_concurrent == 2
        assert cfg.cooldown_minutes == 60
        assert cfg.circuit_breaker_threshold == 5
        assert cfg.timeout_minutes == 30

    def test_enabled_false(self):
        cfg = SelfHealingConfig(enabled=False)
        assert cfg.enabled is False

    def test_override_all_fields(self):
        cfg = SelfHealingConfig(
            enabled=True,
            severity_threshold=1,
            max_concurrent=4,
            cooldown_minutes=120,
            circuit_breaker_threshold=3,
            timeout_minutes=15,
        )
        assert cfg.severity_threshold == 1
        assert cfg.max_concurrent == 4
        assert cfg.cooldown_minutes == 120
        assert cfg.circuit_breaker_threshold == 3
        assert cfg.timeout_minutes == 15

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            SelfHealingConfig(unknown_field=True)

    def test_from_dict(self):
        cfg = SelfHealingConfig(**{"enabled": False, "timeout_minutes": 45})
        assert cfg.enabled is False
        assert cfg.timeout_minutes == 45


# ---------------------------------------------------------------------------
# Sensitivity metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Verify sensitivity declarations for report_error."""

    def test_tool_metadata_returns_dict(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        assert isinstance(meta, dict)

    def test_report_error_has_sensitive_args(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        assert "report_error" in meta
        report_meta = meta["report_error"]
        assert isinstance(report_meta, ToolMeta)
        assert report_meta.arg_sensitivities.get("error_message") is True
        assert report_meta.arg_sensitivities.get("traceback") is True
        assert report_meta.arg_sensitivities.get("context") is True

    def test_error_type_and_call_site_not_marked_sensitive(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        report_meta = meta["report_error"]
        # error_type and call_site are not sensitive (no PII expected there)
        assert "error_type" not in report_meta.arg_sensitivities
        assert "call_site" not in report_meta.arg_sensitivities


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegisterTools:
    """Verify that register_tools registers the two expected MCP tools."""

    async def test_registers_report_error(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        fake_db = MagicMock()
        fake_db.pool = None
        await mod.register_tools(FakeMCP(), SelfHealingConfig(), fake_db)
        assert "report_error" in registered_tools

    async def test_registers_get_healing_status(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        fake_db = MagicMock()
        fake_db.pool = None
        await mod.register_tools(FakeMCP(), SelfHealingConfig(), fake_db)
        assert "get_healing_status" in registered_tools

    async def test_accepts_dict_config(self):
        """register_tools should accept a raw dict config (not just SelfHealingConfig)."""
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        fake_db = MagicMock()
        fake_db.pool = None
        await mod.register_tools(FakeMCP(), {"enabled": True, "timeout_minutes": 20}, fake_db)
        assert "report_error" in registered_tools
        assert mod._config.timeout_minutes == 20


# ---------------------------------------------------------------------------
# wire_runtime
# ---------------------------------------------------------------------------


class TestWireRuntime:
    """Verify wire_runtime sets module state."""

    def test_wire_runtime_sets_butler_name(self):
        mod = _make_module()
        fake_spawner = MagicMock()
        mod.wire_runtime("birthday-butler", fake_spawner, "/tmp/repo")
        assert mod._butler_name == "birthday-butler"

    def test_wire_runtime_sets_spawner(self):
        mod = _make_module()
        fake_spawner = MagicMock()
        mod.wire_runtime("birthday-butler", fake_spawner, "/tmp/repo")
        assert mod._spawner is fake_spawner

    def test_wire_runtime_sets_repo_root(self):
        mod = _make_module()
        fake_spawner = MagicMock()
        mod.wire_runtime("birthday-butler", fake_spawner, "/some/path")
        assert mod._repo_root == Path("/some/path")


# ---------------------------------------------------------------------------
# on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    """Verify on_startup calls recover_stale_attempts + reap_stale_worktrees."""

    async def test_on_startup_calls_recovery_functions(self):
        mod = _make_module()
        pool = _make_fake_pool()
        fake_db = MagicMock()
        fake_db.pool = pool

        with (
            patch(
                "butlers.modules.self_healing.recover_stale_attempts",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_recover,
            patch(
                "butlers.modules.self_healing.reap_stale_worktrees",
                new_callable=AsyncMock,
            ) as mock_reap,
        ):
            await mod.on_startup(SelfHealingConfig(), fake_db)

        mock_recover.assert_awaited_once()
        mock_reap.assert_awaited_once()

    async def test_on_startup_no_pool_skips_recovery(self):
        mod = _make_module()
        fake_db = MagicMock()
        fake_db.pool = None

        with (
            patch(
                "butlers.modules.self_healing.recover_stale_attempts",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_recover,
            patch(
                "butlers.modules.self_healing.reap_stale_worktrees",
                new_callable=AsyncMock,
            ) as mock_reap,
        ):
            await mod.on_startup(SelfHealingConfig(), fake_db)

        mock_recover.assert_not_awaited()
        mock_reap.assert_not_awaited()

    async def test_on_startup_recovery_failure_is_non_fatal(self):
        mod = _make_module()
        pool = _make_fake_pool()
        fake_db = MagicMock()
        fake_db.pool = pool

        with (
            patch(
                "butlers.modules.self_healing.recover_stale_attempts",
                new_callable=AsyncMock,
                side_effect=RuntimeError("DB down"),
            ),
            patch(
                "butlers.modules.self_healing.reap_stale_worktrees",
                new_callable=AsyncMock,
            ),
        ):
            # Should not raise
            await mod.on_startup(SelfHealingConfig(), fake_db)

    async def test_on_startup_accepts_dict_config(self):
        mod = _make_module()
        fake_db = MagicMock()
        fake_db.pool = None
        await mod.on_startup({"enabled": False, "timeout_minutes": 10}, fake_db)
        assert mod._config.timeout_minutes == 10


# ---------------------------------------------------------------------------
# on_shutdown
# ---------------------------------------------------------------------------


class TestOnShutdown:
    """Verify on_shutdown cancels watchdog tasks."""

    async def test_on_shutdown_cancels_pending_tasks(self):
        mod = _make_module()

        # Add a fake pending task
        async def _never():
            await asyncio.sleep(999)

        task = asyncio.create_task(_never())
        mod._watchdog_tasks.append(task)

        await mod.on_shutdown()

        assert task.cancelled()
        assert mod._watchdog_tasks == []

    async def test_on_shutdown_is_idempotent_when_empty(self):
        mod = _make_module()
        # Should not raise when no tasks
        await mod.on_shutdown()
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# report_error — not configured (no pool/spawner)
# ---------------------------------------------------------------------------


class TestReportErrorNotConfigured:
    """report_error returns 'not_configured' when pool or spawner is absent."""

    async def test_no_pool_returns_not_configured(self):
        mod = _make_module()
        mod._pool = None
        mod._spawner = None

        result = await mod._handle_report_error(
            error_type="builtins.ValueError",
            error_message="test error",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "not_configured"
        assert "fingerprint" in result

    async def test_no_spawner_returns_not_configured(self):
        mod = _make_module()
        mod._pool = _make_fake_pool()
        mod._spawner = None  # No spawner

        result = await mod._handle_report_error(
            error_type="builtins.ValueError",
            error_message="test error",
            traceback_str=None,
            call_site=None,
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "not_configured"


# ---------------------------------------------------------------------------
# report_error — already_investigating fast path
# ---------------------------------------------------------------------------


class TestReportErrorAlreadyInvestigating:
    """report_error returns already_investigating when active attempt exists."""

    async def test_fast_path_already_investigating(self):
        mod = _make_module()
        existing_id = uuid.uuid4()
        pool = _make_fake_pool()

        # Simulate get_active_attempt returning an existing row
        existing_attempt = {"id": existing_id, "status": "investigating", "fingerprint": "a" * 64}

        with patch(
            "butlers.modules.self_healing.get_active_attempt",
            new_callable=AsyncMock,
            return_value=existing_attempt,
        ):
            mod._pool = pool
            mod._spawner = MagicMock()

            result = await mod._handle_report_error(
                error_type="builtins.ValueError",
                error_message="some error",
                traceback_str=None,
                call_site=None,
                context=None,
                tool_name=None,
                severity_hint=None,
            )

        assert result["accepted"] is False
        assert result["reason"] == "already_investigating"
        assert result["attempt_id"] == str(existing_id)
        assert "already under investigation" in result["message"]


# ---------------------------------------------------------------------------
# report_error — dispatch acceptance
# ---------------------------------------------------------------------------


class TestReportErrorAccepted:
    """report_error returns accepted=True when dispatch succeeds."""

    async def test_accepted_dispatch(self):
        mod = _make_module()
        attempt_id = uuid.uuid4()
        pool = _make_fake_pool()
        fake_spawner = MagicMock()

        mod._pool = pool
        mod._spawner = fake_spawner
        mod._butler_name = "test-butler"
        mod._repo_root = Path("/tmp/repo")

        from butlers.core.healing.dispatch import DispatchResult

        dispatch_result = DispatchResult(
            accepted=True,
            fingerprint="b" * 64,
            reason="dispatched",
            attempt_id=attempt_id,
        )

        with (
            patch(
                "butlers.modules.self_healing.get_active_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.modules.self_healing.dispatch_healing",
                new_callable=AsyncMock,
                return_value=dispatch_result,
            ) as mock_dispatch,
        ):
            result = await mod._handle_report_error(
                error_type="asyncpg.exceptions.UndefinedTableError",
                error_message="relation does not exist",
                traceback_str="Traceback...",
                call_site="src/butlers/modules/memory/tools.py:store_fact",
                context="I was storing a fact",
                tool_name="memory_store_fact",
                severity_hint="high",
            )

        assert result["accepted"] is True
        assert result["fingerprint"] == "b" * 64
        assert result["attempt_id"] == str(attempt_id)
        assert result["message"] == "Healing agent dispatched"
        mock_dispatch.assert_awaited_once()

    async def test_dispatch_passes_context(self):
        mod = _make_module()
        pool = _make_fake_pool()
        fake_spawner = MagicMock()
        mod._pool = pool
        mod._spawner = fake_spawner
        mod._butler_name = "test-butler"

        from butlers.core.healing.dispatch import DispatchResult

        dispatch_result = DispatchResult(
            accepted=True, fingerprint="c" * 64, reason="dispatched", attempt_id=uuid.uuid4()
        )

        with (
            patch(
                "butlers.modules.self_healing.get_active_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.modules.self_healing.dispatch_healing",
                new_callable=AsyncMock,
                return_value=dispatch_result,
            ) as mock_dispatch,
        ):
            await mod._handle_report_error(
                error_type="builtins.ValueError",
                error_message="bad value",
                traceback_str=None,
                call_site=None,
                context="I was doing something important",
                tool_name=None,
                severity_hint=None,
            )

        # Verify agent_context was passed to dispatch
        call_kwargs = mock_dispatch.call_args.kwargs
        assert call_kwargs["agent_context"] == "I was doing something important"


# ---------------------------------------------------------------------------
# report_error — gate rejections
# ---------------------------------------------------------------------------


class TestReportErrorRejected:
    """report_error returns accepted=False with reason when dispatch gate rejects."""

    async def _call_rejected(
        self, mod: SelfHealingModule, reason: str
    ) -> dict:
        pool = _make_fake_pool()
        fake_spawner = MagicMock()
        mod._pool = pool
        mod._spawner = fake_spawner
        mod._butler_name = "test-butler"

        from butlers.core.healing.dispatch import DispatchResult

        dispatch_result = DispatchResult(
            accepted=False, fingerprint="d" * 64, reason=reason, attempt_id=None
        )

        with (
            patch(
                "butlers.modules.self_healing.get_active_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.modules.self_healing.dispatch_healing",
                new_callable=AsyncMock,
                return_value=dispatch_result,
            ),
        ):
            return await mod._handle_report_error(
                error_type="builtins.ValueError",
                error_message="err",
                traceback_str=None,
                call_site=None,
                context=None,
                tool_name=None,
                severity_hint=None,
            )

    async def test_cooldown_rejection(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "cooldown")
        assert result["accepted"] is False
        assert result["reason"] == "cooldown"
        assert "Cooldown" in result["message"]

    async def test_concurrency_cap_rejection(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "concurrency_cap")
        assert result["accepted"] is False
        assert result["reason"] == "concurrency_cap"

    async def test_circuit_breaker_rejection(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "circuit_breaker")
        assert result["accepted"] is False
        assert result["reason"] == "circuit_breaker"

    async def test_no_model_rejection(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "no_model")
        assert result["accepted"] is False
        assert result["reason"] == "no_model"

    async def test_severity_below_threshold_rejection(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "severity_below_threshold")
        assert result["accepted"] is False
        assert result["reason"] == "severity_below_threshold"

    async def test_unknown_reason_still_rejected_gracefully(self):
        mod = _make_module()
        result = await self._call_rejected(mod, "some_future_gate")
        assert result["accepted"] is False
        assert result["reason"] == "some_future_gate"
        assert "some_future_gate" in result["message"]


# ---------------------------------------------------------------------------
# get_healing_status
# ---------------------------------------------------------------------------


class TestGetHealingStatus:
    """Verify get_healing_status handler responses."""

    async def test_no_pool_returns_empty(self):
        mod = _make_module()
        mod._pool = None

        result = await mod._handle_get_healing_status(fingerprint=None)

        assert result["attempts"] == []
        assert "not configured" in result["message"].lower()

    async def test_no_attempts_returns_empty(self):
        mod = _make_module()
        pool = _make_fake_pool()
        mod._pool = pool
        mod._butler_name = "birthday-butler"

        with patch(
            "butlers.modules.self_healing.list_attempts",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await mod._handle_get_healing_status(fingerprint=None)

        assert result["attempts"] == []
        assert "No healing attempts found" in result["message"]

    async def test_recent_attempts_for_butler(self):
        mod = _make_module()
        pool = _make_fake_pool()
        mod._pool = pool
        mod._butler_name = "birthday-butler"

        attempt_id = uuid.uuid4()
        fake_attempt = {
            "id": attempt_id,
            "butler_name": "birthday-butler",
            "status": "pr_open",
            "fingerprint": "e" * 64,
            "exception_type": "builtins.ValueError",
            "call_site": "src/butlers/modules/foo.py:bar",
            "severity": 2,
            "session_ids": [],
            "created_at": None,
            "updated_at": None,
            "closed_at": None,
            "branch_name": None,
            "worktree_path": None,
            "pr_url": "https://github.com/owner/repo/pull/42",
            "pr_number": 42,
            "healing_session_id": None,
            "sanitized_msg": "some error",
            "error_detail": None,
        }

        with patch(
            "butlers.modules.self_healing.list_attempts",
            new_callable=AsyncMock,
            return_value=[fake_attempt],
        ):
            result = await mod._handle_get_healing_status(fingerprint=None)

        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["status"] == "pr_open"

    async def test_query_by_fingerprint_found(self):
        mod = _make_module()
        pool = _make_fake_pool()
        mod._pool = pool

        attempt_id = uuid.uuid4()
        fake_attempt = {
            "id": attempt_id,
            "butler_name": "birthday-butler",
            "status": "investigating",
            "fingerprint": "f" * 64,
            "exception_type": "asyncpg.exceptions.UndefinedTableError",
            "call_site": "src/butlers/core/something.py:init",
            "severity": 0,
            "session_ids": [],
            "created_at": None,
            "updated_at": None,
            "closed_at": None,
            "branch_name": "hotfix/birthday-butler/ffffff-123456",
            "worktree_path": None,
            "pr_url": None,
            "pr_number": None,
            "healing_session_id": None,
            "sanitized_msg": None,
            "error_detail": None,
        }

        with (
            patch(
                "butlers.modules.self_healing.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.modules.self_healing.get_active_attempt",
                new_callable=AsyncMock,
                return_value=fake_attempt,
            ),
        ):
            result = await mod._handle_get_healing_status(fingerprint="f" * 64)

        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["status"] == "investigating"

    async def test_query_by_fingerprint_not_found(self):
        mod = _make_module()
        pool = _make_fake_pool()
        mod._pool = pool

        with (
            patch(
                "butlers.modules.self_healing.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.modules.self_healing.get_active_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await mod._handle_get_healing_status(fingerprint="a" * 64)

        assert result["attempts"] == []
        assert "aaaaaaaaaaaa" in result["message"]  # fingerprint prefix


# ---------------------------------------------------------------------------
# _serialize_attempt
# ---------------------------------------------------------------------------


class TestSerializeAttempt:
    """Verify _serialize_attempt converts UUID and datetime fields."""

    def test_uuid_converted_to_str(self):
        attempt_id = uuid.uuid4()
        row = {"id": attempt_id, "status": "investigating"}
        result = _serialize_attempt(row)
        assert isinstance(result["id"], str)
        assert result["id"] == str(attempt_id)

    def test_datetime_converted_to_isoformat(self):
        from datetime import datetime

        now = datetime.now(tz=UTC)
        row = {"created_at": now, "status": "failed"}
        result = _serialize_attempt(row)
        assert isinstance(result["created_at"], str)
        assert "T" in result["created_at"]

    def test_session_ids_list_converted(self):
        sid = uuid.uuid4()
        row = {"session_ids": [sid], "status": "investigating"}
        result = _serialize_attempt(row)
        assert result["session_ids"] == [str(sid)]

    def test_plain_fields_unchanged(self):
        row = {"status": "pr_open", "fingerprint": "abc123", "severity": 2}
        result = _serialize_attempt(row)
        assert result["status"] == "pr_open"
        assert result["fingerprint"] == "abc123"
        assert result["severity"] == 2

    def test_none_values_preserved(self):
        row = {"pr_url": None, "pr_number": None}
        result = _serialize_attempt(row)
        assert result["pr_url"] is None
        assert result["pr_number"] is None
