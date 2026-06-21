"""Unit test: the compose_bills_digest MCP tool wires the skill to the tested fn.

The upcoming-bills-check skill must build its digest by calling the
``compose_bills_digest`` MCP tool, which delegates to the tested
``compose_upcoming_bills_digest()`` in ``bills.py`` — the single source of truth.
This guards against the skill regressing to inline prose composition.

No Docker / DB needed: the tool wrapper is pure (it never touches the pool).
"""

from __future__ import annotations

from datetime import date

from butlers.modules._roster_finance.tools import register_tools
from butlers.tools.finance.bills import compose_upcoming_bills_digest


class _FakeMCP:
    """Captures registered tool closures by name."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


class _FakeModule:
    """Module stub — compose_bills_digest never calls _get_pool()."""

    def _get_pool(self):  # pragma: no cover - must not be invoked
        raise AssertionError("compose_bills_digest must not touch the pool")


def _register() -> dict:
    mcp = _FakeMCP()
    register_tools(mcp, _FakeModule(), config=None)
    return mcp.tools


def test_compose_bills_digest_tool_is_registered():
    tools = _register()
    assert "compose_bills_digest" in tools


async def test_tool_delegates_to_tested_function():
    """The tool returns exactly what compose_upcoming_bills_digest() produces."""
    tools = _register()
    sweep = {
        "auto_settled": [{"payee": "HSBC", "amount": "45.00", "paid_at": "2026-06-20"}],
        "candidates": [],
    }
    bills = {
        "needs_action": [
            {
                "bill": {"payee": "Landlord", "amount": "1500.00"},
                "urgency": "due_soon",
                "days_until_due": 5,
            }
        ],
        "autopay": [],
        "predicted": [],
        "totals": {"needs_action_amount": "1500.00"},
    }
    predictions = {"predictions": []}

    result = await tools["compose_bills_digest"](sweep=sweep, bills=bills, predictions=predictions)
    expected = compose_upcoming_bills_digest(sweep, bills, predictions)

    assert result == {"message": expected}
    assert result["message"] is not None
    assert "HSBC" in result["message"]
    assert "Landlord" in result["message"]


async def test_tool_passes_through_early_exit_none():
    """When nothing is worth sending, the tool returns message=None (early exit)."""
    tools = _register()
    empty_sweep = {"auto_settled": [], "candidates": []}
    empty_bills = {"needs_action": [], "autopay": [], "predicted": [], "totals": {}}
    empty_predictions = {"predictions": []}

    result = await tools["compose_bills_digest"](
        sweep=empty_sweep, bills=empty_bills, predictions=empty_predictions
    )
    assert result == {"message": None}


def test_tool_format_matches_function_with_today():
    """Sanity: the tested function still drives the format (date header)."""
    sweep = {"auto_settled": [], "candidates": []}
    bills = {
        "needs_action": [],
        "autopay": [{"bill": {"payee": "Netflix", "amount": "15.49"}}],
        "predicted": [],
        "totals": {},
    }
    predictions = {"predictions": []}
    msg = compose_upcoming_bills_digest(sweep, bills, predictions, today=date(2026, 6, 21))
    assert msg is not None
    assert msg.startswith("Bills — 21 Jun 2026")
    assert "Netflix" in msg
