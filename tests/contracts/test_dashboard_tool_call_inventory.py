"""Every dashboard butler-tool call site is inventoried and classified (RFC 0038 D7).

The scan reads the dashboard source with ``ast``: every ``.call_tool(`` /
``.call_tool_mcp(`` call under ``src/butlers/api`` and ``roster/*/api``. A
literal tool name (or a module-level string constant) is resolved and its
class must agree with the tool's presentation declaration; a variable tool is
``runtime_classified``, or ``owner_capability`` when the site passes ``meta``.
The scan also pins the client: the dashboard constructs exactly one MCP
client, ``DashboardButlerClient``, in ``MCPClientManager.get_client``.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from butlers.api.tool_call_inventory import DASHBOARD_TOOL_CALLS, DashboardToolCall
from butlers.core.service_plane.dashboard_call import routes_through_control_plane

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CALL_METHODS = frozenset({"call_tool", "call_tool_mcp"})
_CLIENT_NAMES = frozenset({"Client", "MCPClient", "LivenessClient", "DashboardButlerClient"})


def _dashboard_files() -> list[Path]:
    return sorted((_ROOT / "src/butlers/api").rglob("*.py")) + sorted(
        _ROOT.glob("roster/*/api/*.py")
    )


def _string_constants(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found.update({t.id: value.value for t in targets if isinstance(t, ast.Name)})
    return found


def _walk(node: ast.AST, scope: str) -> Iterator[tuple[str, ast.AST]]:
    for child in ast.iter_child_nodes(node):
        inner = scope
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            inner = f"{scope}.{child.name}" if scope else child.name
        yield inner, child
        yield from _walk(child, inner)


def _scan() -> tuple[list[tuple[DashboardToolCall, bool]], list[str], list[str]]:
    """(classified sites with their meta flag, client constructions, task= sites)."""
    sites: list[tuple[DashboardToolCall, bool]] = []
    clients: list[str] = []
    tasks: list[str] = []
    for path in _dashboard_files():
        relative = path.relative_to(_ROOT).as_posix()
        tree = ast.parse(path.read_text())
        constants = _string_constants(tree)
        for scope, node in _walk(tree, ""):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if callee in _CLIENT_NAMES:
                clients.append(f"{relative}:{scope}:{callee}")
            if callee not in _CALL_METHODS or not isinstance(func, ast.Attribute):
                continue
            if isinstance(func.value, ast.Call) and getattr(func.value.func, "id", "") == "super":
                continue  # the routing client's own delegation to the direct path
            keywords = {keyword.arg for keyword in node.keywords}
            if "task" in keywords:
                tasks.append(f"{relative}:{node.lineno}")
            first = node.args[0] if node.args else None
            tool: str | None = None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                tool = first.value
            elif isinstance(first, ast.Name):
                tool = constants.get(first.id)
            has_meta = "meta" in keywords
            if tool is not None:
                call_class = (
                    "control_plane" if routes_through_control_plane(tool) else "service_direct"
                )
                key = tool
            else:
                call_class = "owner_capability" if has_meta else "runtime_classified"
                key = ast.unparse(first) if first is not None else "?"
            sites.append((DashboardToolCall(relative, scope, key, call_class), has_meta))
    return sites, clients, tasks


def test_every_site_is_inventoried_with_the_class_its_declaration_implies() -> None:
    scanned, _, _ = _scan()

    found = Counter(site for site, _ in scanned)
    listed = Counter(DASHBOARD_TOOL_CALLS)
    assert found - listed == Counter(), "unclassified dashboard tool call sites"
    assert listed - found == Counter(), "inventoried sites that no longer exist or changed class"


def test_the_generic_mcp_call_proxy_has_an_explicit_class() -> None:
    (proxy,) = [
        site
        for site in DASHBOARD_TOOL_CALLS
        if site.path.endswith("routers/butlers.py") and site.function == "call_butler_mcp_tool"
    ]
    assert proxy.call_class == "runtime_classified"


def test_only_the_owner_capability_site_passes_meta() -> None:
    scanned, _, _ = _scan()

    with_meta = [site for site, has_meta in scanned if has_meta]
    assert [(s.path, s.function, s.call_class) for s in with_meta] == [
        ("src/butlers/api/routers/schedules.py", "_call_mcp_tool", "owner_capability")
    ]


def test_no_site_uses_the_task_path_that_bypasses_the_client_override() -> None:
    _, _, tasks = _scan()
    assert tasks == []


def test_the_dashboard_constructs_exactly_one_client_and_it_is_the_routing_one() -> None:
    _, clients, _ = _scan()
    assert clients == ["src/butlers/api/deps.py:MCPClientManager.get_client:DashboardButlerClient"]
