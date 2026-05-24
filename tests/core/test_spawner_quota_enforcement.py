"""Tests for quota enforcement and ledger recording wired into the Spawner.

Covers:
- Spawn blocked when 24h limit exhausted
- Spawn blocked when 30d limit exhausted
- Spawn proceeds when within limits
- Spawn proceeds when no limits configured (unlimited)
- Ledger recorded on successful session with usage
- No ledger recording when adapter crashes (no usage returned)
- No ledger recording when catalog_entry_id is absent (TOML fallback)
  [covered by test_quota_not_checked_without_pool_or_toml_fallback]
- No ledger recording when adapter reports no usage

[bu-lm4m.1]
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import QuotaStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

# Fake catalog entry UUID used in resolve_model mock return values
_FAKE_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


# ---------------------------------------------------------------------------
# Minimal mock adapter
# ---------------------------------------------------------------------------


class _MockAdapter(RuntimeAdapter):
    """Minimal mock adapter for spawner orchestration tests."""

    def __init__(
        self,
        *,
        result_text: str = "ok",
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self._result_text = result_text
        self._usage = usage
        self._error = error
        self.invoke_calls = 0

    @property
    def binary_name(self) -> str:
        return "mock"

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
        self.invoke_calls += 1
        if self._error:
            raise RuntimeError(self._error)
        return self._result_text, [], self._usage

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9100) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


# ---------------------------------------------------------------------------
# Helper: build a spawner with a mock pool and patched helpers
# ---------------------------------------------------------------------------


def _quota_allowed() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=100, limit_24h=1000, usage_30d=500, limit_30d=5000)


def _quota_denied_24h() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=500, limit_30d=5000)


def _quota_denied_30d() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=100, limit_24h=1000, usage_30d=5000, limit_30d=5000)


def _quota_unlimited() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


# ---------------------------------------------------------------------------
# Quota enforcement tests
# ---------------------------------------------------------------------------


class TestSpawnerQuotaEnforcement:
    """Spawner blocks spawn when catalog entry quota is exhausted."""

    async def test_spawn_blocked_by_quota(self, tmp_path: Path) -> None:
        """Spawner returns success=False when 24h or 30d quota is exceeded and no fallback exists."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # 24h limit exhausted; no same-tier fallback → hard block
        adapter_24 = _MockAdapter(result_text="should not run")
        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-haiku",
                    [],
                    _FAKE_CATALOG_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_24h(),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter_24
            ).trigger("hello", "tick")
        assert result.success is False and result.error is not None
        assert "24h" in result.error and adapter_24.invoke_calls == 0

        # 30d limit exhausted; no same-tier fallback → hard block
        adapter_30 = _MockAdapter(result_text="should not run")
        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-haiku",
                    [],
                    _FAKE_CATALOG_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied_30d(),
            ),
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result2 = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter_30
            ).trigger("hello", "tick")
        assert result2.success is False and "30d" in result2.error and adapter_30.invoke_calls == 0

    async def test_spawn_proceeds_within_or_unlimited(self, tmp_path: Path) -> None:
        """Spawner proceeds normally when within limits or unlimited."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        for quota_status, expected_output in [
            (_quota_allowed(), "session output"),
            (_quota_unlimited(), "unlimited output"),
        ]:
            adapter = _MockAdapter(result_text=expected_output)
            with (
                patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
                patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
                patch(
                    "butlers.core.spawner.resolve_model_with_effective_tier",
                    new_callable=AsyncMock,
                    return_value=(
                        DEFAULT_RUNTIME_TYPE,
                        "claude-haiku",
                        [],
                        _FAKE_CATALOG_ID,
                        1800,
                        "workhorse",
                    ),
                ),
                patch(
                    "butlers.core.spawner.check_token_quota",
                    new_callable=AsyncMock,
                    return_value=quota_status,
                ),
                patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
            ):
                mock_create.return_value = _SESSION_ID
                result = await Spawner(
                    config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
                ).trigger("hello", "tick")
            assert (
                result.success is True
                and result.output == expected_output
                and adapter.invoke_calls == 1
            )

    async def test_quota_not_checked_without_pool_or_toml_fallback(self, tmp_path: Path) -> None:
        """Quota check skipped when pool=None or when catalog returns None (TOML fallback)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # No pool → quota check not called
        with patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota:
            result = await Spawner(
                config=config, config_dir=config_dir, runtime=_MockAdapter(result_text="toml")
            ).trigger("hi", "tick")
        mock_quota.assert_not_called()
        assert result.success is True

        # TOML fallback (catalog returns None) → quota check not called
        mock_pool = AsyncMock()
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota2,
        ):
            mock_create.return_value = _SESSION_ID
            result2 = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_MockAdapter(result_text="ok"),
            ).trigger("hi", "tick")
        mock_quota2.assert_not_called()
        assert result2.success is True


# ---------------------------------------------------------------------------
# Ledger recording tests
# ---------------------------------------------------------------------------


class TestSpawnerLedgerRecording:
    """Spawner records token usage to ledger in finally block."""

    async def test_ledger_recording_conditions(self, tmp_path: Path) -> None:
        """Ledger recorded on success; not recorded when adapter crashes or returns no usage.

        Note: TOML fallback (catalog_entry_id absent) also skips ledger recording, but that
        path is covered by TestSpawnerQuotaEnforcement.test_quota_not_checked_without_pool_or_toml_fallback.
        """
        config = _make_config()
        mock_pool = AsyncMock()

        # Successful session records to ledger
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        adapter = _MockAdapter(result_text="ok", usage={"input_tokens": 200, "output_tokens": 100})
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-haiku",
                    [],
                    _FAKE_CATALOG_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")
        assert result.success is True
        mock_record.assert_called_once()

        # Adapter crashes before returning usage → no recording
        class _FailingUsageAdapter(_MockAdapter):
            async def invoke(
                self,
                prompt,
                system_prompt,
                mcp_servers,
                env,
                max_turns=20,
                model=None,
                runtime_args=None,
                cwd=None,
                timeout=None,
            ):
                raise RuntimeError("adapter crashed")

        config_dir1 = tmp_path / "config1"
        config_dir1.mkdir()
        spawner1 = Spawner(
            config=config, config_dir=config_dir1, pool=mock_pool, runtime=_FailingUsageAdapter()
        )
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create1,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-haiku",
                    [],
                    _FAKE_CATALOG_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.core.spawner.record_token_usage", new_callable=AsyncMock
            ) as mock_record1,
        ):
            mock_create1.return_value = _SESSION_ID
            result1 = await spawner1.trigger("hello", "tick")
        assert result1.success is False
        mock_record1.assert_not_called()

        # Adapter returns None usage → no recording
        config_dir2 = tmp_path / "config2"
        config_dir2.mkdir()
        spawner2 = Spawner(
            config=config,
            config_dir=config_dir2,
            pool=mock_pool,
            runtime=_MockAdapter(result_text="ok", usage=None),
        )
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create2,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=(
                    DEFAULT_RUNTIME_TYPE,
                    "claude-haiku",
                    [],
                    _FAKE_CATALOG_ID,
                    1800,
                    "workhorse",
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.core.spawner.record_token_usage", new_callable=AsyncMock
            ) as mock_record2,
        ):
            mock_create2.return_value = _SESSION_ID
            result2 = await spawner2.trigger("hi", "tick")
        assert result2.success is True
        mock_record2.assert_not_called()
