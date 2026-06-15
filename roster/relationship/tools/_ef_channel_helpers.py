"""Shared entity_facts channel helpers — used by both the MCP tools layer and the API router.

These helpers convert ``relationship.entity_facts`` rows (``has-*`` predicates) into
legacy-compatible type/value pairs.  They are the single authoritative implementation
of the predicate→type and object→display-value mappings.

Designed to be importable from both:
- ``roster/relationship/tools/contact_info.py`` (MCP tools)
- ``roster/relationship/api/router.py`` (dashboard API)

No FastAPI or Pydantic imports — pure asyncpg + stdlib only.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

# ---------------------------------------------------------------------------
# Telegram prefix constant
# ---------------------------------------------------------------------------

TELEGRAM_HANDLE_PREFIX = "telegram:"

# CI types that use the ``has-handle`` predicate and require a ``telegram:`` prefix
# on the stored object value for disambiguation from linkedin/twitter/other handles.
_TELEGRAM_CI_TYPES = frozenset({"telegram", "telegram_user_id", "telegram_username"})


# ---------------------------------------------------------------------------
# Object encoding helper (write side)
# ---------------------------------------------------------------------------


def encode_handle_object(ci_type: str, value: str) -> str:
    """Return the canonical ``has-handle`` object string for *ci_type* and *value*.

    Telegram types (``telegram``, ``telegram_user_id``, ``telegram_username``)
    are stored with a ``"telegram:"`` prefix so the read path can distinguish
    them from other ``has-handle`` entries (linkedin, twitter, etc.).

    Non-telegram types are returned unchanged.

    Idempotent: if *value* already carries the prefix it is not added again.
    """
    if ci_type in _TELEGRAM_CI_TYPES:
        if not value.startswith(TELEGRAM_HANDLE_PREFIX):
            return TELEGRAM_HANDLE_PREFIX + value
    return value


# ---------------------------------------------------------------------------
# Predicate → CI type
# ---------------------------------------------------------------------------


def ef_predicate_to_ci_type(predicate: str, object_val: str) -> str:
    """Derive a legacy-compatible CI type string from an entity_facts row.

    Mapping:
    - ``has-email``   → ``"email"``
    - ``has-phone``   → ``"phone"``
    - ``has-website`` → ``"website"``
    - ``has-handle``  with ``"telegram:"`` prefix → ``"telegram_user_id"``
    - ``has-handle``  without prefix → ``"handle"``

    The ``"telegram_user_id"`` type is used so callers can distinguish
    Telegram entries (numeric id, stripped of the prefix) from bare handles
    (linkedin, twitter, etc.).  Bare ``has-handle`` entries that lack the
    prefix are typed as ``"handle"`` — a neutral label that avoids implying
    any particular network.

    Backward-compat note: legacy rows written before bu-wni4z may store telegram
    handles verbatim (no prefix).  Those rows are classified as ``"handle"``
    until a data migration prefixes them.  The read path in
    ``daemon._resolve_contact_channel_identifier`` accepts both prefixed and
    verbatim forms during the transition period.
    """
    if predicate == "has-email":
        return "email"
    if predicate == "has-phone":
        return "phone"
    if predicate == "has-website":
        return "website"
    # has-handle: distinguish telegram (prefixed) from other handles
    if predicate == "has-handle":
        if object_val.startswith(TELEGRAM_HANDLE_PREFIX):
            return "telegram_user_id"
        return "handle"
    # Fallback: strip the "has-" prefix for any future predicates
    return predicate.removeprefix("has-")


# ---------------------------------------------------------------------------
# Object → display value (strips telegram: prefix)
# ---------------------------------------------------------------------------


def ef_object_to_display_value(predicate: str, object_val: str) -> str:
    """Strip the ``telegram:`` prefix from telegram has-handle values.

    For all other predicates the raw object string is returned as-is.
    """
    if predicate == "has-handle" and object_val.startswith(TELEGRAM_HANDLE_PREFIX):
        return object_val[len(TELEGRAM_HANDLE_PREFIX) :]
    return object_val


# ---------------------------------------------------------------------------
# Batch-fetch helpers
# ---------------------------------------------------------------------------


async def entity_facts_channels_by_entity(
    pool: Any,
    entity_ids: list[UUID],
) -> dict[UUID, list[Any]]:
    """Batch-fetch active has-* triples from relationship.entity_facts.

    Returns a dict mapping entity_id → list of asyncpg Row objects (mapping-like)
    with keys ``id``, ``predicate``, ``object``, ``primary``.

    Entity IDs with no facts map to an empty list.  Entity IDs with
    ``entity_id IS NULL`` are not passed in and must be handled by the
    caller (no facts → empty).
    """
    if not entity_ids:
        return {}
    rows = await pool.fetch(
        """
        SELECT ef.subject AS entity_id, ef.id, ef.predicate, ef.object, ef."primary", ef.verified
        FROM relationship.entity_facts ef
        WHERE ef.subject = ANY($1)
          AND ef.predicate LIKE 'has-%'
          AND ef.validity = 'active'
          AND ef.object_kind = 'literal'
        ORDER BY ef.subject, ef."primary" DESC NULLS LAST, ef.created_at ASC
        """,
        entity_ids,
    )
    result: dict[UUID, list[Any]] = {eid: [] for eid in entity_ids}
    for r in rows:
        eid = r["entity_id"]
        if eid in result:
            result[eid].append(r)
    return result


async def contact_entity_map(
    pool: Any,
    contact_ids: list[UUID],
) -> dict[UUID, UUID | None]:
    """Batch-fetch entity_id for a list of contact IDs.

    Returns a dict mapping contact_id → entity_id (or None when unlinked).
    """
    if not contact_ids:
        return {}
    rows = await pool.fetch(
        "SELECT id, entity_id FROM public.contacts WHERE id = ANY($1)",
        contact_ids,
    )
    mapping: dict[UUID, UUID | None] = {cid: None for cid in contact_ids}
    for r in rows:
        mapping[r["id"]] = r["entity_id"]
    return mapping
