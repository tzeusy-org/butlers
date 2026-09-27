"""Shared catalog selection fixture for Spawner behavior tests."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.runtimes import DEFAULT_RUNTIME_TYPE


@pytest.fixture
def spawner_catalog_candidate():
    """Give non-routing Spawner tests a real catalog-shaped selection by default."""
    with patch(
        "butlers.core.spawner.resolve_model_with_effective_tier",
        new_callable=AsyncMock,
        return_value=(
            DEFAULT_RUNTIME_TYPE,
            "test-catalog-model",
            [],
            uuid.UUID("00000000-0000-0000-0000-000000000098"),
            1800,
            "workhorse",
        ),
    ):
        yield
