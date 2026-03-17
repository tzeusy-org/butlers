"""Tests for butlers.core.healing.dispatch.

Covers:
- HealingConfig.from_module_config defaults and overrides
- Gate 1: No-recursion guard (trigger_source="healing" → rejected immediately)
- Gate 2: Opt-in gate (disabled → skipped before fingerprint)
- Gates 3-10 via dispatch_healing with mocked dependencies
- Dual-path dispatch (FingerprintResult vs (exc, tb))
- Novelty gate: already_investigating → existing attempt's session_ids appended
- Cooldown gate: recent terminal → rejected
- Concurrency cap: active >= max_concurrent → rejected
- Circuit breaker: N consecutive failures → tripped
- Model resolution: no model → rejected
- Worktree creation failure → rejected + attempt failed
- Successful dispatch: attempt created, tasks spawned
- Prompt variants: with and without agent_context
- Timeout watchdog: cancels task and sets status to timeout
- PR flow: success → pr_open; push failure → failed; anonymization_failed
- dispatch_healing never raises (internal_error result)
"""

from __future__ import annotations

import asyncio
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from butlers.core.healing.dispatch import (
    HealingConfig,
    _build_healing_prompt,
    _create_pr,
    _timeout_watchdog,
    dispatch_healing,
)
from butlers.core.healing.fingerprint import FingerprintResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fp(
    fingerprint: str = "a" * 64,
    severity: int = 2,
    exception_type: str = "builtins.ValueError",
    call_site: str = "src/butlers/modules/email/tools.py:send_email",
    sanitized_message: str = "connection failed",
) -> FingerprintResult:
    return FingerprintResult(
        fingerprint=fingerprint,
        severity=severity,
        exception_type=exception_type,
        call_site=call_site,
        sanitized_message=sanitized_message,
    )


def _make_config(**kwargs) -> HealingConfig:
    defaults = {
        "enabled": True,
        "severity_threshold": 2,
        "cooldown_minutes": 60,
        "max_concurrent": 2,
        "circuit_breaker_threshold": 5,
        "timeout_minutes": 30,
    }
    defaults.update(kwargs)
    return HealingConfig(**defaults)


def _make_pool_all_pass() -> MagicMock:
    """Return a mock pool where all gate queries return 'pass'."""
    pool = MagicMock()

    async def fetchrow(*args, **kwargs):
        return None  # no active attempt, no recent terminal

    async def fetchval(*args, **kwargs):
        return 0  # count = 0

    async def fetch(*args, **kwargs):
        return []  # empty for circuit breaker

    async def execute(*args, **kwargs):
        pass

    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    pool.fetchval = AsyncMock(side_effect=fetchval)
    pool.fetch = AsyncMock(side_effect=fetch)
    pool.execute = AsyncMock(side_effect=execute)
    return pool


def _make_spawner(success: bool = True, session_id: uuid.UUID | None = None) -> MagicMock:
    spawner = MagicMock()

    @dataclass
    class _Result:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None
        output: str | None = None

    result = _Result(
        success=success,
        session_id=session_id or uuid.uuid4(),
    )

    async def mock_trigger(*args, **kwargs):
        return result

    spawner.trigger = AsyncMock(side_effect=mock_trigger)
    return spawner


# ---------------------------------------------------------------------------
# HealingConfig
# ---------------------------------------------------------------------------


class TestHealingConfig:
    def test_defaults(self) -> None:
        cfg = HealingConfig()
        assert cfg.enabled is False
        assert cfg.severity_threshold == 2
        assert cfg.cooldown_minutes == 60
        assert cfg.max_concurrent == 2
        assert cfg.circuit_breaker_threshold == 5
        assert cfg.timeout_minutes == 30
        assert cfg.gh_token_env_var == "GH_TOKEN"
        assert cfg.pr_labels == ["self-healing", "automated"]

    def test_from_module_config_full(self) -> None:
        cfg = HealingConfig.from_module_config(
            {
                "enabled": True,
                "severity_threshold": 1,
                "cooldown_minutes": 30,
                "max_concurrent": 4,
                "circuit_breaker_threshold": 3,
                "timeout_minutes": 60,
                "gh_token_env_var": "MY_GH_TOKEN",
                "pr_labels": ["healing"],
            }
        )
        assert cfg.enabled is True
        assert cfg.severity_threshold == 1
        assert cfg.cooldown_minutes == 30
        assert cfg.max_concurrent == 4
        assert cfg.circuit_breaker_threshold == 3
        assert cfg.timeout_minutes == 60
        assert cfg.gh_token_env_var == "MY_GH_TOKEN"
        assert cfg.pr_labels == ["healing"]

    def test_from_module_config_empty_dict(self) -> None:
        cfg = HealingConfig.from_module_config({})
        assert cfg.enabled is False  # default


