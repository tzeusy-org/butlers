"""Regression coverage for the public optional-SimpleFIN tool descriptions."""

from __future__ import annotations

import inspect

from butlers.modules._roster_finance.tools import register_tools


class _FakeMCP:
    """Capture registered tool closures without starting an MCP server."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


class _FakeModule:
    """Minimal module stand-in; these assertions only read tool metadata."""

    def _get_pool(self):  # pragma: no cover - tool wrappers are not invoked
        raise AssertionError("tool description test must not acquire a pool")


def _registered_tools() -> dict[str, object]:
    mcp = _FakeMCP()
    register_tools(mcp, _FakeModule(), config=None)
    return mcp.tools


def test_feed_reconciliation_descriptions_cover_optional_simplefin_degradation() -> None:
    """Public tool descriptions distinguish an optional feed from an all-clear."""
    tools = _registered_tools()
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
