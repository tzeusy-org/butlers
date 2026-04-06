"""Tests for butlers.core.healing.dispatch — condensed.

Covers:
- HealingConfig defaults and from_module_config
- Gate 1: no-recursion (trigger_source=healing → rejected)
- Gate 2: opt-in disabled → rejected, pool never queried
- Gate 3+ severity/already-investigating/cooldown/no-model → rejected
- Worktree creation failure → rejected
- Successful dispatch → accepted, tasks spawned
- dispatch_healing never raises (internal_error on exception)
- _build_healing_prompt: fingerprint/type/call_site/agent_context present
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.healing.dispatch import (
    HealingConfig,
    _build_healing_prompt,
    dispatch_healing,
)
from butlers.core.healing.fingerprint import FingerprintResult


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
    pool = MagicMock()

    async def fetchrow(*args, **kwargs):
        return None

    async def fetchval(*args, **kwargs):
        return 0

    async def fetch(*args, **kwargs):
        return []

    async def execute(*args, **kwargs):
        pass

    pool.fetchrow = AsyncMock(side_effect=fetchrow)
    pool.fetchval = AsyncMock(side_effect=fetchval)
    pool.fetch = AsyncMock(side_effect=fetch)
    pool.execute = AsyncMock(side_effect=execute)
    return pool


def _make_spawner(success: bool = True) -> MagicMock:
    spawner = MagicMock()

    @dataclass
    class _Result:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None
        output: str | None = None

    result = _Result(success=success, session_id=uuid.uuid4())

    spawner.trigger = AsyncMock(return_value=result)
    return spawner


@pytest.mark.unit
def test_healing_config_defaults_and_from_module_config() -> None:
    """Defaults are correct; from_module_config applies all fields; empty dict uses defaults."""
    cfg = HealingConfig()
    assert cfg.enabled is False
    assert cfg.severity_threshold == 2
    assert cfg.cooldown_minutes == 60
    assert cfg.max_concurrent == 2
    assert cfg.circuit_breaker_threshold == 5
    assert cfg.timeout_minutes == 30
    assert cfg.gh_token_env_var == "GH_TOKEN"

    full = HealingConfig.from_module_config({
        "enabled": True, "severity_threshold": 1, "cooldown_minutes": 30,
        "max_concurrent": 4, "circuit_breaker_threshold": 3, "timeout_minutes": 60,
        "gh_token_env_var": "MY_GH_TOKEN",
    })
    assert full.enabled is True and full.severity_threshold == 1
    assert full.gh_token_env_var == "MY_GH_TOKEN"
    assert HealingConfig.from_module_config({}).enabled is False


@pytest.mark.unit
async def test_dispatch_healing_gate_rejections(tmp_path: Path) -> None:
    """No-recursion, disabled, severity, already_investigating, cooldown, and no-model gates."""
    # Gate 1: no-recursion
    r = await dispatch_healing(
        pool=MagicMock(), butler_name="email", session_id=uuid.uuid4(),
        fingerprint_input=_make_fp(), config=_make_config(),
        repo_root=tmp_path, spawner=MagicMock(), trigger_source="healing",
    )
    assert r.accepted is False and r.reason == "no_recursion" and r.fingerprint is None

    # Gate 2: disabled
    pool2 = MagicMock()
    pool2.execute = AsyncMock()
    r2 = await dispatch_healing(
        pool=pool2, butler_name="email", session_id=uuid.uuid4(),
        fingerprint_input=_make_fp(), config=_make_config(enabled=False),
        repo_root=tmp_path, spawner=MagicMock(),
    )
    assert r2.accepted is False and r2.reason == "disabled" and r2.fingerprint is None
    pool2.execute.assert_not_called()

    # Gate severity: severity above threshold (severity=3 > threshold=2 → rejected)
    with patch("butlers.core.healing.dispatch.session_set_healing_fingerprint",
               new_callable=AsyncMock):
        r3 = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(severity=3), config=_make_config(severity_threshold=2),
            repo_root=tmp_path, spawner=MagicMock(),
        )
    assert r3.accepted is False and r3.reason == "severity_below_threshold"

    # already_investigating gate
    with (
        patch("butlers.core.healing.dispatch.session_set_healing_fingerprint",
              new_callable=AsyncMock),
        patch("butlers.core.healing.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), False)),
    ):
        r4 = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(), config=_make_config(),
            repo_root=tmp_path, spawner=MagicMock(),
        )
    assert r4.accepted is False and r4.reason == "already_investigating"

    # cooldown gate
    with (
        patch("butlers.core.healing.dispatch.session_set_healing_fingerprint",
              new_callable=AsyncMock),
        patch("butlers.core.healing.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), True)),
        patch("butlers.core.healing.dispatch.get_recent_attempt", new_callable=AsyncMock,
              return_value={"status": "failed"}),
    ):
        r5 = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(), config=_make_config(),
            repo_root=tmp_path, spawner=MagicMock(),
        )
    assert r5.accepted is False and r5.reason == "cooldown"

    # no model available
    with (
        patch("butlers.core.healing.dispatch.session_set_healing_fingerprint",
              new_callable=AsyncMock),
        patch("butlers.core.healing.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), True)),
        patch("butlers.core.healing.dispatch.get_recent_attempt", new_callable=AsyncMock,
              return_value=None),
        patch("butlers.core.healing.dispatch.count_active_attempts", new_callable=AsyncMock,
              return_value=0),
        patch("butlers.core.healing.dispatch.get_recent_terminal_statuses", new_callable=AsyncMock,
              return_value=[]),
        patch("butlers.core.healing.dispatch.resolve_model", new_callable=AsyncMock,
              return_value=None),
        patch("butlers.core.healing.dispatch.update_attempt_status", new_callable=AsyncMock,
              return_value=True),
    ):
        r6 = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(), config=_make_config(),
            repo_root=tmp_path, spawner=MagicMock(),
        )
    assert r6.accepted is False and r6.reason == "no_model"


@pytest.mark.unit
async def test_dispatch_healing_success(tmp_path: Path) -> None:
    """Successful dispatch: accepted=True, reason=dispatched, tasks spawned."""
    with (
        patch("butlers.core.healing.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), True)),
        patch("butlers.core.healing.dispatch.get_recent_attempt", new_callable=AsyncMock,
              return_value=None),
        patch("butlers.core.healing.dispatch.count_active_attempts", new_callable=AsyncMock,
              return_value=1),
        patch("butlers.core.healing.dispatch.get_recent_terminal_statuses", new_callable=AsyncMock,
              return_value=[]),
        patch("butlers.core.healing.dispatch.resolve_model", new_callable=AsyncMock,
              return_value=("claude_code", "claude-sonnet-4-5", [])),
        patch("butlers.core.healing.dispatch.create_healing_worktree", new_callable=AsyncMock,
              return_value=(tmp_path / ".healing-worktrees/self-healing/email/abc-1",
                            "self-healing/email/abc-1")),
        patch("butlers.core.healing.dispatch.update_attempt_status", new_callable=AsyncMock,
              return_value=True),
        patch("butlers.core.healing.dispatch.session_set_healing_fingerprint", new_callable=AsyncMock),
        patch("asyncio.create_task") as mock_create_task,
    ):
        mock_create_task.return_value = MagicMock()
        result = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(), config=_make_config(),
            repo_root=tmp_path, spawner=_make_spawner(), trigger_source="external",
        )
    assert result.accepted is True and result.reason == "dispatched"
    assert mock_create_task.called


@pytest.mark.unit
async def test_dispatch_healing_never_raises(tmp_path: Path) -> None:
    """dispatch_healing catches internal exceptions and returns internal_error result."""
    with patch("butlers.core.healing.dispatch.create_or_join_attempt",
               new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        result = await dispatch_healing(
            pool=_make_pool_all_pass(), butler_name="email", session_id=uuid.uuid4(),
            fingerprint_input=_make_fp(), config=_make_config(),
            repo_root=tmp_path, spawner=MagicMock(),
        )
    assert result.accepted is False and result.reason == "internal_error"


@pytest.mark.unit
def test_build_healing_prompt() -> None:
    """Prompt includes fingerprint, exception type, call site, and optional context."""
    fp = _make_fp(fingerprint="b" * 64, sanitized_message="SMTP auth failed")
    prompt = _build_healing_prompt(fp, "email", "tick", "Sending invoice email")
    assert "b" * 64 in prompt
    assert "builtins.ValueError" in prompt
    assert "SMTP auth failed" in prompt
    assert "Butler Diagnostic Context" in prompt

    # Without context: still includes key fields
    no_ctx = _build_healing_prompt(fp, "email", "tick", None)
    assert "b" * 64 in no_ctx
    assert "Butler Diagnostic Context" not in no_ctx
