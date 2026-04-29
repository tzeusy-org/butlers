"""Contact info — structured contact details and addresses."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.feed import _log_activity

_CONTACT_INFO_TYPES = {"email", "phone", "telegram", "linkedin", "twitter", "website", "other"}
_CONTACT_INFO_CONTEXTS = {"personal", "work", "other"}

logger = logging.getLogger(__name__)

# Pending actions expire after 72 hours by default.
_PENDING_ACTION_EXPIRY_HOURS = 72

# Work-domain heuristic: email addresses at these domains are auto-tagged
# context='work' when no explicit context is provided on insert.
#
# Override at runtime via BUTLERS_WORK_DOMAINS env var (comma-separated list
# of lowercase domain names, e.g. "example.com,acme.corp").
# Conservative: existing rows are never updated; only new inserts pick up
# this heuristic.
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


async def _is_owner_contact(pool: asyncpg.Pool, contact_id: uuid.UUID) -> bool:
    """Return True if *contact_id* belongs to the owner contact.

    Joins public.contacts to public.entities to read the ``roles`` array.
    Returns False on any DB error or if the contact does not exist.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT 1
            FROM public.contacts c
            LEFT JOIN public.entities e ON e.id = c.entity_id
            WHERE c.id = $1
              AND 'owner' = ANY(COALESCE(e.roles, '{}'))
            """,
            contact_id,
        )
        return row is not None
    except Exception:  # noqa: BLE001
        logger.debug(
            "contact_info: owner check failed for contact %s; treating as non-owner",
            contact_id,
            exc_info=True,
        )
        return False


async def _create_pending_action(
    pool: asyncpg.Pool,
    tool_name: str,
    tool_args: dict[str, Any],
    summary: str,
) -> uuid.UUID:
    """Insert a pending_actions row and return its action_id.

    Writes status='pending' so the action awaits human approval before any
    mutation to public.contact_info occurs.
    """
    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=_PENDING_ACTION_EXPIRY_HOURS)

    await pool.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, session_id, status, "
        "requested_at, expires_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        action_id,
        tool_name,
        json.dumps(tool_args),
        summary,
        None,  # session_id not available at this layer
        "pending",
        now,
        expires_at,
    )
    return action_id


async def contact_info_add(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str,
    value: str,
    label: str | None = None,
    is_primary: bool = False,
    context: str | None = None,
) -> dict[str, Any]:
    """Add a piece of contact information (email, phone, etc.) for a contact.

    Owner gate: if *contact_id* resolves to the owner contact, the mutation is
    blocked and a ``pending_actions`` row is created for human approval.  The
    caller receives a ``{"status": "pending_approval", ...}`` dict instead of
    the inserted row.  Non-owner contacts are written immediately as before.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    contact_id:
        UUID of the contact to attach this info to.
    type:
        Channel type (``email``, ``phone``, ``telegram``, etc.).
    value:
        Channel value (email address, phone number, handle, etc.).
    label:
        Optional human-readable label (e.g. ``"Work"``).
    is_primary:
        Whether this is the primary entry of its type for this contact.
    context:
        Optional context tag: ``'personal'``, ``'work'``, or ``'other'``.
        When ``None`` and ``type='email'``, the work-domain heuristic runs:
        if the email domain is in the configured work-domain list the row is
        stored with ``context='work'``.  An explicit caller-supplied value is
        always respected and never overridden.
    """
    if type not in _CONTACT_INFO_TYPES:
        raise ValueError(
            f"Invalid contact info type '{type}'. Must be one of {sorted(_CONTACT_INFO_TYPES)}"
        )
    if context is not None and context not in _CONTACT_INFO_CONTEXTS:
        raise ValueError(
            f"Invalid context '{context}'. Must be one of {sorted(_CONTACT_INFO_CONTEXTS)}"
        )

    # Verify contact exists
    existing = await pool.fetchrow("SELECT id FROM contacts WHERE id = $1", contact_id)
    if existing is None:
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )

    # Apply work-domain heuristic: only when context is not explicitly set
    # and the type is email.  Never overrides an explicit caller-supplied value.
    effective_context = context
    if effective_context is None and type == "email":
        effective_context = classify_email_context(value)

    # Owner gate — block direct mutation for the owner contact
    if await _is_owner_contact(pool, contact_id):
        tool_args: dict[str, Any] = {
            "contact_id": str(contact_id),
            "type": type,
            "value": value,
        }
        if label is not None:
            tool_args["label"] = label
        if is_primary:
            tool_args["is_primary"] = is_primary
        if effective_context is not None:
            tool_args["context"] = effective_context

        summary = f"contact_info_add: add {type} '{value}' to owner contact {contact_id}"
        action_id = await _create_pending_action(pool, "contact_info_add", tool_args, summary)

        logger.warning(
            "contact_info_add: owner-contact mutation blocked and parked as pending_action %s "
            "(contact=%s, type=%s, value=%r)",
            action_id,
            contact_id,
            type,
            value,
        )
        return {
            "status": "pending_approval",
            "action_id": str(action_id),
            "message": (
                f"Adding {type} to the owner contact requires human approval. "
                f"Action {action_id} is queued for review."
            ),
        }

    # Non-owner path — write immediately

    # If marking as primary, unset any existing primary for this type
    if is_primary:
        await pool.execute(
            """
            UPDATE public.contact_info SET is_primary = false
            WHERE contact_id = $1 AND type = $2 AND is_primary = true
            """,
            contact_id,
            type,
        )

    row = await pool.fetchrow(
        """
        INSERT INTO public.contact_info (contact_id, type, value, label, is_primary, context)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING *
        """,
        contact_id,
        type,
        value,
        label,
        is_primary,
        effective_context,
    )
    result = dict(row)
    desc = f"Added {type}: {value}"
    if label:
        desc += f" ({label})"
    await _log_activity(pool, contact_id, "contact_info_added", desc)
    return result


async def contact_info_update(
    pool: asyncpg.Pool,
    contact_info_id: uuid.UUID,
    value: str | None = None,
    label: str | None = None,
    is_primary: bool | None = None,
) -> dict[str, Any]:
    """Update fields on an existing contact_info entry.

    Owner gate: if the entry belongs to the owner contact, the mutation is
    blocked and a ``pending_actions`` row is created for human approval.  The
    caller receives a ``{"status": "pending_approval", ...}`` dict instead of
    the updated row.  Non-owner contact entries are updated immediately.

    At least one of *value*, *label*, or *is_primary* must be provided.
    """
    if value is None and label is None and is_primary is None:
        raise ValueError("At least one of value, label, or is_primary must be provided.")

    row = await pool.fetchrow(
        "SELECT * FROM public.contact_info WHERE id = $1",
        contact_info_id,
    )
    if row is None:
        raise ValueError(
            f"Contact info {contact_info_id} not found. "
            "Use contact_info_list(contact_id=...) to list contact info entries."
        )

    contact_id: uuid.UUID = row["contact_id"]

    # Owner gate — block direct mutation for the owner contact
    if await _is_owner_contact(pool, contact_id):
        tool_args: dict[str, Any] = {"contact_info_id": str(contact_info_id)}
        if value is not None:
            tool_args["value"] = value
        if label is not None:
            tool_args["label"] = label
        if is_primary is not None:
            tool_args["is_primary"] = is_primary

        summary = (
            f"contact_info_update: update contact_info {contact_info_id} "
            f"(type={row['type']}) on owner contact {contact_id}"
        )
        action_id = await _create_pending_action(pool, "contact_info_update", tool_args, summary)

        logger.warning(
            "contact_info_update: owner-contact mutation blocked and parked as pending_action %s "
            "(contact_info=%s, contact=%s)",
            action_id,
            contact_info_id,
            contact_id,
        )
        return {
            "status": "pending_approval",
            "action_id": str(action_id),
            "message": (
                f"Updating contact info on the owner contact requires human approval. "
                f"Action {action_id} is queued for review."
            ),
        }

    # Non-owner path — update immediately

    # Build SET clause from provided fields
    updates: dict[str, Any] = {}
    if value is not None:
        updates["value"] = value
    if label is not None:
        updates["label"] = label
    if is_primary is not None:
        updates["is_primary"] = is_primary

    # If marking as primary, unset any existing primary for this type on the same contact
    if is_primary:
        await pool.execute(
            """
            UPDATE public.contact_info SET is_primary = false
            WHERE contact_id = $1 AND type = $2 AND is_primary = true AND id != $3
            """,
            contact_id,
            row["type"],
            contact_info_id,
        )

    # Build dynamic SET clause
    set_parts = [f"{col} = ${i + 2}" for i, col in enumerate(updates)]
    params: list[Any] = [contact_info_id, *updates.values()]
    updated = await pool.fetchrow(
        f"UPDATE public.contact_info SET {', '.join(set_parts)} WHERE id = $1 RETURNING *",  # noqa: S608
        *params,
    )

    result = dict(updated)
    desc_parts = []
    if value is not None:
        desc_parts.append(f"value → {value}")
    if label is not None:
        desc_parts.append(f"label → {label}")
    if is_primary is not None:
        desc_parts.append(f"is_primary → {is_primary}")
    desc = f"Updated {row['type']}: {', '.join(desc_parts)}"
    await _log_activity(pool, contact_id, "contact_info_updated", desc)
    return result


async def contact_info_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """List contact info for a contact, optionally filtered by type."""
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
    pool: asyncpg.Pool,
    contact_info_id: uuid.UUID,
) -> None:
    """Remove a piece of contact information by its ID."""
    row = await pool.fetchrow(
        "SELECT * FROM public.contact_info WHERE id = $1",
        contact_info_id,
    )
    if row is None:
        raise ValueError(
            f"Contact info {contact_info_id} not found. "
            "Use contact_info_list(contact_id=...) to list contact info entries."
        )

    await pool.execute("DELETE FROM public.contact_info WHERE id = $1", contact_info_id)
    await _log_activity(
        pool,
        row["contact_id"],
        "contact_info_removed",
        f"Removed {row['type']}: {row['value']}",
    )


async def contact_search_by_info(
    pool: asyncpg.Pool,
    value: str,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """Search contacts by contact info value (reverse lookup).

    Finds all contacts that have a matching contact info entry.
    Optionally filter by info type (email, phone, etc.).
    Uses ILIKE for case-insensitive partial matching.
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
