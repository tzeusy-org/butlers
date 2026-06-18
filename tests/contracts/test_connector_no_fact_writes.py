"""Connector fact-write guardrail — source scan over src/butlers/connectors/.

The ``relationship-entity-lifecycle`` "Ingest" requirement is binding:
connectors MAY *read* ``relationship.entity_facts`` (e.g. priority-contact
policy lookups in ``gmail_policy.py`` and contact-role weighting in
``discretion.py``) but MUST NOT create entities or write facts. Concretely, no
connector code may call ``relationship_assert_fact`` or issue
``INSERT`` / ``UPDATE`` / ``DELETE`` against ``relationship.entity_facts``. Entity
and fact creation happens only through the sanctioned ingest paths (the
fact-extraction pipeline, the central writer, dashboard owner actions, and the
switchboard temp-contact flow) — never a connector.

This is a static source-scan of ``src/butlers/connectors/``. It needs no DB and
is fast and safe to run anywhere. It mirrors the scan style of
``roster/relationship/tests/test_conf_immutability_guardrail.py`` and the
switchboard fact-write guardrail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Source root
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve()
# tests/contracts/ -> tests/ -> repo/
_REPO_ROOT = _HERE.parents[2]
_CONNECTORS_ROOT = _REPO_ROOT / "src" / "butlers" / "connectors"


def _connector_source_files() -> list[Path]:
    """All .py files under src/butlers/connectors/."""
    return list(_CONNECTORS_ROOT.rglob("*.py"))


# ---------------------------------------------------------------------------
# Detection (shared shape with the switchboard fact-write guardrail)
# ---------------------------------------------------------------------------

_FRAGMENT_JOIN = re.compile(r"""["']\s*["']""")
_WHITESPACE = re.compile(r"\s+")

# Write-DML against relationship.entity_facts, allowing an optional table alias.
# Reads (SELECT / FROM / JOIN) are intentionally NOT matched — the priority-
# contact and contact-weight lookups are permitted read-only SQL.
_FACT_WRITE_DML = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+relationship\.entity_facts\b",
    re.IGNORECASE,
)
_ASSERT_FACT_CALL = re.compile(r"\brelationship_assert_fact\s*\(")


def _normalise(source: str) -> str:
    joined = _FRAGMENT_JOIN.sub(" ", source)
    return _WHITESPACE.sub(" ", joined)


def _fact_write_offenders(source: str) -> list[str]:
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


def test_connector_surface_is_non_empty() -> None:
    """The scan must actually cover connector source files."""
    assert _connector_source_files(), f"Expected at least one .py file under {_CONNECTORS_ROOT}"


def test_connectors_write_no_entity_facts() -> None:
    """HEAD: no connector writes relationship.entity_facts or calls
    relationship_assert_fact (relationship-entity-lifecycle "Ingest")."""
    violations: dict[str, list[str]] = {}
    for path in sorted(_connector_source_files()):
        offenders = _fact_write_offenders(path.read_text(encoding="utf-8"))
        if offenders:
            violations[str(path.relative_to(_REPO_ROOT))] = offenders

    assert not violations, (
        "Connectors MUST NOT write entity facts (relationship-entity-lifecycle "
        "Ingest): found relationship_assert_fact call(s) and/or write-DML on "
        "relationship.entity_facts. Connectors may read entity_facts for policy "
        "lookups but never create entities or assert facts; fact creation is the "
        "domain-session / central-writer responsibility.\n"
        f"Offenders: {violations}"
    )


@pytest.mark.parametrize(
    "synthetic",
    [
        "INSERT INTO relationship.entity_facts (subject, predicate, object) VALUES ($1, $2, $3)",
        "UPDATE relationship.entity_facts SET validity = 'superseded' WHERE id = $1",
        "UPDATE relationship.entity_facts AS src SET object = $1 WHERE src.id = $2",
        '"DELETE FROM relationship.entity_facts " "WHERE subject = $1"',
        "await relationship_assert_fact(self._pool, subject=eid, predicate='has-email')",
    ],
)
def test_guardrail_fires_on_synthetic_connector_fact_write(synthetic: str) -> None:
    """The scan must flag a synthetic connector fact-write (red on violation)."""
    assert _fact_write_offenders(synthetic), (
        "Guardrail failed to detect a synthetic connector fact-write — it would "
        "not catch a real assertion path leaking into a connector."
    )


def test_guardrail_allows_read_only_entity_facts_sql() -> None:
    """Read-only SQL against entity_facts (policy lookups) stays permitted."""
    legal = [
        # gmail_policy.py priority-contact JOIN.
        """
        SELECT DISTINCT ef.object AS value
        FROM public.priority_contacts pc
        JOIN public.contacts c ON c.id = pc.contact_id
        JOIN relationship.entity_facts ef ON ef.subject = c.entity_id
        WHERE ef.predicate = 'has-email'
        """,
        # discretion.py contact-role weighting read.
        "SELECT ef.subject FROM relationship.entity_facts ef WHERE ef.predicate = $1",
        # A prose mention without a call site.
        "# connectors never call relationship_assert_fact; reads only",
    ]
    for sql in legal:
        assert not _fact_write_offenders(sql), f"False positive on read-only SQL: {sql!r}"
