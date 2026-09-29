"""Explicit ordinary collection vocabulary: declarations, aliases, resolution.

RFC 0037 "Collection and alias compatibility" / REQ-general-capture-003.

- :func:`collection_declare` records a declared ordinary collection with a
  nonblank shape, its canonical spelling and optional aliases.  Spellings share
  one normalized key space (case, whitespace, ``_``/``-`` and punctuation
  insensitive) whose uniqueness the database enforces among ordinary parents
  only, so concurrent variant declares converge on one declaration.
- :func:`collection_resolve` resolves only an exact ordinary collection name or
  an exact declared spelling.  Normalized and fuzzy near matches are returned
  as suggestions for the caller to choose from, never as a guessed identity.

Everything here is ordinary-only: private parents never resolve, never appear
in suggestions or counts, and never cause a conflict.  ``item_create`` and
``collection_create`` keep their existing absent-name creation (there is no
unknown-name refusal), and nothing seeds, renames or consolidates vocabulary.
"""

from __future__ import annotations

import difflib
import uuid
from typing import Any

import asyncpg

from butlers.tools.general.items import resolve_ordinary_collection
from butlers.tools.general.source_authority import (
    ORDINARY_PARENT,
    ordinary_transaction,
    require_ordinary_namespace,
)

MAX_SPELLING = 200
MAX_SHAPE = 2000
MAX_ALIASES = 20
MAX_SUGGESTIONS = 5
# Upper bound on ordinary spellings considered for fuzzy suggestions.
_SUGGESTION_SCAN = 2000
_FUZZY_CUTOFF = 0.6


class _Converged(Exception):
    """A concurrent declare of an equivalent spelling won; retry to adopt it."""


def _spelling(value: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"collection_declare requires a non-empty {what}.")
    spelling = value.strip()
    if len(spelling) > MAX_SPELLING:
        raise ValueError(f"collection_declare {what} exceeds {MAX_SPELLING} characters.")
    return spelling


_DECLARED_BY_KEY = f"""
    SELECT key_row.collection_id, canonical.spelling AS name, vocabulary.shape_description
    FROM collection_vocabulary_keys AS key_row
    JOIN collections AS c ON c.id = key_row.collection_id
    JOIN collection_vocabulary AS vocabulary ON vocabulary.collection_id = key_row.collection_id
    JOIN collection_vocabulary_keys AS canonical
      ON canonical.collection_id = key_row.collection_id AND canonical.kind = 'canonical'
    WHERE key_row.normalized_key = collection_vocabulary_key($1)
      AND NOT key_row.custody_private
      AND {ORDINARY_PARENT}
"""


async def _declaration(conn: asyncpg.Connection, collection_id: uuid.UUID) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT key_row.spelling, key_row.kind, vocabulary.shape_description
        FROM collection_vocabulary_keys AS key_row
        JOIN collection_vocabulary AS vocabulary
          ON vocabulary.collection_id = key_row.collection_id
        WHERE key_row.collection_id = $1
        ORDER BY key_row.kind, key_row.spelling
        """,
        collection_id,
    )
    return {
        "collection_id": collection_id,
        "name": next(r["spelling"] for r in rows if r["kind"] == "canonical"),
        "shape_description": rows[0]["shape_description"],
        "aliases": [r["spelling"] for r in rows if r["kind"] == "alias"],
    }


async def _add_aliases(
    conn: asyncpg.Connection, collection_id: uuid.UUID, aliases: list[str]
) -> None:
    for alias in aliases:
        inserted = await conn.fetchval(
            """
            INSERT INTO collection_vocabulary_keys (collection_id, spelling, kind)
            VALUES ($1, $2, 'alias')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            collection_id,
            alias,
        )
        if inserted is not None:
            continue
        holder = await conn.fetchval(
            f"""
            SELECT key_row.collection_id
            FROM collection_vocabulary_keys AS key_row
            JOIN collections AS c ON c.id = key_row.collection_id
            WHERE key_row.normalized_key = collection_vocabulary_key($1)
              AND NOT key_row.custody_private AND {ORDINARY_PARENT}
            """,
            alias,
        )
        if holder != collection_id:
            raise ValueError(
                f"collection_declare alias {alias!r} is already a declared ordinary "
                "spelling of another collection."
            )


