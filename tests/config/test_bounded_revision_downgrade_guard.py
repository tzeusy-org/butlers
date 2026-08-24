"""Guard: no test upgrades past a trusted-bootstrap boundary and then rolls back below it.

Rollback is not uniformly available across the core chain. Some revisions
install a *trusted-bootstrap boundary*: objects owned by a privileged bootstrap
role, whose ``downgrade()`` refuses an ordinary migration login outright
(``core_198 downgrade requires trusted bootstrap rollback interface``). A test
that migrates to ``core@head`` and then downgrades into the 130s-170s drags
those boundaries into an unrelated rollback, and the failure surfaces as the
boundary revision exploding inside a test about some much older migration.

The remedy is the bounded-revision pattern documented on
:func:`butlers.testing.migration.create_migrated_test_db`: bound the upgrade to
the revision the test owns (``revisions={"core": "core_NNN"}``, or
``command.upgrade(cfg, f"core@{module.revision}")``) instead of going to head
first. bu-2rqrl fixed five instances of the unbounded shape; this guard exists
so the class cannot come back silently (bu-elwgq).

Everything here is a filesystem scan — no database, no Docker.

How the boundary is derived
---------------------------
Nothing in this module names a boundary revision. :func:`boundary_revisions`
parses every migration in every chain and classifies a revision as a boundary
when its ``downgrade()`` contains a refusal — a Python ``raise`` or a SQL
``RAISE EXCEPTION`` — whose message names both a rollback direction
(``downgrade``/``rollback``/``teardown``) and a privileged authority
(``bootstrap``/``privileged``/``superuser``/``rolsuper``). The next boundary
revision is picked up the day it lands, with no edit here. Chain position comes
from Alembic's own ``walk_revisions()`` (via
:func:`butlers.migrations.get_chain_revision_ids`'s script directory), so
"below" means "earlier in the real revision graph", not "smaller number".

What the detector cannot see
----------------------------
- **Non-literal revision arguments.** ``command.downgrade(cfg, target)`` where
  ``target`` is computed, parametrized, or read from a fixture is invisible.
  Only string literals (resolved through one level of local ``name = literal``
  binding) and relative ``core@-N`` steps are classified. The same cut applies
  on the upgrade side: ``command.upgrade(cfg, f"core@{module.revision}")`` and
  ``revisions={"core": module.revision}`` are unreadable, so the chain counts
  as *unknown* rather than as head — which is safe (no false alarm) but means
  those call sites carry no protection from this guard.
- **Migrations driven from a source string.** Tests that run Alembic in a
  subprocess built from a triple-quoted program (as the runtime-attention
  concurrency tests do) are text to the AST, not calls.
- **Indirection outside the module.** The upgrade→downgrade call graph is
  resolved within one file, including pytest fixture parameters that name a
  same-module fixture. A ``conftest.py`` fixture that migrates to head is not
  followed.
- **Refusals worded outside the house style.** A future boundary whose
  exception message omits the authority vocabulary above is not classified as
  a boundary, and downgrades past it are not flagged.
- **Non-reversible revisions that are not authority boundaries** (e.g.
  ``memory_010``'s ``NotImplementedError``, ``approvals_012``'s data-conditional
  refusal) are a related failure mode this guard deliberately does not cover.

The backstop for all of these is unchanged: the migration test itself fails
loudly when it runs, and AGENTS.md carries the convention. This guard removes
the cases a reviewer can see statically, which is where the recurrences came
from.
"""

from __future__ import annotations

import ast
import re
import textwrap
from functools import cache
from pathlib import Path

import pytest

from butlers.migrations import _chain_script_directory, _resolve_chain_dir, get_all_chains

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GUARDED_TEST_ROOTS = ("tests", "roster")

#: A refusal message must name the direction *and* the authority to count as a
#: trusted-bootstrap boundary. Direction alone matches ordinary invariant
#: checks; authority alone matches upgrade-side installer guards.
_ROLLBACK_DIRECTION_TERMS = ("downgrade", "rollback", "teardown")
_TRUSTED_AUTHORITY_TERMS = ("bootstrap", "privileged", "superuser", "rolsuper")

