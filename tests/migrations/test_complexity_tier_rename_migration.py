"""Regression checks for the complexity-tier rename migration."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_upgrade_drops_old_checks_before_writing_new_tiers() -> None:
    """Old check constraints reject the new canonical tier values."""
    source = Path("alembic/versions/core/core_093_complexity_tier_rename.py").read_text()

    assert source.index("DROP CONSTRAINT IF EXISTS chk_model_catalog_complexity_tier") < source.index(
        "UPDATE public.model_catalog"
    )
    assert source.index(
        "DROP CONSTRAINT IF EXISTS chk_butler_model_overrides_complexity_tier"
    ) < source.index("UPDATE public.butler_model_overrides")
    assert source.index("DROP CONSTRAINT IF EXISTS chk_rr_complexity_tier") < source.index(
        "INSERT INTO public.model_round_robin_counters"
    )
