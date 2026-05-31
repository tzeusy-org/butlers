"""Contact info — structured contact details and addresses.

Write-path cut-over (Migration bead 8, bu-k9ylx)
------------------------------------------------
``public.contact_info`` is now **read-only**.  Channel-identity writes go
through the central writer ``relationship_assert_fact()`` into
``relationship.entity_facts`` ONLY.  Reads (``contact_info_list``,
``contact_search_by_info``) still query ``public.contact_info`` until the table
is dropped at Migration bead 10.

- ``contact_info_add`` resolves the contact's ``entity_id`` and asserts a
  channel triple via ``relationship_assert_fact()``.  Owner-entity writes are
  parked as ``pending_actions`` by the central writer's RFC 0017 carve-out.
- ``contact_info_update`` / ``contact_info_remove`` are keyed by the
  ``public.contact_info.id`` PK, which has no equivalent in the triple store.
  After the cut-over there is no in-place mutate/delete-by-id path; callers must
  re-assert (update) or retract (remove) the channel fact via
  ``relationship_assert_fact()``.  These functions now fail fast via the
  contact_info write-block guard.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import asyncpg

from butlers.contact_info_write_guard import assert_contact_info_writes_blocked
from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.relationship_assert_fact import (
    contact_info_type_to_predicate,
    relationship_assert_fact,
)

_CONTACT_INFO_TYPES = {"email", "phone", "telegram", "linkedin", "twitter", "website", "other"}
_CONTACT_INFO_CONTEXTS = {"personal", "work", "other"}

logger = logging.getLogger(__name__)

# Work-domain heuristic: email addresses at these domains are auto-tagged
# context='work' when no explicit context is provided on insert.
#
# Override at runtime via BUTLERS_WORK_DOMAINS env var (comma-separated list
# of lowercase domain names, e.g. "example.com,acme.corp").
_DEFAULT_WORK_DOMAINS: frozenset[str] = frozenset(["example.com"])


def _get_work_domains() -> frozenset[str]:
    """Return the current work-domain set.

    Reads BUTLERS_WORK_DOMAINS once per call; the env var is intentionally
    re-read each call so runtime changes are picked up without restart.
    Setting BUTLERS_WORK_DOMAINS to an empty string disables the heuristic
    (returns an empty set); unset falls back to _DEFAULT_WORK_DOMAINS.
    """
    raw = os.environ.get("BUTLERS_WORK_DOMAINS")
    if raw is not None:
        return frozenset(d.strip().lower() for d in raw.split(",") if d.strip())
    return _DEFAULT_WORK_DOMAINS


def classify_email_context(email: str) -> str | None:
    """Return 'work' if the email domain is in the work-domain list, else None.

    Parameters
    ----------
    email:
        An email address string (e.g. ``"alice@example.com"``).

    Returns
    -------
    str | None
        ``'work'`` when the domain matches a known work domain, else ``None``.
    """
    at = email.rfind("@")
    if at == -1:
        return None
    domain = email[at + 1 :].lower()
    return "work" if domain in _get_work_domains() else None


async def _resolve_contact_entity(pool: asyncpg.Pool, contact_id: uuid.UUID) -> uuid.UUID | None:
    """Resolve the entity_id linked to *contact_id*, or None if absent.

    Reads ``public.contacts`` (a SELECT — still allowed after the cut-over).
    Returns None when the contact does not exist or has no linked entity.
    """
    row = await pool.fetchrow(
        "SELECT entity_id FROM public.contacts WHERE id = $1",
        contact_id,
    )
    if row is None:
        return None
    return row["entity_id"]


async def contact_info_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    value: str,
    label: str | None = None,  # noqa: ARG001 — accepted for back-compat; not stored on triples
    is_primary: bool = False,
    context: str | None = None,  # noqa: ARG001 — accepted for back-compat; not stored on triples
) -> dict[str, Any]:
    """Add a channel-identity fact for a contact via the central writer.

    Write-path cut-over (bu-k9ylx): this asserts a triple in
    ``relationship.entity_facts`` through ``relationship_assert_fact()`` instead
    of inserting into ``public.contact_info``.  The contact's ``entity_id`` is
    resolved first (SELECT on ``public.contacts``); the ``type`` is mapped to a
    contact predicate (``has-email``, ``has-phone``, ``has-handle``,
    ``has-website``).

    Owner carve-out (RFC 0017 §2.3): when the contact's entity carries the
    ``'owner'`` role, ``relationship_assert_fact()`` parks the mutation as a
    ``pending_actions`` row and returns ``pending_approval``; this function
    surfaces that as ``{"status": "pending_approval", "action_id": ...}``.

    ``label`` and ``context`` are accepted for backward compatibility but are
    NOT part of the triple model — they are ignored.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    contact_id:
        UUID of the contact (resolved to its linked entity).
    type:
        Channel type (``email``, ``phone``, ``telegram``, etc.).
    value:
        Channel value (email address, phone number, handle, etc.).
    is_primary:
        Whether this is the primary entry of its type; encoded on the triple's
        ``primary`` field.
    """
    if type not in _CONTACT_INFO_TYPES:
        raise ValueError(
            f"Invalid contact info type '{type}'. Must be one of {sorted(_CONTACT_INFO_TYPES)}"
        )

    predicate = contact_info_type_to_predicate(type)
    if predicate is None:
        raise ValueError(
            f"Contact info type '{type}' has no registered triple predicate; "
            "cannot assert a channel fact for it after the write-path cut-over."
        )

    entity_id = await _resolve_contact_entity(pool, contact_id)
    if entity_id is None:
        raise ValueError(
            f"Contact {contact_id} not found or has no linked entity. "
            "Channel facts must be anchored to an entity. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )

    result = await relationship_assert_fact(
        pool,
        entity_id,
        predicate,
        value,
        src="relationship",
        object_kind="literal",
        primary=is_primary,
    )

    if result.outcome.value == "pending_approval":
        return {
            "status": "pending_approval",
            "action_id": str(result.action_id),
            "message": (
                f"Adding {type} to the owner's entity requires human approval. "
                f"Action {result.action_id} is queued for review."
            ),
        }

    return {
        "status": "asserted",
        "outcome": result.outcome.value,
        "fact_id": str(result.fact_id) if result.fact_id is not None else None,
        "entity_id": str(entity_id),
        "type": type,
        "value": value,
        "is_primary": is_primary,
    }


async def contact_info_update(
    pool: asyncpg.Pool,  # noqa: ARG001 — guard rejects before use
    contact_info_id: uuid.UUID,  # noqa: ARG001
    value: str | None = None,  # noqa: ARG001
    label: str | None = None,  # noqa: ARG001
    is_primary: bool | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    """Reject in-place updates to the read-only ``contact_info`` table.

    Write-path cut-over (bu-k9ylx): ``public.contact_info`` is read-only and the
    triple store has no ``contact_info.id``-addressable update.  To change a
    channel fact, re-assert it via ``contact_info_add`` /
    ``relationship_assert_fact()`` (supersession applies on changed provenance).
    """
    assert_contact_info_writes_blocked("update")


async def contact_info_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """List contact info for a contact, optionally filtered by type.

    READ path — still queries ``public.contact_info`` (reads remain allowed
    after the cut-over until the table is dropped at Migration bead 10).
    """
    if type is not None:
        rows = await pool.fetch(
            """
            SELECT * FROM public.contact_info
            WHERE contact_id = $1 AND type = $2
            ORDER BY is_primary DESC, created_at
            """,
            contact_id,
            type,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM public.contact_info
            WHERE contact_id = $1
            ORDER BY type, is_primary DESC, created_at
            """,
            contact_id,
        )
    return [dict(row) for row in rows]


