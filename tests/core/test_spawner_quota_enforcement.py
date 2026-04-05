"""Tests for quota enforcement and ledger recording wired into the Spawner.

Covers:
- Spawn blocked when 24h limit exhausted
- Spawn blocked when 30d limit exhausted
- Spawn proceeds when within limits
- Spawn proceeds when no limits configured (unlimited)
- Failed session with usage still records to ledger
- No ledger recording when catalog_entry_id is absent (TOML fallback)
- No ledger recording when adapter reports no usage

[bu-lm4m.1]
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeConfig
from butlers.core.model_routing import QuotaStatus
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
        runtime=RuntimeConfig(max_concurrent_sessions=1),
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
        """Spawner returns success=False when 24h or 30d quota is exceeded; adapter not invoked."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # 24h limit exhausted
        adapter_24 = _MockAdapter(result_text="should not run")
        with (
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_denied_24h()),
        ):
            result = await Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter_24).trigger("hello", "tick")
        assert result.success is False and result.error is not None
        assert "24h" in result.error and adapter_24.invoke_calls == 0

        # 30d limit exhausted
        adapter_30 = _MockAdapter(result_text="should not run")
        with (
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_denied_30d()),
        ):
            result2 = await Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter_30).trigger("hello", "tick")
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
                patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                      return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
                patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                      return_value=quota_status),
                patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
            ):
                mock_create.return_value = _SESSION_ID
                result = await Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter).trigger("hello", "tick")
            assert result.success is True and result.output == expected_output and adapter.invoke_calls == 1

    async def test_quota_error_message_includes_alias_and_windows(self, tmp_path: Path) -> None:
        """Error message from quota block includes catalog alias and window details."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()

        denied = QuotaStatus(
            allowed=False, usage_24h=1500, limit_24h=1000, usage_30d=200, limit_30d=5000
        )

        with (
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=denied),
        ):
            result = await Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter).trigger("hi", "tick")

        assert result.success is False and result.error is not None
        assert "claude-haiku" in result.error and "24h" in result.error
        assert "1500" in result.error and "1000" in result.error

    async def test_quota_not_checked_without_pool_or_toml_fallback(self, tmp_path: Path) -> None:
        """Quota check skipped when pool=None or when catalog returns None (TOML fallback)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # No pool → quota check not called
        with patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota:
            result = await Spawner(config=config, config_dir=config_dir, runtime=_MockAdapter(result_text="toml")).trigger("hi", "tick")
        mock_quota.assert_not_called()
        assert result.success is True

        # TOML fallback (catalog returns None) → quota check not called
        mock_pool = AsyncMock()
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock, return_value=None),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota2,
        ):
            mock_create.return_value = _SESSION_ID
            result2 = await Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=_MockAdapter(result_text="ok")).trigger("hi", "tick")
        mock_quota2.assert_not_called()
        assert result2.success is True


# ---------------------------------------------------------------------------
# Ledger recording tests
# ---------------------------------------------------------------------------


class TestSpawnerLedgerRecording:
    """Spawner records token usage to ledger in finally block."""

    async def test_ledger_recording_on_success_and_despite_db_failure(self, tmp_path: Path) -> None:
        """Ledger recorded on success with correct fields; also recorded when adapter returns usage but session_complete fails."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        # Successful session records to ledger with correct fields
        adapter = _MockAdapter(result_text="ok", usage={"input_tokens": 200, "output_tokens": 100})
        spawner = Spawner(config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_allowed()),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record,
        ):
            mock_create.return_value = _SESSION_ID
            result = await spawner.trigger("hello", "tick")
        assert result.success is True
        mock_record.assert_called_once()
        kw = mock_record.call_args.kwargs
        assert kw["catalog_entry_id"] == _FAKE_CATALOG_ID
        assert kw["butler_name"] == "test-butler"
        assert kw["session_id"] == _SESSION_ID
        assert kw["input_tokens"] == 200 and kw["output_tokens"] == 100

        # Adapter returns usage but session_complete fails → ledger still written
        adapter2 = _MockAdapter(result_text="ok", usage={"input_tokens": 50, "output_tokens": 25})
        spawner2 = Spawner(config=config, config_dir=tmp_path / "config2", pool=mock_pool, runtime=adapter2)
        (tmp_path / "config2").mkdir()
        _call_count = [0]

        async def _session_complete_side_effect(*args: Any, **kwargs: Any) -> None:
            _call_count[0] += 1
            if _call_count[0] == 1:
                raise RuntimeError("DB write failed on success path")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create2,
            patch("butlers.core.spawner.session_complete", side_effect=_session_complete_side_effect),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_allowed()),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record2,
        ):
            mock_create2.return_value = _SESSION_ID
            result2 = await spawner2.trigger("hello", "tick")
        assert result2.success is False
        mock_record2.assert_called_once()
        kw2 = mock_record2.call_args.kwargs
        assert kw2["input_tokens"] == 50 and kw2["output_tokens"] == 25
        assert kw2["catalog_entry_id"] == _FAKE_CATALOG_ID

    async def test_no_ledger_recording_conditions(self, tmp_path: Path) -> None:
        """No ledger recording when: adapter crashes before returning usage; TOML fallback (no catalog_entry_id); adapter returns None usage."""
        config = _make_config()
        mock_pool = AsyncMock()

        # Adapter crashes before returning usage → no recording
        class _FailingUsageAdapter(_MockAdapter):
            async def invoke(self, prompt, system_prompt, mcp_servers, env,
                             max_turns=20, model=None, runtime_args=None, cwd=None, timeout=None):
                raise RuntimeError("adapter crashed")

        config_dir1 = tmp_path / "config1"
        config_dir1.mkdir()
        adapter1 = _FailingUsageAdapter()
        spawner1 = Spawner(config=config, config_dir=config_dir1, pool=mock_pool, runtime=adapter1)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create1,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_allowed()),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record1,
        ):
            mock_create1.return_value = _SESSION_ID
            result1 = await spawner1.trigger("hello", "tick")
        assert result1.success is False
        mock_record1.assert_not_called()

        # TOML fallback (resolve_model returns None) → no recording
        config_dir2 = tmp_path / "config2"
        config_dir2.mkdir()
        adapter2 = _MockAdapter(result_text="toml result", usage={"input_tokens": 100, "output_tokens": 50})
        spawner2 = Spawner(config=config, config_dir=config_dir2, pool=mock_pool, runtime=adapter2)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create2,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock, return_value=None),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record2,
        ):
            mock_create2.return_value = _SESSION_ID
            result2 = await spawner2.trigger("hi", "tick")
        assert result2.success is True
        mock_record2.assert_not_called()

        # Adapter returns None usage → no recording
        config_dir3 = tmp_path / "config3"
        config_dir3.mkdir()
        adapter3 = _MockAdapter(result_text="ok", usage=None)
        spawner3 = Spawner(config=config, config_dir=config_dir3, pool=mock_pool, runtime=adapter3)
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create3,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch("butlers.core.spawner.resolve_model", new_callable=AsyncMock,
                  return_value=("claude", "claude-haiku", [], _FAKE_CATALOG_ID)),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock,
                  return_value=_quota_allowed()),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock) as mock_record3,
        ):
            mock_create3.return_value = _SESSION_ID
            result3 = await spawner3.trigger("hi", "tick")
        assert result3.success is True
        mock_record3.assert_not_called()
