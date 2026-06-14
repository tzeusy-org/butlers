"""Switchboard fact-write guardrail — source scan for entity-fact assertions.

The ``switchboard-identity`` entity-v3 delta is binding: the Switchboard MUST
NOT call ``relationship_assert_fact`` and MUST NOT issue ``INSERT`` / ``UPDATE``
/ ``DELETE`` against ``relationship.entity_facts``. Its access to that table is
read-only channel resolution via ``resolve_contact_by_channel()`` (a ``SELECT``
join, per ``relationship-facts`` and ``switchboard-identity``). Fact assertion
arising from ingress happens exclusively inside the routed domain-butler session,
never in switchboard classify-and-route code.

Two flows stay LEGAL and MUST NOT be flagged:

1. The mandated read — ``resolve_contact_by_channel()`` issues ``SELECT`` /
   ``FROM`` / ``JOIN`` against ``relationship.entity_facts``. Reads are fine.
2. The standing temp-contact flow — the unknown-sender path creates rows in
   ``public.contacts`` / ``public.entities`` before routing. Those writes target
   ``public.*``, never ``relationship.entity_facts``, so they are not entity-fact
   assertions.

Scan scope is the Switchboard module itself: its roster tree
(``roster/switchboard/``) plus the switchboard-owned source modules
(``src/butlers/switchboard_wiring.py``, ``src/butlers/core_tools/_switchboard.py``),
AND the shared channel-resolution helper ``src/butlers/identity.py``. The helper
hosts ``create_temp_contact()``, which runs on the switchboard ingress path; the
entity-v3 ``switchboard-identity`` delta requires that ingress write nothing to
``relationship.entity_facts``. As of bu-hvrt1 the channel-triple assertion moved
OUT of ``create_temp_contact`` into a deterministic routing-pipeline hook
(``relationship.tools.relationship_assert_fact.assert_sender_channel_fact``), so
``identity.py`` must now be free of ``relationship_assert_fact`` calls and
``entity_facts`` write-DML — this scan guards that. The helper's *mandated read*
(``resolve_contact_by_channel``) is covered positively (see
``test_identity_helper_keeps_the_mandated_read``).

It needs no DB, no async fixtures, and no external dependencies. It mirrors the
scan style of ``roster/relationship/tests/test_chronicler_boundary.py`` and
``test_conf_immutability_guardrail.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Source roots to scan
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()

# roster/switchboard/
_SWITCHBOARD_ROSTER = _HERE.parents[1]
assert _SWITCHBOARD_ROSTER.name == "switchboard", (
    f"Unexpected roster root: {_SWITCHBOARD_ROSTER}. "
    "This test must live at roster/switchboard/tests/test_switchboard_no_fact_writes.py"
)

# Repo root: roster/switchboard/tests/ -> roster/switchboard/ -> roster/ -> repo/
_REPO_ROOT = _SWITCHBOARD_ROSTER.parents[1]

# Switchboard-owned source modules outside the roster tree (the switchboard
# wiring + core-tool surface). These are switchboard code, so they fall inside
# this invariant.
_SWITCHBOARD_SRC_FILES = (
    _REPO_ROOT / "src" / "butlers" / "switchboard_wiring.py",
    _REPO_ROOT / "src" / "butlers" / "core_tools" / "_switchboard.py",
)

# The shared channel-resolution helper that hosts the mandated read.
_IDENTITY_HELPER = _REPO_ROOT / "src" / "butlers" / "identity.py"


def _switchboard_source_files() -> list[Path]:
    """All .py files on the switchboard module surface, excluding this test file.

    Tests under roster/switchboard/tests/ are excluded: they construct synthetic
    SQL to prove the guardrail bites (see the synthetic-violation tests below).

    The shared channel-resolution helper (``src/butlers/identity.py``) is included
    because ``create_temp_contact()`` runs on the switchboard ingress path and
    must (post bu-hvrt1) never write ``relationship.entity_facts``.
    """
    roster_files = [
        p
        for p in _SWITCHBOARD_ROSTER.rglob("*.py")
        if p.resolve() != _HERE and "tests" not in p.parts
    ]
    src_files = [p for p in (*_SWITCHBOARD_SRC_FILES, _IDENTITY_HELPER) if p.exists()]
    return roster_files + src_files


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
#
# SQL in this codebase is written both as multi-line triple-quoted strings and
# as adjacent concatenated string fragments ("UPDATE ..." "SET ..."). To match
# across both shapes we normalise the raw source: drop the quote/newline noise
# between adjacent string fragments and collapse whitespace, then scan the
# normalised text.

# Adjacent-string-fragment join: `"  ...  "   "  ...  "` -> single space.
_FRAGMENT_JOIN = re.compile(r"""["']\s*["']""")
_WHITESPACE = re.compile(r"\s+")

# Write-DML against relationship.entity_facts. An optional table alias may follow
# the table name (e.g. `UPDATE relationship.entity_facts AS src SET ...`). Reads
# (SELECT / FROM / JOIN) are intentionally NOT matched — they are the mandated
# resolve_contact_by_channel() path.
_FACT_WRITE_DML = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+relationship\.entity_facts\b",
    re.IGNORECASE,
)

