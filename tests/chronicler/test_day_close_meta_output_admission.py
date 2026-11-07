"""Shared read/write prose admission contains status output without rewriting it."""

import httpx
import pytest

from butlers.chronicler.prose_admission import INADMISSIBLE_PROSE, classify_prose_shape
from tests.chronicler.test_day_close_reader_api import _cache_row, _make_app, _mock_pool

pytestmark = pytest.mark.unit


def test_original_mcp_and_delivery_meta_output_is_not_retrospective():
    assert (
        classify_prose_shape(
            "Delivery deferred by quiet hours.\n\n**MCP tools called:** 1. chronicler_day_close_bundle"
        )
        == INADMISSIBLE_PROSE
    )
    assert (
        classify_prose_shape("**MCP tools called:**\n1. chronicler_day_close_bundle")
        == INADMISSIBLE_PROSE
    )
    assert classify_prose_shape("Delivery of the parcel made the afternoon easier.") is None


async def test_persisted_legacy_meta_is_contained_by_real_reader_without_rewriting():
    """REQ-chronicler-api-017: legacy read uses the same admission predicate."""
    pool = _mock_pool(
        fetchrow_side_effect=[
            _cache_row(prose="**MCP tools called:**\n1. chronicler_day_close_bundle")
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app(pool)), base_url="http://test"
    ) as client:
        response = await client.get("/api/chronicler/aggregate/day-close?date=2026-04-23&tz=UTC")
    assert response.status_code == 200
    payload = response.json()
    assert payload["invalid_reason"] == INADMISSIBLE_PROSE
    assert "prose" not in payload and "provenance_refs" not in payload
    assert pool.fetchrow.await_count == 1  # no staleness probe or generation
    pool.execute.assert_not_awaited()  # containment never destroys the audit row
