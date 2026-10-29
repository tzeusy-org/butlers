"""Regression coverage for the public optional-SimpleFIN tool descriptions."""

from __future__ import annotations

import inspect

from fastmcp import FastMCP

from butlers.modules._roster_finance.tools import register_tools
from butlers.testing.mcp import registered_tools


class _FakeModule:
    """Minimal module stand-in; these assertions only read tool metadata."""

    def _get_pool(self):  # pragma: no cover - tool wrappers are not invoked
        raise AssertionError("tool description test must not acquire a pool")


async def _registered_tools() -> dict[str, object]:
    mcp = FastMCP()
    register_tools(mcp, _FakeModule(), config=None)
    return await registered_tools(mcp)


async def test_feed_reconciliation_descriptions_cover_optional_simplefin_degradation() -> None:
    """Public tool descriptions distinguish an optional feed from an all-clear."""
    tools = await _registered_tools()
    reconcile_description = inspect.getdoc(tools["reconcile_feed_vs_email"]) or ""
    freshness_description = inspect.getdoc(tools["account_feed_freshness"]) or ""
    normalized_reconcile_description = " ".join(reconcile_description.split())
    normalized_freshness_description = " ".join(freshness_description.split())

    assert "optional SimpleFIN" in reconcile_description
    assert "successful aggregator sync" in reconcile_description
    assert "persisted completed feed-sync evidence" in normalized_reconcile_description
    assert "current credential or configuration" in normalized_reconcile_description
    assert "no credential or configuration" not in reconcile_description
    assert "configured=false" in reconcile_description

    assert "optional SimpleFIN" in freshness_description
    assert "persisted completed feed-sync evidence" in normalized_freshness_description
    assert "current credential or configuration" in normalized_freshness_description
    assert "never_synced" in freshness_description
    assert "stale" in freshness_description
    assert "every account reports" not in freshness_description
