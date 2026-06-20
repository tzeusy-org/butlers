"""Read-only butler-facing entity lookup for ``relationship_lookup``.

This is the symmetric READ path to :func:`relationship_assert_fact`'s write
path.  Butlers can already *write* facts about a person, vendor, company, or
place; this closes the gap by letting any butler *read* the owner's
relationship knowledge so it can contextualise advice.

Contract (binding spec
``openspec/changes/entity-v3-lifecycle-and-depth/specs/relationship-entity-lookup``):

1. **Exactly one of** ``entity_id`` (UUID) or ``entity_ref`` (a name / alias /
   contact-value string) MUST be supplied.  Both or neither raises
   :class:`ValueError`.

2. **Deterministic resolution** — ``entity_ref`` is resolved with the SAME
   rule-based ranking as ``GET /api/relationship/entities/search`` (prefix 100 >
   contact-value 70 > substring 50 > predicate-label 30), with the lookup
   tie-break of ``last_seen DESC`` then pinned ``tier ASC``.  No model call, no
   embedding service — pure SQL ``ILIKE``.

3. **Ambiguity is surfaced, never guessed** — when the top score is shared by
   more than one entity, ``entity`` is ``None`` and ``facts`` is empty; up to 3
   candidates are returned so the caller re-invokes with an explicit
   ``entity_id``.

4. **Provenance + staleness** — every fact row carries ``src``, ``conf``,
   ``verified``, ``primary``, ``observed_at``/``last_seen`` and a read-time
   derived ``staleness_band`` (see :mod:`.staleness`).

5. **Read-only** — the function performs ZERO writes, mutations, or schedule
   side effects.  Repeated identical calls leave the database byte-identical.

6. **Miss is a value, not an error** — an unresolved reference returns a
   structured ``{entity: None, facts: [], resolution: {ambiguous: False,
   candidates: []}}`` so caller sessions branch without retry loops.
"""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from .staleness import (
    identity_staleness_band_sql,
    narrative_scope_sql,
    narrative_staleness_band_sql,
    staleness_band_sql_for,
)

# ---------------------------------------------------------------------------
# Ranking constants — MUST match GET /entities/search (router.py::search_entities).
# ---------------------------------------------------------------------------
_SCORE_PREFIX = 100
_SCORE_CONTACT_FACT = 70
_SCORE_SUBSTRING = 50
_SCORE_PREDICATE = 30

#: Maximum candidates surfaced on an ambiguous / miss resolution.
_MAX_CANDIDATES = 3


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def _validate_args(entity_id: uuid.UUID | None, entity_ref: str | None) -> None:
    """Raise ``ValueError`` unless exactly one of the two arguments is supplied.

    The spec requires a single resolution mode per call: an explicit
    ``entity_id`` OR a textual ``entity_ref`` — never both, never neither.
    """
    has_id = entity_id is not None
    # Treat an all-whitespace ref as absent (it would resolve to nothing anyway).
    has_ref = entity_ref is not None and entity_ref.strip() != ""
    if has_id and has_ref:
        raise ValueError(
            "relationship_lookup: pass exactly one of entity_id or entity_ref, not both."
        )
    if not has_id and not has_ref:
        raise ValueError("relationship_lookup: exactly one of entity_id or entity_ref is required.")


# ---------------------------------------------------------------------------
# Reference resolution — deterministic ranking (mirrors GET /entities/search)
# ---------------------------------------------------------------------------


