"""The registered finance MCP wrappers agree with the functions they delegate to (bu-vxnpx5).

``alert_configure`` passed ``type=`` to a function that takes ``alert_type``,
and ``spending_trends`` defaulted ``comparison`` to ``"month_over_month"``,
which the function refuses (it takes ``mom``/``yoy``). Both wrappers failed on
every call made with their defaults. These tests call the registered wrapper,
and a static sweep binds every ``_module.fn(module._get_pool(), ...)`` call in
the wrapper module against the real signature, so the same drift elsewhere fails here.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from decimal import Decimal
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from fastmcp import FastMCP

from butlers.modules._roster_finance.tools import register_tools
from butlers.testing.mcp import registered_tools
from butlers.tools.finance import alerts as _alerts
from butlers.tools.finance import anomaly_detection as _anomaly
from butlers.tools.finance import budgets as _budgets

pytestmark = pytest.mark.unit

TOOLS = Path(__file__).resolve().parents[1] / "modules" / "tools.py"


class _Unreachable(Exception):
    """Raised by the fake pool: the wrapper got past argument validation."""


class _Pool:
    def __getattr__(self, name):
        raise _Unreachable(name)


class _Module:
    def __init__(self, pool):
        self.pool = pool

    def _get_pool(self):
        return self.pool


async def _tools(pool=None) -> dict:
    mcp = FastMCP()
    register_tools(mcp, _Module(pool or _Pool()), config=None)
    return await registered_tools(mcp)


async def _param_schema(tool: str, param: str) -> dict:
    """The published MCP input schema of one wrapper parameter."""
    mcp = FastMCP()
    register_tools(mcp, _Module(_Pool()), config=None)
    registered = await mcp.get_tool(tool)
    assert registered is not None
    return registered.parameters["properties"][param]


async def test_alert_configure_passes_the_alert_type_the_function_takes(monkeypatch):
    spy = create_autospec(_alerts.alert_configure, return_value={"type": "large_transaction"})
    monkeypatch.setattr(_alerts, "alert_configure", spy)
    pool = _Pool()
    tools = await _tools(pool)
    await tools["alert_configure"](type="large_transaction", threshold=250.0)
    spy.assert_awaited_once_with(
        pool, alert_type="large_transaction", threshold=250.0, currency="USD", enabled=True
    )


async def test_spending_trends_defaults_to_a_comparison_the_function_accepts():
    tools = await _tools()
    # Validation passes and the real function reaches the pool (refused by the fake).
    with pytest.raises(_Unreachable):
        await tools["spending_trends"]()
    for comparison in ("mom", "yoy"):
        with pytest.raises(_Unreachable):
            await tools["spending_trends"](comparison=comparison)
    with pytest.raises(ValueError, match="mom"):
        await tools["spending_trends"](comparison="month_over_month")
    schema = await _param_schema("spending_trends", "comparison")
    assert (set(schema["enum"]), schema["default"]) == ({"mom", "yoy"}, "mom")


async def test_anomaly_scan_takes_the_sensitivity_levels_the_function_reads():
    """The function reads high/medium/low and silently treats anything else as medium.

    The wrapper took a float stddev multiplier (default 2.5) that was always ignored, and
    its schema refused the anomaly-triage skill's ``sensitivity="medium"``.
    """
    schema = await _param_schema("anomaly_scan", "sensitivity")
    assert (
        set(schema["enum"]) == set(_anomaly._SENSITIVITY_MULTIPLIERS) == {"high", "medium", "low"}
    )
    assert schema["default"] == "medium"


def _delegations():
    """Each ``_alias.fn(module._get_pool() | pool, ...)`` call in the wrapper module."""
    tree = ast.parse(TOOLS.read_text())
    aliases = {
        alias.asname or alias.name: f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "butlers.tools.finance"
        for alias in node.names
    }
    wrappers = {
        node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    }
    for wrapper in wrappers.values():
        for call in ast.walk(wrapper):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in aliases
                and call.args
                and ast.unparse(call.args[0]) in ("module._get_pool()", "pool")
            ):
                continue
            target = getattr(importlib.import_module(aliases[call.func.value.id]), call.func.attr)
            yield wrapper, call, target


DELEGATIONS = list(_delegations())

# A function default of None that the function replaces with a module constant:
# the wrapper's literal default must equal that constant.
EFFECTIVE_DEFAULTS = {
    ("budget_set", "warn_threshold"): _budgets.DEFAULT_WARN_THRESHOLD,
    ("budget_set", "alert_threshold"): _budgets.DEFAULT_ALERT_THRESHOLD,
}


def test_the_sweep_sees_the_wrappers():
    names = {wrapper.name for wrapper, _, _ in DELEGATIONS}
    assert {"alert_configure", "spending_trends", "record_transaction"} <= names
    assert len(DELEGATIONS) >= 40


@pytest.mark.parametrize(
    ("wrapper", "call", "target"),
    DELEGATIONS,
    ids=[f"{wrapper.name}->{target.__name__}" for wrapper, _, target in DELEGATIONS],
)
def test_every_delegation_binds_and_keeps_the_function_defaults(wrapper, call, target):
    signature = inspect.signature(target)
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    # Every keyword the wrapper passes is one the function takes (no **kwargs splat).
    assert all(kw.arg is not None for kw in call.keywords)
    signature.bind(*([None] * len(call.args)), **dict.fromkeys(keywords))
    # A wrapper param passed straight through keeps the function's literal default,
    # so calling the wrapper with its defaults never sends a value the function refuses.
    positional = wrapper.args.args[len(wrapper.args.args) - len(wrapper.args.defaults) :]
    defaults = {
        arg.arg: default.value
        for arg, default in [
            *zip(positional, wrapper.args.defaults, strict=True),
            *zip(wrapper.args.kwonlyargs, wrapper.args.kw_defaults, strict=True),
        ]
        if isinstance(default, ast.Constant)
    }
    for name, value in keywords.items():
        passed = value.id if isinstance(value, ast.Name) else None
        parameter = signature.parameters[name]
        if passed in defaults and parameter.default is not inspect.Parameter.empty:
            if (target.__name__, name) in EFFECTIVE_DEFAULTS and parameter.default is None:
                assert Decimal(str(defaults[passed])) == EFFECTIVE_DEFAULTS[target.__name__, name]
                continue
            assert defaults[passed] == parameter.default, (
                f"{wrapper.name}({passed}={defaults[passed]!r}) -> "
                f"{target.__name__}({name}={parameter.default!r})"
            )
