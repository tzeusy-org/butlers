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

# The lifecycle spec says conf immutability holds "anywhere in the codebase", so
# the scan must also cover the main package (src/butlers/) — e.g.
# src/butlers/scripts/contact_backfill_triples.py writes entity_facts via an
# ON CONFLICT clause and lived outside the original roster-only scan.
_REPO_ROOT = _HERE.parents[3]  # repo root (roster/relationship/tests/<file>)
_SRC_ROOT = _REPO_ROOT / "src" / "butlers"


def _scan_files_under(root: Path) -> list[Path]:
    """All .py files under *root*, excluding this test, tests dirs, and migrations.

    - Tests are excluded: they construct synthetic SQL to *prove* the guardrail
      bites (see ``test_guardrail_fires_on_synthetic_conf_update``).
    - Migrations are excluded: a migration MAY rewrite ``conf`` as part of a
      schema/data correction; the immutability rule governs runtime write paths,
      not one-shot DDL/backfill.
    """
    skip_dirs = {"tests", "migrations"}
    return [
        p for p in root.rglob("*.py") if p.resolve() != _HERE and not (skip_dirs & set(p.parts))
    ]


def _relationship_source_files() -> list[Path]:
    """All runtime .py files in scope: roster/relationship/ + src/butlers/.

    The lifecycle spec forbids in-place conf UPDATEs "anywhere in the codebase",
    not just inside the relationship roster. ``src/butlers/`` holds shared write
    paths (backfill scripts, modules) that also touch ``relationship.entity_facts``.
    """
    files = _scan_files_under(_ROSTER_ROOT)
    if _SRC_ROOT.is_dir():
        files += _scan_files_under(_SRC_ROOT)
    return files


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

# INSERT ... ON CONFLICT (...) [WHERE ...] DO UPDATE SET <...> conf/observed_at = ...
#   The upsert form mutates an existing ACTIVE row in place on the
#   concurrent-writer race. The lifecycle spec forbids this exactly as it forbids
#   a bare UPDATE: supersession is the only path, and the superseded row keeps its
#   observed_at. We anchor on the entity_facts table name appearing before the
#   ON CONFLICT (the INSERT target) and capture the DO UPDATE SET body.
_CONF_ON_CONFLICT_UPDATE = re.compile(
    r"relationship\.entity_facts\b.*?\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\b\s*\bSET\b"
    r"(?P<setbody>.*?)"
    r"(?:\bWHERE\b|\bRETURNING\b|;|$)",
    re.IGNORECASE,
)

# In-place mutation of either column is forbidden on an active row: conf is
# immutable, and observed_at must be preserved on the superseded row.
_CONF_ASSIGN = re.compile(r"\b(?:conf|observed_at)\b\s*=", re.IGNORECASE)


def _normalise(source: str) -> str:
    joined = _FRAGMENT_JOIN.sub(" ", source)
    return _WHITESPACE.sub(" ", joined)


def _conf_update_offenders(source: str) -> list[str]:
    """Return SET bodies that mutate conf/observed_at on entity_facts in place.

    Catches both shapes of in-place mutation:
      - a bare ``UPDATE relationship.entity_facts ... SET conf=`` (decay job), and
      - an ``INSERT ... ON CONFLICT ... DO UPDATE SET conf=`` upsert, which
        overwrites an existing ACTIVE row on the concurrent-writer race instead
        of routing the collision through supersession.
    """
    text = _normalise(source)
    offenders: list[str] = []
    for pattern in (_CONF_UPDATE, _CONF_ON_CONFLICT_UPDATE):
        for m in pattern.finditer(text):
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
            # Scan now spans two roots (roster/relationship + src/butlers), so
            # report a path relative to the repo root rather than assuming the
            # offender lives under the roster root.
            try:
                rel = path.relative_to(_REPO_ROOT)
            except ValueError:
                rel = path
            violations[str(rel)] = offenders

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


@pytest.mark.parametrize(
    "synthetic",
    [
        # The exact pre-fix shape: upsert that overwrites conf + observed_at on
        # the active row when the concurrent-writer race fires.
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object, conf, observed_at)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (subject, predicate, object) WHERE validity = 'active'
        DO UPDATE
            SET src         = EXCLUDED.src,
                conf        = EXCLUDED.conf,
                observed_at = EXCLUDED.observed_at,
                updated_at  = now()
        RETURNING id
        """,
        # Single-line ON CONFLICT DO UPDATE touching conf only.
        "INSERT INTO relationship.entity_facts (subject) VALUES ($1) "
        "ON CONFLICT (subject, predicate, object) DO UPDATE SET conf = EXCLUDED.conf",
        # ON CONFLICT DO UPDATE touching observed_at only (also forbidden:
        # superseded rows must keep their observed_at).
        "INSERT INTO relationship.entity_facts (subject) VALUES ($1) "
        "ON CONFLICT (subject, predicate, object) DO UPDATE SET observed_at = EXCLUDED.observed_at",
        # Concatenated-fragment form.
        '"INSERT INTO relationship.entity_facts (subject) VALUES ($1) " '
        "\"ON CONFLICT (subject, predicate, object) WHERE validity = 'active' \" "
        '"DO UPDATE SET conf = EXCLUDED.conf, updated_at = now()"',
    ],
)
def test_guardrail_fires_on_synthetic_on_conflict_conf_update(synthetic):
    """The scan must flag an ON CONFLICT ... DO UPDATE that mutates conf/observed_at.

    This is the blind spot that let the central-writer race violation (bu-be16a)
    ship green: the old regex only matched a bare ``UPDATE ... SET``.
    """
    assert _conf_update_offenders(synthetic), (
        "Guardrail failed to detect a synthetic ON CONFLICT DO UPDATE of "
        "conf/observed_at — it would not catch the central-writer upsert race."
    )


def test_guardrail_ignores_legitimate_non_conf_updates():
    """Supersession / re-point UPDATEs (validity, subject, object) are permitted."""
    legitimate = [
        "UPDATE relationship.entity_facts SET validity = 'superseded', updated_at = now() WHERE id = $1",  # noqa: E501
        "UPDATE relationship.entity_facts SET subject = $1, updated_at = now() WHERE id = $2",
        "UPDATE relationship.entity_facts SET object = $1, updated_at = now() WHERE id = $2",
        # Backfill-style ON CONFLICT DO UPDATE that touches only updated_at +
        # primary — this is the spec-permitted idempotent "touch" and must NOT
        # be flagged (it leaves conf and observed_at untouched).
        "INSERT INTO relationship.entity_facts (subject) VALUES ($1) "
        "ON CONFLICT (subject, predicate, object) WHERE validity = 'active' "
        'DO UPDATE SET updated_at = now(), "primary" = EXCLUDED."primary"',
    ]
    for sql in legitimate:
        assert not _conf_update_offenders(sql), f"False positive on: {sql}"