async def _declare_once(
    pool: asyncpg.Pool, name: str, shape: str, aliases: list[str]
) -> dict[str, Any]:
    async with ordinary_transaction(pool) as conn:
        await require_ordinary_namespace(conn)
        existing = await conn.fetchrow(_DECLARED_BY_KEY, name)
        if existing is not None:
            await _add_aliases(conn, existing["collection_id"], aliases)
            return {**await _declaration(conn, existing["collection_id"]), "declared": False}

        parent = await resolve_ordinary_collection(conn, name, create=True)
        await conn.execute(
            """
            INSERT INTO collection_vocabulary (collection_id, shape_description)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            parent["id"],
            shape,
        )
        canonical = await conn.fetchval(
            """
            INSERT INTO collection_vocabulary_keys (collection_id, spelling, kind)
            VALUES ($1, $2, 'canonical')
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            parent["id"],
            name,
        )
        if canonical is None:
            # An equivalent spelling (or this collection's own canonical) was
            # declared concurrently; roll this attempt back and adopt it.
            raise _Converged()
        await _add_aliases(conn, parent["id"], aliases)
        return {**await _declaration(conn, parent["id"]), "declared": True}


async def collection_declare(
    pool: asyncpg.Pool,
    name: str,
    shape_description: str,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """Declare an ordinary collection with a required shape and optional aliases.

    Adopts an existing ordinary collection of that exact name, or creates one.
    A spelling whose normalized key is already declared by an ordinary
    collection converges on that declaration (``declared`` is False) instead of
    creating a twin.  An alias already declared for another ordinary collection
    refuses the whole declare without a write.
    """
    spelling = _spelling(name, "name")
    if not isinstance(shape_description, str) or not shape_description.strip():
        raise ValueError("collection_declare requires a non-empty shape_description.")
    shape = shape_description.strip()
    if len(shape) > MAX_SHAPE:
        raise ValueError(f"collection_declare shape_description exceeds {MAX_SHAPE} characters.")
    alias_list = [_spelling(alias, "alias") for alias in (aliases or [])]
    if len(alias_list) > MAX_ALIASES:
        raise ValueError(f"collection_declare accepts at most {MAX_ALIASES} aliases.")

    for _ in range(3):
        try:
            return await _declare_once(pool, spelling, shape, alias_list)
        except _Converged:
            continue
    raise RuntimeError("collection_declare could not converge; retry the request.")


async def collection_resolve(pool: asyncpg.Pool, name: str) -> dict[str, Any]:
    """Resolve a name exactly in the ordinary namespace, else suggest candidates.

    Returns ``{"collection": {...} | None, "match": "name" | "alias" | None,
    "suggestions": [...]}``.  Only an exact ordinary collection name or an
    exact declared spelling resolves; normalized-equal and fuzzy candidates are
    suggestions only.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("collection_resolve requires a non-empty name.")
    name = name.strip()
    async with ordinary_transaction(pool) as conn:
        await require_ordinary_namespace(conn)
        exact = await conn.fetchrow(
            f"SELECT c.id, c.name FROM collections AS c WHERE c.name = $1 AND {ORDINARY_PARENT}",
            name,
        )
        if exact is not None:
            return {
                "collection": {"id": exact["id"], "name": exact["name"]},
                "match": "name",
                "suggestions": [],
            }
        alias = await conn.fetchrow(
            f"""
            SELECT c.id, c.name
            FROM collection_vocabulary_keys AS key_row
            JOIN collections AS c ON c.id = key_row.collection_id
            WHERE key_row.spelling = $1 AND NOT key_row.custody_private AND {ORDINARY_PARENT}
            """,
            name,
        )
        if alias is not None:
            return {
                "collection": {"id": alias["id"], "name": alias["name"]},
                "match": "alias",
                "suggestions": [],
            }
        candidates = await conn.fetch(
            f"""
            SELECT spelling, collection_id, name, normalized_equal FROM (
                SELECT key_row.spelling, c.id AS collection_id, c.name,
                       key_row.normalized_key = collection_vocabulary_key($1)
                           AS normalized_equal
                FROM collection_vocabulary_keys AS key_row
                JOIN collections AS c ON c.id = key_row.collection_id
                WHERE NOT key_row.custody_private AND {ORDINARY_PARENT}
                UNION ALL
                SELECT c.name, c.id, c.name, false
                FROM collections AS c
                WHERE {ORDINARY_PARENT}
            ) AS candidate
            ORDER BY normalized_equal DESC, spelling
            LIMIT {_SUGGESTION_SCAN}
            """,
            name,
        )

    by_spelling: dict[str, asyncpg.Record] = {}
    for row in candidates:
        by_spelling.setdefault(row["spelling"], row)
    ranked = [row["spelling"] for row in candidates if row["normalized_equal"]]
    ranked += difflib.get_close_matches(
        name, list(by_spelling), n=MAX_SUGGESTIONS * 2, cutoff=_FUZZY_CUTOFF
    )
    suggestions: list[dict[str, Any]] = []
    seen: set[uuid.UUID] = set()
    for spelling in ranked:
        row = by_spelling[spelling]
        if row["collection_id"] in seen:
            continue
        seen.add(row["collection_id"])
        suggestions.append({"collection_id": row["collection_id"], "name": row["name"]})
        if len(suggestions) == MAX_SUGGESTIONS:
            break
    return {"collection": None, "match": None, "suggestions": suggestions}
