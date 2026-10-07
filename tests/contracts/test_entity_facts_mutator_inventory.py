"""Contract: every production ``relationship.entity_facts`` mutator is inventoried.

relationship-fact-effective-time makes ``entity_facts`` rows carry an effective
occurrence and packet. A mutator that selects rows by subject, predicate or
triple can silently collapse two occurrences or rewrite a packet, so the
legacy ``uq_ef_spo_active`` index may only be cut over once EVERY production
mutation path either preserves each selected occurrence or refuses before its
first write (openspec/changes/relationship-fact-effective-time, design §8).

This guard pins that inventory. It scans ``roster/relationship/`` and
``src/butlers/`` (tests excluded) for SQL string literals that INSERT into,
UPDATE or DELETE FROM ``relationship.entity_facts`` -- plus the companion-entity
hard deletes whose FK cascade removes fact rows -- and maps each to its enclosing
function. The set of mutating functions must equal :data:`_INVENTORY` exactly:

* a new, unclassified mutator fails (classify it and give it a real-seam test
  that proves it preserves occurrences or fails before writing);
* a stale entry fails too, so the inventory cannot drift into fiction.

Each value names the behaviour the function owns; the real-PostgreSQL proof
for it lives in the Relationship writer, merge and mutator-fence tests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = (_REPO_ROOT / "roster" / "relationship", _REPO_ROOT / "src" / "butlers")

_MUTATION_RE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+relationship\.entity_facts\b"
    r"|\bDELETE\s+FROM\s+public\.entities\b",
    re.IGNORECASE,
)

_WRITER = "roster/relationship/tools/relationship_assert_fact.py"
_ROUTER = "roster/relationship/api/router.py"
_MIGRATIONS = "roster/relationship/migrations"

#: (repo-relative path, enclosing function qualname) -> owned behaviour.
_INVENTORY: dict[tuple[str, str], str] = {
    (_WRITER, "_insert_active_fact"): (
        "central writer: occurrence-scoped targetless insert, entity rows locked first"
    ),
    (_WRITER, "_supersede_and_replace"): (
        "central writer: exact-row version replacement, entity rows locked first"
    ),
    (_WRITER, "_upsert_candidate"): (
        "candidate writer: selected occurrence and complete stored packet preserved; "
        "candidate remains outside active identity/projection/coverage"
    ),
    ("roster/relationship/tools/fact_identity_decisions.py", "decide_identity_fact"): (
        "owner decision: exact candidate and compatible occurrence survivor under entity/slot/row "
        "locks; packet/history/evidence preserved, incompatible survivor refuses before effects"
    ),
    (_WRITER, "retract_contact_info_fact._retract"): (
        "SPO retraction: one occurrence or refuse; no FK column write, no entity lock"
    ),
    (_WRITER, "_supersede_active_prefers_channel"): "prefers-channel: fenced singleton only",
    ("src/butlers/owner_bootstrap.py", "_seed_owner_telegram_handle"): (
        "owner bootstrap: insert-only unknown default, no sibling"
    ),
    ("roster/relationship/tools/entity_merge.py", "merge_entity_pair"): (
        "entity merge: lock+plan, repoint intact, collision refused"
    ),
    ("roster/relationship/tools/entity_merge.py", "_move_candidate_reports"): (
        "entity merge: candidate reports repointed under the locked merge plan; complete "
        "occurrence/packet/report retained, candidates do not become active identities"
    ),
    ("roster/relationship/tools/contacts.py", "_repoint_entity_facts"): (
        "legacy contact merge: fence re-run under locks, one all-or-nothing transaction"
    ),
    (_ROUTER, "delete_entity_contact"): "hash selector: one occurrence or 409, exact-id retract",
    (_ROUTER, "update_entity_contact"): (
        "hash selector; entity then row locked before retract, temporal value edit refused"
    ),
    (_ROUTER, "forget_entity"): "intentional all-occurrence retraction with projections",
    ("src/butlers/google_account_registry.py", "disconnect_account"): (
        "companion hard delete: destructive all-version FK cascade"
    ),
    ("src/butlers/steam_account_registry.py", "disconnect_account"): (
        "companion hard delete: destructive all-version FK cascade"
    ),
    (f"{_MIGRATIONS}/019_prefix_telegram_has_handle.py", "upgrade"): (
        "historical: ordered before rel_035, never a post-cutover repair path"
    ),
    (f"{_MIGRATIONS}/019_prefix_telegram_has_handle.py", "downgrade"): (
        "historical: ordered before rel_035, never a post-cutover repair path"
    ),
    (f"{_MIGRATIONS}/027_prefix_telegram_ingress_handles.py", "upgrade"): (
        "historical: ordered before rel_035, never a post-cutover repair path"
    ),
    (f"{_MIGRATIONS}/027_prefix_telegram_ingress_handles.py", "downgrade"): (
        "historical: ordered before rel_035, never a post-cutover repair path"
    ),
    (f"{_MIGRATIONS}/028_backfill_entity_info_nonsecret_to_facts.py", "projection_insert_sql"): (
        "historical: ordered before rel_035, never a post-cutover repair path"
    ),
}


def _string_nodes(tree: ast.AST):
    """Yield (node, text) for every string literal, f-strings flattened."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            yield node, text