async def _resolve_ref(
    pool: asyncpg.Pool,
    ref: str,
) -> tuple[asyncpg.Record | None, bool, list[dict[str, Any]]]:
    """Resolve *ref* to a single entity via deterministic ranking.

    Returns ``(top_row, ambiguous, candidates)`` where:

    - ``top_row`` is the unambiguous winner row (or ``None`` on miss/ambiguity).
    - ``ambiguous`` is ``True`` when ≥2 entities share the top score.
    - ``candidates`` is the top-N candidate dicts ``{id, canonical_name, score}``.

    Ranking branches and scores match ``search_entities``; the tie-break adds
    ``last_seen DESC`` (most recently observed first) then pinned ``tier ASC``.
    Pure SQL — no LLM, no embedding.
    """
    rows = await pool.fetch(
        """
        WITH ranked AS (
            SELECT
                entity_id,
                MAX(score) AS score,
                (ARRAY_AGG(match_kind ORDER BY score DESC))[1] AS match_kind
            FROM (
                -- Branch 1: prefix match on canonical_name or any alias (score=100)
                SELECT e.id AS entity_id, $2::int AS score, 'prefix'::text AS match_kind
                FROM public.entities e
                WHERE (e.metadata->>'merged_into') IS NULL
                  AND (
                      e.canonical_name ILIKE ($1 || '%')
                      OR EXISTS (
                          SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) AS alias_val
                          WHERE alias_val ILIKE ($1 || '%')
                      )
                  )

                UNION ALL

                -- Branch 2: contact-fact value match (score=70)
                SELECT f.subject AS entity_id, $3::int AS score, 'contact_fact'::text AS match_kind
                FROM relationship.entity_facts f
                WHERE f.predicate LIKE 'has-%'
                  AND f.object_kind = 'literal'
                  AND f.object ILIKE ('%' || $1 || '%')
                  AND f.validity = 'active'

                UNION ALL

                -- Branch 3: substring match on canonical_name or any alias (score=50)
                SELECT e.id AS entity_id, $4::int AS score, 'substring'::text AS match_kind
                FROM public.entities e
                WHERE (e.metadata->>'merged_into') IS NULL
                  AND (
                      e.canonical_name ILIKE ('%' || $1 || '%')
                      OR EXISTS (
                          SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) AS alias_val
                          WHERE alias_val ILIKE ('%' || $1 || '%')
                      )
                  )

                UNION ALL

                -- Branch 4: predicate label match (score=30)
                SELECT f.subject AS entity_id, $5::int AS score, 'predicate'::text AS match_kind
                FROM relationship.entity_facts f
                WHERE f.predicate ILIKE ('%' || $1 || '%')
                  AND f.validity = 'active'
            ) AS candidates
            GROUP BY entity_id
        ),
        annotated AS (
            SELECT
                r.entity_id,
                e.canonical_name,
                r.score,
                r.match_kind,
                (
                    SELECT max(rf.last_seen)
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = r.entity_id
                      AND rf.validity = 'active'
                ) AS last_seen,
                (
                    SELECT f.content::int
                    FROM facts f
                    WHERE f.entity_id = r.entity_id
                      AND f.predicate = 'dunbar_tier_override'
                      AND f.scope     = 'relationship'
                      AND f.validity  = 'active'
                    LIMIT 1
                ) AS tier
            FROM ranked r
            JOIN public.entities e ON e.id = r.entity_id
            WHERE (e.metadata->>'merged_into') IS NULL
        )
        SELECT entity_id, canonical_name, score, match_kind, last_seen, tier
        FROM annotated
        ORDER BY score DESC, last_seen DESC NULLS LAST, tier ASC NULLS LAST, entity_id ASC
        """,
        ref.strip(),
        _SCORE_PREFIX,
        _SCORE_CONTACT_FACT,
        _SCORE_SUBSTRING,
        _SCORE_PREDICATE,
    )

    if not rows:
        return None, False, []

    candidates = [
        {
            "id": str(r["entity_id"]),
            "canonical_name": r["canonical_name"],
            "score": int(r["score"]),
        }
        for r in rows[:_MAX_CANDIDATES]
    ]

    top_score = rows[0]["score"]
    top_tied = [r for r in rows if r["score"] == top_score]
    ambiguous = len(top_tied) > 1

    if ambiguous:
        return None, True, candidates
    return rows[0], False, candidates


# ---------------------------------------------------------------------------
# Entity header
# ---------------------------------------------------------------------------


