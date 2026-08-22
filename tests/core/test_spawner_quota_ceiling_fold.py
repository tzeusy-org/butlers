"""Tests for the quota/ceiling pre-spawn gate fold (bu-ep4ks.13 follow-up / bu-k9te9).

"Fold the quota/ceiling pre-spawn gates into the resolve CTE" (rest of slice 3, deferred
from PR #3587 as too invasive for one PR). Covers the equivalence-critical seams introduced
by the fold, on top of (not instead of) the pre-existing quota/ceiling/permission/same-tier-
failover suites, which pin that the fallback paths remain byte-for-byte identical to
pre-fold behavior:

- Fast path: when resolve_model_with_effective_tier(quota_aware=True) returns without
  raising TierQuotaExhausted, check_token_quota must NOT be called at all -- the fold
  already proved quota headroom.
- A spend rule that reroutes the model invalidates the fold's quota_ok guarantee (computed
  for the PRE-rule candidate) -- the sequential quota loop must still run for the
  rule-selected model even though the initial resolve was quota-confirmed.
- Gate precedence is unchanged: permission -> quota -> ceiling. The ceiling fetch is now
  kicked off concurrently with permission/quota, but its DENY decision still only fires
  after the quota gate settles, and a permission denial still preempts it entirely.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.config import ButlerConfig, RuntimeSeedConfig
from butlers.core.model_routing import CeilingStatus, QuotaStatus, TierQuotaExhausted
from butlers.core.permissions import PermissionStatus
from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE
from butlers.core.runtimes.base import RuntimeAdapter
from butlers.core.spawner import Spawner

pytestmark = pytest.mark.unit

_PRIMARY_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_RULE_TARGET_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
_SESSION_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


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


def _catalog_primary() -> tuple[str, str, list[str], uuid.UUID, int, str]:
    return (DEFAULT_RUNTIME_TYPE, "claude-primary", [], _PRIMARY_ID, 1800, "workhorse")


def _quota_allowed() -> QuotaStatus:
    return QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)


def _quota_denied() -> QuotaStatus:
    return QuotaStatus(allowed=False, usage_24h=1000, limit_24h=1000, usage_30d=0, limit_30d=None)


def _ceiling_under() -> CeilingStatus:
    return CeilingStatus(allowed=True, mtd_usd=1.0, ceiling_usd=100.0)


def _ceiling_over() -> CeilingStatus:
    return CeilingStatus(allowed=False, mtd_usd=125.0, ceiling_usd=100.0)


class TestFastPathSkipsSequentialQuotaLoop:
    """When the fold confirms quota up front, check_token_quota is never called."""

    async def test_no_check_token_quota_call_when_resolve_confirms_quota(
        self, tmp_path: Path
    ) -> None:
        """resolve_model_with_effective_tier(quota_aware=True) succeeding => quota loop skipped."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="fast-path-ran")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ) as mock_resolve,
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota,
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_under(),
            ),
        ):
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        assert result.output == "fast-path-ran"
        # The fold's whole point: no separate quota round trip on the confirmed-ok path.
        mock_quota.assert_not_called()
        # The spawner opts into the fold explicitly.
        mock_resolve.assert_awaited_once()
        assert mock_resolve.call_args.kwargs.get("quota_aware") is True

    async def test_check_token_quota_called_when_resolve_raises_exhausted(
        self, tmp_path: Path
    ) -> None:
        """TierQuotaExhausted => the pre-existing sequential loop still runs (fallback path)."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="should not run")

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                side_effect=TierQuotaExhausted(
                    effective_tier="workhorse", representative=_catalog_primary()
                ),
            ),
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_denied(),
            ) as mock_quota,
            patch(
                "butlers.core.spawner.next_same_tier_candidate",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert adapter.invoke_calls == 0
        mock_quota.assert_awaited_once()


class TestSpendRuleInvalidatesFoldGuarantee:
    """A spend-rule reroute must force the sequential quota loop for the NEW model."""

    async def test_spend_rule_reroute_forces_sequential_quota_check(self, tmp_path: Path) -> None:
        """Rule reroutes to a different catalog entry => check_token_quota runs for it.

        Even though the initial resolve was quota-confirmed for the PRE-rule candidate, that
        guarantee says nothing about the rule-selected model, so the fast path must not apply.
        """
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="rule-target-ran")

        rule_resolved = (
            DEFAULT_RUNTIME_TYPE,
            "claude-rule-target",
            [],
            _RULE_TARGET_ID,
            1800,
        )

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.apply_spend_routing_rules",
                new_callable=AsyncMock,
            ) as mock_rules,
            patch(
                "butlers.core.spawner.check_token_quota",
                new_callable=AsyncMock,
                return_value=_quota_allowed(),
            ) as mock_quota,
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_under(),
            ),
        ):
            from butlers.core.model_routing import SpendRoutingResult

            mock_rules.return_value = SpendRoutingResult(resolved=rule_resolved)
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        # The rerouted candidate's quota was actually checked -- the fold's guarantee for the
        # pre-rule candidate was correctly treated as invalidated.
        mock_quota.assert_awaited_once()
        assert mock_quota.call_args.args[1] == _RULE_TARGET_ID

    async def test_spend_rule_no_reroute_keeps_fast_path(self, tmp_path: Path) -> None:
        """A rule that matches but does not change the catalog entry keeps the fast path."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="ok")

        with (
            patch("butlers.core.spawner.session_create", new_callable=AsyncMock) as mock_create,
            patch("butlers.core.spawner.session_complete", new_callable=AsyncMock),
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.apply_spend_routing_rules",
                new_callable=AsyncMock,
            ) as mock_rules,
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota,
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_under(),
            ),
        ):
            from butlers.core.model_routing import SpendRoutingResult

            # Cap-only rule: matches, but keeps the same (pre-rule) resolved tuple.
            mock_rules.return_value = SpendRoutingResult(
                resolved=_catalog_primary()[:5], max_cost_per_call=None
            )
            mock_create.return_value = _SESSION_ID
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is True
        mock_quota.assert_not_called()


