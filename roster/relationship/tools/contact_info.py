"""Contact info — structured contact details and addresses."""

from __future__ import annotations

import os
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.contacts import _parse_contact
from butlers.tools.relationship.feed import _log_activity

_CONTACT_INFO_TYPES = {"email", "phone", "telegram", "linkedin", "twitter", "website", "other"}

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
    """
    raw = os.environ.get("BUTLERS_WORK_DOMAINS", "")
    if raw.strip():
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
