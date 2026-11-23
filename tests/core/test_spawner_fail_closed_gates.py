"""The Spawner refuses when permission, quota or ceiling state cannot be read (bu-svqpzo.3).

Each gate used to fail open: a failed read allowed the dispatch. Each now refuses with a reason
distinct from the owner's own decision, and the unavailable ceiling raises no fleet-halt.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.model_routing import (
    CEILING_DENIAL_REASON_PREFIX,
    CEILING_UNAVAILABLE_REASON,
    CeilingStatus,
    QuotaStatus,
    TierQuotaExhausted,
)
from butlers.core.permissions import PermissionStatus
from butlers.core.spawner import Spawner
from tests.core.test_spawner_permission_enforcement import (
    _catalog_resolution,
    _make_config,
    _MockAdapter,
)

pytest_plugins = ("tests.core.spawner_fixtures",)
pytestmark = [
    pytest.mark.unit,
    pytest.mark.real_gate_state,
    pytest.mark.usefixtures("spawner_catalog_candidate"),
]

_ALLOWED = PermissionStatus(allowed=True, explicit=False)
_QUOTA_OK = QuotaStatus(allowed=True, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None)
_CEILING_OK = CeilingStatus(allowed=True, mtd_usd=0.0, ceiling_usd=None)


def _trigger(tmp_path: Path, adapter: _MockAdapter, pool: AsyncMock):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return Spawner(
        config=_make_config(), config_dir=config_dir, pool=pool, runtime=adapter
    ).trigger("hello", "tick")


async def test_unreadable_permission_store_refuses_the_spawn(tmp_path: Path) -> None:
    """A permissions read that raises is not a grant: no adapter run, a provenance row."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=OSError("db down"))
    adapter = _MockAdapter(result_text="must not run")
    with (
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=_catalog_resolution(),
        ),
        patch("butlers.core.spawner._write_dispatch_attempt", new_callable=AsyncMock) as attempt,
    ):
        result = await _trigger(tmp_path, adapter, pool)

    assert result.success is False
    assert "permission state unavailable" in (result.error or "").lower()
    assert "denied" not in (result.error or "").lower()
    assert adapter.invoke_calls == 0
    attempt.assert_awaited_once()
    assert attempt.await_args.kwargs["outcome"] == "quota_skip"


async def test_unreadable_token_quota_refuses_with_its_own_reason(tmp_path: Path) -> None:
    resolved = _catalog_resolution()
    adapter = _MockAdapter(result_text="must not run")
    unavailable = QuotaStatus(
        allowed=False, usage_24h=0, limit_24h=None, usage_30d=0, limit_30d=None, unavailable=True
    )
    with (
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            side_effect=TierQuotaExhausted(effective_tier="workhorse", representative=resolved),
        ),
        patch("butlers.core.spawner.check_permission", AsyncMock(return_value=_ALLOWED)),
        patch("butlers.core.spawner.check_token_quota", AsyncMock(return_value=unavailable)),
        patch(
            "butlers.core.spawner.next_same_tier_candidate",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("butlers.core.spawner._write_dispatch_attempt", new_callable=AsyncMock) as attempt,
    ):
        result = await _trigger(tmp_path, adapter, AsyncMock())

    assert result.success is False
    assert "Token quota unavailable" in (result.error or "")
    assert "exhausted" not in (result.error or "")
    assert adapter.invoke_calls == 0
    assert attempt.await_args.kwargs["failure_reason"].startswith("Token quota unavailable")


async def test_unreadable_ceiling_refuses_without_raising_the_fleet_halt(tmp_path: Path) -> None:
    adapter = _MockAdapter(result_text="must not run")
    unavailable = CeilingStatus(allowed=False, mtd_usd=0.0, ceiling_usd=None, unavailable=True)
    with (
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=_catalog_resolution(),
        ),
        patch("butlers.core.spawner.check_permission", AsyncMock(return_value=_ALLOWED)),
        patch("butlers.core.spawner.check_token_quota", AsyncMock(return_value=_QUOTA_OK)),
        patch("butlers.core.spawner.check_monthly_ceiling", AsyncMock(return_value=unavailable)),
        patch("butlers.core.spawner._write_dispatch_attempt", new_callable=AsyncMock) as attempt,
    ):
        result = await _trigger(tmp_path, adapter, AsyncMock())

    assert result.success is False and result.error == CEILING_UNAVAILABLE_REASON
    assert not CEILING_UNAVAILABLE_REASON.startswith(CEILING_DENIAL_REASON_PREFIX)
    assert adapter.invoke_calls == 0
    kwargs = attempt.await_args.kwargs
    assert kwargs["outcome"] == "quota_skip"
    assert kwargs["failure_reason"] == CEILING_UNAVAILABLE_REASON
    assert kwargs["produce_fleet_halt"] is False


async def test_a_real_ceiling_breach_still_raises_the_fleet_halt(tmp_path: Path) -> None:
    adapter = _MockAdapter(result_text="must not run")
    over = CeilingStatus(allowed=False, mtd_usd=125.0, ceiling_usd=100.0)
    with (
        patch(
            "butlers.core.spawner.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=_catalog_resolution(),
        ),
        patch("butlers.core.spawner.check_permission", AsyncMock(return_value=_ALLOWED)),
        patch("butlers.core.spawner.check_token_quota", AsyncMock(return_value=_QUOTA_OK)),
        patch("butlers.core.spawner.check_monthly_ceiling", AsyncMock(return_value=over)),
        patch("butlers.core.spawner._write_dispatch_attempt", new_callable=AsyncMock) as attempt,
    ):
        result = await _trigger(tmp_path, adapter, AsyncMock())

    assert (result.error or "").startswith(CEILING_DENIAL_REASON_PREFIX)
    assert attempt.await_args.kwargs["produce_fleet_halt"] is True