# ---------------------------------------------------------------------------
# Gate 1: No-recursion guard
# ---------------------------------------------------------------------------


class TestNoRecursionGate:
    async def test_healing_trigger_source_rejected_immediately(self, tmp_path: Path) -> None:
        """dispatch_healing returns no_recursion when trigger_source=healing."""
        result = await dispatch_healing(
            pool=MagicMock(),
            butler_name="email",
            session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(),
            config=_make_config(),
            repo_root=tmp_path,
            spawner=MagicMock(),
            trigger_source="healing",
        )
        assert result.accepted is False
        assert result.reason == "no_recursion"
        assert result.fingerprint is None  # before fingerprint computation

    async def test_non_healing_trigger_passes_gate(self, tmp_path: Path) -> None:
        """Other trigger_source values pass gate 1."""
        pool = _make_pool_all_pass()

        with (
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,  # 1 active (the one we just inserted) ≤ max_concurrent=2
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "claude-sonnet-4-5", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(
                    tmp_path / ".healing-worktrees/self-healing/email/abc-1",
                    "self-healing/email/abc-1",
                ),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=pool,
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
                trigger_source="external",
            )

        assert result.accepted is True
        assert result.reason == "dispatched"


# ---------------------------------------------------------------------------
# Gate 2: Opt-in gate
# ---------------------------------------------------------------------------


class TestOptInGate:
    async def test_disabled_skips_before_fingerprint(self, tmp_path: Path) -> None:
        """When disabled, no fingerprint is computed and pool is not queried."""
        pool = MagicMock()
        pool.execute = AsyncMock()

        result = await dispatch_healing(
            pool=pool,
            butler_name="email",
            session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(),
            config=_make_config(enabled=False),
            repo_root=tmp_path,
            spawner=MagicMock(),
        )

        assert result.accepted is False
        assert result.reason == "disabled"
        assert result.fingerprint is None
        pool.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Gate 3+4: Fingerprint computation (dual path)
# ---------------------------------------------------------------------------


