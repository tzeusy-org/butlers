"""Collection vocabulary resolution -- the declared-name boundary.

General no longer auto-vivifies a collection from any string a caller hands
it (bu-2jtfw.9). ``resolve_collection_name`` is the only path ``item_create``
uses to turn a caller-supplied name into a canonical collection: an exact or
punctuation-insensitive match against ``collection_vocabulary``/
``collection_aliases`` resolves silently; a near match resolves via trigram
similarity; anything else raises ``UnknownCollectionError`` naming the
nearest candidates and the ``collection_declare`` verb.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import asyncpg

# Below this trigram similarity score, a candidate is offered as a suggestion
# but never auto-resolved to.
TRIGRAM_RESOLUTION_THRESHOLD = 0.4


class UnknownCollectionError(ValueError):
    """Raised when a collection name cannot be resolved to a canonical entry.

    ``candidates`` holds the nearest known canonical names (may be empty),
    ordered best match first.
    """

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        hint = (
            f"nearest candidates: {', '.join(candidates)}" if candidates else "no near match found"
        )
        super().__init__(
            f"Unknown collection {name!r} ({hint}). "
            "Use collection_declare(name, shape_description) to create it first."
        )


def _normalize(name: str) -> str:
    """Punctuation/case/separator-insensitive key for exact-ish matching.

    Lowercases, collapses runs of whitespace/underscore/hyphen into a single
    hyphen, and strips anything else that isn't alphanumeric or a hyphen --
    so 'email_records', 'Email Records', and 'email-records' all normalize
    to 'email-records'.
    """
    lowered = name.strip().lower()
    collapsed = re.sub(r"[\s_\-]+", "-", lowered)
    stripped = re.sub(r"[^a-z0-9\-]", "", collapsed)
    return stripped.strip("-")


async def _canonical_for_alias(pool: asyncpg.Pool, alias: str) -> str | None:
    row = await pool.fetchrow(
        "SELECT canonical_name, merged_into FROM collection_aliases WHERE alias = $1",
        alias,
    )
    if row is None:
        return None
    return row["merged_into"] or row["canonical_name"]


async def resolve_collection_name(
    pool: asyncpg.Pool,
    name: str,
    *,
    threshold: float = TRIGRAM_RESOLUTION_THRESHOLD,
) -> str:
    """Resolve a caller-supplied collection name to its canonical form.

    Raises ``UnknownCollectionError`` when no declared collection matches
    closely enough. Never creates anything -- callers use
    ``collection_declare`` for that.
    """
    if not name or not name.strip():
        raise UnknownCollectionError(name, [])

    # 1. Exact canonical match.
    exact = await pool.fetchval(
        "SELECT canonical_name FROM collection_vocabulary WHERE canonical_name = $1", name
    )
    if exact is not None:
        return exact

    # 2. Exact alias match (following a merge repoint if one exists).
    aliased = await _canonical_for_alias(pool, name)
    if aliased is not None:
        return aliased

    # 3 & 4. Normalized match, then trigram fallback -- both scanned against
    # the full canonical + alias name set (small by construction: this is a
    # deliberately-declared vocabulary, not an open set).
    rows = await pool.fetch(
        """
        SELECT canonical_name AS name, canonical_name AS resolved,
               similarity($1, canonical_name) AS sim
        FROM collection_vocabulary
        UNION ALL
        SELECT alias AS name, COALESCE(merged_into, canonical_name) AS resolved,
               similarity($1, alias) AS sim
        FROM collection_aliases
        ORDER BY sim DESC
        """,
        name,
    )

    normalized_target = _normalize(name)
    for row in rows:
        if _normalize(row["name"]) == normalized_target:
            return row["resolved"]

    if rows and rows[0]["sim"] is not None and rows[0]["sim"] >= threshold:
        return rows[0]["resolved"]

    candidates = [row["resolved"] for row in rows[:5] if row["sim"] and row["sim"] > 0]
    # de-duplicate while preserving order
    seen: set[str] = set()
    deduped = [c for c in candidates if not (c in seen or seen.add(c))]
    raise UnknownCollectionError(name, deduped)


async def collection_declare(
    pool: asyncpg.Pool,
    name: str,
    shape_description: str,
) -> uuid.UUID:
    """Declare a new canonical collection. Rejects a missing/blank shape.

    Race-safe against a literal re-declare of the same name: two concurrent
    declares of the same exact string resolve to one row via
    ``ON CONFLICT DO NOTHING`` plus a follow-up read. Also refuses to
    declare a punctuation-only twin of an already-declared name (the
    'banking' / 'banking_transactions' / 'bank-transaction-alerts' mess this
    vocabulary exists to prevent) -- this check is a pre-flight read, not a
    DB constraint, so a genuinely simultaneous declare of two twin spellings
    is not fully race-proof; the exact-name path above is.
    """
    if not shape_description or not shape_description.strip():
        raise ValueError(f"collection_declare({name!r}) requires a non-empty shape_description.")
    if not name or not name.strip():
        raise ValueError("collection_declare requires a non-empty name.")

    normalized_target = _normalize(name)
    existing_names = await pool.fetch(
        "SELECT canonical_name FROM collection_vocabulary"
        " UNION SELECT alias FROM collection_aliases"
    )
    for row in existing_names:
        existing = row["canonical_name"]
        if existing != name and _normalize(existing) == normalized_target:
            raise ValueError(
                f"collection_declare({name!r}) would duplicate the existing declared name "
                f"{existing!r} (differs only by punctuation). Use that name instead."
            )

    collection_id = await pool.fetchval(
        """
        INSERT INTO collection_vocabulary (canonical_name, shape_description)
        VALUES ($1, $2)
        ON CONFLICT (canonical_name) DO NOTHING
        RETURNING id
        """,
        name,
        shape_description.strip(),
    )
    if collection_id is None:
        collection_id = await pool.fetchval(
            "SELECT id FROM collection_vocabulary WHERE canonical_name = $1", name
        )
    return collection_id


async def vocabulary_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all declared canonical collections with their shape description."""
    rows = await pool.fetch(
        "SELECT id, canonical_name, shape_description, created_at "
        "FROM collection_vocabulary ORDER BY canonical_name"
    )
    return [dict(r) for r in rows]
