"""conf-immutability guardrail — source scan for in-place UPDATE of conf.

``relationship-facts`` §"Requirement: conf is immutable after write" and
``relationship-entity-lifecycle`` §"Assert — supersession and immutable
confidence" forbid ANY in-place ``UPDATE`` of ``conf`` on a
``relationship.entity_facts`` row. Changed certainty is expressed ONLY through
the central writer's supersession (old row ``validity='superseded'``, new row
inserted with the new ``conf``). In particular, any time-based confidence-decay
job is a spec violation, because in-place mutation silently flips merge winners
(merge conflict-resolution keeps higher-``conf`` facts).

This is a static scan of the relationship butler source tree. It needs no DB and
is fast and safe to run anywhere. It mirrors the scan style of
``test_chronicler_boundary.py`` and ``test_finder_no_llm_guardrail.py``.

The companion DB-layer assertion (re-asserting with a different conf supersedes
rather than mutates) lives in
``test_relationship_assert_fact.py::TestConfImmutability``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Source roots
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
_ROSTER_ROOT = _HERE.parents[1]  # roster/relationship/
assert _ROSTER_ROOT.name == "relationship", (
    f"Unexpected roster root: {_ROSTER_ROOT}. "
    "This test must live at roster/relationship/tests/test_conf_immutability_guardrail.py"
)


def _relationship_source_files() -> list[Path]:
    """All .py files under roster/relationship/, excluding tests and migrations.

    - Tests are excluded: they construct synthetic SQL to *prove* the guardrail
      bites (see ``test_guardrail_fires_on_synthetic_conf_update``).
    - Migrations are excluded: a migration MAY rewrite ``conf`` as part of a
      schema/data correction; the immutability rule governs runtime write paths,
      not one-shot DDL/backfill.
    """
    skip_dirs = {"tests", "migrations"}
    return [
        p
        for p in _ROSTER_ROOT.rglob("*.py")
        if p.resolve() != _HERE and not (skip_dirs & set(p.parts))
    ]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
#
# SQL in this codebase is written both as multi-line triple-quoted strings and
# as adjacent concatenated string fragments ("UPDATE ..." "SET ..."). To match
# across both shapes we normalise the raw source: drop the quote/newline noise
# between adjacent string fragments and collapse whitespace, then scan the
# normalised text for an UPDATE on relationship.entity_facts whose SET clause
# assigns conf.

# Adjacent-string-fragment join: `"  ...  "   "  ...  "` → single space.
_FRAGMENT_JOIN = re.compile(r"""["']\s*["']""")
_WHITESPACE = re.compile(r"\s+")

# UPDATE relationship.entity_facts [alias] SET <...> conf <...> = ...
#   - allows an optional table alias after the table name
#   - the SET clause runs up to a WHERE / RETURNING / statement terminator;
#     we stop the lazy match at those boundaries so we only inspect the SET body
_CONF_UPDATE = re.compile(
    r"UPDATE\s+relationship\.entity_facts\b[^;]*?\bSET\b"
    r"(?P<setbody>.*?)"
    r"(?:\bWHERE\b|\bRETURNING\b|;|$)",
    re.IGNORECASE,
)
_CONF_ASSIGN = re.compile(r"\bconf\b\s*=", re.IGNORECASE)


def _normalise(source: str) -> str:
    joined = _FRAGMENT_JOIN.sub(" ", source)
    return _WHITESPACE.sub(" ", joined)


def _conf_update_offenders(source: str) -> list[str]:
    """Return SET bodies of any UPDATE on entity_facts that assigns conf."""
    text = _normalise(source)
    offenders: list[str] = []
    for m in _CONF_UPDATE.finditer(text):
        setbody = m.group("setbody")
        if _CONF_ASSIGN.search(setbody):
            offenders.append(setbody.strip())
    return offenders


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_in_place_conf_update_in_relationship_source():
    """HEAD: no runtime write path UPDATEs conf on relationship.entity_facts."""
    violations: dict[str, list[str]] = {}
    for path in _relationship_source_files():
        offenders = _conf_update_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            violations[str(path.relative_to(_ROSTER_ROOT))] = offenders

    assert not violations, (
        "conf is immutable after write (relationship-facts spec): found in-place "
        "UPDATE(s) of conf on relationship.entity_facts. Express changed certainty "
        "via supersession (relationship_assert_fact), never an in-place UPDATE.\n"
        f"Offenders: {violations}"
    )


@pytest.mark.parametrize(
    "synthetic",
    [
        # Triple-quoted multi-line form.
        """
        UPDATE relationship.entity_facts
        SET conf = conf * 0.9, updated_at = now()
        WHERE created_at < now() - INTERVAL '90 days'
        """,
        # Single-line form.
        "UPDATE relationship.entity_facts SET conf = 0.5 WHERE id = $1",
        # Concatenated-fragment form (as the codebase often writes SQL).
        '"UPDATE relationship.entity_facts " "SET conf = $1, updated_at = now() " "WHERE id = $2"',
        # conf alongside other columns in the SET clause.
        "UPDATE relationship.entity_facts SET src = $1, conf = $2 WHERE id = $3",
    ],
)
def test_guardrail_fires_on_synthetic_conf_update(synthetic):
    """The scan must flag a synthetic in-place conf UPDATE (red on violation)."""
    assert _conf_update_offenders(synthetic), (
        "Guardrail failed to detect a synthetic in-place conf UPDATE — it would "
        "not catch a real decay job."
    )


def test_guardrail_ignores_legitimate_non_conf_updates():
    """Supersession / re-point UPDATEs (validity, subject, object) are permitted."""
    legitimate = [
        "UPDATE relationship.entity_facts SET validity = 'superseded', updated_at = now() WHERE id = $1",  # noqa: E501
        "UPDATE relationship.entity_facts SET subject = $1, updated_at = now() WHERE id = $2",
        "UPDATE relationship.entity_facts SET object = $1, updated_at = now() WHERE id = $2",
    ]
    for sql in legitimate:
        assert not _conf_update_offenders(sql), f"False positive on: {sql}"
