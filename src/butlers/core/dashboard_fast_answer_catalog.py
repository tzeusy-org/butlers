"""Typed, provenance-only catalog evidence for dashboard fast admission.

This module consumes an owning memory module's held-authority search hook.
It never fetches a canonical pointer or turns catalog similarity into authority.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from butlers.core.memory_hooks import search_fast_answer_catalog


@dataclass(frozen=True)
class CatalogCandidate:
    catalog_id: str
    owner: str
    source_schema: str
    source_table: str
    source_id: str
    ranking_method: Literal["rrf"]
    rrf_score: float
    semantic_rank: int
    keyword_rank: int

    def provenance(self) -> dict[str, Any]:
        """Return the bounded admission evidence, without catalog content."""
        return {
            "catalog_id": self.catalog_id,
            "owner": self.owner,
            "source_schema": self.source_schema,
            "source_table": self.source_table,
            "source_id": self.source_id,
            "ranking_method": self.ranking_method,
            "rrf_score": self.rrf_score,
            "semantic_rank": self.semantic_rank,
            "keyword_rank": self.keyword_rank,
        }


@dataclass(frozen=True)
class CatalogOutcome:
    state: Literal["matched", "no_match", "unavailable"]
    candidates: tuple[CatalogCandidate, ...] = ()
    selected_owner: str | None = None
    reason: str | None = None


def _identity(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing_catalog_provenance")
    return value


def _candidate(row: Any) -> CatalogCandidate:
    if not isinstance(row, dict):
        raise ValueError("malformed_catalog_candidate")
    owner = row.get("source_butler")
    if owner is None:
        owner = row.get("source_schema")
    owner = _identity(owner)
    score = row.get("rrf_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("malformed_catalog_score")
    if not math.isfinite(score) or score < 0:
        raise ValueError("malformed_catalog_score")
    ranks = (row.get("semantic_rank"), row.get("keyword_rank"))
    if any(type(rank) is not int or rank < 1 for rank in ranks):
        raise ValueError("malformed_catalog_rank")
    return CatalogCandidate(
        catalog_id=_identity(row.get("id")),
        owner=owner,
        source_schema=_identity(row.get("source_schema")),
        source_table=_identity(row.get("source_table")),
        source_id=_identity(row.get("source_id")),
        ranking_method="rrf",
        rrf_score=float(score),
        semantic_rank=ranks[0],
        keyword_rank=ranks[1],
    )


def validate_catalog_evidence(rows: Any, *, eligible_owners: frozenset[str]) -> CatalogOutcome:
    """Poison malformed evidence and select only the adopted same-owner rule.

    All returned rows stay in their original order, including well-formed but
    ineligible owners. No score margin can erase owner disagreement.
    """
    if not isinstance(rows, list) or len(rows) > 3:
        return CatalogOutcome("unavailable", reason="malformed_catalog_envelope")
    try:
        candidates = tuple(_candidate(row) for row in rows)
    except (ValueError, TypeError):
        return CatalogOutcome("unavailable", reason="malformed_catalog_evidence")
    if not candidates:
        return CatalogOutcome("no_match")
    selected = candidates[0].owner
    if selected not in eligible_owners:
        selected = None
    elif len(candidates) > 1:
        highest = max(candidate.rrf_score for candidate in candidates)
        if candidates[1].owner != selected or any(
            candidate.owner != selected
            for candidate in candidates
            if candidate.rrf_score == highest
        ):
            selected = None
    return CatalogOutcome("matched", candidates=candidates, selected_owner=selected)


async def resolve_fast_answer_catalog(
    question: str, *, butler_name: str = "switchboard", eligible_owners: frozenset[str]
) -> CatalogOutcome:
    """Distinguish an authoritative empty read from an unavailable dependency."""
    result = await search_fast_answer_catalog(butler_name, question)
    if result["status"] != "ok":
        return CatalogOutcome("unavailable", reason=result["reason"])
    return validate_catalog_evidence(result["rows"], eligible_owners=eligible_owners)