#: The single sanctioned way a migration test obtains privileged bootstrap
#: authority. A downgrade issued through it is using the interface the boundary
#: demands, not asking the boundary to make an exception.
_BOOTSTRAP_URL_FACTORY = "migration_bootstrap_db_url"

#: Call sites that migrate a chain all the way to its head.
_HEAD = "head"

#: ``command.downgrade(cfg, "core@-3")`` walks three revisions back from wherever
#: the chain currently stands, which is resolvable once the upgrade target is.
_RELATIVE_STEPS = re.compile(r"-(?P<steps>\d+)")

_BOUNDED_REVISION_REFERENCE = "src/butlers/testing/migration.py"


# ---------------------------------------------------------------------------
# Chain topology (derived from the real Alembic revision graph)
# ---------------------------------------------------------------------------


@cache
def chain_revision_order() -> dict[str, tuple[str, ...]]:
    """Every chain's revisions in applied order, base first, head last."""
    return {
        chain: tuple(
            reversed([rev.revision for rev in _chain_script_directory(chain).walk_revisions()])
        )
        for chain in get_all_chains()
    }


@cache
def _revision_positions() -> dict[str, tuple[str, int]]:
    """Map every revision id in the repo to its ``(chain, index)`` position."""
    return {
        revision: (chain, index)
        for chain, revisions in chain_revision_order().items()
        for index, revision in enumerate(revisions)
    }


def _refusal_messages(downgrade: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Every message a ``downgrade()`` body can refuse with.

    Two shapes exist in this codebase: a Python ``raise SomeError("...")`` and a
    SQL ``DO $$ ... RAISE EXCEPTION '...' ... $$`` handed to ``op.execute``.
    """
    messages = [
        constant.value
        for node in ast.walk(downgrade)
        if isinstance(node, ast.Raise)
        for constant in ast.walk(node)
        if isinstance(constant, ast.Constant) and isinstance(constant.value, str)
    ]
    messages += [
        node.value
        for node in ast.walk(downgrade)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "raise exception" in node.value.lower()
    ]
    return messages


def _refuses_without_trusted_bootstrap(source: str) -> bool:
    """True when a migration's ``downgrade()`` refuses an unprivileged caller."""
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != "downgrade":
            continue
        for message in _refusal_messages(node):
            lowered = message.lower()
            if any(term in lowered for term in _ROLLBACK_DIRECTION_TERMS) and any(
                term in lowered for term in _TRUSTED_AUTHORITY_TERMS
            ):
                return True
    return False


@cache
def boundary_revisions() -> dict[str, tuple[str, ...]]:
    """Chain → its trusted-bootstrap boundary revisions, in applied order.

    Derived from the migration sources themselves, never from a literal written
    here: a revision is a boundary when its ``downgrade()`` refuses a caller
    that lacks privileged bootstrap authority.
    """
    boundaries: dict[str, tuple[str, ...]] = {}
    for chain, revisions in chain_revision_order().items():
        chain_dir = _resolve_chain_dir(chain)
        assert chain_dir is not None, f"chain {chain!r} has no version directory"
        refusing: set[str] = set()
        for path in sorted(chain_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            if _refuses_without_trusted_bootstrap(source):
                refusing.add(_declared_revision(source, path))
        found = tuple(revision for revision in revisions if revision in refusing)
        if found:
            boundaries[chain] = found
    return boundaries


def _declared_revision(source: str, path: Path) -> str:
    """The ``revision = "..."`` id a migration module declares."""
    for node in ast.parse(source).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(target, ast.Name) and target.id == "revision" for target in targets):
            assert isinstance(node.value, ast.Constant), f"{path}: revision is not a literal"
            assert isinstance(node.value.value, str)
            return node.value.value
    raise AssertionError(f"{path}: migration declares no revision id")


# ---------------------------------------------------------------------------
# Detector: upgrade target vs downgrade target, per test function
# ---------------------------------------------------------------------------


def _string_literal(node: ast.expr, bindings: dict[str, ast.expr]) -> str | None:
    """The string a node evaluates to, through one level of local binding."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in bindings:
        bound = bindings[node.id]
        if isinstance(bound, ast.Constant) and isinstance(bound.value, str):
            return bound.value
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _argument(call: ast.Call, name: str, position: int) -> ast.expr | None:
    """A call argument by keyword, falling back to its positional slot."""
    found = _keyword(call, name)
    if found is not None:
        return found
    if len(call.args) > position:
        return call.args[position]
    return None


def _called_name(call: ast.Call) -> str | None:
    """The bare name of the callee (``command.upgrade`` → ``upgrade``)."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _is_alembic_command(call: ast.Call, action: str) -> bool:
    """True for ``command.upgrade``/``command.downgrade`` and dotted variants."""
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == action
        and isinstance(call.func.value, ast.Name | ast.Attribute)
        and (
            call.func.value.id == "command"
            if isinstance(call.func.value, ast.Name)
            else call.func.value.attr == "command"
        )
    )


