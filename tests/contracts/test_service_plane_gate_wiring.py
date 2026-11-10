"""The Messenger delivery gate is wired at both ends (RFC 0038 D7, bu-2c1g7v).

The gate's behaviour is tested in ``tests/core/test_service_plane_messenger_delivery.py``.
These tests fail if either call site is deleted or moved past the point it guards:

- Messenger's ``route.execute`` must consult ``admit_delivery_request`` and
  return its refusal before the Messenger-only inline delivery branch;
- Switchboard's ``route()`` must merge ``messenger_delivery_headers`` into the
  headers of the transport it delivers with.
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _function(path: str, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    tree = ast.parse((_ROOT / path).read_text(encoding="utf-8"))
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    )


def _calls(function: ast.AST, callee: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == callee
    ]


def test_messenger_route_execute_refuses_through_the_gate_before_inline_delivery() -> None:
    inner = _function("src/butlers/core_tools/_routing.py", "_route_execute_inner")
    (gate,) = _calls(inner, "admit_delivery_request")
    messenger_branch = next(
        node
        for node in ast.walk(inner)
        # The branch that sends every non-Messenger request to route_inbox and keeps
        # Messenger's inline delivery below it.
        if isinstance(node, ast.Compare)
        and ast.unparse(node) == "daemon.config.name != 'messenger'"
    )
    # The verdict is bound, tested, and a refusal returns.
    bound = next(
        node
        for node in ast.walk(inner)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Await)
        and node.value.value is gate
    )
    (name,) = [t.id for t in bound.targets if isinstance(t, ast.Name)]
    guard = next(
        node
        for node in ast.walk(inner)
        if isinstance(node, ast.If)
        and any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node.test))
    )

    assert any(isinstance(n, ast.Return) for n in ast.walk(guard))
    assert gate.lineno < messenger_branch.lineno


def test_switchboard_route_sends_the_delivery_headers_on_its_transport() -> None:
    route = _function("roster/switchboard/tools/routing/route.py", "route")
    (headers_call,) = _calls(route, "messenger_delivery_headers")
    transports = _calls(route, "StreamableHttpTransport")
    merged = next(
        node
        for node in ast.walk(route)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "headers" for t in node.targets)
        and any(n is headers_call for n in ast.walk(node.value))
    )

    assert transports, "route() no longer builds a header-bearing transport"
    assert all(
        any(k.arg == "headers" for k in t.keywords) and merged.lineno < t.lineno for t in transports
    )