def _qualnames(tree: ast.AST) -> dict[int, str]:
    """Map each line to the qualname of its innermost enclosing function."""
    owner: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = f"{prefix}{child.name}"
                for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    owner[line] = name
                visit(child, f"{name}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return owner


def _production_mutators() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in _SCAN_ROOTS:
        for path in root.rglob("*.py"):
            if "tests" in path.relative_to(root).parts:
                continue
            source = path.read_text(encoding="utf-8")
            if not _MUTATION_RE.search(source):
                continue
            tree = ast.parse(source)
            owners = _qualnames(tree)
            relative = path.relative_to(_REPO_ROOT).as_posix()
            for node, text in _string_nodes(tree):
                if _MUTATION_RE.search(text):
                    found.add((relative, owners.get(node.lineno, "<module>")))
    return found


def test_every_entity_facts_mutator_is_inventoried() -> None:
    found = _production_mutators()
    unclassified = sorted(found - _INVENTORY.keys())
    stale = sorted(_INVENTORY.keys() - found)
    assert not unclassified, (
        "Unclassified relationship.entity_facts mutators block the effective-time "
        f"cutover; classify them in _INVENTORY with a real-seam proof: {unclassified}"
    )
    assert not stale, f"Inventory entries no longer mutate entity_facts: {stale}"


# ---------------------------------------------------------------------------
# Lock order (bu-ab0zys): entity rows before fact rows on every insert path
# ---------------------------------------------------------------------------

_LOCK_HELPER = "_lock_fact_entities"
_REPORT_LOCK_HELPER = "locked_report"
#: Writer functions that open an insert/replace path and must take the entity
#: lock before anything that reads-to-write or locks a fact row.
_LOCKED_ENTRIES = {"_upsert_fact", "assert_prefers_channel._do"}
#: Calls that lock or write fact rows (the helpers below the entries).
_FACT_TOUCHING_CALLS = {
    "_active_occurrence",
    "_correct_fact",
    "_supersede_and_replace",
    "_insert_active_fact",
    "_fence_prefers_channel",
    "_supersede_active_prefers_channel",
    "_upsert_candidate",
}
_INSERT_RE = re.compile(r"\bINSERT\s+INTO\s+relationship\.entity_facts\b", re.IGNORECASE)


def _writer_functions() -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    tree = ast.parse((_REPO_ROOT / _WRITER).read_text(encoding="utf-8"))
    functions: dict[str, ast.AsyncFunctionDef | ast.FunctionDef] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                functions[f"{prefix}{child.name}"] = child
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return functions


def _own_calls(function: ast.AST) -> list[ast.Call]:
    """Calls in *function*'s own body, excluding nested function definitions."""
    calls: list[ast.Call] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                calls.append(child)
            visit(child)

    visit(function)
    return calls


def _is_bare_lock_statement(stmt: ast.stmt) -> bool:
    """An unconditional entity lock or complete report-lock assignment."""
    value = stmt.value if isinstance(stmt, ast.Expr | ast.Assign | ast.AnnAssign) else None
    return (
        isinstance(value, ast.Await)
        and isinstance(value.value, ast.Call)
        and isinstance(value.value.func, ast.Name)
        and value.value.func.id in {_LOCK_HELPER, _REPORT_LOCK_HELPER}
    )


def test_every_writer_insert_path_locks_entities_before_facts() -> None:
    """Each entry locks entities first, and every insert is reached only via an entry.

    The first half pins the order inside each entry; the second walks the
    writer's call graph up from every ``INSERT INTO relationship.entity_facts``
    so a new insert path that bypasses the entries fails here.
    """
    functions = _writer_functions()
    # The newer attribution seam owns the full subject/object/reporter/confirm
    # FK union. Accept that actual helper only while it still performs the
    # unconditional batch lock; a same-named no-op cannot satisfy this guard.
    report_tree = ast.parse(
        (_REPO_ROOT / "roster/relationship/tools/fact_authority.py").read_text()
    )
    report_lock = next(
        node
        for node in report_tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == _REPORT_LOCK_HELPER
    )
    assert any(
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Await)
        and isinstance(stmt.value.value, ast.Call)
        and isinstance(stmt.value.value.func, ast.Name)
        and stmt.value.value.func.id == "_lock_fact_entities_batch"
        for stmt in report_lock.body
    ), "locked_report must unconditionally lock its complete entity union"

    for entry in sorted(_LOCKED_ENTRIES):
        # Statement-level, not line-level: the lock must be an unconditional
        # top-level entity/report lock statement that precedes
        # the first top-level statement touching a fact. A lock nested in one
        # branch (say, only the correction path) leaves the others unlocked
        # while still sitting on an earlier line.
        body = functions[entry].body
        lock_at = next((i for i, stmt in enumerate(body) if _is_bare_lock_statement(stmt)), None)
        fact_at = next(
            (
                i
                for i, stmt in enumerate(body)
                if any(c.func.id in _FACT_TOUCHING_CALLS for c in _own_calls(stmt))
            ),
            None,
        )
        assert lock_at is not None, (
            f"{entry} has no unconditional top-level `await {_LOCK_HELPER}(...)` statement"
        )
        assert fact_at is not None, f"{entry} no longer touches fact rows; update _LOCKED_ENTRIES"
        assert lock_at < fact_at, (
            f"{entry} touches a fact row before {_LOCK_HELPER} (bu-ab0zys lock order)"
        )
        if entry == "_upsert_fact":
            # bu-cbpakv: the writer's lock must cover the entity object too, or an
            # object-side insert is left unlocked against a merge.
            call = body[lock_at].value.value
            if call.func.id == _REPORT_LOCK_HELPER:
                passed = {
                    kw.arg: kw.value.id for kw in call.keywords if isinstance(kw.value, ast.Name)
                }
                assert {key: passed.get(key) for key in ("subject", "object", "object_kind")} == {
                    "subject": "subject",
                    "object": "object",
                    "object_kind": "object_kind",
                }, "_upsert_fact report lock must cover its subject and entity object"
            else:
                passed = [a.id for a in call.args if isinstance(a, ast.Name)]
                assert passed[1:] == ["subject", "object", "object_kind"], (
                    f"_upsert_fact must lock subject, object and object_kind, not {passed[1:]}"
                )

    callers: dict[str, set[str]] = {name: set() for name in functions}
    for name, function in functions.items():
        for call in _own_calls(function):
            if call.func.id in functions:
                callers[call.func.id].add(name)

    inserts = {
        name
        for name, function in functions.items()
        if any(
            _INSERT_RE.search(text)
            for node, text in _string_nodes(function)
            if _qualnames_of(functions, node) == name
        )
    }
    assert inserts, "found no entity_facts INSERT in the writer; the guard is blind"
    unguarded: list[str] = []
    seen: set[str] = set()
    frontier = list(inserts)
    while frontier:
        name = frontier.pop()
        if name in seen or name in _LOCKED_ENTRIES:
            continue
        seen.add(name)
        if not callers[name]:
            unguarded.append(name)
        frontier.extend(callers[name])
    assert not unguarded, (
        f"entity_facts insert paths reachable without {_LOCK_HELPER}: {sorted(unguarded)}"
    )


def _qualnames_of(functions: dict[str, ast.AST], node: ast.AST) -> str | None:
    """The innermost writer function whose line span contains *node*."""
    best: tuple[int, str] | None = None
    for name, function in functions.items():
        start, end = function.lineno, function.end_lineno or function.lineno
        if start <= node.lineno <= end and (best is None or end - start < best[0]):
            best = (end - start, name)
    return best[1] if best else None