class TestGatePrecedenceUnchangedByConcurrentCeilingFetch:
    """permission -> quota -> ceiling stays the effective precedence after the fold."""

    async def test_permission_denial_preempts_ceiling_even_though_prefetched(
        self, tmp_path: Path
    ) -> None:
        """A denied permission blocks the spawn; the concurrently-kicked-off ceiling fetch
        (which would ALSO deny) must never be the reported reason."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="should not run")

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch(
                "butlers.core.spawner.check_permission",
                new_callable=AsyncMock,
                return_value=PermissionStatus(allowed=False, explicit=True, reason="disabled"),
            ),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota,
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_over(),
            ) as mock_ceiling,
        ):
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "Permission denied" in result.error
        assert "ceiling" not in result.error.lower()
        assert adapter.invoke_calls == 0
        # Permission is evaluated first; the quota gate is never reached.
        mock_quota.assert_not_called()
        # The ceiling task may have been kicked off concurrently, but its DENY was
        # never consulted for this result -- the permission denial already returned.
        del mock_ceiling  # presence/absence of the call is not the contract here

    async def test_ceiling_still_blocks_after_fast_path_quota_confirms(
        self, tmp_path: Path
    ) -> None:
        """Fast-path quota confirmation does not bypass the ceiling gate."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        mock_pool = AsyncMock()
        adapter = _MockAdapter(result_text="should not run")

        with (
            patch(
                "butlers.core.spawner.resolve_model_with_effective_tier",
                new_callable=AsyncMock,
                return_value=_catalog_primary(),
            ),
            patch("butlers.core.spawner.check_token_quota", new_callable=AsyncMock) as mock_quota,
            patch(
                "butlers.core.spawner.check_monthly_ceiling",
                new_callable=AsyncMock,
                return_value=_ceiling_over(),
            ),
            patch(
                "butlers.core.spawner._write_dispatch_attempt",
                new_callable=AsyncMock,
            ),
        ):
            result = await Spawner(
                config=_make_config(), config_dir=config_dir, pool=mock_pool, runtime=adapter
            ).trigger("hello", "tick")

        assert result.success is False
        assert result.error is not None
        assert "ceiling" in result.error.lower()
        assert adapter.invoke_calls == 0
        # Quota fast path applied (no sequential check needed); ceiling still fired.
        mock_quota.assert_not_called()