def _resolve_revision_spec(spec: str) -> tuple[str, str] | None:
    """Turn an Alembic target into ``(chain, revision)``.

    Handles ``core@head``, ``core@core_170``, a bare ``core_170``, and ``base``
    (which is spelled without a chain and resolved by the caller).
    """
    if "@" in spec:
        chain, _, revision = spec.partition("@")
        if chain in chain_revision_order():
            return chain, revision
        return None
    position = _revision_positions().get(spec)
    if position is not None:
        return position[0], spec
    return None


def _revision_index(chain: str, revision: str) -> int | None:
    """Applied-order position of a target: ``head`` is last, ``base`` precedes all."""
    if revision == _HEAD:
        return len(chain_revision_order()[chain]) - 1
    if revision == "base":
        return -1
    position = _revision_positions().get(revision)
    if position is None or position[0] != chain:
        return None
    return position[1]


def _upgrade_targets(call: ast.Call, bindings: dict[str, ast.expr]) -> dict[str, str]:
    """Chains this call migrates, mapped to the revision it stops at."""
    name = _called_name(call)
    if _is_alembic_command(call, "upgrade"):
        spec = _string_literal(call.args[1], bindings) if len(call.args) > 1 else None
        resolved = _resolve_revision_spec(spec) if spec else None
        return dict([resolved]) if resolved else {}
    if name == "run_migrations":
        chain_node = _argument(call, "chain", 1)
        chain = _string_literal(chain_node, bindings) if chain_node is not None else "core"
        return {chain: _HEAD} if chain in chain_revision_order() else {}
    if name in ("create_migrated_test_db", "create_migrated_test_pool"):
        return _migrated_db_targets(call, bindings)
    return {}


def _migrated_db_targets(call: ast.Call, bindings: dict[str, ast.expr]) -> dict[str, str]:
    """Targets of the ``chains=[...] / revisions={...}`` migration-test factory.

    A chain listed in ``revisions`` is the bounded-revision pattern itself: it
    stops where the test asked, so it is not an upgrade past a boundary.
    """
    chains_node = _argument(call, "chains", 2)
    if not isinstance(chains_node, ast.List | ast.Tuple):
        return {}
    chains = [
        literal
        for element in chains_node.elts
        if (literal := _string_literal(element, bindings)) is not None
    ]
    bounded: dict[str, str | None] = {}
    revisions_node = _keyword(call, "revisions")
    if isinstance(revisions_node, ast.Dict):
        for key, value in zip(revisions_node.keys, revisions_node.values, strict=True):
            chain = _string_literal(key, bindings) if key is not None else None
            if chain is None:
                # An unreadable key means an unknown chain is bounded, so no
                # chain in this call can be claimed to reach head.
                return {}
            bounded[chain] = _string_literal(value, bindings)

    targets: dict[str, str] = {}
    for chain in chains:
        if chain not in chain_revision_order():
            continue
        if chain not in bounded:
            targets[chain] = _HEAD
        elif bounded[chain] is not None:
            # A computed bound (``revisions={"core": module.revision}``) is the
            # documented remedy spelled without a literal: unknown, not head.
            targets[chain] = str(bounded[chain])
    return targets


def _traces_to_bootstrap_url(node: ast.expr | None, bindings: dict[str, ast.expr]) -> bool:
    """True when an expression derives from the privileged bootstrap login.

    Local names are followed, so both the inline
    ``_build_alembic_config(migration_bootstrap_db_url(...), ...)`` shape and
    the two-step ``bootstrap_url = migration_bootstrap_db_url(...)`` shape
    resolve.
    """
    if node is None:
        return False
    seen: set[str] = set()
    stack: list[ast.expr] = [node]
    while stack:
        for child in ast.walk(stack.pop()):
            if isinstance(child, ast.Call) and _called_name(child) == _BOOTSTRAP_URL_FACTORY:
                return True
            if isinstance(child, ast.Name) and child.id in bindings and child.id not in seen:
                seen.add(child.id)
                stack.append(bindings[child.id])
    return False