# A call to the central writer relationship_assert_fact(...). Matches the call
# form (trailing paren) so that prose mentions in comments/docstrings that lack
# a call site do not trip the scan; an actual invocation always has the paren.
_ASSERT_FACT_CALL = re.compile(r"\brelationship_assert_fact\s*\(")


def _normalise(source: str) -> str:
    joined = _FRAGMENT_JOIN.sub(" ", source)
    return _WHITESPACE.sub(" ", joined)


def _fact_write_offenders(source: str) -> list[str]:
    """Return offending fragments: write-DML on entity_facts or assert-fact calls."""
    text = _normalise(source)
    offenders: list[str] = []
    for m in _FACT_WRITE_DML.finditer(text):
        offenders.append(m.group(0).strip())
    for m in _ASSERT_FACT_CALL.finditer(text):
        offenders.append(m.group(0).strip())
    return offenders


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_switchboard_surface_is_non_empty() -> None:
    """The scan must actually cover switchboard source (guards against a glob
    that silently matches nothing)."""
    sources = _switchboard_source_files()
    assert sources, "Expected at least one .py file on the switchboard module surface"
    # The identity-injection ingress tool (the temp-contact caller) must be in
    # scope — it is the most likely place a switchboard fact-write would regress.
    assert any(p.name == "inject.py" for p in sources), (
        "roster/switchboard/tools/identity/inject.py (ingress identity injection) must be scanned."
    )


def test_switchboard_asserts_no_entity_facts() -> None:
    """HEAD: no switchboard-module code writes relationship.entity_facts or calls
    relationship_assert_fact (switchboard-identity entity-v3 delta)."""
    violations: dict[str, list[str]] = {}
    for path in sorted(_switchboard_source_files()):
        offenders = _fact_write_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            violations[str(path.relative_to(_REPO_ROOT))] = offenders

    assert not violations, (
        "Switchboard MUST NOT assert entity facts (switchboard-identity delta): "
        "found relationship_assert_fact call(s) and/or write-DML on "
        "relationship.entity_facts. Fact assertion happens only inside the routed "
        "domain-butler session; the switchboard's access to entity_facts is "
        "read-only channel resolution via resolve_contact_by_channel().\n"
        f"Offenders: {violations}"
    )


