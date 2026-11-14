"""No import route from the public MCP server to a model (REQ-public-mcp-surface-001; RFC 0039 D14).

Two checks over the three packages the server is built from:

* statically, an AST walk of every ``butlers.*`` module they reach, through every import
  statement (function-local ones included, ``if TYPE_CHECKING:`` blocks excluded), every
  ``from pkg import submodule`` and every parent package an import executes. A banned module
  fails with the chain that reaches it, and any ``butlers.*`` module outside the pinned
  closure fails, so a new butler client or runtime path forces review here;
* at runtime, importing the entrypoint loads no banned module and no ``butlers.*`` module
  the static walk did not see.

``butlers.core.runtime_probe_control``'s package ``__init__`` re-exports nothing, so the host
reaching the assertion codec (``capability``, ``keys``) loads neither the probe client nor the
coordinator, whose function-local imports reach the spawner and runtimes.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SRC = Path(__file__).resolve().parents[2] / "src"
ROOTS = ("butlers.mcp_public", "butlers.capability_host", "butlers.knowledge_read")

#: D14 item 1, widened: no spawner, runtime, model SDK, embedding or memory search/recall, no
#: session host or goal runner, no Switchboard/route/notify/delegate client, no daemon, API or
#: module code, and no butlers module that opens an HTTP client.
BANNED = (
    "butlers.core.spawner",
    "butlers.core.spawn_hooks",
    "butlers.core.runtimes",
    "butlers.core.model_call",
    "butlers.core.model_routing",
    "butlers.core.route_inbox",
    "butlers.core.delegation_ledger",
    "butlers.core.delegation_wake",
    "butlers.core.notify_attachments",
    "butlers.core.service_plane",
    "butlers.core.runtime_probe_control.client",
    "butlers.core.runtime_probe_control.coordinator",
    "butlers.core.runtime_probe_control.activation",
    "butlers.core_tools",
    "butlers.modules",
    "butlers.session_host",
    "butlers.goal_runner",
    "butlers.jobs",
    "butlers.daemon",
    "butlers.api",
    "butlers.connectors",
    "butlers.switchboard_wiring",
    "anthropic",
    "openai",
    "google.genai",
    "claude_agent_sdk",
    "sentence_transformers",
    "transformers",
    "torch",
)
#: Third-party HTTP clients no butlers module in the closure may import (the MCP SDK and the
#: ASGI server import httpx themselves; that is transport, not a route to a butler).
BANNED_THIRD_PARTY = ("httpx", "aiohttp", "requests", "urllib3")

#: The whole butlers.* closure. Adding a module here is a review decision: it must not reach
#: a model, a butler or the network.
PINNED_CLOSURE = frozenset(
    {
        "butlers",
        "butlers.capability_host",
        "butlers.capability_host.assertion",
        "butlers.capability_host.budgets",
        "butlers.capability_host.config",
        "butlers.capability_host.dlp",
        "butlers.capability_host.host",
        "butlers.capability_host.observe",
        "butlers.capability_host.projection",
        "butlers.core",
        "butlers.core.runtime_probe_control",
        "butlers.core.runtime_probe_control.capability",
        "butlers.core.runtime_probe_control.keys",
        "butlers.knowledge_read",
        "butlers.knowledge_read.chronology",
        "butlers.knowledge_read.core",
        "butlers.knowledge_read.describe",
        "butlers.knowledge_read.entities",
        "butlers.knowledge_read.finance",
        "butlers.knowledge_read.graph",
        "butlers.knowledge_read.health",
        "butlers.knowledge_read.manifest",
        "butlers.knowledge_read.records",
        "butlers.knowledge_read.search",
        "butlers.knowledge_read.travel",
        "butlers.mcp_public",
        "butlers.mcp_public.app",
        "butlers.mcp_public.catalogue",
        "butlers.mcp_public.server",
    }
)


def _banned(name: str, banned: tuple[str, ...] = BANNED) -> bool:
    return any(name == b or name.startswith(b + ".") for b in banned)


def module_file(src: Path, name: str) -> Path | None:
    base = src.joinpath(*name.split("."))
    for path in (base.with_suffix(".py"), base / "__init__.py"):
        if path.is_file():
            return path
    return None


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _nodes(tree: ast.AST):
    """Every node, skipping the bodies of ``if TYPE_CHECKING:`` (never executed)."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.If) and _is_type_checking(child.test):
                stack.extend(child.orelse)
            else:
                stack.append(child)


