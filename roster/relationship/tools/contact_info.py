"""Contact info — structured contact details and addresses.

Read-path cut-over (Migration bead 10, bu-twbt0)
-------------------------------------------------
``public.contact_info`` reads are fully replaced by ``relationship.entity_facts``
queries.  The table can now be dropped.

Write-path cut-over (Migration bead 8, bu-k9ylx)
------------------------------------------------
``public.contact_info`` is **read-only** since bead 8.  Channel-identity writes go
through the central writer ``relationship_assert_fact()`` into
``relationship.entity_facts`` ONLY.

- ``contact_info_add`` resolves the contact's ``entity_id`` and asserts a
  channel triple via ``relationship_assert_fact()``.  Owner-entity writes are
  parked as ``pending_actions`` by the central writer's RFC 0017 carve-out.
- ``contact_info_update`` / ``contact_info_remove`` are keyed by the
  ``public.contact_info.id`` PK, which has no equivalent in the triple store.
  After the cut-over there is no in-place mutate/delete-by-id path; callers must
  re-assert (update) or retract (remove) the channel fact via
  ``relationship_assert_fact()``.  These functions now fail fast via the
  contact_info write-block guard.
- ``contact_info_list`` / ``contact_search_by_info`` read from
  ``relationship.entity_facts`` (has-* predicates) via shared helpers in
  ``_ef_channel_helpers``.  Telegram entries are stored as
  ``has-handle`` with object ``"telegram:<numeric_id>"``; the prefix is
  stripped on read so callers receive the bare numeric id.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import asyncpg

from butlers.contact_info_write_guard import assert_contact_info_writes_blocked
from butlers.tools.relationship._ef_channel_helpers import (
    ef_object_to_display_value,
    ef_predicate_to_ci_type,
    encode_handle_object,
    entity_facts_channels_by_entity,
)
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

    # Encode the stored object: telegram types must carry the "telegram:" prefix so
    # the daemon read path can distinguish them from linkedin/twitter/other has-handle rows.
    ef_object = encode_handle_object(type, value)

    result = await relationship_assert_fact(
        pool,
        entity_id,
        predicate,
        ef_object,
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

    READ path — queries ``relationship.entity_facts`` (has-* predicates).
    Contact is resolved to its linked entity via ``public.contacts.entity_id``;
    contacts with no linked entity return an empty list.

    When *type* is provided it is mapped to the corresponding predicate
    (``has-email``, ``has-phone``, ``has-handle``, ``has-website``) and only
    facts with that predicate are returned.  Note that ``telegram``,
    ``linkedin``, ``twitter`` and ``other`` all map to ``has-handle``; the
    returned entry types are ``"telegram_user_id"`` (for prefixed handles) or
    ``"handle"`` (for bare handles — linkedin, twitter, other).  Passing
    ``type="telegram"`` will return both telegram and non-telegram handles
    because they share the ``has-handle`` predicate.
    """
    entity_id = await _resolve_contact_entity(pool, contact_id)
    if entity_id is None:
        return []

    facts_by_entity = await entity_facts_channels_by_entity(pool, [entity_id])
    facts = facts_by_entity.get(entity_id, [])

    if type is not None:
        target_predicate = contact_info_type_to_predicate(type)
        if target_predicate is not None:
            facts = [f for f in facts if f["predicate"] == target_predicate]
        else:
            # Unmapped type (e.g. 'address') — no triple predicate home
            facts = []

    result: list[dict[str, Any]] = []
    for fact in facts:
        predicate: str = fact["predicate"]
        raw_obj: str = fact["object"]
        ci_type = ef_predicate_to_ci_type(predicate, raw_obj)
        display_val = ef_object_to_display_value(predicate, raw_obj)
        primary_raw = fact["primary"]
        result.append(
            {
                "id": fact["id"],
                "contact_id": contact_id,
                "type": ci_type,
                "value": display_val,
                "is_primary": bool(primary_raw) if primary_raw is not None else False,
                "label": None,
                "context": None,
                "source": "entity_facts",
            }
        )
    return result


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

    READ path — queries ``relationship.entity_facts`` (has-* predicates).
    Finds all contacts that have a matching channel fact.  Optionally filter by
    info type (email, phone, etc.).  Uses ILIKE for case-insensitive partial
    matching against the stored object value.

    Telegram search note: Telegram IDs are stored in entity_facts as
    ``has-handle`` with object ``"telegram:<numeric_id>"``.  Searching for the
    bare numeric ID (e.g. ``"210454304"``) still matches because the ILIKE
    ``'%210454304%'`` pattern matches the stored ``"telegram:210454304"``
    string.  Callers do NOT need to add the ``telegram:`` prefix themselves.

    When *type* is provided it is mapped to the corresponding predicate before
    filtering (e.g. ``"telegram"`` → ``"has-handle"``).  All handle types
    (telegram, linkedin, twitter, other) share ``has-handle``; passing ``type``
    narrows to that predicate but does not further discriminate within it.
    """
    # Map optional type to predicate filter
    predicate_filter: str | None = None
    if type is not None:
        predicate_filter = contact_info_type_to_predicate(type)
        if predicate_filter is None:
            # Unmapped type (e.g. 'address') — no triple predicate home
            return []

    if predicate_filter is not None:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*
            FROM contacts c
            JOIN relationship.entity_facts ef ON ef.subject = c.entity_id
            WHERE ef.predicate = $1
              AND ef.object ILIKE '%' || $2 || '%'
              AND ef.validity = 'active'
              AND ef.object_kind = 'literal'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            predicate_filter,
            value,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT DISTINCT c.*
            FROM contacts c
            JOIN relationship.entity_facts ef ON ef.subject = c.entity_id
            WHERE ef.predicate LIKE 'has-%%'
              AND ef.object ILIKE '%' || $1 || '%'
              AND ef.validity = 'active'
              AND ef.object_kind = 'literal'
              AND c.listed = true
            ORDER BY c.first_name, c.last_name, c.nickname
            """,
            value,
        )
    return [_parse_contact(row) for row in rows]