def _downgrade_uses_bootstrap_authority(call: ast.Call, bindings: dict[str, ast.expr]) -> bool:
    """True when the Alembic config being downgraded was built on a bootstrap URL.

    That is the trusted rollback interface the boundary asks for, so crossing a
    boundary through it is the supported operation rather than the mistake.
    """
    return bool(call.args) and _traces_to_bootstrap_url(call.args[0], bindings)


def _scope_nodes(scope: ast.AST) -> list[ast.AST]:
    """Every node belonging to *scope*, without descending into nested functions."""
    nodes: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _local_bindings(nodes: list[ast.AST]) -> dict[str, ast.expr]:
    """Map simple ``name = <expr>`` assignments among *nodes* to their value nodes."""
    bindings: dict[str, ast.expr] = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
    return bindings


def _expected_failure_spans(nodes: list[ast.AST]) -> list[tuple[int, int]]:
    """Line ranges of ``with pytest.raises(...)`` blocks in a scope.

    A downgrade inside one is a test *about* the refusal, which is exactly what
    ``tests/migrations/test_runtime_attention_outbox_migration.py`` does on
    purpose. Those must never be flagged.
    """
    spans = []
    for node in nodes:
        if not isinstance(node, ast.With | ast.AsyncWith):
            continue
        expects_failure = any(
            isinstance(child, ast.Call) and _called_name(child) == "raises"
            for item in node.items
            for child in ast.walk(item.context_expr)
        )
        if expects_failure:
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


class _Scope:
    """One analyzed function (or the module body), ready for call-graph closure."""

    def __init__(self, name: str, node: ast.AST) -> None:
        self.name = name
        nodes = _scope_nodes(node)
        self.bindings = _local_bindings(nodes)
        self.calls = [child for child in nodes if isinstance(child, ast.Call)]
        self.expected_failure_spans = _expected_failure_spans(nodes)
        self.upgrades: dict[str, int] = {}
        for call in self.calls:
            for chain, revision in _upgrade_targets(call, self.bindings).items():
                index = _revision_index(chain, revision)
                if index is not None:
                    self.upgrades[chain] = max(self.upgrades.get(chain, -1), index)
        self.callees = {name for call in self.calls if (name := _called_name(call)) is not None}
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            # A pytest fixture reaches the test through its parameter name.
            self.callees |= {argument.arg for argument in node.args.args}

    def expects_failure_at(self, lineno: int) -> bool:
        return any(start <= lineno <= end for start, end in self.expected_failure_spans)