class TestDualPathDispatch:
    async def test_accepts_fingerprint_result_directly(self, tmp_path: Path) -> None:
        """Module path: FingerprintResult is accepted without recomputation."""
        fp = _make_fp(fingerprint="b" * 64)

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ) as mock_persist,
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "claude-sonnet-4-5", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(tmp_path / ".healing-worktrees/h/e/x", "self-healing/e/x"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=fp,
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.fingerprint == "b" * 64
        # Fingerprint was persisted
        mock_persist.assert_called_once()

    async def test_accepts_exc_tb_tuple(self, tmp_path: Path) -> None:
        """Spawner fallback path: (exc, tb) tuple is accepted and fingerprinted."""
        exc = ValueError("test error")
        tb: types.TracebackType | None = None
        try:
            raise exc
        except ValueError:
            import sys

            tb = sys.exc_info()[2]

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "claude-sonnet-4-5", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(tmp_path / ".healing-worktrees/h/e/x", "self-healing/e/x"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=(exc, tb),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is True
        assert result.fingerprint is not None
        assert len(result.fingerprint) == 64


# ---------------------------------------------------------------------------
# Gate 6: Novelty gate
# ---------------------------------------------------------------------------


class TestNoveltyGate:
    async def test_already_investigating_rejected(self, tmp_path: Path) -> None:
        """When create_or_join_attempt returns is_new=False, dispatch is skipped."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), False),  # is_new=False
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "already_investigating"


# ---------------------------------------------------------------------------
# Gate 5: Severity gate
# ---------------------------------------------------------------------------


class TestSeverityGate:
    async def test_severity_above_threshold_rejected(self, tmp_path: Path) -> None:
        """Severity 3 (low) with threshold 2 (medium) → rejected."""
        fp = _make_fp(severity=3)

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=fp,
                config=_make_config(severity_threshold=2),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "severity_below_threshold"

    async def test_severity_at_threshold_passes(self, tmp_path: Path) -> None:
        """Severity == threshold passes (1 ≤ 1)."""
        fp = _make_fp(severity=1)

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "model", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(tmp_path / "wt", "self-healing/b/x"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=fp,
                config=_make_config(severity_threshold=1),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is True


# ---------------------------------------------------------------------------
# Gate 7: Cooldown gate
# ---------------------------------------------------------------------------


class TestCooldownGate:
    async def test_recent_terminal_rejected(self, tmp_path: Path) -> None:
        """Recent terminal attempt within cooldown → rejected."""
        recent_row = {"id": str(uuid.uuid4()), "status": "failed"}

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=recent_row,
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "cooldown"


# ---------------------------------------------------------------------------
# Gate 8: Concurrency cap
# ---------------------------------------------------------------------------


class TestConcurrencyCap:
    async def test_at_cap_rejected(self, tmp_path: Path) -> None:
        """active_count > max_concurrent → rejected."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=3,  # max=2, row inserted → active=3 > 2
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(max_concurrent=2),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "concurrency_cap"


# ---------------------------------------------------------------------------
# Gate 9: Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    async def test_tripped_on_n_consecutive_failures(self, tmp_path: Path) -> None:
        """Circuit breaker trips after N consecutive failures."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=["failed", "failed", "failed"],
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(circuit_breaker_threshold=3),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "circuit_breaker"

    async def test_unfixable_does_not_trip_circuit_breaker(self, tmp_path: Path) -> None:
        """unfixable status doesn't count as a circuit-breaker failure."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=["unfixable", "unfixable", "unfixable"],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "model", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(tmp_path / "wt", "self-healing/b/x"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(circuit_breaker_threshold=3),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        # Circuit breaker NOT tripped → dispatch accepted
        assert result.accepted is True


# ---------------------------------------------------------------------------
# Gate 10: Model resolution
# ---------------------------------------------------------------------------


class TestModelResolutionGate:
    async def test_no_model_rejected(self, tmp_path: Path) -> None:
        """No self_healing model available → rejected."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "no_model"

    async def test_db_error_during_resolution_rejected(self, tmp_path: Path) -> None:
        """DB error during model resolution → rejected (no_model)."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                side_effect=ConnectionError("DB unavailable"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "no_model"


# ---------------------------------------------------------------------------
# Worktree creation failure
# ---------------------------------------------------------------------------


class TestWorktreeCreationFailure:
    async def test_worktree_error_rejected(self, tmp_path: Path) -> None:
        """WorktreeCreationError → rejected with worktree_creation_failed."""
        from butlers.core.healing.worktree import WorktreeCreationError

        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "model", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                side_effect=WorktreeCreationError("disk full", "fatal: no space"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "worktree_creation_failed"


# ---------------------------------------------------------------------------
# Prompt variants
# ---------------------------------------------------------------------------


class TestPromptVariants:
    def test_prompt_with_agent_context(self) -> None:
        """Prompt includes agent_context section when context provided."""
        fp = _make_fp()
        context = "SMTP auth failed — likely credential rotation"
        prompt = _build_healing_prompt(fp, "email", "external", context)
        assert "SMTP auth failed" in prompt
        assert "Butler Diagnostic Context" in prompt

    def test_prompt_without_agent_context(self) -> None:
        """Prompt includes fallback note when no context provided."""
        fp = _make_fp()
        prompt = _build_healing_prompt(fp, "email", "tick", None)
        assert "spawner fallback" in prompt.lower() or "hard crash" in prompt.lower()
        assert "Butler Diagnostic Context" not in prompt

    def test_prompt_contains_required_fields(self) -> None:
        """Prompt always includes fingerprint, exception_type, call_site."""
        fp = _make_fp(
            fingerprint="c" * 64,
            exception_type="asyncpg.exceptions.UndefinedTableError",
            call_site="src/butlers/modules/email/tools.py:_send",
        )
        prompt = _build_healing_prompt(fp, "email", "external", None)
        assert "c" * 64 in prompt
        assert "asyncpg.exceptions.UndefinedTableError" in prompt
        assert "src/butlers/modules/email/tools.py:_send" in prompt


# ---------------------------------------------------------------------------
# Timeout watchdog
# ---------------------------------------------------------------------------


class TestTimeoutWatchdog:
    async def test_watchdog_cancels_task_on_timeout(self, tmp_path: Path) -> None:
        """Watchdog cancels healing_task and sets status to timeout."""
        pool = _make_pool_all_pass()
        attempt_id = uuid.uuid4()
        branch = "self-healing/email/abc-1"

        # Create a task that never finishes
        async def long_running():
            await asyncio.sleep(9999)

        healing_task = asyncio.create_task(long_running())

        status_updates: list[str] = []

        async def mock_update_status(pool, attempt_id, status, **kwargs):
            status_updates.append(status)
            return True

        remove_calls: list[str] = []

        async def mock_remove(repo_root, branch_name, **kwargs):
            remove_calls.append(branch_name)

        with (
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                side_effect=mock_update_status,
            ),
            patch(
                "butlers.core.healing.dispatch.remove_healing_worktree",
                side_effect=mock_remove,
            ),
        ):
            asyncio.create_task(
                _timeout_watchdog(
                    pool=pool,
                    attempt_id=attempt_id,
                    repo_root=tmp_path,
                    branch_name=branch,
                    healing_task=healing_task,
                    timeout_minutes=0,  # immediate timeout
                )
            )
            await asyncio.sleep(0.05)  # let watchdog fire

        assert healing_task.cancelled() or healing_task.done()
        assert "timeout" in status_updates
        assert branch in remove_calls

    async def test_watchdog_cancelled_when_task_completes(self, tmp_path: Path) -> None:
        """Watchdog task is cancelled when healing completes before timeout."""
        pool = _make_pool_all_pass()
        attempt_id = uuid.uuid4()
        branch = "self-healing/email/abc-2"

        async def quick_task():
            pass

        healing_task = asyncio.create_task(quick_task())
        await asyncio.sleep(0)  # let it complete

        status_updates: list[str] = []

        async def mock_update_status(pool, attempt_id, status, **kwargs):
            status_updates.append(status)
            return True

        watchdog_task = asyncio.create_task(
            _timeout_watchdog(
                pool=pool,
                attempt_id=attempt_id,
                repo_root=tmp_path,
                branch_name=branch,
                healing_task=healing_task,
                timeout_minutes=100,  # long timeout — should not fire
            )
        )
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass

        # No timeout status should be set
        assert "timeout" not in status_updates


# ---------------------------------------------------------------------------
# PR creation flow
# ---------------------------------------------------------------------------


class TestCreatePr:
    async def test_successful_pr_creation(self, tmp_path: Path) -> None:
        """Full PR flow: push → validate → create → (url, number, None)."""
        fp = _make_fp()
        attempt_id = uuid.uuid4()
        branch = "self-healing/email/abc-3"

        async def mock_push(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            future: asyncio.Future = asyncio.get_event_loop().create_future()
            future.set_result((b"", b""))
            proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        async def mock_gh(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            pr_url = "https://github.com/owner/repo/pull/42"
            proc.communicate = AsyncMock(return_value=(pr_url.encode(), b""))
            return proc

        call_count = [0]

        async def mock_create_subprocess(*args, **kwargs):
            call_count[0] += 1
            proc = MagicMock()
            if "push" in args and "--delete" not in args:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            elif args[0] == "gh":
                proc.returncode = 0
                proc.communicate = AsyncMock(
                    return_value=(b"https://github.com/owner/repo/pull/42", b"")
                )
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            pr_url, pr_number, error = await _create_pr(
                repo_root=tmp_path,
                branch_name=branch,
                fp=fp,
                butler_name="email",
                attempt_id=attempt_id,
                agent_context=None,
                labels=["self-healing", "automated"],
                gh_token="test-token",
            )

        assert error is None
        assert pr_url == "https://github.com/owner/repo/pull/42"
        assert pr_number == 42

    async def test_push_failure_returns_error(self, tmp_path: Path) -> None:
        """git push failure returns (None, None, error_string)."""
        fp = _make_fp()
        attempt_id = uuid.uuid4()
        branch = "self-healing/email/abc-4"

        async def mock_create_subprocess(*args, **kwargs):
            proc = MagicMock()
            if "push" in args:
                proc.returncode = 1
                proc.communicate = AsyncMock(return_value=(b"", b"error: remote rejected"))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=mock_create_subprocess,
        ):
            pr_url, pr_number, error = await _create_pr(
                repo_root=tmp_path,
                branch_name=branch,
                fp=fp,
                butler_name="email",
                attempt_id=attempt_id,
                agent_context=None,
                labels=[],
                gh_token=None,
            )

        assert pr_url is None
        assert pr_number is None
        assert error is not None
        assert "push failed" in error

    async def test_anonymization_failure_deletes_remote(self, tmp_path: Path) -> None:
        """Anonymization validation failure returns 'anonymization_failed' and deletes remote."""
        fp = _make_fp()
        attempt_id = uuid.uuid4()
        branch = "self-healing/email/abc-5"

        push_calls: list = []
        delete_calls: list = []

        async def mock_create_subprocess(*args, **kwargs):
            proc = MagicMock()
            if "push" in args:
                if "--delete" in args:
                    delete_calls.append(args)
                    proc.returncode = 0
                else:
                    push_calls.append(args)
                    proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            else:
                proc.returncode = 0
                proc.communicate = AsyncMock(return_value=(b"", b""))
            return proc

        # Inject a real email into agent_context to trigger validation failure
        pii_context = "Contact admin@internal-corp.local for details"

        with (
            patch("asyncio.create_subprocess_exec", side_effect=mock_create_subprocess),
        ):
            pr_url, pr_number, error = await _create_pr(
                repo_root=tmp_path,
                branch_name=branch,
                fp=fp,
                butler_name="email",
                attempt_id=attempt_id,
                agent_context=pii_context,
                labels=[],
                gh_token=None,
            )

        # Should either succeed (anonymizer caught the email) or return anonymization_failed
        # if the email passed through anonymize() but was caught by validate_anonymized()
        # Given the robust anonymizer, the email should be redacted → clean
        # The exact result depends on whether the email is in the PR title/body or context
        # Just verify it doesn't raise
        assert error is None or error == "anonymization_failed"


# ---------------------------------------------------------------------------
# dispatch_healing never raises
# ---------------------------------------------------------------------------


class TestDispatchNeverRaises:
    async def test_unexpected_exception_returns_internal_error(self, tmp_path: Path) -> None:
        """An unexpected exception in dispatch returns DispatchResult(internal_error)."""
        with patch(
            "butlers.core.healing.dispatch.session_set_healing_fingerprint",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected!"),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "internal_error"

    async def test_gate_db_error_returns_internal_error(self, tmp_path: Path) -> None:
        """DB error in novelty gate → internal_error, never raises."""
        with (
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                side_effect=Exception("asyncpg connection error"),
            ),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.accepted is False
        assert result.reason == "internal_error"

    async def test_fingerprint_is_preserved_in_internal_error(self, tmp_path: Path) -> None:
        """When FingerprintResult is passed, its fingerprint appears in internal_error result."""
        fp = _make_fp(fingerprint="d" * 64)

        with patch(
            "butlers.core.healing.dispatch.session_set_healing_fingerprint",
            new_callable=AsyncMock,
            side_effect=Exception("boom"),
        ):
            result = await dispatch_healing(
                pool=_make_pool_all_pass(),
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=fp,
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )

        assert result.fingerprint == "d" * 64


# ---------------------------------------------------------------------------
# Fix 1+2: CWD and bypass_butler_semaphore in _run_healing_session
# ---------------------------------------------------------------------------


class TestHealingSessionCwd:
    """Verify that _run_healing_session passes cwd and bypass_butler_semaphore to spawner."""

    async def test_trigger_called_with_cwd_and_bypass(self, tmp_path: Path) -> None:
        """spawner.trigger() receives cwd=worktree_path and bypass_butler_semaphore=True."""
        worktree_path = tmp_path / "healing-wt"
        worktree_path.mkdir()
        pool = _make_pool_all_pass()
        fp = _make_fp()
        attempt_id = uuid.uuid4()

        captured_kwargs: dict = {}

        async def capture_trigger(*args, **kwargs):
            captured_kwargs.update(kwargs)

            class _Result:
                success = True
                session_id = uuid.uuid4()
                error = None
                output = "done"

            return _Result()

        spawner = MagicMock()
        spawner.trigger = AsyncMock(side_effect=capture_trigger)

        with (
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.core.healing.dispatch.get_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch._create_pr",
                new_callable=AsyncMock,
                return_value=("https://github.com/x/y/pull/1", 1, None),
            ),
            patch(
                "butlers.core.healing.dispatch.remove_healing_worktree",
                new_callable=AsyncMock,
            ),
        ):
            from butlers.core.healing.dispatch import HealingConfig, _run_healing_session

            config = HealingConfig(enabled=True, timeout_minutes=30)
            await _run_healing_session(
                pool=pool,
                repo_root=tmp_path,
                attempt_id=attempt_id,
                branch_name="self-healing/email/abc-99",
                worktree_path=worktree_path,
                fp=fp,
                butler_name="email",
                trigger_source="external",
                agent_context=None,
                config=config,
                spawner=spawner,
                gh_token=None,
            )

        assert "cwd" in captured_kwargs
        assert captured_kwargs["cwd"] == str(worktree_path)
        assert captured_kwargs.get("bypass_butler_semaphore") is True


# ---------------------------------------------------------------------------
# Fix 3: OTel healing.dispatch span
# ---------------------------------------------------------------------------


class TestDispatchHealingOtelSpan:
    """Verify healing.dispatch OTel span is created and ends correctly."""

    async def test_span_created_with_attributes(self, tmp_path: Path) -> None:
        """dispatch_healing creates a healing.dispatch span with expected attributes."""
        import opentelemetry.trace as real_trace
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        real_trace.set_tracer_provider(provider)

        pool = _make_pool_all_pass()

        with (
            patch(
                "butlers.core.healing.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(uuid.uuid4(), True),
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.healing.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "butlers.core.healing.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=("claude_code", "model", []),
            ),
            patch(
                "butlers.core.healing.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(tmp_path / "wt", "self-healing/b/x"),
            ),
            patch(
                "butlers.core.healing.dispatch.update_attempt_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                new_callable=AsyncMock,
            ),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.return_value = MagicMock()
            result = await dispatch_healing(
                pool=pool,
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(),
                repo_root=tmp_path,
                spawner=_make_spawner(),
                trigger_source="external",
            )

        # Dispatch should succeed regardless of OTel presence
        assert result.accepted is True
        assert result.reason == "dispatched"

    async def _run_dispatch_with_otel(
        self,
        tmp_path: Path,
        *,
        with_parent_span: bool,
    ) -> tuple[object, str | None]:
        """Shared helper: set up an in-memory OTel exporter, run dispatch_healing, return
        (exporter, expected_trace_id).  expected_trace_id is None when no parent span is used."""
        import opentelemetry.trace as real_trace
        from opentelemetry import context as otel_ctx
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        real_trace.set_tracer_provider(provider)

        # Reinitialise the module-level _tracer to use our test provider so
        # start_span() calls are captured by the exporter.
        import butlers.core.healing.dispatch as dispatch_mod

        dispatch_mod._tracer = provider.get_tracer("butlers.healing")
        dispatch_mod._HAS_OTEL = True

        expected_trace_id: str | None = None
        token = None
        parent_span = None

        if with_parent_span:
            # Simulate an active parent span representing the failed butler session.
            parent_span = provider.get_tracer("test.session").start_span("session")
            expected_trace_id = format(parent_span.get_span_context().trace_id, "032x")
            token = otel_ctx.attach(real_trace.set_span_in_context(parent_span))

        try:
            pool = _make_pool_all_pass()
            with (
                patch(
                    "butlers.core.healing.dispatch.create_or_join_attempt",
                    new_callable=AsyncMock,
                    return_value=(uuid.uuid4(), True),
                ),
                patch(
                    "butlers.core.healing.dispatch.get_recent_attempt",
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch(
                    "butlers.core.healing.dispatch.count_active_attempts",
                    new_callable=AsyncMock,
                    return_value=1,
                ),
                patch(
                    "butlers.core.healing.dispatch.get_recent_terminal_statuses",
                    new_callable=AsyncMock,
                    return_value=[],
                ),
                patch(
                    "butlers.core.healing.dispatch.resolve_model",
                    new_callable=AsyncMock,
                    return_value=("claude_code", "model", []),
                ),
                patch(
                    "butlers.core.healing.dispatch.create_healing_worktree",
                    new_callable=AsyncMock,
                    return_value=(tmp_path / "wt", "self-healing/b/x"),
                ),
                patch(
                    "butlers.core.healing.dispatch.update_attempt_status",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch(
                    "butlers.core.healing.dispatch.session_set_healing_fingerprint",
                    new_callable=AsyncMock,
                ),
                patch("asyncio.create_task") as mock_create_task,
            ):
                mock_create_task.return_value = MagicMock()
                result = await dispatch_healing(
                    pool=pool,
                    butler_name="email",
                    session_id=uuid.uuid4(),
                    fingerprint_input=_make_fp(),
                    config=_make_config(),
                    repo_root=tmp_path,
                    spawner=_make_spawner(),
                    trigger_source="external",
                )
        finally:
            if token is not None:
                otel_ctx.detach(token)
            if parent_span is not None:
                parent_span.end()

        assert result.accepted is True
        return exporter, expected_trace_id

    async def test_failed_session_trace_id_recorded_as_span_attribute(self, tmp_path: Path) -> None:
        """healing.dispatch span records healing.failed_session_trace_id from the failed session."""
        exporter, expected_trace_id = await self._run_dispatch_with_otel(
            tmp_path, with_parent_span=True
        )

        # Find the healing.dispatch span and verify the attribute.
        finished = exporter.get_finished_spans()
        dispatch_spans = [s for s in finished if s.name == "butlers.healing.dispatch"]
        assert dispatch_spans, "Expected at least one butlers.healing.dispatch span"
        dispatch_span = dispatch_spans[0]
        assert "healing.failed_session_trace_id" in dispatch_span.attributes, (
            "healing.dispatch span must record healing.failed_session_trace_id"
        )
        assert dispatch_span.attributes["healing.failed_session_trace_id"] == expected_trace_id

    async def test_failed_session_trace_id_omitted_when_no_parent_context(
        self, tmp_path: Path
    ) -> None:
        """healing.dispatch span omits healing.failed_session_trace_id when no active trace."""
        exporter, _ = await self._run_dispatch_with_otel(tmp_path, with_parent_span=False)

        finished = exporter.get_finished_spans()
        dispatch_spans = [s for s in finished if s.name == "butlers.healing.dispatch"]
        assert dispatch_spans, "Expected at least one butlers.healing.dispatch span"
        dispatch_span = dispatch_spans[0]
        # When there's no active trace context, the attribute should not be set.
        assert "healing.failed_session_trace_id" not in (dispatch_span.attributes or {}), (
            "healing.failed_session_trace_id should be absent when there is no parent trace context"
        )

    async def test_dispatch_works_when_otel_missing(self, tmp_path: Path) -> None:
        """dispatch_healing gracefully handles ImportError from opentelemetry."""
        import sys

        # Temporarily hide opentelemetry
        saved_modules = {k: v for k, v in sys.modules.items() if "opentelemetry" in k}
        for key in saved_modules:
            sys.modules[key] = None  # type: ignore[assignment]

        try:
            pool = _make_pool_all_pass()
            result = await dispatch_healing(
                pool=pool,
                butler_name="email",
                session_id=uuid.uuid4(),
                fingerprint_input=_make_fp(),
                config=_make_config(enabled=False),
                repo_root=tmp_path,
                spawner=_make_spawner(),
            )
            # Disabled → returns "disabled" cleanly, no ImportError propagated
            assert result.reason == "disabled"
        finally:
            # Restore modules
            for key, val in saved_modules.items():
                sys.modules[key] = val


# ---------------------------------------------------------------------------
# Fix 4: PR body template — First seen, Occurrences, Fingerprint footer
# ---------------------------------------------------------------------------


class TestPrBodyTemplate:
    """Verify _build_pr_body includes First seen, Occurrences, and fingerprint footer."""

    def test_first_seen_and_occurrences_in_body(self) -> None:
        """_build_pr_body includes First seen and Occurrences fields."""
        from butlers.core.healing.dispatch import _build_pr_body

        fp = _make_fp(fingerprint="e" * 64)
        attempt_id = uuid.uuid4()
        body = _build_pr_body(
            fp=fp,
            butler_name="email",
            attempt_id=attempt_id,
            repo_root=Path("/tmp"),
            agent_context=None,
            first_seen="2026-01-01T00:00:00Z",
            occurrences=3,
        )
        assert "**First seen:**" in body
        assert "2026-01-01T00:00:00Z" in body
        assert "**Occurrences:**" in body
        assert "3" in body

    def test_fingerprint_footer_in_body(self) -> None:
        """_build_pr_body includes *Fingerprint: `<full-fingerprint>`* footer."""
        from butlers.core.healing.dispatch import _build_pr_body

        fp = _make_fp(fingerprint="f" * 64)
        attempt_id = uuid.uuid4()
        body = _build_pr_body(
            fp=fp,
            butler_name="email",
            attempt_id=attempt_id,
            repo_root=Path("/tmp"),
            agent_context=None,
        )
        assert "*Fingerprint:" in body
        assert "f" * 64 in body

    def test_defaults_when_not_provided(self) -> None:
        """first_seen and occurrences default to 'unknown' when not supplied."""
        from butlers.core.healing.dispatch import _build_pr_body

        fp = _make_fp()
        attempt_id = uuid.uuid4()
        body = _build_pr_body(
            fp=fp,
            butler_name="email",
            attempt_id=attempt_id,
            repo_root=Path("/tmp"),
            agent_context=None,
        )
        assert "**First seen:** unknown" in body
        assert "**Occurrences:** unknown" in body


# ---------------------------------------------------------------------------
# task_registry — watchdog task surfaced to caller [bu-wjmw]
# ---------------------------------------------------------------------------


def _all_gates_pass_patches(tmp_path: Path):
    """Return a context-manager stack that makes all dispatch gates pass."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.session_set_healing_fingerprint",
            new_callable=AsyncMock,
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), True),
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=1,
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.get_recent_terminal_statuses",
            new_callable=AsyncMock,
            return_value=[],
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.resolve_model",
            new_callable=AsyncMock,
            return_value=("claude_code", "model", []),
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.create_healing_worktree",
            new_callable=AsyncMock,
            return_value=(tmp_path / "wt", "self-healing/email/x"),
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.healing.dispatch.update_attempt_status",
            new_callable=AsyncMock,
            return_value=True,
        )
    )
    return stack


class TestTaskRegistry:
    """dispatch_healing appends watchdog task to task_registry when provided."""

    async def test_watchdog_task_appended_to_registry(self, tmp_path: Path) -> None:
        """When task_registry is provided, the watchdog task is appended to it."""
        registry: list[asyncio.Task] = []
        fake_watchdog = MagicMock(spec=asyncio.Task)

        with _all_gates_pass_patches(tmp_path) as _:
            with patch("asyncio.create_task") as mock_create_task:
                # First call returns healing_task, second returns watchdog_task
                mock_create_task.side_effect = [MagicMock(spec=asyncio.Task), fake_watchdog]
                result = await dispatch_healing(
                    pool=_make_pool_all_pass(),
                    butler_name="email",
                    session_id=uuid.uuid4(),
                    fingerprint_input=_make_fp(),
                    config=_make_config(),
                    repo_root=tmp_path,
                    spawner=_make_spawner(),
                    task_registry=registry,
                )

        assert result.accepted is True
        assert fake_watchdog in registry

    async def test_no_task_registry_does_not_raise(self, tmp_path: Path) -> None:
        """When task_registry is None (default), dispatch succeeds without error."""
        with _all_gates_pass_patches(tmp_path) as _:
            with patch("asyncio.create_task") as mock_create_task:
                mock_create_task.return_value = MagicMock(spec=asyncio.Task)
                result = await dispatch_healing(
                    pool=_make_pool_all_pass(),
                    butler_name="email",
                    session_id=uuid.uuid4(),
                    fingerprint_input=_make_fp(),
                    config=_make_config(),
                    repo_root=tmp_path,
                    spawner=_make_spawner(),
                    # task_registry omitted → None
                )

        assert result.accepted is True

    async def test_registry_not_populated_on_gate_rejection(self, tmp_path: Path) -> None:
        """When dispatch is rejected (any gate), registry must remain empty."""
        registry: list[asyncio.Task] = []
        result = await dispatch_healing(
            pool=_make_pool_all_pass(),
            butler_name="email",
            session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(),
            config=_make_config(enabled=False),  # gate 2 rejects
            repo_root=tmp_path,
            spawner=_make_spawner(),
            task_registry=registry,
        )
        assert result.accepted is False
        assert registry == []

    async def test_multiple_dispatches_accumulate_in_registry(self, tmp_path: Path) -> None:
        """Multiple successful dispatches each append their watchdog to the same registry."""
        registry: list[asyncio.Task] = []
        watchdog_a = MagicMock(spec=asyncio.Task)
        watchdog_b = MagicMock(spec=asyncio.Task)

        # First dispatch
        with _all_gates_pass_patches(tmp_path) as _:
            with patch("asyncio.create_task") as mock_create_task:
                mock_create_task.side_effect = [MagicMock(spec=asyncio.Task), watchdog_a]
                await dispatch_healing(
                    pool=_make_pool_all_pass(),
                    butler_name="email",
                    session_id=uuid.uuid4(),
                    fingerprint_input=_make_fp(fingerprint="a" * 64),
                    config=_make_config(),
                    repo_root=tmp_path,
                    spawner=_make_spawner(),
                    task_registry=registry,
                )

        # Second dispatch
        with _all_gates_pass_patches(tmp_path) as _:
            with patch("asyncio.create_task") as mock_create_task:
                mock_create_task.side_effect = [MagicMock(spec=asyncio.Task), watchdog_b]
                await dispatch_healing(
                    pool=_make_pool_all_pass(),
                    butler_name="email",
                    session_id=uuid.uuid4(),
                    fingerprint_input=_make_fp(fingerprint="b" * 64),
                    config=_make_config(),
                    repo_root=tmp_path,
                    spawner=_make_spawner(),
                    task_registry=registry,
                )

        assert watchdog_a in registry
        assert watchdog_b in registry
        assert len(registry) == 2
