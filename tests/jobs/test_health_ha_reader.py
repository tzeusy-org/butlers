"""Tests for butlers.jobs.health_ha_reader — HaEnvironmentReader factory.

Verifies that:
1. ``build_ha_environment_reader`` always returns ``None`` -- health has no
   HA environment-entity source to build a working reader from.
2. ``_run_health_insight_scan_job`` in scheduled_jobs.py wires whatever the
   factory returns into ``run_insight_scan`` (reader present or ``None``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.jobs.health_ha_reader import build_ha_environment_reader

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# build_ha_environment_reader — always unavailable for health
# ---------------------------------------------------------------------------


async def test_build_ha_environment_reader_returns_none():
    """No HA entity-snapshot source exists for health; always returns None."""
    pool = MagicMock()

    result = await build_ha_environment_reader(pool)

    assert result is None


async def test_build_ha_environment_reader_ignores_pool_contents():
    """The pool argument is unused -- result does not depend on its contents."""
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=AssertionError("pool should not be queried"))

    result = await build_ha_environment_reader(pool)

    assert result is None
    pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# _run_health_insight_scan_job dispatch wiring
# Proves (a) the factory's return value is injected into run_insight_scan and
# (b) a None reader is passed through cleanly when HA is unavailable.
# ---------------------------------------------------------------------------


async def test_dispatch_injects_reader_when_factory_returns_callable():
    """_run_health_insight_scan_job passes a non-None reader through unchanged."""
    from butlers.scheduled_jobs import _run_health_insight_scan_job

    pool = MagicMock()

    async def fake_reader():
        return []

    mock_mod = MagicMock()
    mock_mod.run_insight_scan = AsyncMock(return_value={"scanned": 0})

    with (
        patch(
            "butlers.jobs.health_ha_reader.build_ha_environment_reader",
            new=AsyncMock(return_value=fake_reader),
        ),
        patch(
            "butlers.jobs._roster_loader.load_roster_jobs",
            return_value=mock_mod,
        ),
    ):
        await _run_health_insight_scan_job(pool, None)

    mock_mod.run_insight_scan.assert_awaited_once()
    call_kwargs = mock_mod.run_insight_scan.call_args
    assert call_kwargs.kwargs.get("ha_environment_reader") is fake_reader


async def test_dispatch_passes_none_reader_when_ha_absent():
    """_run_health_insight_scan_job passes None reader when HA is not configured.

    Environment correlation is skipped cleanly -- the real-world default,
    since build_ha_environment_reader always returns None for health.
    """
    from butlers.scheduled_jobs import _run_health_insight_scan_job

    pool = MagicMock()

    mock_mod = MagicMock()
    mock_mod.run_insight_scan = AsyncMock(return_value={"scanned": 0})

    with (
        patch(
            "butlers.jobs.health_ha_reader.build_ha_environment_reader",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.jobs._roster_loader.load_roster_jobs",
            return_value=mock_mod,
        ),
    ):
        await _run_health_insight_scan_job(pool, None)

    mock_mod.run_insight_scan.assert_awaited_once()
    call_kwargs = mock_mod.run_insight_scan.call_args
    assert call_kwargs.kwargs.get("ha_environment_reader") is None
