"""Shared QA test fixtures."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def no_paused_proposal(request):
    """Make Gate 5.6 see "no retained proposal" on mocked pools (bu-ejow5p.2).

    Mocked pools return a truthy ``fetchval``, which Gate 5.6 would read as a held
    fingerprint.  Tests on a real database exercise the real query instead.
    """
    if {"db_pool", "fresh_db", "migrated_core_postgres_pool"} & set(request.fixturenames):
        yield None
        return
    with patch(
        "butlers.core.qa.dispatch.get_paused_proposal",
        new_callable=AsyncMock,
        return_value=None,
    ) as paused:
        yield paused


@pytest.fixture(autouse=True)
def _no_paused_proposal(no_paused_proposal):
    return no_paused_proposal