def _analyze_scopes(tree: ast.Module) -> dict[str, _Scope]:
    """Every function in the module plus its body, with upgrade reach resolved."""
    scopes = {"<module>": _Scope("<module>", tree)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scopes[node.name] = _Scope(node.name, node)

    # Fixed point: a scope reaches every upgrade its callees (and fixtures) reach.
    changed = True
    while changed:
        changed = False
        for scope in scopes.values():
            for callee in scope.callees:
                reached = scopes.get(callee)
                if reached is None or reached is scope:
                    continue
                for chain, index in reached.upgrades.items():
                    if index > scope.upgrades.get(chain, -1):
                        scope.upgrades[chain] = index
                        changed = True
    return scopes


def bounded_revision_findings(path: Path, source: str) -> list[str]:
    """Report every downgrade that rolls back through a trusted-bootstrap boundary."""
    scopes = _analyze_scopes(ast.parse(source))
    boundaries = boundary_revisions()
    findings: dict[int, str] = {}
    for scope in scopes.values():
        for call in scope.calls:
            if not _is_alembic_command(call, "downgrade") or len(call.args) < 2:
                continue
            spec = _string_literal(call.args[1], scope.bindings)
            if spec is None:
                continue
            resolved = _resolve_revision_spec(spec)
            chains = [resolved[0]] if resolved else list(scope.upgrades)
            revision = resolved[1] if resolved else spec
            for chain in chains:
                upgraded = scope.upgrades.get(chain)
                if upgraded is None:
                    continue
                target = _revision_index(chain, revision)
                relative = _RELATIVE_STEPS.fullmatch(revision)
                if target is None and relative is not None:
                    target = upgraded - int(relative.group("steps"))
                if target is None:
                    continue
                crossed = [
                    boundary
                    for boundary in boundaries.get(chain, ())
                    if target < (_revision_index(chain, boundary) or -1) <= upgraded
                ]
                if not crossed:
                    continue
                if scope.expects_failure_at(call.lineno):
                    continue
                if _downgrade_uses_bootstrap_authority(call, scope.bindings):
                    continue
                upgraded_to = chain_revision_order()[chain][upgraded]
                findings[call.lineno] = (
                    f"{path}:{call.lineno} {scope.name}() migrates {chain} up to "
                    f"{upgraded_to} and then downgrades to {revision!r}, rolling back "
                    f"through {', '.join(crossed)}"
                )
    return [findings[lineno] for lineno in sorted(findings)]


def _guarded_test_sources() -> list[Path]:
    return sorted(
        path
        for root in _GUARDED_TEST_ROOTS
        for path in (_REPO_ROOT / root).rglob("*.py")
        if path.name.startswith("test_") or path.name == "conftest.py"
    )


def migration_call_sites(path: Path, source: str) -> dict[str, list[str]]:
    """Every migration call site the detector *resolved* in one file.

    Non-vacuity depends on this: a detector that resolves nothing makes every
    "no findings" assertion trivially true, which is the failure mode a
    silently-blind static check dies of.
    """
    tree = ast.parse(source)
    sites: dict[str, list[str]] = {"upgrades": [], "downgrades": []}
    for scope in _analyze_scopes(tree).values():
        for call in scope.calls:
            for chain, revision in _upgrade_targets(call, scope.bindings).items():
                sites["upgrades"].append(f"{path}:{call.lineno} {chain}@{revision}")
            if _is_alembic_command(call, "downgrade") and len(call.args) > 1:
                spec = _string_literal(call.args[1], scope.bindings)
                if spec is not None:
                    sites["downgrades"].append(f"{path}:{call.lineno} {spec}")
    return sites


# ---------------------------------------------------------------------------
# The derivation must be real, and must be derived
# ---------------------------------------------------------------------------


def test_boundary_derivation_finds_real_trusted_bootstrap_boundaries():
    """Non-vacuity: the classifier must actually find the boundaries that exist.

    A derivation that returns nothing would make the whole guard silently inert
    — every downgrade would cross zero boundaries and pass.
    """
    scanned = sum(len(revisions) for revisions in chain_revision_order().values())
    assert scanned > 50, f"only {scanned} revisions scanned; the chain scan found nothing real"

    boundaries = boundary_revisions()
    assert boundaries, (
        "no trusted-bootstrap boundary revisions were derived from the migration "
        "sources, so this guard would pass vacuously for every test in the repo. "
        "Either the derivation in boundary_revisions() is broken, or a boundary "
        "landed whose downgrade refusal is worded outside the vocabulary it "
        f"recognizes ({_ROLLBACK_DIRECTION_TERMS} x {_TRUSTED_AUTHORITY_TERMS})."
    )
    assert "core" in boundaries, f"expected the core chain to carry boundaries, got {boundaries}"


def test_every_derived_boundary_refuses_in_its_own_migration_source():
    """A derived boundary must be a revision whose ``downgrade()` really refuses.

    This is what fails when the boundary is hardcoded (or drifts): a literal
    revision id names a migration whose source carries no refusal at all.
    """
    checked = 0
    for chain, revisions in boundary_revisions().items():
        chain_dir = _resolve_chain_dir(chain)
        assert chain_dir is not None
        sources = {
            _declared_revision(source, path): source
            for path in sorted(chain_dir.glob("*.py"))
            if path.name != "__init__.py"
            for source in [path.read_text(encoding="utf-8")]
        }
        for revision in revisions:
            assert revision in sources, (
                f"{chain} boundary {revision!r} names no migration in {chain_dir} — "
                "the boundary must be derived from the revision sources, not written here"
            )
            assert _refuses_without_trusted_bootstrap(sources[revision]), (
                f"{chain} boundary {revision!r} has a downgrade() that refuses nobody"
            )
            checked += 1
    assert checked > 0, "no boundary revisions were checked; the derivation returned nothing"


def test_no_revision_above_the_last_boundary_refuses_without_bootstrap():
    """The last boundary really is the last one — nothing later also refuses.

    The guard treats "below the boundary" as "earlier than the latest refusing
    revision", so a later refusing revision missing from the derived tuple would
    silently under-report.
    """
    for chain, revisions in boundary_revisions().items():
        order = chain_revision_order()[chain]
        chain_dir = _resolve_chain_dir(chain)
        assert chain_dir is not None
        last = max(order.index(revision) for revision in revisions)
        later = [
            path
            for path in sorted(chain_dir.glob("*.py"))
            if path.name != "__init__.py"
            and order.index(_declared_revision(path.read_text(encoding="utf-8"), path)) > last
            and _refuses_without_trusted_bootstrap(path.read_text(encoding="utf-8"))
        ]
        assert later == [], f"{chain}: refusing revisions after the last derived boundary: {later}"


# ---------------------------------------------------------------------------
# The repo-wide sweep
# ---------------------------------------------------------------------------


def test_the_guard_resolves_real_migration_call_sites_in_the_repo():
    """Non-vacuity: the sweep below must be looking at something.

    If the parser resolves no upgrade and no downgrade call sites, the sweep's
    ``findings == []`` says nothing at all.
    """
    sites: dict[str, list[str]] = {"upgrades": [], "downgrades": []}
    for path in _guarded_test_sources():
        source = path.read_text(encoding="utf-8")
        if "upgrade" not in source and "downgrade" not in source:
            continue
        for kind, found in migration_call_sites(path, source).items():
            sites[kind].extend(found)

    assert sites["upgrades"], (
        "the detector resolved zero chain-upgrade call sites across "
        f"{_GUARDED_TEST_ROOTS}, so the bounded-revision sweep is vacuous — "
        "every assertion below would pass on an empty set"
    )
    assert sites["downgrades"], (
        "the detector resolved zero command.downgrade call sites across "
        f"{_GUARDED_TEST_ROOTS}, so the bounded-revision sweep is vacuous — "
        "every assertion below would pass on an empty set"
    )


def test_no_test_migrates_past_a_boundary_then_downgrades_below_it():
    """The guard itself: no test asks for a rollback a boundary is built to refuse."""
    findings = []
    for path in _guarded_test_sources():
        source = path.read_text(encoding="utf-8")
        if "downgrade" not in source:
            continue
        findings.extend(bounded_revision_findings(path, source))

    assert findings == [], (
        "Test(s) migrate a chain past a trusted-bootstrap boundary and then roll "
        "back below it. Those boundaries refuse an ordinary migration login, so "
        "the failure surfaces as the boundary revision exploding inside a test "
        "about a much older migration. Bound the upgrade to the revision the "
        "test owns instead — the bounded-revision pattern documented on "
        f"create_migrated_test_db in {_BOUNDED_REVISION_REFERENCE} "
        '(revisions={"core": "core_NNN"}, or command.upgrade(cfg, '
        'f"core@{module.revision}")). A test that means to assert the refusal '
        "should wrap the downgrade in pytest.raises, and a rollback that really "
        f"is privileged should build its config on {_BOOTSTRAP_URL_FACTORY}():\n"
        + "\n".join(findings)
    )


# ---------------------------------------------------------------------------
# Detector behaviour on synthetic sources
# ---------------------------------------------------------------------------


def _core_boundary() -> str:
    return boundary_revisions()["core"][-1]


def _revision_well_below_the_boundary() -> str:
    order = chain_revision_order()["core"]
    return order[max(0, order.index(_core_boundary()) - 20)]


def test_guard_fires_on_head_upgrade_followed_by_a_downgrade_below_the_boundary(tmp_path):
    source = textwrap.dedent(
        f"""
        def test_old_migration_round_trips(postgres_container):
            db_url = create_migration_db(postgres_container, migration_db_name())
            config = _build_alembic_config(db_url, chains=["core"])
            command.upgrade(config, "core@head")
            command.downgrade(config, {_revision_well_below_the_boundary()!r})
        """
    )

    findings = bounded_revision_findings(tmp_path / "test_bad.py", source)

    assert len(findings) == 1, findings
    assert _core_boundary() in findings[0]
    assert _revision_well_below_the_boundary() in findings[0]


def test_guard_fires_when_the_head_upgrade_arrives_through_a_fixture(tmp_path):
    """The upgrade and the downgrade are routinely in different functions."""
    source = textwrap.dedent(
        f"""
        @pytest.fixture(scope="module")
        def migrated_db(postgres_container):
            return create_migrated_test_db(
                postgres_container, migration_db_name(), chains=["core"]
            )

        def test_old_migration_round_trips(migrated_db):
            command.downgrade(
                _build_alembic_config(migrated_db, chains=["core"]),
                {_revision_well_below_the_boundary()!r},
            )
        """
    )

    findings = bounded_revision_findings(tmp_path / "test_fixture.py", source)

    assert len(findings) == 1, findings


def test_guard_accepts_the_bounded_revision_pattern(tmp_path):
    """``revisions={"core": ...}`` is the documented remedy and must stay clean."""
    bounded = _revision_well_below_the_boundary()
    source = textwrap.dedent(
        f"""
        def test_old_migration_round_trips(postgres_container):
            db_url = create_migrated_test_db(
                postgres_container,
                migration_db_name(),
                chains=["core"],
                revisions={{"core": {bounded!r}}},
            )
            command.downgrade(_build_alembic_config(db_url, chains=["core"]), "core_100")
        """
    )

    assert bounded_revision_findings(tmp_path / "test_bounded.py", source) == []


def test_guard_accepts_a_downgrade_that_asserts_the_documented_refusal(tmp_path):
    """The runtime-attention tests exercise the refusal on purpose (bu-pq0yw)."""
    source = textwrap.dedent(
        f"""
        def test_rollback_is_refused(postgres_container):
            db_url = create_migration_db(postgres_container, migration_db_name())
            config = _build_alembic_config(db_url, chains=["core"])
            command.upgrade(config, "core@head")
            with pytest.raises(DBAPIError, match="trusted bootstrap rollback interface"):
                command.downgrade(config, {_revision_well_below_the_boundary()!r})
        """
    )

    assert bounded_revision_findings(tmp_path / "test_refusal.py", source) == []


def test_guard_accepts_a_rollback_issued_through_the_trusted_bootstrap_login(tmp_path):
    """A privileged rollback is the interface the boundary asks for, not a mistake."""
    source = textwrap.dedent(
        f"""
        def test_bootstrap_rollback(postgres_container):
            db_name = migration_db_name()
            db_url = create_migration_db(postgres_container, db_name)
            bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
            command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
            config = _build_alembic_config(bootstrap_url, chains=["core"])
            command.downgrade(config, {_revision_well_below_the_boundary()!r})
        """
    )

    assert bounded_revision_findings(tmp_path / "test_bootstrap.py", source) == []


def test_guard_accepts_a_downgrade_that_stops_at_the_boundary(tmp_path):
    """Rolling back *to* the boundary never runs the boundary's own downgrade."""
    source = textwrap.dedent(
        f"""
        def test_stops_at_the_boundary(postgres_container):
            db_url = create_migration_db(postgres_container, migration_db_name())
            config = _build_alembic_config(db_url, chains=["core"])
            command.upgrade(config, "core@head")
            command.downgrade(config, {_core_boundary()!r})
        """
    )

    assert bounded_revision_findings(tmp_path / "test_at_boundary.py", source) == []


def test_guard_ignores_a_chain_that_carries_no_boundary(tmp_path):
    """Chains without a trusted-bootstrap boundary roll back freely."""
    clean_chains = sorted(set(chain_revision_order()) - set(boundary_revisions()))
    assert clean_chains, "expected at least one chain with no trusted-bootstrap boundary"
    chain = clean_chains[0]
    source = textwrap.dedent(
        f"""
        def test_round_trip(postgres_container):
            config = _build_alembic_config(db_url, chains=[{chain!r}])
            command.upgrade(config, "{chain}@head")
            command.downgrade(config, "base")
        """
    )

    assert bounded_revision_findings(tmp_path / "test_clean_chain.py", source) == []
