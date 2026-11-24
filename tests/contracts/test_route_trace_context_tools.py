"""route() adds its trace carrier to exactly the tools that declare it (bu-dhloi1.293).

route() injects ``trace_context`` while a span is active. A tool whose
signature lacks that parameter refuses the call, so route() adds it only to the
tools listed in ``TRACE_CONTEXT_TOOLS``. This scan keeps the list exact: every
decorated function in ``src/`` and ``roster/`` with a ``trace_context``
parameter is listed under its registered name, and every listed name has one.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]


def _registered_name(node: ast.AsyncFunctionDef | ast.FunctionDef) -> str:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    return str(keyword.value.value)
    return node.name


def _declaring_tools() -> set[str]:
    names: set[str] = set()
    for base in ("src", "roster"):
        for path in (_ROOT / base).rglob("*.py"):
            if "tests" in path.relative_to(_ROOT).parts:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                    continue
                if not node.decorator_list:
                    continue
                params = node.args.args + node.args.kwonlyargs
                if any(arg.arg == "trace_context" for arg in params):
                    names.add(_registered_name(node))
    return names


def test_the_carrier_list_is_exactly_the_tools_that_declare_it() -> None:
    routing = importlib.import_module("butlers.tools.switchboard.routing.route")

    assert _declaring_tools() == set(routing.TRACE_CONTEXT_TOOLS)
