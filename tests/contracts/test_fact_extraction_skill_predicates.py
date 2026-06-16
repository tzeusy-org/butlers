"""Contract: fact-extraction skill edge vocabulary must stay a subset of the registry.

Every predicate routed to ``relationship_assert_fact()`` in
``roster/relationship/.agents/skills/fact-extraction/SKILL.md`` MUST resolve
(directly or via the underscore→hyphen alias map) to a relational predicate in
``relationship.entity_predicate_registry``, OR appear on the documented
narrative allowlist.

This is the guard that would have caught the original drift where registry-
relational edges were incorrectly routed to ``memory_store_fact()`` instead of
``relationship_assert_fact()``.

Background: relational-edges-single-home (bu-hkwpo / Track B).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]

_SKILL_MD = (
    _REPO_ROOT / "roster" / "relationship" / ".agents" / "skills" / "fact-extraction" / "SKILL.md"
)

# ---------------------------------------------------------------------------
# Canonical relational registry predicates
# (hyphenated names, as seeded in relationship.entity_predicate_registry)
# ---------------------------------------------------------------------------

_RELATIONAL_REGISTRY_PREDICATES: frozenset[str] = frozenset(
    {
        "knows",
        "family-of",
        "partner-of",
        "parent-of",
        "child-of",
        "colleague-of",
        "friend-of",
        "co-attended",
        "purchased-from",
        "subscribed-to",
        "visited",
        "works-at",
        "member-of",
    }
)

# ---------------------------------------------------------------------------
# Underscore→hyphen alias map (mirrors _PREDICATE_ALIAS_MAP in
# roster/relationship/tools/relationship_assert_fact.py — must stay in sync).
# ---------------------------------------------------------------------------

_PREDICATE_ALIAS_MAP: dict[str, str] = {
    "works_at": "works-at",
    "friend_of": "friend-of",
    "child_of": "child-of",
    "parent_of": "parent-of",
    "colleague_of": "colleague-of",
    "family_of": "family-of",
    "partner_of": "partner-of",
    "member_of": "member-of",
    "sibling_of": "family-of",
    "married_to": "partner-of",
}

# ---------------------------------------------------------------------------
# Narrative allowlist — predicates that are documented as narrative (non-
# registry) edges and are expected to appear in the skill but routed to
# memory_store_fact(), NOT relationship_assert_fact().
# If any predicate in relationship_assert_fact() calls is on this list the
# test fails — the allowlist is for memory_store_fact() narrative predicates
# only, not for relationship_assert_fact() predicates.
# ---------------------------------------------------------------------------

_NARRATIVE_ALLOWLIST: frozenset[str] = frozenset(
    {
        "planned_dinner_with",
        "wake_coordination",
        "social_exchange_with",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_to_registry(predicate: str) -> str | None:
    """Resolve *predicate* to a canonical hyphenated registry name, or None.

    Accepts hyphenated forms directly if in the registry, or resolves via
    the underscore→hyphen alias map.
    """
    if predicate in _RELATIONAL_REGISTRY_PREDICATES:
        return predicate
    resolved = _PREDICATE_ALIAS_MAP.get(predicate)
    if resolved in _RELATIONAL_REGISTRY_PREDICATES:
        return resolved
    return None


# Extract predicate= keyword arg from relationship_assert_fact(...) calls.
# Matches both single- and double-quoted string values.
_ASSERT_FACT_PREDICATE_RE = re.compile(
    r"""relationship_assert_fact\s*\([^)]*?predicate\s*=\s*['"]([^'"]+)['"]""",
    re.DOTALL,
)


def _extract_assert_fact_predicates(text: str) -> list[str]:
    """Return all predicate values found in relationship_assert_fact() calls."""
    return _ASSERT_FACT_PREDICATE_RE.findall(text)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_skill_md_exists() -> None:
    """Sanity: the fact-extraction SKILL.md must exist at its expected path."""
    assert _SKILL_MD.exists(), (
        f"fact-extraction SKILL.md not found at {_SKILL_MD}. "
        "Update the path if the skill directory moved."
    )


def test_skill_md_routes_relational_edges_to_assert_fact() -> None:
    """The SKILL.md must contain at least one relationship_assert_fact() call.

    If zero calls are found the skill was not updated for Track B, which
    means relational edges have no documented routing.
    """
    text = _SKILL_MD.read_text(encoding="utf-8")
    predicates = _extract_assert_fact_predicates(text)
    assert predicates, (
        "No relationship_assert_fact(predicate=...) calls found in "
        f"{_SKILL_MD.relative_to(_REPO_ROOT)}. "
        "Track B requires the skill to route registry-relational edges through "
        "relationship_assert_fact() — update the skill."
    )


def test_all_assert_fact_predicates_resolve_to_registry() -> None:
    """Every predicate in relationship_assert_fact() calls must resolve to the registry.

    Scans the fact-extraction SKILL.md for all ``relationship_assert_fact(predicate=...)``
    calls and verifies each predicate either:
    1. Is a canonical relational registry predicate (hyphenated), OR
    2. Resolves via the underscore→hyphen alias map to one.

    A predicate on the narrative allowlist is NOT accepted here — narrative
    predicates belong in memory_store_fact(), not relationship_assert_fact().
    """
    text = _SKILL_MD.read_text(encoding="utf-8")
    predicates = _extract_assert_fact_predicates(text)

    violations: list[str] = []
    for pred in predicates:
        resolved = _resolve_to_registry(pred)
        if resolved is None:
            if pred in _NARRATIVE_ALLOWLIST:
                violations.append(
                    f"  {pred!r}: narrative predicate routed to relationship_assert_fact() "
                    "(should be in memory_store_fact())"
                )
            else:
                violations.append(
                    f"  {pred!r}: not in relational registry and not in narrative allowlist "
                    "(add to registry via migration, add to alias map, or add to narrative allowlist)"
                )

    assert not violations, (
        f"Fact-extraction skill has {len(violations)} predicate(s) in "
        "relationship_assert_fact() calls that do not resolve to the relational registry:\n"
        + "\n".join(violations)
        + "\n\nAdd missing predicates to the registry (migration) or to _PREDICATE_ALIAS_MAP, "
        "or route narrative predicates to memory_store_fact() instead."
    )


def test_alias_map_mirrors_relationship_assert_fact() -> None:
    """The alias map in this test must cover the same keys as in relationship_assert_fact.py.

    This is a meta-test that verifies the alias map hasn't drifted between the
    contract test and the live implementation.
    """
    impl_path = _REPO_ROOT / "roster" / "relationship" / "tools" / "relationship_assert_fact.py"
    assert impl_path.exists(), f"relationship_assert_fact.py not found at {impl_path}"
    source = impl_path.read_text(encoding="utf-8")

    # Extract the _PREDICATE_ALIAS_MAP dict literal from the source.
    # We look for all key: value string pairs within the _PREDICATE_ALIAS_MAP block.
    alias_map_match = re.search(
        r"_PREDICATE_ALIAS_MAP\s*:\s*dict\[str,\s*str\]\s*=\s*\{([^}]+)\}",
        source,
        re.DOTALL,
    )
    assert alias_map_match, (
        "_PREDICATE_ALIAS_MAP not found in relationship_assert_fact.py — "
        "update this contract test if the variable was renamed."
    )
    impl_map_text = alias_map_match.group(1)
    # Parse key→value pairs from the dict literal text.
    impl_pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', impl_map_text)
    impl_keys = {k for k, _ in impl_pairs}
    test_keys = set(_PREDICATE_ALIAS_MAP.keys())

    missing_from_test = impl_keys - test_keys
    extra_in_test = test_keys - impl_keys
    issues: list[str] = []
    if missing_from_test:
        issues.append(f"Keys in impl but missing from test alias map: {sorted(missing_from_test)}")
    if extra_in_test:
        issues.append(f"Keys in test alias map but absent from impl: {sorted(extra_in_test)}")

    assert not issues, (
        "Test _PREDICATE_ALIAS_MAP has drifted from relationship_assert_fact.py:\n"
        + "\n".join(issues)
    )
