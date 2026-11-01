"""Every owner-link write sits in one transaction with its episode upsert (bu-qp92dv).

``upsert_owner_episode_entity`` is a plain execute. An adapter that calls it outside
``async with owner_episode_transaction(conn):`` (or with no episode upsert in that block)
can commit an episode without its owner link. This source scan fails on such a call.
The helper itself is also exercised here without a database.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from butlers.chronicler.adapters._owner_entity import owner_episode_transaction

pytestmark = pytest.mark.unit

_ADAPTERS = Path(__file__).resolve().parents[2] / "src" / "butlers" / "chronicler" / "adapters"
_HELPER = "owner_episode_transaction"
_OWNER_CALL = "upsert_owner_episode_entity"
_EPISODE_CALL = "upsert_episode"


def _callee(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _calls_in(nodes: list[ast.stmt]) -> list[ast.Call]:
    return [c for n in nodes for c in ast.walk(n) if isinstance(c, ast.Call)]


def _violations(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    guarded: set[int] = set()
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncWith):
            continue
        if not any(
            isinstance(item.context_expr, ast.Call) and _callee(item.context_expr) == _HELPER
            for item in node.items
        ):
            continue
        calls = _calls_in(node.body)
        if any(_callee(c) == _EPISODE_CALL for c in calls):
            guarded.update(id(c) for c in calls if _callee(c) == _OWNER_CALL)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) == _OWNER_CALL and id(node) not in guarded:
            found.append(f"{label}:{node.lineno}")
    return found


def test_every_adapter_owner_link_shares_a_transaction_with_its_episode_upsert():
    scanned = 0
    violations: list[str] = []
    for path in sorted(_ADAPTERS.glob("*.py")):
        if path.name == "_owner_entity.py":
            continue
        source = path.read_text(encoding="utf-8")
        if _OWNER_CALL in source:
            scanned += 1
        violations += _violations(source, path.name)
    assert scanned >= 15, "the scan must actually see the owner-only adapters"
    assert violations == []


def test_the_scan_flags_an_unwrapped_or_episodeless_owner_link():
    unwrapped = "async def f(conn):\n    await upsert_owner_episode_entity(conn, 1, owner_id=2)\n"
    assert _violations(unwrapped, "x") == ["x:2"]

    episodeless = (
        "async def f(conn):\n"
        "    async with owner_episode_transaction(conn):\n"
        "        await upsert_owner_episode_entity(conn, 1, owner_id=2)\n"
    )
    assert _violations(episodeless, "x") == ["x:3"]

    ok = (
        "async def f(conn):\n"
        "    async with owner_episode_transaction(conn):\n"
        "        ep = await upsert_episode(conn, e)\n"
        "        await upsert_owner_episode_entity(conn, ep.id, owner_id=2)\n"
    )
    assert _violations(ok, "x") == []


async def test_helper_runs_the_block_inside_the_connection_transaction():
    events: list[str] = []

    class _Tx:
        async def __aenter__(self):
            events.append("begin")

        async def __aexit__(self, *exc):
            events.append("rollback" if exc[0] else "commit")

    conn = MagicMock()
    conn.transaction = MagicMock(return_value=_Tx())

    async with owner_episode_transaction(conn):
        events.append("body")
    with pytest.raises(RuntimeError):
        async with owner_episode_transaction(conn):
            raise RuntimeError("link failed")
    assert events == ["begin", "body", "commit", "begin", "rollback"]
