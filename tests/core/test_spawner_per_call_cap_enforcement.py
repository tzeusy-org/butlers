"""Tests for per-call spend-cap enforcement wired into the Spawner.

A matching spend rule may carry an ``action.max_cost_per_call`` effect — a hard
per-dispatch USD cap. ``apply_spend_routing_rules`` surfaces the cap on its
``SpendRoutingResult``; the spawner enforces it as a DENY gate using a worst-case
pre-spawn cost estimate (the resolved model's input price times this dispatch's
input-token budget). These tests pin that gate:

- Spawn BLOCKED when the estimated worst-case per-call cost exceeds the cap.
- Spawn ALLOWED when the worst-case is within the cap.
- Spawn ALLOWED (cap not enforceable) when the call has no input-token budget.
- No cap (no matching rule effect) → gate does nothing.

Mirrors the ceiling harness in test_spawner_ceiling_enforcement.py.

[bu-xclyn]
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import CeilingStatus, QuotaStatus, SpendRoutingResult
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_FAKE_CATALOG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_SESSION_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")


class _MockAdapter(RuntimeAdapter):
    """Minimal mock adapter for spawner orchestration tests."""

    def __init__(self, *, result_text: str = "ok") -> None:
        self._result_text = result_text
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
        return self._result_text, [], None

    async def reset(self) -> None:
        pass

    def build_config_file(self, mcp_servers: dict[str, Any], tmp_dir: Path) -> Path:
        import json

        p = tmp_dir / "cfg.json"
        p.write_text(json.dumps({"mcpServers": mcp_servers}))
        return p

    def parse_system_prompt_file(self, config_dir: Path) -> str:
        return ""


def _make_config(name: str = "test-butler", port: int = 9300) -> ButlerConfig:
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


def _catalog_resolution() -> tuple[str, str, list[str], uuid.UUID, int, str]:
    return (DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800, "workhorse")


def _routing_with_cap(cap: float) -> SpendRoutingResult:
    """Routing result that keeps the resolved model but attaches a per-call cap."""
    return SpendRoutingResult(
        resolved=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
        max_cost_per_call=cap,
    )


def _routing_no_cap() -> SpendRoutingResult:
    return SpendRoutingResult(
        resolved=(DEFAULT_RUNTIME_TYPE, "claude-haiku", [], _FAKE_CATALOG_ID, 1800),
        max_cost_per_call=None,
    )


def _enter_base_patches(stack: ExitStack, routing: SpendRoutingResult) -> None:
    """Enter common patches: catalog resolution, routing result, quota, ceiling allowed."""
    stack.enter_context(
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=_catalog_resolution(),
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.spawner.apply_spend_routing_rules",
            new_callable=AsyncMock,
            return_value=routing,
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.spawner.check_token_quota",
            new_callable=AsyncMock,
            return_value=_quota_allowed(),
        )
    )
    stack.enter_context(
        patch(
            "butlers.core.spawner.check_monthly_ceiling",
            new_callable=AsyncMock,
            return_value=_ceiling_unset(),
        )
    )


def _enter_session_patches(stack: ExitStack) -> AsyncMock:
    """Enter session_create / session_complete / record_token_usage patches.

    Returns the session_create mock so the caller can set its return value.
    """
    mock_create = stack.enter_context(
        patch("butlers.core.spawner.session_create", new_callable=AsyncMock)
    )
    stack.enter_context(patch("butlers.core.spawner.session_complete", new_callable=AsyncMock))
    stack.enter_context(patch("butlers.core.spawner.record_token_usage", new_callable=AsyncMock))
    mock_create.return_value = _SESSION_ID
    return mock_create


class TestSpawnerPerCallCapEnforcement:
    async def test_spawn_blocked_when_worst_case_over_cap(self, tmp_path: Path) -> None:
        """Spawn denied (adapter never runs) when worst-case per-call cost exceeds the cap."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="should not run")

        with ExitStack() as stack:
            _enter_base_patches(stack, _routing_with_cap(0.05))
            # Worst-case estimate ($0.50) exceeds the $0.05 cap.
            stack.enter_context(
                patch("butlers.core.spawner._estimate_worst_case_call_cost", return_value=0.50)
            )
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick", max_token_budget=100_000)

        assert result.success is False
        assert result.error is not None
        assert "per-call spend cap" in result.error.lower()
        assert adapter.invoke_calls == 0

    async def test_spawn_allowed_when_worst_case_within_cap(self, tmp_path: Path) -> None:
        """Spawn proceeds when the worst-case per-call cost is within the cap."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="session output")

        with ExitStack() as stack:
            _enter_session_patches(stack)
            _enter_base_patches(stack, _routing_with_cap(1.00))
            stack.enter_context(
                patch("butlers.core.spawner._estimate_worst_case_call_cost", return_value=0.30)
            )
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick", max_token_budget=100_000)

        assert result.success is True
        assert result.output == "session output"
        assert adapter.invoke_calls == 1

    async def test_spawn_allowed_when_cap_not_enforceable(self, tmp_path: Path) -> None:
        """When worst-case cannot be estimated (no budget), the cap is not enforced."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="unbounded")

        with ExitStack() as stack:
            _enter_session_patches(stack)
            _enter_base_patches(stack, _routing_with_cap(0.01))
            # No token budget → estimator returns None → cap not enforceable.
            stack.enter_context(
                patch("butlers.core.spawner._estimate_worst_case_call_cost", return_value=None)
            )
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "unbounded"
        assert adapter.invoke_calls == 1

    async def test_no_cap_does_not_invoke_estimator(self, tmp_path: Path) -> None:
        """When no rule sets a cap, the per-call gate is skipped entirely."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config = _make_config()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="ok")

        with ExitStack() as stack:
            _enter_session_patches(stack)
            _enter_base_patches(stack, _routing_no_cap())
            mock_est = stack.enter_context(
                patch("butlers.core.spawner._estimate_worst_case_call_cost")
            )
            result = await Spawner(
                config=config, config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick", max_token_budget=100_000)

        mock_est.assert_not_called()
        assert result.success is True
        assert adapter.invoke_calls == 1