async def _fetch_entity_header(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Return the entity header dict, or ``None`` when the entity does not exist.

    ``tier`` is non-null ONLY when a ``dunbar_tier_override`` fact is pinned.
    ``state`` is the curation classification (healthy / unidentified /
    duplicate-candidate / stale) — derived deterministically, no LLM.
    """
    row = await pool.fetchrow(
        """
        SELECT
            e.id,
            e.canonical_name,
            e.entity_type,
            COALESCE(e.aliases, '{}') AS aliases,
            COALESCE(e.roles, '{}')   AS roles,
            (
                SELECT f.content::int
                FROM facts f
                WHERE f.entity_id = e.id
                  AND f.predicate = 'dunbar_tier_override'
                  AND f.scope     = 'relationship'
                  AND f.validity  = 'active'
                LIMIT 1
            ) AS tier
        FROM public.entities e
        WHERE e.id = $1
          AND (e.metadata->>'merged_into') IS NULL
        """,
        entity_id,
    )
    if row is None:
        return None

    return {
        "id": str(row["id"]),
        "canonical_name": row["canonical_name"],
        "entity_type": row["entity_type"],
        "aliases": list(row["aliases"]) if row["aliases"] else [],
        "roles": list(row["roles"]) if row["roles"] else [],
        "tier": row["tier"],
        "state": await _classify_state(pool, entity_id),
    }


async def _classify_state(pool: asyncpg.Pool, entity_id: uuid.UUID) -> str:
    """Derive the entity curation ``state`` deterministically.

    Mirrors the priority order of ``router.py::_classify_entity_state``:
    unidentified > duplicate-candidate > stale > healthy.  Read-only.
    """
    row = await pool.fetchrow(
        """
        SELECT
            (e.metadata->>'unidentified' = 'true')        AS is_unidentified,
            (e.metadata->>'duplicate_candidate' = 'true') AS is_dup_flagged,
            EXISTS (
                SELECT 1 FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
                  AND rf.last_seen > (now() - INTERVAL '365 days')
            ) AS has_fresh_fact,
            EXISTS (
                SELECT 1
                FROM relationship.entity_facts f_self
                JOIN relationship.entity_facts f_peer
                  ON f_peer.predicate = f_self.predicate
                 AND f_peer.object    = f_self.object
                 AND f_peer.validity  = 'active'
                 AND f_peer.subject  <> f_self.subject
                WHERE f_self.subject = e.id
                  AND f_self.validity = 'active'
                  AND f_self.predicate IN ('has-email', 'has-phone')
            ) AS shares_contact
        FROM public.entities e
        WHERE e.id = $1
        """,
        entity_id,
    )
    if row is None:
        return "healthy"
    if row["is_unidentified"]:
        return "unidentified"
    if row["is_dup_flagged"] or row["shares_contact"]:
        return "duplicate-candidate"
    if not row["has_fresh_fact"]:
        return "stale"
    return "healthy"


# ---------------------------------------------------------------------------
# Fact reads — identity store (relationship.entity_facts) then narrative (facts)
# ---------------------------------------------------------------------------


async def _fetch_identity_facts(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Active identity-store facts with read-time ``staleness_band``."""
    rows = await pool.fetch(
        f"""
        SELECT
            f.predicate,
            f.object,
            f.object_kind,
            f.src,
            f.conf,
            f.verified,
            f."primary",
            f.observed_at,
            f.last_seen,
            {identity_staleness_band_sql("f")} AS staleness_band
        FROM relationship.entity_facts f
        WHERE f.subject  = $1
          AND f.validity = 'active'
        ORDER BY f.created_at DESC
        """,
        entity_id,
    )
    return [
        {
            "store": "identity",
            "predicate": r["predicate"],
            "object": r["object"],
            "object_kind": r["object_kind"],
            "src": r["src"],
            "conf": float(r["conf"]) if r["conf"] is not None else 1.0,
            "verified": bool(r["verified"]),
            "primary": r["primary"],
            "observed_at": r["observed_at"],
            "last_seen": r["last_seen"],
            "staleness_band": r["staleness_band"],
        }
        for r in rows
    ]


async def _fetch_narrative_facts(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Active narrative-store facts (memory-module ``facts``) with staleness.

    The narrative ``facts`` table has no ``last_seen``, ``verified`` or
    ``primary`` columns; those fields are emitted as ``None`` (``last_seen`` is
    omitted by the caller for narrative rows per the spec).  ``content`` is the
    object value; ``source_butler`` is the provenance src.

    Scope-filtered per the canonical narrative read rule
    (``staleness.narrative_scope_sql`` — ``scope IN ('relationship', 'global')``)
    so this MCP tool surfaces the SAME fact set as the dashboard drill, delta
    banner, and compare blocks (bu-3jrq3). It previously applied no scope filter,
    which surfaced foreign-butler-scoped rows the dashboard hid.

    Read-only: this is a plain SELECT against the memory module's ``facts``
    table — no write to that module's surface.
    """
    rows = await pool.fetch(
        f"""
        SELECT
            f.predicate,
            f.content     AS object,
            f.source_butler AS src,
            f.confidence  AS conf,
            f.observed_at,
            (
                CASE WHEN f.object_entity_id IS NOT NULL THEN 'entity' ELSE 'literal' END
            ) AS object_kind,
            {narrative_staleness_band_sql("f")} AS staleness_band
        FROM facts f
        WHERE f.entity_id = $1
          AND {narrative_scope_sql("f")}
          AND f.validity  = 'active'
        ORDER BY f.created_at DESC
        """,
        entity_id,
    )
    return [
        {
            "store": "narrative",
            "predicate": r["predicate"],
            "object": r["object"],
            "object_kind": r["object_kind"],
            "src": r["src"],
            "conf": float(r["conf"]) if r["conf"] is not None else 1.0,
            "verified": None,
            "primary": None,
            "observed_at": r["observed_at"],
            # last_seen intentionally omitted — narrative store has no such column.
            "staleness_band": r["staleness_band"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Recency
# ---------------------------------------------------------------------------


async def _fetch_recency(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
) -> dict[str, Any]:
    """Entity-wide recency block: ``{last_seen, last_interaction_at, staleness_band}``.

    ``last_seen`` is the MAX across active identity facts.  ``last_interaction_at``
    mirrors ``router.py::get_contact`` — the MAX ``valid_at`` over active
    ``interaction_*`` narrative facts keyed on the entity.  The entity-level
    ``staleness_band`` is derived from ``last_seen`` (identity chain) so the
    whole-entity freshness is consistent with its facts.
    """
    row = await pool.fetchrow(
        """
        SELECT
            (
                SELECT max(rf.last_seen)
                FROM relationship.entity_facts rf
                WHERE rf.subject = $1
                  AND rf.validity = 'active'
            ) AS last_seen,
            (
                SELECT max(f.valid_at)
                FROM facts f
                WHERE f.entity_id = $1
                  AND f.predicate LIKE 'interaction_%'
                  AND f.validity = 'active'
                  AND f.scope = 'relationship'
            ) AS last_interaction_at
        """,
        entity_id,
    )
    last_seen = row["last_seen"] if row else None
    last_interaction_at = row["last_interaction_at"] if row else None

    # Derive whole-entity band from the identity chain reference (last_seen).
    # Reuse the single staleness band builder so the thresholds and the exact
    # boundary semantics stay identical to per-fact bands — no inline duplicate
    # of the 30d/180d intervals here.
    band: str | None = None
    if last_seen is not None:
        band_row = await pool.fetchrow(
            f"SELECT {staleness_band_sql_for('$1::timestamptz')} AS band",
            last_seen,
        )
        band = band_row["band"] if band_row else None

    return {
        "last_seen": last_seen,
        "last_interaction_at": last_interaction_at,
        "staleness_band": band,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def relationship_lookup(
    pool: asyncpg.Pool,
    *,
    entity_id: uuid.UUID | None = None,
    entity_ref: str | None = None,
) -> dict[str, Any]:
    """Read the owner's relationship knowledge for one entity (read-only).

    Pass EXACTLY ONE of ``entity_id`` or ``entity_ref``.  Returns a structured
    dict ``{entity, facts, recency, resolution}``.  Misses and ambiguity are
    values (``entity: None``), never exceptions; only a bad-argument call
    raises :class:`ValueError`.

    See the module docstring for the full contract.
    """
    _validate_args(entity_id, entity_ref)

    resolution: dict[str, Any] | None = None
    resolved_id: uuid.UUID | None = entity_id

    if entity_ref is not None and entity_ref.strip() != "":
        top_row, ambiguous, candidates = await _resolve_ref(pool, entity_ref)
        if ambiguous:
            # Equal top score → do NOT guess. Return candidates only.
            return {
                "entity": None,
                "facts": [],
                "recency": None,
                "resolution": {
                    "matched_on": None,
                    "score": None,
                    "ambiguous": True,
                    "candidates": candidates,
                },
            }
        if top_row is None:
            # Miss — structured value, not an exception.
            return {
                "entity": None,
                "facts": [],
                "recency": None,
                "resolution": {
                    "matched_on": None,
                    "score": None,
                    "ambiguous": False,
                    "candidates": [],
                },
            }
        resolved_id = top_row["entity_id"]
        resolution = {
            "matched_on": top_row["match_kind"],
            "score": int(top_row["score"]),
            "ambiguous": False,
            "candidates": candidates,
        }

    assert resolved_id is not None  # guaranteed by _validate_args + resolution branch
    header = await _fetch_entity_header(pool, resolved_id)
    if header is None:
        # entity_id supplied but not found (or tombstoned) → structured miss.
        # Spec (relationship-entity-lookup §"Miss is a value, not an error"):
        # a miss MUST carry a structured resolution block on BOTH paths, never a
        # bare ``resolution: None``. The id-path miss never built a resolution
        # (no ref to resolve), so synthesise the canonical empty-miss block; a
        # ref that resolved then vanished keeps its existing resolution.
        return {
            "entity": None,
            "facts": [],
            "recency": None,
            "resolution": resolution
            if resolution is not None
            else {"matched_on": None, "score": None, "ambiguous": False, "candidates": []},
        }

    identity_facts = await _fetch_identity_facts(pool, resolved_id)
    narrative_facts = await _fetch_narrative_facts(pool, resolved_id)
    # Identity facts ordered before narrative facts (lifecycle layering).
    facts = identity_facts + narrative_facts

    recency = await _fetch_recency(pool, resolved_id)

    return {
        "entity": header,
        "facts": facts,
        "recency": recency,
        "resolution": resolution,
    }
