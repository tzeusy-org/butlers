"""A connector cannot bypass the result checks of the shared ingest submitter."""

import ast
from pathlib import Path

import pytest

from butlers.testing.source_guard import parse_file

pytestmark = pytest.mark.contract

_CONNECTORS = Path(__file__).resolve().parents[2] / "src" / "butlers" / "connectors"
_HELPER = _CONNECTORS / "ingest_submit.py"


def direct_ingest_calls(tree: ast.Module) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "call_tool"
        ):
            continue
        tool = (
            node.args[0]
            if node.args
            else next(
                (keyword.value for keyword in node.keywords if keyword.arg == "tool_name"),
                None,
            )
        )
        if isinstance(tool, ast.Constant) and tool.value == "ingest":
            lines.append(node.lineno)
    return lines


def test_submit_ingest_is_the_only_direct_connector_call() -> None:
    sources = sorted(_CONNECTORS.rglob("*.py"))
    assert _HELPER in sources
    offenders = {
        str(path.relative_to(_CONNECTORS)): lines
        for path in sources
        if path != _HELPER and (lines := direct_ingest_calls(parse_file(path)))
    }
    assert not offenders, f"Submit through butlers.connectors.ingest_submit: {offenders}"
    assert len(direct_ingest_calls(parse_file(_HELPER))) == 1


@pytest.mark.parametrize(
    ("source", "count"),
    [
        ('await client.call_tool("ingest", envelope)', 1),
        ('await client.call_tool(\n    "ingest", envelope\n)', 1),
        ('await client.call_tool(args=envelope, tool_name="ingest")', 1),
        ('"""call_tool("ingest", envelope)"""\nawait client.call_tool("notify", envelope)', 0),
    ],
)
def test_scan_matches_actual_ingest_calls(source, count):
    assert len(direct_ingest_calls(ast.parse(source))) == count
