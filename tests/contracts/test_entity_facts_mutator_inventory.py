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
    (_WRITER, "_insert_active_fact"): "central writer: occurrence-scoped targetless insert",
    (_WRITER, "_supersede_and_replace"): "central writer: exact-row version replacement",
    (_WRITER, "retract_contact_info_fact._retract"): "SPO retraction: one occurrence or refuse",
    (_WRITER, "_supersede_active_prefers_channel"): "prefers-channel: fenced singleton only",
    (_WRITER, "assert_prefers_channel._do"): "prefers-channel: fenced singleton only",
    ("src/butlers/owner_bootstrap.py", "_seed_owner_telegram_handle"): (
        "owner bootstrap: insert-only unknown default, no sibling"
    ),
    ("roster/relationship/tools/entity_merge.py", "merge_entity_pair"): (
        "entity merge: lock+plan, repoint intact, collision refused"
    ),
    ("roster/relationship/tools/contacts.py", "_repoint_entity_facts"): (
        "legacy contact merge: fence re-run under locks, one all-or-nothing transaction"
    ),
    (_ROUTER, "delete_entity_contact"): "hash selector: one occurrence or 409, exact-id retract",
    (_ROUTER, "verify_entity_contact"): "hash selector: one occurrence or 409, exact-id verify",
    (_ROUTER, "update_entity_contact"): (
        "hash selector; row re-locked before retract, temporal value edit refused"
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
