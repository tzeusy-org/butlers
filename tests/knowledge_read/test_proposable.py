"""The ``propose_fact`` allowlist (REQ-public-mcp-proposals-001; RFC 0039 D13).

The set is pinned literally, and every member is checked against the Relationship predicate
registry as its migrations seed it (read statically: no database).
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from butlers.knowledge_read.proposable import (
    ProposableError,
    load_proposable,
    proposable_predicates,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "roster" / "relationship" / "migrations"
# RFC 0039 D13: the family-gated set stays owner-direct (partner-of is named there too).
FAMILY = {"family-of", "partner-of", "parent-of", "child-of"}


def _migration(name: str):
    spec = importlib.util.spec_from_file_location(f"_seed_{name}", MIGRATIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry() -> dict[str, tuple[str, str]]:
    """predicate -> (kind, object_kind), from rel_014's and rel_024's seed data."""
    seeds = _migration("014_predicate_registry")._ALL_PREDICATES
    seeds += _migration("024_seed_works_at_member_of")._NEW_PREDICATES
    return {predicate: (kind, object_kind) for predicate, kind, object_kind, _ in seeds}


def _family_gate() -> frozenset[str]:
    """The gated set the assert path enforces, read from its source without importing it."""
    tree = ast.parse((ROOT / "roster/relationship/tools/relationship_assert_fact.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == (
            "_FAMILY_GATE_PREDICATES"
        ):
            return frozenset(ast.literal_eval(node.value.args[0]))  # type: ignore[union-attr]
    raise AssertionError("_FAMILY_GATE_PREDICATES not found")


def test_the_allowlist_is_exactly_the_v1_set_in_order():
    assert proposable_predicates() == (
        "knows",
        "colleague-of",
        "friend-of",
        "co-attended",
        "visited",
        "works-at",
        "member-of",
    )


def test_every_proposable_predicate_is_a_registered_relational_entity_predicate():
    registry = _registry()
    for predicate in proposable_predicates():
        assert registry.get(predicate) == ("relational", "entity"), predicate


def test_no_proposable_predicate_is_family_gated_contact_or_override():
    registry = _registry()
    gated = FAMILY | _family_gate()
    assert gated >= {"family-of", "parent-of", "child-of"}
    assert not gated & set(proposable_predicates())
    assert {p for p, (kind, _) in registry.items() if kind in ("contact", "override")}
    assert not {p for p, (kind, _) in registry.items() if kind in ("contact", "override")} & set(
        proposable_predicates()
    )


def test_the_loader_refuses_a_malformed_file():
    assert load_proposable(b'predicates = ["knows"]') == ("knows",)
    for bad in (
        b"",
        b"predicates = []",
        b'predicates = ["knows", "knows"]',
        b'predicates = ["knows", ""]',
        b'predicates = [" knows"]',
        b"predicates = [1]",
        b'predicates = ["knows"]\nextra = 1',
        b"not toml [",
        b"\xff",
    ):
        with pytest.raises(ProposableError):
            load_proposable(bad)
