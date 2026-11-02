"""Schema discovery must not filter system schemas with ``LIKE 'pg_%'``.

``_`` is a LIKE wildcard, so ``NOT LIKE 'pg_%'`` also drops supported butler
schemas such as ``pgpeer`` or ``pga``. Spell the system-schema filter as
``left(<name>, 3) <> 'pg_'`` (the core_291 pattern), which has no wildcard and
no ``%`` for drivers to escape.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = ("src", "alembic", "roster")

# Unescaped ``pg_`` followed by ``%`` (or the driver-escaped ``%%``) in a LIKE.
# ``pg\_%`` escapes the underscore and stays allowed.
_WILDCARD_PREFIX = re.compile(r"\bLIKE\s+E?'pg_%", re.IGNORECASE)

# Core shared-DDL scans still pending in PR #5247 (bu-mrmn5g), which fixes them
# in place. Drop this allow-list once that PR merges; entries that no longer
# match are harmless until then.
_PENDING_IN_5247 = frozenset(
    f"alembic/versions/core/{name}.py"
    for name in (
        "core_246_ha_person_mapping_receipts",
        "core_247_dashboard_message_citations",
        "core_261_dashboard_fact_source_attribution",
        "core_265_conversation_identity_split",
        "core_266_qa_publication_operations",
        "core_267_qa_publication_operation_invariants",
        "core_268_qa_publication_ledger_hardening",
        "core_290_served_identity_evidence",
        "core_294_qa_publication_exports",
        "core_297_dashboard_job_health",
        "core_298_qa_publication_pr_adoption",
        "core_300_qa_publication_egress_started_at",
        "core_301_qa_publication_export_retention",
        "core_302_qa_publication_refusals",
        "core_303_owner_places",
        "core_305_healing_attempts_closed_at_stamp",
    )
)


def _offenders() -> list[str]:
    hits = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in _PENDING_IN_5247:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if _WILDCARD_PREFIX.search(line):
                    hits.append(f"{rel}:{lineno}: {line.strip()}")
    return hits


def test_wildcard_detector_matches_both_driver_spellings() -> None:
    assert _WILDCARD_PREFIX.search("AND nspname NOT LIKE 'pg_%'")
    assert _WILDCARD_PREFIX.search("AND namespace.nspname NOT LIKE 'pg_%%'")
    assert not _WILDCARD_PREFIX.search(r"AND nspname NOT LIKE 'pg\_%'")
    assert not _WILDCARD_PREFIX.search("AND left(nspname, 3) <> 'pg_'")


def test_no_like_wildcard_system_schema_filters() -> None:
    assert _offenders() == []
