"""bu-ly3lv5.14: connectors reach Switchboard's ``ingest`` tool only through ``submit_ingest``.

A direct ``call_tool("ingest", ...)`` skips the status check, so a ``status: error`` reply counts
as success. This scans real call expressions with ``ast`` (a docstring or comment that merely
mentions the call does not trip it) and needs no DB.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CONNECTORS = Path(__file__).resolve().parents[2] / "src" / "butlers" / "connectors"
_HELPER = _CONNECTORS / "ingest_submit.py"


def _is_ingest_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "call_tool"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "ingest"
    )


def direct_ingest_calls(source: str) -> list[int]:
    return [node.lineno for node in ast.walk(ast.parse(source)) if _is_ingest_call(node)]


def test_no_connector_calls_the_ingest_tool_directly() -> None:
    offenders = {
        str(path.relative_to(_CONNECTORS)): lines
        for path in sorted(_CONNECTORS.rglob("*.py"))
        if path != _HELPER and (lines := direct_ingest_calls(path.read_text(encoding="utf-8")))
    }

    assert offenders == {}, (
        "Submit through butlers.connectors.ingest_submit.submit_ingest(); a direct "
        f'call_tool("ingest", ...) ignores a status: error reply: {offenders}'
    )


def test_the_helper_is_the_one_place_that_does() -> None:
    assert len(direct_ingest_calls(_HELPER.read_text(encoding="utf-8"))) == 1


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('await client.call_tool("ingest", env)', 1),
        ('await self._mcp_client.call_tool(\n    "ingest", env\n)', 1),
        ('"""call_tool("ingest", ...) in a docstring"""', 0),
        ('# call_tool("ingest", env)\nx = 1', 0),
        ('await client.call_tool("notify", env)', 0),
    ],
)
def test_the_scan_sees_real_calls_only(source: str, expected: int) -> None:
    assert len(direct_ingest_calls(source)) == expected
