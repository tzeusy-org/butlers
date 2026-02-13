"""Contact resolution — resolve name strings to contact IDs."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_NONE = "none"


def _display_name_from_row(row: asyncpg.Record | dict[str, Any]) -> str:
    first = (row.get("first_name") if isinstance(row, dict) else row["first_name"]) or ""
    last = (row.get("last_name") if isinstance(row, dict) else row["last_name"]) or ""
    nickname = (row.get("nickname") if isinstance(row, dict) else row["nickname"]) or ""
    full = " ".join(part for part in [first, last] if part).strip()
    return full or nickname or first or "Unknown"


async def contact_resolve(
    pool: asyncpg.Pool,
    name: str,
    context: str | None = None,
) -> dict[str, Any]:
    """Resolve a name string to a contact_id.

    Resolution strategy (in order):
    1. Exact full-name match (case-insensitive) -> HIGH confidence, single contact_id.
    2. Partial match (first name or last name, case-insensitive) -> MEDIUM confidence, candidates.
    3. Context-boosted: if context is provided and a candidate's details/notes match,
       boost that candidate's relevance.
    4. No match -> {contact_id: None, confidence: "none", candidates: []}.

    Returns:
        {
            "contact_id": uuid | None,
            "confidence": "high" | "medium" | "none",
            "candidates": [{"contact_id": uuid, "name": str, "confidence": str, "score": int}]
        }
    """
    name = name.strip()
    if not name:
        return {"contact_id": None, "confidence": CONFIDENCE_NONE, "candidates": []}

    # Step 1: Exact match (case-insensitive, listed contacts only)
    exact_rows = await pool.fetch(
        """
        SELECT id, first_name, last_name, nickname, company, job_title, metadata
        FROM contacts
        WHERE listed = true
          AND (
            LOWER(COALESCE(first_name, '')) = LOWER($1)
            OR LOWER(COALESCE(nickname, '')) = LOWER($1)
            OR LOWER(
                TRIM(CONCAT_WS(' ', COALESCE(first_name, ''), COALESCE(last_name, '')))
            ) = LOWER($1)
          )
        ORDER BY updated_at DESC
        """,
        name,
    )

    if len(exact_rows) == 1:
        row = exact_rows[0]
        return {
            "contact_id": row["id"],
            "confidence": CONFIDENCE_HIGH,
            "candidates": [
                {
                    "contact_id": row["id"],
                    "name": _display_name_from_row(row),
                    "confidence": CONFIDENCE_HIGH,
                    "score": 100,
                }
            ],
        }

    if len(exact_rows) > 1:
        # Multiple exact matches -- ambiguous, return as MEDIUM with context boosting
        candidates = _build_candidates(exact_rows, base_score=90)
        if context:
            candidates = await _boost_by_context(pool, candidates, context)
        candidates.sort(key=lambda c: c["score"], reverse=True)
        # If context boosting yields a clear winner, return HIGH
        if len(candidates) >= 2 and candidates[0]["score"] > candidates[1]["score"]:
            return {
                "contact_id": candidates[0]["contact_id"],
                "confidence": CONFIDENCE_HIGH,
                "candidates": candidates,
            }
        return {
            "contact_id": None,
            "confidence": CONFIDENCE_MEDIUM,
            "candidates": candidates,
        }

    # Step 2: Partial match -- first name, last name, or substring
    name_parts = name.split()
    partial_rows = await pool.fetch(
        """
        SELECT id, first_name, last_name, nickname, company, job_title, metadata
        FROM contacts
        WHERE listed = true
          AND (
            first_name ILIKE '%' || $1 || '%'
            OR last_name ILIKE '%' || $1 || '%'
            OR nickname ILIKE '%' || $1 || '%'
            OR company ILIKE '%' || $1 || '%'
            OR EXISTS (
                SELECT 1 FROM unnest(
                    string_to_array(
                        TRIM(CONCAT_WS(' ', COALESCE(first_name, ''), COALESCE(last_name, ''))),
                        ' '
                    )
                ) AS word
                WHERE LOWER(word) = LOWER($1)
            )
          )
        ORDER BY first_name, last_name, nickname
        """,
        name,
    )

    # Also try matching individual input words against contact name words
    if not partial_rows and len(name_parts) > 1:
        # Multi-word input with no substring match -- try each word
        conditions = []
        params: list[Any] = []
        for i, part in enumerate(name_parts, start=1):
            conditions.append(f"first_name ILIKE '%' || ${i} || '%'")
            conditions.append(f"last_name ILIKE '%' || ${i} || '%'")
            conditions.append(f"nickname ILIKE '%' || ${i} || '%'")
            params.append(part)
        query = f"""
            SELECT id, first_name, last_name, nickname, company, job_title, metadata
            FROM contacts
            WHERE listed = true AND ({" OR ".join(conditions)})
            ORDER BY first_name, last_name, nickname
        """
        partial_rows = await pool.fetch(query, *params)

    if not partial_rows:
        return {"contact_id": None, "confidence": CONFIDENCE_NONE, "candidates": []}

    # Score partial matches
    candidates = _score_partial_matches(partial_rows, name, name_parts)

    # Context boosting
    if context:
        candidates = await _boost_by_context(pool, candidates, context)

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # If there's exactly one candidate, or one clearly leads, return it
    if len(candidates) == 1:
        return {
            "contact_id": candidates[0]["contact_id"],
            "confidence": CONFIDENCE_MEDIUM,
            "candidates": candidates,
        }

    return {
        "contact_id": None,
        "confidence": CONFIDENCE_MEDIUM,
        "candidates": candidates,
    }


def _build_candidates(rows: list[asyncpg.Record], base_score: int = 50) -> list[dict[str, Any]]:
    """Build candidate list from DB rows with a base score."""
    return [
        {
            "contact_id": row["id"],
            "name": _display_name_from_row(row),
            "confidence": CONFIDENCE_MEDIUM,
            "score": base_score,
        }
        for row in rows
    ]


def _score_partial_matches(
    rows: list[asyncpg.Record],
    query_name: str,
    query_parts: list[str],
) -> list[dict[str, Any]]:
    """Score partial matches based on how well the name matches the query."""
    candidates = []
    query_lower = query_name.lower()

    for row in rows:
        contact_name = _display_name_from_row(row)
        contact_lower = contact_name.lower()
        contact_parts = [p.lower() for p in contact_name.split()]
        score = 0

        # Check if query matches the beginning of first name (strongest partial signal)
        if contact_parts and contact_parts[0].startswith(query_lower):
            score = 70
        # Check if query matches the beginning of last name
        elif len(contact_parts) > 1 and contact_parts[-1].startswith(query_lower):
            score = 65
        # Check if any word in contact name exactly matches any query word
        elif any(cp == qp.lower() for cp in contact_parts for qp in query_parts):
            score = 60
        # Check if query is a substring of the contact name
        elif query_lower in contact_lower:
            score = 50
        # Any other match (from the SQL)
        else:
            score = 40

        candidates.append(
            {
                "contact_id": row["id"],
                "name": contact_name,
                "confidence": CONFIDENCE_MEDIUM,
                "score": score,
            }
        )

    return candidates


async def _boost_by_context(
    pool: asyncpg.Pool,
    candidates: list[dict[str, Any]],
    context: str,
) -> list[dict[str, Any]]:
    """Boost candidate scores based on context matching against metadata and notes."""
    context_words = [w.lower() for w in context.split() if len(w) > 2]

    for candidate in candidates:
        cid = candidate["contact_id"]

        # Check metadata and explicit profile fields
        detail_row = await pool.fetchrow(
            """
            SELECT metadata, company, job_title, first_name, last_name, nickname
            FROM contacts
            WHERE id = $1
            """,
            cid,
        )
        if detail_row:
            metadata = detail_row["metadata"]
            metadata_text = (
                json.dumps(metadata).lower()
                if isinstance(metadata, dict)
                else str(metadata or "").lower()
            )
            profile_text = " ".join(
                [
                    str(detail_row["company"] or ""),
                    str(detail_row["job_title"] or ""),
                    str(detail_row["first_name"] or ""),
                    str(detail_row["last_name"] or ""),
                    str(detail_row["nickname"] or ""),
                ]
            ).lower()
            for word in context_words:
                if word in metadata_text or word in profile_text:
                    candidate["score"] += 10
                    break

        # Check notes
        note_rows = await pool.fetch("SELECT body FROM notes WHERE contact_id = $1 LIMIT 10", cid)
        for note in note_rows:
            note_text = str(note["body"] or "").lower()
            for word in context_words:
                if word in note_text:
                    candidate["score"] += 5
                    break

        # Check interactions
        interaction_rows = await pool.fetch(
            "SELECT summary FROM interactions WHERE contact_id = $1"
            " AND summary IS NOT NULL LIMIT 10",
            cid,
        )
        for interaction in interaction_rows:
            int_text = interaction["summary"].lower()
            for word in context_words:
                if word in int_text:
                    candidate["score"] += 5
                    break

    return candidates
