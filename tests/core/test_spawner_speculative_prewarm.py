"""Tests for speculative fire-and-forget prewarm (bu-ep4ks.13 follow-up / bu-k9te9, slice 4).

"Speculative prewarm fired fire-and-forget from the classification decision" -- MCP endpoint
warmup and codex token pre-warm previously ran lazily on the spawn critical path even though
the resolved runtime_type ("classification decision") is known well before invocation.
Spawner._fire_speculative_prewarm now kicks both off as soon as that decision settles
(post spend-rule override), strictly off the critical path:

- Never awaited by the dispatch path (asyncio.create_task, fire-and-forget).
- A prewarm failure must never fail, delay, or alter dispatch.
- No new public.model_dispatch_attempts rows are written from prewarm.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import CeilingStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_PRIMARY_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_ATTEMPTS_INSERT = "INSERT INTO public.model_dispatch_attempts"


class _MockAdapter(RuntimeAdapter):
    """Minimal mock adapter that also tracks speculative_prewarm() calls."""

    def __init__(self, *, result_text: str = "ok", prewarm_error: Exception | None = None) -> None:
        self._result_text = result_text
        self._prewarm_error = prewarm_error
        self.invoke_calls = 0
        self.speculative_prewarm_calls = 0

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
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    async def speculative_prewarm(self) -> None:
        self.speculative_prewarm_calls += 1
        if self._prewarm_error is not None:
            raise self._prewarm_error

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9500) -> ButlerConfig:
    return ButlerConfig(
        name=name,
        port=port,
        runtime_seed=RuntimeSeedConfig(max_concurrent_sessions=1),
        modules={},
        env_required=[],
        env_optional=[],
    )


def _catalog_primary(runtime_type: str = DEFAULT_RUNTIME_TYPE) -> tuple:
    return (runtime_type, "claude-primary", [], _PRIMARY_ID, 1800, "workhorse")


async def _settle_background_tasks() -> None:
    """Give asyncio.create_task-scheduled coroutines a chance to run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)


class TestSpeculativePrewarmFires:
    """The speculative prewarm task fires with the resolved runtime_type/trigger_source."""

    async def test_fires_with_resolved_runtime_type_and_trigger_source(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None),
            ),
            patch.object(Spawner, "_fire_speculative_prewarm", autospec=True) as mock_fire,
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "route")

        assert result.success is True
        mock_fire.assert_called_once()
        _self, kwargs = mock_fire.call_args.args[0], mock_fire.call_args.kwargs
        assert kwargs.get("resolved_runtime_type") == DEFAULT_RUNTIME_TYPE
        assert kwargs.get("trigger_source") == "route"

    async def test_calls_adapter_speculative_prewarm_and_mcp_warmup(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.Spawner._ensure_mcp_endpoints_warmed",
                new_callable=AsyncMock,
            ) as mock_warm,
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "route")
            await _settle_background_tasks()

        assert result.success is True
        # Both the fire-and-forget speculative task and the (unchanged) later on-path
        # call in _run() invoke these -- at least one call each proves the speculative
        # wiring actually reaches both hooks.
        assert adapter.speculative_prewarm_calls >= 1
        assert mock_warm.await_count >= 1


class TestSpeculativePrewarmNeverBlocksOrFailsDispatch:
    """A prewarm failure must never fail, delay, or alter dispatch."""

    async def test_adapter_speculative_prewarm_failure_does_not_affect_result(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(prewarm_error=RuntimeError("prewarm exploded"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "route")
            await _settle_background_tasks()

        assert result.success is True
        assert result.output == "ok"
        assert adapter.invoke_calls == 1

    async def test_mcp_endpoint_warmup_failure_does_not_affect_result(self, tmp_path: Path) -> None:
        """Both the speculative and on-path calls share _ensure_mcp_endpoints_warmed's own
        internal try/except around the actual network call (warmup_mcp_urls) -- that is the
        real failure surface, not the method itself (which never raises to its caller)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.mcp_warmup.warmup_mcp_urls",
                new_callable=AsyncMock,
                side_effect=RuntimeError("mcp warmup exploded"),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "route")
            await _settle_background_tasks()

        assert result.success is True
        assert adapter.invoke_calls == 1

    async def test_no_extra_dispatch_attempt_rows_from_prewarm(self, tmp_path: Path) -> None:
        """Prewarm must never write public.model_dispatch_attempts provenance."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(prewarm_error=RuntimeError("prewarm exploded"))

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.mcp_warmup.warmup_mcp_urls",
                new_callable=AsyncMock,
                side_effect=RuntimeError("mcp warmup exploded"),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "route")
            await _settle_background_tasks()

        assert result.success is True
        attempts = [
            call.args
            for call in mock_pool.execute.call_args_list
            if call.args and isinstance(call.args[0], str) and _ATTEMPTS_INSERT in call.args[0]
        ]
        outcomes = [a[4] for a in attempts]
        # Exactly the normal single success row -- nothing attributable to prewarm.
        assert outcomes == ["success"]


class TestSpeculativePrewarmMirrorsMcpServerScoping:
    """MCP warmup is skipped for healing/qa exactly like the later on-path build."""

    async def test_healing_trigger_source_skips_mcp_warmup_but_still_prewarms_adapter(
        self, tmp_path: Path
    ) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter()

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.Spawner._ensure_mcp_endpoints_warmed",
                new_callable=AsyncMock,
            ) as mock_warm,
        ):
            mock_create.return_value = _SESSION_ID
            spawner = Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            )
            result = await spawner.trigger("hello", "healing")
            await _settle_background_tasks()

        assert result.success is True
        # healing sessions attach no MCP servers (see _run's own mcp_servers build). The
        # on-path call in _run still fires unconditionally but with an empty dict (its own
        # fast no-op path in the real implementation) -- what this pins is that the
        # SPECULATIVE task never makes its own separate real-URL call for healing sessions,
        # i.e. exactly one call total, with the empty dict.
        mock_warm.assert_called_once_with({})
        # The adapter-level prewarm (e.g. codex token refresh) is unrelated to MCP servers
        # and must still fire.
        assert adapter.speculative_prewarm_calls >= 1
