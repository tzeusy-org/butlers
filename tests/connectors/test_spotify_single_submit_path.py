"""Spotify has one path to Switchboard: it writes evidence for the envelope, then submits.

A new notification kind that bypasses `_submit_envelope`, or calls it without an
evidence writer, fails here before it can acknowledge anything it did not record (bu-uhbhzq.4).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from butlers.connectors import spotify

_SOURCE = Path(inspect.getsourcefile(spotify) or "").read_text()
_TREE = ast.parse(_SOURCE)


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner[id(node)] = fn.name
    return owner


def test_the_ingest_tool_is_called_from_exactly_one_method() -> None:
    owner = _enclosing_functions(_TREE)
    calls = [
        owner.get(id(node))
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "submit_ingest"
    ]
    assert calls == ["_submit_envelope"]


def test_every_submit_names_its_evidence_writer() -> None:
    sites = [
        node
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_submit_envelope"
    ]
    assert sites, "the choke point has callers"
    for call in sites:
        assert "evidence" in {kw.arg for kw in call.keywords}, f"line {call.lineno}"


def test_evidence_is_written_before_the_ingest_call_in_the_choke_point() -> None:
    fn = next(
        n
        for n in ast.walk(_TREE)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_submit_envelope"
    )
    lines = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            lines.setdefault(node.func.id, node.lineno)
    assert lines["evidence"] < lines["submit_ingest"]


def test_submit_deliveries_go_through_the_delivery_wrapper() -> None:
    """Notification kinds call `_deliver` (hold and retry); only it and replay call the gate."""
    owner = _enclosing_functions(_TREE)
    callers = {
        owner.get(id(node))
        for node in ast.walk(_TREE)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_submit_envelope"
    }
    assert callers == {"_deliver", "_submit_to_ingest_direct"}
