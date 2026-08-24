"""Repo-wide integrity of the Alembic revision graph.

Every migration ships with a per-file chain test asserting its own ``revision``
and ``down_revision`` strings. Those tests are individually correct and
collectively blind: a file can only see itself, so two migrations that pick the
*same* revision id both pass their own test. Alembic then degrades the collision
to a ``UserWarning`` -- ``Revision X is present more than once`` -- and reports
``X`` twice in ``get_heads()``, at which point ``upgrade head`` fails with
multiple heads and one of the two migrations is silently unreachable.

That is what landed on ``main`` when two PRs each numbered themselves
``core_204`` against ``core_203``: each branch was green, and the union was
broken. These tests ask the question at the level where the breakage lives --
across every chain at once, which is also how ``build_alembic_config`` loads
them (all ``version_locations`` together, so ids must be globally unique, not
merely unique within a chain).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from butlers.migrations import _resolve_chain_dir, get_all_chains


def _literal(node: ast.AST) -> object:
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


def _revision_metadata(path: Path) -> tuple[str | None, object]:
    """Return ``(revision, down_revision)`` parsed statically from a migration.

    Parsed with ``ast`` rather than imported: importing every migration in the
    repo to read two constants would execute module-level code for no benefit.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    revision: str | None = None
    down_revision: object = None
    # Both spellings ship in this repo: bare ``revision = "core_204"`` and the
    # annotated ``revision: str = "core_063"`` that newer Alembic templates
    # emit. Reading only ``ast.Assign`` silently skips the annotated ones, and
    # a uniqueness check blind to part of its corpus is worse than none.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id] if node.value is not None else []
        else:
            continue
        for name in names:
            if name == "revision":
                value = _literal(node.value)
                revision = value if isinstance(value, str) else None
            elif name == "down_revision":
                down_revision = _literal(node.value)
    return revision, down_revision


def _migration_files() -> list[Path]:
    files: list[Path] = []
    for chain in get_all_chains():
        chain_dir = _resolve_chain_dir(chain)
        if chain_dir is None:
            continue
        files.extend(sorted(p for p in chain_dir.glob("*.py") if not p.name.startswith("__")))
    return files


@pytest.fixture(scope="module")
def migrations() -> list[tuple[Path, str, object]]:
    """Every migration file that declares a revision id, across every chain."""
    collected = []
    for path in _migration_files():
        revision, down_revision = _revision_metadata(path)
        if revision is not None:
            collected.append((path, revision, down_revision))
    assert collected, "no migrations discovered -- chain discovery is broken"
    return collected


def test_revision_ids_are_globally_unique(migrations) -> None:
    """No two migrations may claim the same revision id.

    Global, not per-chain: ``build_alembic_config`` passes every chain
    directory in ``version_locations`` at once so a shared ``alembic_version``
    table can resolve any revision, which makes the id namespace repo-wide.
    """
    owners: dict[str, list[str]] = {}
    for path, revision, _ in migrations:
        owners.setdefault(revision, []).append(path.name)
    duplicates = {rev: names for rev, names in owners.items() if len(names) > 1}
    assert not duplicates, (
        "duplicate Alembic revision ids -- Alembic downgrades this to a warning "
        f"and upgrade head then fails with multiple heads: {duplicates}"
    )


def test_each_chain_has_exactly_one_head(migrations) -> None:
    """Within a chain, exactly one revision may be unreferenced as a parent.

    A head is a revision no other revision points at via ``down_revision``. Two
    heads in one chain means ``upgrade head`` cannot pick a target; zero means
    the chain is cyclic.
    """
    by_chain: dict[Path, list[tuple[str, object]]] = {}
    for path, revision, down_revision in migrations:
        by_chain.setdefault(path.parent, []).append((revision, down_revision))

    problems: dict[str, list[str]] = {}
    for chain_dir, entries in by_chain.items():
        revisions = {rev for rev, _ in entries}
        parents: set[str] = set()
        for _, down in entries:
            if isinstance(down, str):
                parents.add(down)
            elif isinstance(down, (tuple, list)):
                parents.update(d for d in down if isinstance(d, str))
        heads = sorted(revisions - parents)
        if len(heads) != 1:
            problems[chain_dir.name] = heads
    assert not problems, f"chains without exactly one head: {problems}"


def test_every_down_revision_resolves(migrations) -> None:
    """A ``down_revision`` naming a revision that does not exist orphans a chain."""
    known = {revision for _, revision, _ in migrations}
    dangling: dict[str, list[str]] = {}
    for path, _, down_revision in migrations:
        if down_revision is None:
            continue
        parents = (
            [down_revision]
            if isinstance(down_revision, str)
            else [d for d in down_revision if isinstance(d, str)]
        )
        missing = [p for p in parents if p not in known]
        if missing:
            dangling[path.name] = missing
    assert not dangling, f"down_revision pointing at unknown revisions: {dangling}"
