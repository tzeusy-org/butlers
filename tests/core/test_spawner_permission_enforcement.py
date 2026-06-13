"""Tests for permissions-matrix enforcement wired into the Spawner.

The Settings → Permissions matrix (``public.permissions``) governs which butler
may act. Before this fix it was persisted, audited, and surfaced in the dashboard
but NEVER enforced — the matrix was decorative. These tests pin the spawn-time
gate that consults the matrix's ``spawn`` permission:

- Spawn BLOCKED when the butler's ``spawn`` permission is explicitly revoked.
- Spawn ALLOWED when the ``spawn`` permission is granted.
- Spawn ALLOWED when no explicit row exists (default-allow / opt-in deny).
- The permission check is skipped without a pool / on TOML fallback.

Mirrors the spend-ceiling harness in test_spawner_ceiling_enforcement.py.

[bu-qu8ma.1]
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import CeilingStatus, QuotaStatus
from butlers.core.permissions import PermissionStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_FAKE_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


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


def _make_config(name: str = "test-butler", port: int = 9210) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


def _quota_allowed() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _ceiling_unset() -> CeilingStatus:
    return CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None)


def _perm_denied() -> PermissionStatus:
    return PermissionStatus(allowed=False, explicit=True, reason="revoked by owner")


def _perm_granted() -> PermissionStatus:
    return PermissionStatus(allowed=True, explicit=True, reason="default")


def _perm_default_allow() -> PermissionStatus:
    return PermissionStatus(allowed=True, explicit=False)


def _catalog_resolution() -> tuple[str, str, list[str], uuid.UUID, int, str]:
    return (DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800, "workhorse")


class TestSpawnerPermissionEnforcement:
    """Spawner blocks the spawn when the matrix revokes the butler's spawn grant."""

    async def test_spawn_blocked_when_permission_revoked(self, tmp_path: Path) -> None:
        """Spawn is denied (adapter never runs) when ``spawn`` is granted=false.

        Pre-fix this fails: the matrix was ignored, so the spawn proceeded.
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _MockAdapter(result_text="should not run")
        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_resolution(),
            ),
            patch(
                "butlers.core.spawner.check_permission",
                new_callable=AsyncMock,
                return_value=_perm_denied(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_unset(),
            ),
        ):
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "permission denied" in result.error.lower()
        assert adapter.invoke_calls == 0

    async def test_spawn_allowed_when_permission_granted(self, tmp_path: Path) -> None:
        """Spawn proceeds normally when ``spawn`` is granted."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _MockAdapter(result_text="session output")
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_resolution(),
            ),
            patch(
                "butlers.core.spawner.check_permission",
                new_callable=AsyncMock,
                return_value=_perm_granted(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_unset(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "session output"
        assert adapter.invoke_calls == 1

    async def test_spawn_allowed_on_default_allow(self, tmp_path: Path) -> None:
        """Spawn proceeds when no explicit row exists (opt-in deny default-allow)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()

        adapter = _MockAdapter(result_text="default output")
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_resolution(),
            ),
            patch(
                "butlers.core.spawner.check_permission",
                new_callable=AsyncMock,
                return_value=_perm_default_allow(),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ),
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_unset(),
            ),
            patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "default output"
        assert adapter.invoke_calls == 1

    async def test_permission_not_checked_without_pool_or_toml_fallback(
        self, tmp_path: Path
    ) -> None:
        """Permission check skipped when pool=None or catalog returns None (TOML fallback)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()

        # No pool → permission check not called.
        with patch("butlers.core.spawner.check_permission", new_callable=AsyncMock) as mock_perm:
            result = await Spawner(
                config=config, config_dir=config_dir, runtime=_MockAdapter(result_text="toml")
            ).trigger("hi", "tick")
        mock_perm.assert_not_called()
        assert result.success is True

        # TOML fallback (catalog returns None) → permission check not called.
        mock_pool = AsyncMock()
        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("butlers.core.spawner.check_permission", new_callable=AsyncMock) as mock_perm2,
        ):
            mock_create.return_value = _SESSION_ID
            result2 = await Spawner(
                config=config,
                config_dir=config_dir,
                pool=mock_pool,
                runtime=_MockAdapter(result_text="ok"),
            ).trigger("hi", "tick")
        mock_perm2.assert_not_called()
        assert result2.success is True
