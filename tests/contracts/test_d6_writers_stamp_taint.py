"""RFC 0038 D6: every writer of a taint-bearing store stamps the goal lineage (bu-pkuny2 AC4).

An exposed goal's write must carry ``taint_goal_id`` / ``taint_invocation_id``.
Any function whose SQL inserts into a D6 store must therefore take the stamp from
``goal_lineage.taint_insert`` or ``goal_lineage.taint_stamp``; a writer that
names neither would store exposed content untainted.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SOURCES = (_ROOT / "src" / "butlers", _ROOT / "roster")
_D6_INSERT = re.compile(
    r"\bINSERT\s+INTO\s+(?:\w+\.)?"
    r"(facts|entity_facts|episodes|rules|captures|dashboard_messages|insight_candidates)\b",
    re.IGNORECASE,
)
_STAMPERS = {"taint_insert", "taint_stamp"}
#: Same table name, different store: not a D6 store, and it has no taint columns.
_NOT_D6 = {
    # chronicler.episodes is the Chronicler timeline, not memory episodes (mem_017).
    "src/butlers/chronicler/storage.py::upsert_episode",
}


def _sources():
    for base in _SOURCES:
        for path in sorted(base.rglob("*.py")):
            parts = set(path.relative_to(_ROOT).parts)
            if parts & {"migrations", "tests", "testing"}:
                continue
            yield path


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def _own_nodes(function: ast.AST):
    """The function's own nodes; a nested function is judged on its own."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _FUNCTIONS):
            stack.extend(ast.iter_child_nodes(node))


def _offenders(source: str, label: str) -> list[str]:
    offenders = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        own = [node for node in _own_nodes(function) if not isinstance(node, _FUNCTIONS)]
        prose = {id(node.value) for node in own if isinstance(node, ast.Expr)}  # docstrings
        sql = " ".join(
            node.value
            for node in own
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
        )
        calls = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in own
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)
        }
        where = f"{label}::{function.name}"
        if _D6_INSERT.search(sql) and not calls & _STAMPERS and where not in _NOT_D6:
            offenders.append(where)
    return offenders


def test_every_d6_store_writer_stamps_the_goal_lineage():
    offenders = [
        where
        for path in _sources()
        for where in _offenders(path.read_text(encoding="utf-8"), str(path.relative_to(_ROOT)))
    ]
    assert offenders == []


def test_the_scan_flags_a_writer_that_bypasses_the_stamp():
    """Negative control: a bare INSERT (f-string or nested) is caught; a stamped one is not."""
    source = '''
async def bare(pool):
    """Docstring: INSERT INTO facts is prose here."""
    await pool.execute(f"INSERT INTO relationship.entity_facts (subject) VALUES ($1)", 1)

async def outer(pool):
    taint = taint_insert(2)
    async def inner():
        await pool.execute("INSERT INTO facts (subject) VALUES ($1)", 1)
    await pool.execute(f"INSERT INTO facts (subject{taint.columns}) VALUES ($1)", 1)

def prose_only():
    """Call it like: INSERT INTO episodes (content) VALUES ($1)."""
'''
    assert _offenders(source, "x.py") == ["x.py::bare", "x.py::inner"]


def test_the_scan_sees_the_known_writers():
    """Positive control: the scan finds real D6 writers, so an empty list means something."""
    found = set()
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        found.update(match.group(1).lower() for match in _D6_INSERT.finditer(text))
    assert {"facts", "entity_facts", "episodes", "captures", "insight_candidates"} <= found