def test_identity_helper_keeps_the_mandated_read() -> None:
    """The shared resolve_contact_by_channel() read MUST remain present.

    ``switchboard-identity`` keeps switchboard's entity_facts access as read-only
    channel resolution. This pins the mandated read so a refactor that drops it
    (and silently strands switchboard routing) is caught here.
    """
    assert _IDENTITY_HELPER.exists(), f"Expected shared identity helper at {_IDENTITY_HELPER}"
    text = _IDENTITY_HELPER.read_text(encoding="utf-8")
    assert "def resolve_contact_by_channel" in text, (
        "resolve_contact_by_channel() is the switchboard's mandated read into "
        "relationship.entity_facts (switchboard-identity). It must remain present."
    )


def test_identity_helper_does_not_assert_entity_facts() -> None:
    """entity-v3 (bu-hvrt1): ``src/butlers/identity.py`` must not write entity_facts.

    ``create_temp_contact()`` runs on the switchboard ingress path. After bu-hvrt1
    it mints only ``public.entities`` / ``public.contacts`` rows; the sender's
    channel triple is asserted by the deterministic routing-pipeline hook
    ``assert_sender_channel_fact``. The helper must therefore be free of
    ``relationship_assert_fact`` calls and ``entity_facts`` write-DML.
    """
    assert _IDENTITY_HELPER.exists(), f"Expected shared identity helper at {_IDENTITY_HELPER}"
    offenders = _fact_write_offenders(_IDENTITY_HELPER.read_text(encoding="utf-8"))
    assert not offenders, (
        "src/butlers/identity.py MUST NOT assert entity facts on the switchboard "
        "ingress path (entity-v3 switchboard-identity delta). The channel-triple "
        "assertion belongs to the routing-pipeline hook assert_sender_channel_fact, "
        f"not create_temp_contact.\nOffenders: {offenders}"
    )


@pytest.mark.parametrize(
    "synthetic",
    [
        # INSERT — multi-line triple-quoted form.
        """
        INSERT INTO relationship.entity_facts (subject, predicate, object)
        VALUES ($1, 'has-handle', $2)
        """,
        # UPDATE — single-line form.
        "UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = $1",
        # UPDATE with table alias.
        "UPDATE relationship.entity_facts AS src SET object = $1 WHERE src.id = $2",
        # DELETE — concatenated-fragment form (as the codebase often writes SQL).
        '"DELETE FROM relationship.entity_facts " "WHERE id = $1"',
        # Direct call to the central writer.
        "await relationship_assert_fact(pool, subject=eid, predicate='has-email')",
    ],
)
def test_guardrail_fires_on_synthetic_fact_write(synthetic: str) -> None:
    """The scan must flag a synthetic switchboard fact-write (red on violation)."""
    assert _fact_write_offenders(synthetic), (
        "Guardrail failed to detect a synthetic switchboard fact-write — it would "
        "not catch a real assertion path leaking into classify-and-route code."
    )


def test_guardrail_allows_the_mandated_read_and_temp_contact_flow() -> None:
    """Legal flows MUST NOT trip the scan.

    1. resolve_contact_by_channel() reads entity_facts via SELECT/FROM/JOIN.
    2. The temp-contact flow writes public.contacts / public.entities, not
       relationship.entity_facts.
    """
    legal = [
        # The mandated read — SELECT join on entity_facts.
        """
        SELECT ef.subject, e.roles
        FROM relationship.entity_facts ef
        JOIN public.entities e ON e.id = ef.subject
        WHERE ef.predicate = $1 AND ef.object = $2 AND ef.validity = 'active'
        """,
        # Temp-contact creation — writes public.*, never entity_facts.
        "INSERT INTO public.entities (id, display_name) VALUES ($1, $2)",
        "INSERT INTO public.contacts (id, entity_id, name) VALUES ($1, $2, $3)",
        # Unrelated switchboard state write.
        "INSERT INTO butler_state (key, value) VALUES ($1, $2)",
        # A prose mention of the writer name without a call site (comment/docstring).
        "# fact assertion happens via relationship_assert_fact in the routed session",
    ]
    for sql in legal:
        assert not _fact_write_offenders(sql), f"False positive on legal flow: {sql!r}"
