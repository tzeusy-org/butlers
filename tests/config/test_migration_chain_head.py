"""Tests for butlers.migrations.get_chain_head / get_chain_revision_ids.

These are pure filesystem scans (no database, no docker) — used by the
migration-drift sentinel (bu-9r3hd.1) to resolve "what does the codebase say
the head of this migration chain should be." Exercised against the real
"core" chain (deterministic, no fixture needed) plus a synthetic temp chain
for the error paths (unknown chain, multiple unmerged heads).
"""

from __future__ import annotations

import ast
import textwrap
from fnmatch import fnmatchcase
from functools import cache
from pathlib import Path

import pytest
import yaml

from butlers.migrations import (
    _resolve_chain_dir,
    chain_root_workflow_path_filters,
    get_all_chains,
    get_chain_head,
    get_chain_revision_ids,
)

pytestmark = pytest.mark.unit

_MIGRATION_CHAIN_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "migration-chain-main.yml"
)


def _migration_chain_workflow_push_paths() -> list[str]:
    """Load the workflow path filters without YAML 1.1 coercing ``on`` to ``True``."""
    workflow = yaml.load(
        _MIGRATION_CHAIN_WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    assert isinstance(workflow, dict)
    push = workflow["on"]["push"]
    assert "main" in push["branches"]
    paths = push["paths"]
    assert isinstance(paths, list)
    assert all(isinstance(path, str) for path in paths)
    return paths


@cache
def _github_path_glob_matches(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
    """Match the segment glob semantics used by the workflow's path filters."""
    if not pattern_parts:
        return not path_parts

    pattern_part = pattern_parts[0]
    remaining_patterns = pattern_parts[1:]
    if pattern_part == "**":
        return any(
            _github_path_glob_matches(path_parts[index:], remaining_patterns)
            for index in range(len(path_parts) + 1)
        )

    return (
        bool(path_parts)
        and fnmatchcase(path_parts[0], pattern_part)
        and _github_path_glob_matches(path_parts[1:], remaining_patterns)
    )


def _workflow_selects_path(changed_path: str, path_filters: list[str]) -> bool:
    """Apply the ordered include/exclude behavior of GitHub Actions path filters."""
    selected = False
    for path_filter in path_filters:
        is_exclusion = path_filter.startswith("!")
        pattern = path_filter.removeprefix("!")
        if _github_path_glob_matches(tuple(changed_path.split("/")), tuple(pattern.split("/"))):
            selected = not is_exclusion
    return selected


def test_get_chain_head_returns_the_real_core_head():
    head = get_chain_head("core")
    assert head.startswith("core_")


def test_get_chain_revision_ids_includes_the_head_and_is_frozen():
    head = get_chain_head("core")
    revisions = get_chain_revision_ids("core")
    assert head in revisions
    assert isinstance(revisions, frozenset)
    # A codebase this mature has well more than a handful of core revisions.
    assert len(revisions) > 50


def test_get_chain_head_raises_for_unknown_chain():
    with pytest.raises(ValueError, match="Unknown migration chain"):
        get_chain_head("definitely_not_a_real_chain")


def test_every_recognized_chain_resolves_exactly_one_head():
    """Every chain get_all_chains() discovers must have exactly one head.

    Two unmerged heads left in a chain is itself a codebase defect the drift
    sentinel should surface loudly (get_chain_head raises) rather than
    silently pick one — this test catches that regression across the whole
    repo, not just "core".
    """
    for chain in get_all_chains():
        head = get_chain_head(chain)
        assert head in get_chain_revision_ids(chain)


def _representative_changed_path(path_filter: str) -> str:
    """Build one concrete changed-file path that should match ``path_filter``.

    ``**`` is substituted first (with a filename) so the subsequent
    single-segment ``*`` substitution doesn't also match inside it.
    """
    path = path_filter.replace("**", "001_future.py")
    return path.replace("*", "future_module")


@pytest.mark.parametrize("path_filter", chain_root_workflow_path_filters())
def test_post_merge_migration_chain_workflow_covers_all_discovered_root_families(
    path_filter: str,
):
    """Every root family migrations.py declares must select the main-chain gate.

    ``chain_root_workflow_path_filters()`` is the single source of truth that
    ``_resolve_chain_dir`` also resolves against, so a fourth migration-root
    shape added to ``_CHAIN_ROOT_FAMILIES`` without a matching workflow
    path-filter glob makes this test fail instead of landing with the
    "Migration Chain Integrity (main)" check silently absent — the same
    silent-absence failure mode behind the core_164 duplicate-revision
    incident.
    """
    workflow_path_filters = _migration_chain_workflow_push_paths()
    assert set(workflow_path_filters) == set(chain_root_workflow_path_filters())

    changed_path = _representative_changed_path(path_filter)
    assert _workflow_selects_path(changed_path, workflow_path_filters), (
        f"Migration change {changed_path!r} (root family {path_filter!r}) does not "
        "select the post-merge chain workflow"
    )


def _collect_revision_locations(chain_dirs: dict[str, Path]) -> dict[str, list[str]]:
    revision_locations: dict[str, list[str]] = {}
    for chain, chain_dir in chain_dirs.items():
        for migration_path in chain_dir.glob("*.py"):
            if migration_path.name == "__init__.py":
                continue
            tree = ast.parse(migration_path.read_text(encoding="utf-8"))
            revision_id = None
            for node in tree.body:
                is_revision_assignment = isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                )
                is_typed_revision_assignment = (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "revision"
                )
                if is_revision_assignment or is_typed_revision_assignment:
                    assert isinstance(node.value, ast.Constant)
                    assert isinstance(node.value.value, str)
                    revision_id = node.value.value
                    break
            assert revision_id is not None, f"Migration {migration_path} has no revision id"
            revision_locations.setdefault(revision_id, []).append(f"{chain}/{migration_path.name}")
    return revision_locations


def test_every_recognized_chain_has_globally_unique_revision_ids():
    chain_dirs = {}
    for chain in get_all_chains():
        chain_dir = _resolve_chain_dir(chain)
        assert chain_dir is not None
        chain_dirs[chain] = chain_dir

    duplicates = {
        revision_id: locations
        for revision_id, locations in _collect_revision_locations(chain_dirs).items()
        if len(locations) > 1
    }

    assert duplicates == {}, f"Duplicate migration revisions: {duplicates}"


def _write_revision(directory: Path, *, revision: str, down_revision: str | None) -> None:
    directory.joinpath(f"{revision}.py").write_text(
        textwrap.dedent(
            f"""
            revision = {revision!r}
            down_revision = {down_revision!r}
            branch_labels = None
            depends_on = None

            def upgrade():
                pass

            def downgrade():
                pass
            """
        )
    )


def test_revision_guard_catches_cross_chain_collision(tmp_path):
    first_chain = tmp_path / "first"
    second_chain = tmp_path / "second"
    first_chain.mkdir()
    second_chain.mkdir()
    _write_revision(first_chain, revision="shared_001", down_revision=None)
    _write_revision(second_chain, revision="shared_001", down_revision=None)

    revision_locations = _collect_revision_locations({"first": first_chain, "second": second_chain})

    assert revision_locations["shared_001"] == [
        "first/shared_001.py",
        "second/shared_001.py",
    ]


def test_get_chain_head_raises_on_multiple_unmerged_heads(tmp_path, monkeypatch):
    chain_dir = tmp_path / "fake_chain"
    chain_dir.mkdir()
    _write_revision(chain_dir, revision="fake_001", down_revision=None)
    _write_revision(chain_dir, revision="fake_002a", down_revision="fake_001")
    _write_revision(
        chain_dir, revision="fake_002b", down_revision="fake_001"
    )  # second, unmerged head

    monkeypatch.setattr(
        "butlers.migrations._resolve_chain_dir",
        lambda chain: chain_dir if chain == "fake_chain" else None,
    )

    with pytest.raises(RuntimeError, match="has 2 head"):
        get_chain_head("fake_chain")