def imports_of(src: Path, name: str) -> set[str]:
    """Absolute names of everything module ``name`` may import, parent packages included."""
    path = module_file(src, name)
    assert path is not None, name
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    for node in _nodes(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            found.add(target)
            for alias in node.names:
                if module_file(src, f"{target}.{alias.name}") is not None:
                    found.add(f"{target}.{alias.name}")
    with_parents = set()
    for item in found:
        parts = item.split(".")
        with_parents.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return with_parents


def walk(src: Path, roots: tuple[str, ...]) -> tuple[dict[str, str | None], set[str]]:
    """``(parent map over the butlers.* closure, third-party names it imports)``."""
    parent: dict[str, str | None] = {}
    queue: deque[str] = deque()
    for root in roots:
        for path in sorted(src.joinpath(*root.split(".")).rglob("*.py")):
            rel = path.relative_to(src).with_suffix("")
            name = ".".join(rel.parts[:-1] if rel.name == "__init__" else rel.parts)
            parent.setdefault(name, None)
            queue.append(name)
    third_party: set[str] = set()
    top = roots[0].split(".")[0]
    while queue:
        name = queue.popleft()
        for target in sorted(imports_of(src, name)):
            if target.split(".")[0] != top:
                third_party.add(target)
            elif target not in parent and module_file(src, target) is not None:
                parent[target] = name
                queue.append(target)
    return parent, third_party


def chain(parent: dict[str, str | None], name: str) -> str:
    links = [name]
    while (up := parent[links[-1]]) is not None:
        links.append(up)
    return " -> ".join(reversed(links))


def violations(src: Path, roots: tuple[str, ...]) -> list[str]:
    parent, third_party = walk(src, roots)
    found = [f"banned: {chain(parent, m)}" for m in sorted(parent) if _banned(m)]
    found += [f"banned third-party import: {m}" for m in sorted(third_party) if _banned(m)]
    found += [
        f"HTTP client in the closure: {m}"
        for m in sorted(third_party)
        if _banned(m, BANNED_THIRD_PARTY)
    ]
    return found


# -- the contract ------------------------------------------------------------------------


def test_no_package_reaches_a_banned_module():
    assert violations(SRC, ROOTS) == []


def test_the_closure_is_exactly_the_pinned_set():
    parent, _ = walk(SRC, ROOTS)
    assert set(parent) == PINNED_CLOSURE, {
        "unexpected": sorted(set(parent) - PINNED_CLOSURE),
        "missing": sorted(PINNED_CLOSURE - set(parent)),
    }


def test_importing_the_entrypoint_loads_no_banned_module():
    code = (
        "import sys, json\n"
        "import butlers.mcp_public.server, butlers.mcp_public.app\n"
        "from butlers.mcp_public.catalogue import catalogue, tools_list_result\n"
        "catalogue(); tools_list_result()\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=True
    )
    loaded = json.loads(out.stdout)
    assert [m for m in loaded if _banned(m)] == []
    parent, _ = walk(SRC, ROOTS)
    assert sorted(m for m in loaded if m.split(".")[0] == "butlers" and m not in parent) == []


# -- the walker itself catches every route (synthetic tree) -------------------------------


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        pytest.param(
            {"butlers/knowledge_read/a.py": "from butlers.util import x\n"},
            "butlers.knowledge_read.a -> butlers.util -> butlers.core.spawner",
            id="transitive",
        ),
        pytest.param(
            {"butlers/knowledge_read/a.py": "def f():\n    import butlers.util\n"},
            "butlers.knowledge_read.a -> butlers.util -> butlers.core.spawner",
            id="function-local",
        ),
        pytest.param(
            {"butlers/knowledge_read/a.py": "from ..core import spawner\n"},
            "butlers.knowledge_read.a -> butlers.core.spawner",
            id="relative-submodule",
        ),
        pytest.param(
            {"butlers/knowledge_read/a.py": "import butlers.pkg.leaf\n"},
            "butlers.knowledge_read.a -> butlers.pkg -> butlers.util -> butlers.core.spawner",
            id="parent-package-init",
        ),
    ],
)
def test_the_walker_names_the_chain(tmp_path, files, expected):
    src = _tree(
        tmp_path,
        {
            "butlers/__init__.py": "",
            "butlers/knowledge_read/__init__.py": "",
            "butlers/core/__init__.py": "",
            "butlers/core/spawner.py": "",
            "butlers/util.py": "import butlers.core.spawner\n",
            "butlers/pkg/__init__.py": "from butlers import util\n",
            "butlers/pkg/leaf.py": "",
            **files,
        },
    )
    assert f"banned: {expected}" in violations(src, ("butlers.knowledge_read",))


def test_the_walker_ignores_type_checking_imports(tmp_path):
    src = _tree(
        tmp_path,
        {
            "butlers/__init__.py": "",
            "butlers/knowledge_read/__init__.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    import butlers.core.spawner\n"
            ),
            "butlers/core/__init__.py": "",
            "butlers/core/spawner.py": "",
        },
    )
    assert violations(src, ("butlers.knowledge_read",)) == []


def test_the_walker_flags_a_model_sdk_and_an_http_client(tmp_path):
    src = _tree(
        tmp_path,
        {
            "butlers/__init__.py": "",
            "butlers/knowledge_read/__init__.py": "import anthropic\nimport httpx\n",
        },
    )
    assert violations(src, ("butlers.knowledge_read",)) == [
        "banned third-party import: anthropic",
        "HTTP client in the closure: httpx",
    ]
