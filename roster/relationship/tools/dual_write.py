"""Dual-write shim helpers for the contacts → triples migration.

Amendment 1.1.C bead 4 (bu-8w730) — feature-flag-gated post-commit calls to
``relationship_assert_fact()`` after every legacy SQL write to
``public.contact_info``.

Design contract (Amendment 14):
- SQL is authoritative.  The legacy write commits FIRST; the triple write is
  best-effort and MUST NOT block or roll back the committed SQL row.
- MCP call failures are swallowed: logged at WARNING, never re-raised.
- The shim is gated by the ``BUTLERS_CONTACT_INFO_DUAL_WRITE`` env var
  (see :func:`dual_write_enabled`).  When the flag is off the helpers return
  immediately without touching ``relationship.entity_facts``.

Feature flag
------------
``BUTLERS_CONTACT_INFO_DUAL_WRITE``
    Set to any non-empty string to activate dual-write.  Default is off
    (empty / unset).

    The flag is re-read on every call so that it can be toggled at runtime
    without restarting the daemon.

Type → predicate mapping
------------------------
The mapping must stay in sync with ``_CI_TYPE_TO_PREDICATE`` in
``roster/relationship/jobs/relationship_jobs.py`` and the SQL CASE expression
in the reconciler sweep query.

    email    → has-email
    phone    → has-phone
    telegram → has-handle   (scoped handle)
    linkedin → has-handle
    twitter  → has-handle
    website  → has-website
    other    → has-handle
"""

from __future__ import annotations

import logging
import os
import uuid

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

_FLAG_ENV = "BUTLERS_CONTACT_INFO_DUAL_WRITE"


def dual_write_enabled() -> bool:
    """Return True when the dual-write feature flag is active.

    Reads ``BUTLERS_CONTACT_INFO_DUAL_WRITE`` on every call so toggling the
    env var takes effect without a daemon restart.
    """
    return bool(os.environ.get(_FLAG_ENV, "").strip())


# ---------------------------------------------------------------------------
# Type → predicate mapping (must mirror reconciler's _CI_TYPE_TO_PREDICATE)
# ---------------------------------------------------------------------------

_CI_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
}


def contact_info_type_to_predicate(ci_type: str) -> str | None:
    """Return the ``relationship.entity_predicate_registry`` predicate for *ci_type*.

    Returns ``None`` when the type has no registered predicate mapping
    (e.g. ``'address'``, ``'fax'``) — callers should skip dual-write for
    those rows.  The mapping stays in sync with the reconciler's
    ``_CI_TYPE_TO_PREDICATE`` so that shim and reconciler assert the same
    predicate for a given type (avoids duplicate triples).
    """
    return _CI_TYPE_TO_PREDICATE.get(ci_type)


# ---------------------------------------------------------------------------
# Core shim: emit a triple for a contact_info insert / update
# ---------------------------------------------------------------------------


async def emit_contact_info_fact(
    pool: asyncpg.Pool,
    *,
    contact_id: uuid.UUID,
    ci_type: str,
    value: str,
    is_primary: bool = False,
    src: str = "dual-write",
) -> None:
    """Best-effort post-commit call to ``relationship_assert_fact()``.

    Resolves the entity linked to *contact_id*, maps *ci_type* to a predicate,
    and calls the central writer.  Any failure (including unknown predicate,
    missing entity, or DB error) is swallowed and logged — this MUST never
    raise.

    Must be called AFTER the SQL transaction has committed.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    contact_id:
        UUID of the contact whose ``contact_info`` row was just written.
    ci_type:
        ``contact_info.type`` value (``email``, ``phone``, ``telegram``, etc.).
    value:
        ``contact_info.value`` — the channel address / handle being asserted.
    is_primary:
        Whether this entry is the primary of its type; encoded in the triple's
        ``primary`` field.
    src:
        Provenance source slug for the triple (default ``'dual-write'``).
    """
    if not dual_write_enabled():
        return

    predicate = contact_info_type_to_predicate(ci_type)
    if predicate is None:
        logger.debug(
            "emit_contact_info_fact: no predicate mapping for ci_type=%r — skipping dual-write",
            ci_type,
        )
        return

    try:
        # Resolve entity_id from the contact record.
        row = await pool.fetchrow(
            "SELECT entity_id FROM public.contacts WHERE id = $1",
            contact_id,
        )
        if row is None or row["entity_id"] is None:
            logger.debug(
                "emit_contact_info_fact: contact %s has no entity_id — skipping dual-write",
                contact_id,
            )
            return

        entity_id: uuid.UUID = row["entity_id"]

        from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

        await relationship_assert_fact(
            pool,
            entity_id,
            predicate,
            value,
            src=src,
            object_kind="literal",
            primary=is_primary,
        )
        logger.debug(
            "emit_contact_info_fact: asserted (%s, %s, %r) for entity %s",
            entity_id,
            predicate,
            value,
            entity_id,
        )
    except Exception:  # noqa: BLE001 — best-effort: never raise
        logger.warning(
            "emit_contact_info_fact: failed to assert triple for contact %s "
            "(ci_type=%r, value=%r) — dual-write failure swallowed",
            contact_id,
            ci_type,
            value,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Shim for contact_info deletions / retractions (placeholder)
# ---------------------------------------------------------------------------


async def retract_contact_info_fact(
    pool: asyncpg.Pool,
    *,
    contact_id: uuid.UUID,
    ci_type: str,
    value: str,
    src: str = "dual-write",  # noqa: ARG001
) -> None:
    """Best-effort post-commit retraction of a contact_info triple.

    Called after a ``DELETE FROM public.contact_info`` commit.

    NOTE: ``relationship_assert_fact()`` does not currently support an
    explicit ``validity='retracted'`` write path — it always writes active
    rows.  This helper is a **placeholder** that logs the intent and returns.
    When the central writer gains a retraction path (a future bead) this
    function will be updated to call it.

    The reconciler (bu-75a3s) will sweep any drift within its 30-minute window
    until the retraction path is implemented.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    contact_id:
        UUID of the contact whose ``contact_info`` row was just deleted.
    ci_type:
        ``contact_info.type`` of the deleted row.
    value:
        ``contact_info.value`` of the deleted row.
    src:
        Provenance source slug (default ``'dual-write'``).
    """
    if not dual_write_enabled():
        return

    predicate = contact_info_type_to_predicate(ci_type)
    if predicate is None:
        return

    try:
        row = await pool.fetchrow(
            "SELECT entity_id FROM public.contacts WHERE id = $1",
            contact_id,
        )
        if row is None or row["entity_id"] is None:
            return

        entity_id: uuid.UUID = row["entity_id"]

        # TODO(bu-8w730): call relationship_assert_fact() with validity='retracted'
        # when the central writer supports explicit retraction.
        logger.info(
            "retract_contact_info_fact: retraction logged for entity %s "
            "(%s=%r) — retraction write path not yet implemented; "
            "reconciler will sweep within 30min",
            entity_id,
            predicate,
            value,
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning(
            "retract_contact_info_fact: resolution failed for contact %s (ci_type=%r, value=%r)",
            contact_id,
            ci_type,
            value,
            exc_info=True,
        )
