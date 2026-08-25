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
from butlers.testing.source_guard import (
    enclosing_statement,
    local_bindings,
    parent_map,
    pragma_declaration,
    scope_nodes,
    scopes,
    string_constants,
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


def _all_chain_dirs() -> dict[str, Path]:
    chain_dirs = {}
    for chain in get_all_chains():
        chain_dir = _resolve_chain_dir(chain)
        assert chain_dir is not None
        chain_dirs[chain] = chain_dir
    return chain_dirs


def test_every_recognized_chain_has_globally_unique_revision_ids():
    duplicates = {
        revision_id: locations
        for revision_id, locations in _collect_revision_locations(_all_chain_dirs()).items()
        if len(locations) > 1
    }

    assert duplicates == {}, f"Duplicate migration revisions: {duplicates}"


def _collect_down_revisions(chain_dirs: dict[str, Path]) -> dict[str, list[str]]:
    """Map ``chain/filename`` to the revision ids its ``down_revision`` names."""
    parents: dict[str, list[str]] = {}
    for chain, chain_dir in chain_dirs.items():
        for migration_path in chain_dir.glob("*.py"):
            if migration_path.name == "__init__.py":
                continue
            tree = ast.parse(migration_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names = [node.target.id] if node.value is not None else []
                else:
                    continue
                if "down_revision" not in names:
                    continue
                assert node.value is not None
                value = ast.literal_eval(node.value)
                named = [value] if isinstance(value, str) else list(value or ())
                parents[f"{chain}/{migration_path.name}"] = [
                    item for item in named if isinstance(item, str)
                ]
                break
    return parents


def test_every_down_revision_resolves_to_a_revision_that_exists():
    """A ``down_revision`` naming a revision that is not in the tree orphans a chain.

    ``get_chain_head`` computes heads as "revisions nothing points down to", so
    a parent that does not exist leaves the head count at one and this file's
    other tests all stay green while ``alembic upgrade head`` cannot resolve
    the chain at all. Deleting or renaming a migration whose child stayed
    behind is the shape that produces it.
    """
    chain_dirs = _all_chain_dirs()
    known = set(_collect_revision_locations(chain_dirs))

    dangling = {
        location: missing
        for location, parents in _collect_down_revisions(chain_dirs).items()
        if (missing := [parent for parent in parents if parent not in known])
    }

    assert dangling == {}, f"down_revision pointing at unknown revisions: {dangling}"


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


# ---------------------------------------------------------------------------
# Head-literal guard (bu-4sgl8)
#
# A bare revision literal is ambiguous by inspection: ``core_201`` sometimes
# names *that* revision (correct to leave alone) and sometimes names *the
# head* (invalidated by the next migration). No grep disambiguates the two,
# which is why every core migration kept breaking head assertions written as
# literals — twice in two days, at core_200 and core_201. This guard removes
# the ambiguity by requiring every ``alembic_version`` comparison to either
# derive the head or declare in the source that it pins a revision on purpose.
#
# It is deliberately AST-based rather than a regex: most of these assertions
# put the ``SELECT`` and the literal on different lines, so a line-scoped
# regex under-counts them (2 found where 5 existed).
# ---------------------------------------------------------------------------

_GUARDED_SOURCE_ROOTS = ("src", "tests", "roster", "alembic")
_VERSION_NUM_READ_MARKERS = ("version_num", "alembic_version")
# The reason after the colon is mandatory (enforced by ``pragma_declaration``):
# a bare marker would let the guard be silenced without anyone articulating which
# of the two readings the literal has, and that articulation is the entire point.
_PINNED_REVISION_PRAGMA = "pinned-revision:"


@cache
def _all_revision_ids() -> frozenset[str]:
    """Every revision id in the codebase, across every chain."""
    return frozenset().union(*(get_chain_revision_ids(chain) for chain in get_all_chains()))


def _revision_literals(node: ast.AST) -> set[str]:
    """Revision ids spelled as literals inside *node* (``["core_201"]`` included)."""
    return {value for value in string_constants(node) if value in _all_revision_ids()}


def _reads_alembic_version(node: ast.AST, bindings: dict[str, ast.AST]) -> bool:
    """True when *node* evaluates a ``SELECT version_num FROM ...alembic_version``.

    ``bindings`` resolves one level of local assignment so the two-statement
    form (``versions = [... SELECT version_num ...]`` then ``assert
    "core_201" in versions``) is caught alongside the inline form.
    """
    candidates = [node]
    if isinstance(node, ast.Name) and node.id in bindings:
        candidates.append(bindings[node.id])
    return any(
        all(
            marker in " ".join(string_constants(candidate)).lower()
            for marker in _VERSION_NUM_READ_MARKERS
        )
        for candidate in candidates
    )


def head_literal_findings(path: Path, source: str) -> list[str]:
    """Report every revision literal compared against an ``alembic_version`` read."""
    tree = ast.parse(source)
    lines = source.splitlines()
    parents = parent_map(tree)
    findings: dict[int, str] = {}
    for scope in scopes(tree):
        nodes = scope_nodes(scope)
        bindings = local_bindings(nodes)
        for node in nodes:
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            literals = {
                index: found
                for index, operand in enumerate(operands)
                if (found := _revision_literals(operand))
            }
            if not literals:
                continue
            reads = {
                index
                for index, operand in enumerate(operands)
                if _reads_alembic_version(operand, bindings)
            }
            if not (reads - set(literals)):
                continue
            statement = enclosing_statement(node, parents)
            marker, reason = pragma_declaration(statement, lines, _PINNED_REVISION_PRAGMA)
            if marker and reason:
                continue
            spelled = ", ".join(sorted(value for found in literals.values() for value in found))
            detail = (
                f" (its '# {_PINNED_REVISION_PRAGMA}' comment states no reason)" if marker else ""
            )
            findings[statement.lineno] = (
                f"{path}:{statement.lineno} compares alembic_version to {spelled}{detail}"
            )
    return [findings[lineno] for lineno in sorted(findings)]


def _guarded_python_sources() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[2]
    return sorted(
        path for root in _GUARDED_SOURCE_ROOTS for path in (repo_root / root).rglob("*.py")
    )


def test_no_alembic_version_assertion_hardcodes_a_revision_literal():
    """Head assertions must derive the head, not spell today's revision.

    ``assert_at_chain_head`` (or ``get_chain_head``) resolves the head from the
    script directory, so the assertion survives the next migration. A test that
    genuinely pins an older revision keeps its literal and says why with a
    ``# pinned-revision: <reason>`` comment.
    """
    findings = []
    for path in _guarded_python_sources():
        source = path.read_text(encoding="utf-8")
        # The detector needs both markers spelled in the source, so a file
        # without "alembic_version" in it cannot produce a finding.
        if "alembic_version" not in source:
            continue
        findings.extend(head_literal_findings(path, source))

    assert findings == [], (
        "Hardcoded alembic_version revision literals found. Use "
        "butlers.testing.migration.assert_at_chain_head() (or compare against "
        "butlers.migrations.get_chain_head(chain)) when the assertion means "
        '"the head", or annotate a deliberate pin with a '
        f"'# {_PINNED_REVISION_PRAGMA} <reason>' comment — the reason is required, "
        "and must sit on the marker line:\n" + "\n".join(findings)
    )


_STALE_HEAD_LITERAL_SOURCE = textwrap.dedent(
    """
    def test_upgrade_lands_on_head(connection):
        command.upgrade(config, "core@head")
        assert (
            connection.execute(
                text("SELECT version_num FROM general.alembic_version")
            ).scalar_one()
            == {head!r}
        )
    """
)


def test_guard_fires_on_a_multiline_head_literal(tmp_path):
    """The guard must catch the multi-line shape a line-scoped regex misses."""
    head = get_chain_head("core")
    source = _STALE_HEAD_LITERAL_SOURCE.format(head=head)
    assert not any("version_num" in line and head in line for line in source.splitlines()), (
        "the fixture must keep the SELECT and the literal on separate lines — "
        "that separation is what a line-scoped regex misses"
    )

    findings = head_literal_findings(tmp_path / "test_stale.py", source)

    assert len(findings) == 1
    assert head in findings[0]


def test_guard_fires_on_a_head_literal_bound_through_a_local_name(tmp_path):
    """The two-statement ``versions = [...]`` / ``assert literal in versions`` shape."""
    source = textwrap.dedent(
        f"""
        def test_upgrade_lands_on_head(conn):
            versions = [r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))]
            assert {get_chain_head("approvals")!r} in versions
        """
    )

    findings = head_literal_findings(tmp_path / "test_stale.py", source)

    assert len(findings) == 1


def test_guard_accepts_a_deliberate_pin_declared_in_the_source(tmp_path):
    """The pragma is honoured anywhere in the comment block above the statement."""
    source = textwrap.dedent(
        f"""
        def test_failed_upgrade_leaves_the_schema_behind(connection):
            # {_PINNED_REVISION_PRAGMA} the head upgrade is expected to fail,
            # so this schema must stay where it was.
            assert (
                connection.execute(
                    text("SELECT version_num FROM general.alembic_version")
                ).scalar_one()
                == {get_chain_head("core")!r}
            )
        """
    )

    assert head_literal_findings(tmp_path / "test_pinned.py", source) == []


def test_guard_rejects_a_pin_declared_without_a_reason(tmp_path):
    """A bare marker must not silence the guard — the reason is the point."""
    source = textwrap.dedent(
        f"""
        def test_failed_upgrade_leaves_the_schema_behind(connection):
            # {_PINNED_REVISION_PRAGMA}
            assert (
                connection.execute(
                    text("SELECT version_num FROM general.alembic_version")
                ).scalar_one()
                == {get_chain_head("core")!r}
            )
        """
    )

    findings = head_literal_findings(tmp_path / "test_bare_pin.py", source)

    assert len(findings) == 1
    assert "states no reason" in findings[0]


def test_guard_accepts_a_derived_head_assertion(tmp_path):
    source = textwrap.dedent(
        """
        def test_upgrade_lands_on_head(connection):
            assert_at_chain_head(connection, "general")
            assert (
                connection.execute(
                    text("SELECT version_num FROM general.alembic_version")
                ).scalar_one()
                == get_chain_head("core")
            )
        """
    )

    assert head_literal_findings(tmp_path / "test_derived.py", source) == []


def test_guard_ignores_a_revision_literal_unrelated_to_alembic_version(tmp_path):
    source = textwrap.dedent(
        f"""
        def test_migration_declares_its_place_in_the_chain(module):
            assert module.revision == {get_chain_head("core")!r}
            assert module.down_revision == "core_200"
        """
    )

    assert head_literal_findings(tmp_path / "test_contract.py", source) == []