async def contact_info_remove(
    pool: asyncpg.Pool,  # noqa: ARG001 — guard rejects before use
    contact_info_id: uuid.UUID,  # noqa: ARG001
) -> None:
    """Reject deletes from the read-only ``contact_info`` table.

    Write-path cut-over (bu-k9ylx): ``public.contact_info`` is read-only.
    Channel-fact retraction is handled through the relationship butler's triple
    retraction path, not by deleting a ``contact_info`` row by id.
    """
    assert_contact_info_writes_blocked("delete")


async def contact_search_by_info(
    pool: asyncpg.Pool,
    value: str,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """Search contacts by contact info value (reverse lookup).

    READ path — still queries ``public.contact_info`` (reads remain allowed
    after the cut-over). Finds all contacts that have a matching contact info
    entry. Optionally filter by info type (email, phone, etc.). Uses ILIKE for
    case-insensitive partial matching.
    """
    if type is not None:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*, ci.type AS matched_type, ci.value AS matched_value
            FROM contacts c
            JOIN public.contact_info ci ON c.id = ci.contact_id
            WHERE ci.type = $1
              AND ci.value ILIKE '%' || $2 || '%'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            type,
            value,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*, ci.type AS matched_type, ci.value AS matched_value
            FROM contacts c
            JOIN public.contact_info ci ON c.id = ci.contact_id
            WHERE ci.value ILIKE '%' || $1 || '%'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            value,
        )
    return [_parse_contact(row) for row in rows]
