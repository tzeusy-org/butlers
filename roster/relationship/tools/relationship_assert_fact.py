"""Central writer for relationship.entity_facts — single authoritative ingress point.

ALL writes to ``relationship.entity_facts`` MUST go through
:func:`relationship_assert_fact`.  No other butler MAY issue a direct
``INSERT`` or ``UPDATE`` to that table.

Contract (Amendment 14 + spec §"Requirement: Central writer"):

1. **Predicate validation** — rejected immediately if the predicate is not
   present in ``relationship.entity_predicate_registry``.

2. **Idempotency on (subject, predicate, object)** — repeated calls with the
   same identity tuple produce exactly ONE active row.

3. **Supersession** — if the existing active row differs in provenance
   (``src``, ``conf``, ``verified``, ``last_seen``) the old row is marked
   ``validity='superseded'`` and a new active row is inserted.

4. **Transaction safety** — the writer accepts an optional ``conn`` parameter.
   When provided, all SQL executes on that connection without opening a new
   transaction (safe inside an already-open ``asyncpg`` transaction).  When
   omitted, the writer acquires a connection from the pool itself and opens its
   own transaction for the supersession read-then-write pair.

5. **Owner carve-out (RFC 0017 §2.3)** — when *subject* resolves to an entity
   whose ``roles`` array contains ``'owner'``, the mutation is NOT written
   directly.  Instead, a ``pending_actions`` row is created for human approval,
   mirroring ``contact_info.py::contact_info_add``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Pending actions expire after 72 hours (mirrors contact_info.py).
_PENDING_ACTION_EXPIRY_HOURS = 72


# ---------------------------------------------------------------------------
# Channel-type → contact predicate mapping
# ---------------------------------------------------------------------------
# Maps a ``public.contact_info.type`` (or channel type) to the contact predicate
# in ``relationship.entity_predicate_registry``.  This lived in the now-removed
# dual-write shim (dual_write.py) during the migration window; after the
# write-path cut-over (Migration bead 8, bu-k9ylx) it is owned by the central
# writer module so all writers resolve channel-type → predicate from one place.
#
# Must stay in sync with the reconciler's CASE mapping in
# ``roster/relationship/jobs/relationship_jobs.py`` and the channel-type mapping
# in ``src/butlers/identity.py::_CHANNEL_TYPE_TO_PREDICATE``.
#
#     email    → has-email
#     phone    → has-phone
#     telegram → has-handle   (scoped handle)
#     linkedin → has-handle
#     twitter  → has-handle
#     website  → has-website
#     other    → has-handle
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
    """Return the contact predicate for *ci_type*, or ``None`` when unmapped.

    Returns ``None`` for types with no registered predicate mapping (e.g.
    ``'address'``, ``'fax'``, ``'telegram_chat_id'``, ``'google_health'``) —
    callers MUST skip the triple write for those types.
    """
    return _CI_TYPE_TO_PREDICATE.get(ci_type)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


class AssertOutcome(StrEnum):
    """Outcome of a single :func:`relationship_assert_fact` call."""

    inserted = "inserted"  # brand-new active row
    unchanged = "unchanged"  # identical provenance — no write needed
    superseded = "superseded"  # old row retracted; new row inserted
    pending_approval = "pending_approval"  # owner carve-out triggered


@dataclass
class AssertResult:
    """Result returned by :func:`relationship_assert_fact`."""

    outcome: AssertOutcome
    # UUID of the now-active row in relationship.entity_facts (None for pending_approval).
    fact_id: uuid.UUID | None
    # action_id of the pending_actions row (only for pending_approval outcome).
    action_id: uuid.UUID | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "fact_id": str(self.fact_id) if self.fact_id is not None else None,
            "action_id": str(self.action_id) if self.action_id is not None else None,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _is_owner_entity(
    conn: asyncpg.Connection,
    entity_id: uuid.UUID,
) -> bool:
    """Return True if *entity_id* resolves to an entity with role 'owner'.

    Queries ``public.entities.roles`` directly.  Returns False on any DB error
    or if the entity does not exist.

    Fails open (returns False on error) so that a DB hiccup during the owner
    check does not silently convert all triple writes into pending_actions.
    """
    try:
        row = await conn.fetchrow(
            "SELECT roles FROM public.entities WHERE id = $1",
            entity_id,
        )
        if row is None:
            return False
        roles = row["roles"] or []
        return "owner" in roles
    except Exception:  # noqa: BLE001
        logger.debug(
            "relationship_assert_fact: owner check failed for entity %s; treating as non-owner",
            entity_id,
            exc_info=True,
        )
        return False


async def _validate_predicate(conn: asyncpg.Connection, predicate: str) -> None:
    """Raise ValueError if *predicate* is not in relationship.entity_predicate_registry."""
    exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM relationship.entity_predicate_registry WHERE predicate = $1)",
        predicate,
    )
    if not exists:
        raise ValueError(
            f"Unknown predicate {predicate!r}: not registered in "
            "relationship.entity_predicate_registry. "
            "Add it via migration or use one of the seeded predicate names."
        )


async def _create_pending_action(
    conn: asyncpg.Connection,
    tool_name: str,
    tool_args: dict[str, Any],
    summary: str,
    *,
    dedup_match: dict[str, Any] | None = None,
    why: str | None = None,
    evidence: list[str] | None = None,
) -> uuid.UUID:
    """Insert a pending_actions row (or return an existing pending match).

    When *dedup_match* is provided, the writer first looks for an existing
    ``status='pending'`` row with the same ``tool_name`` whose ``tool_args``
    JSONB-contains *dedup_match*.  If found, the existing ``action_id`` is
    returned and no new row is created.  This prevents reconciler-driven
    duplicate approvals when the same (subject, predicate, object) fact is
    re-asserted on successive sweeps before the owner has acted on the prior
    request.

    *why* and *evidence* populate the ``pending_actions.why`` and
    ``pending_actions.evidence`` columns added in migration ``core_097`` so the
    Dispatch dossier UI can render a human-readable rationale for each pending
    approval rather than showing it blank.
    """
    if dedup_match is not None:
        existing = await conn.fetchval(
            """
            SELECT id FROM pending_actions
             WHERE tool_name = $1
               AND status   = 'pending'
               AND tool_args @> $2::jsonb
             ORDER BY requested_at ASC
             LIMIT 1
            """,
            tool_name,
            dedup_match,
        )
        if existing is not None:
            return existing

    action_id = uuid.uuid4()
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=_PENDING_ACTION_EXPIRY_HOURS)

    await conn.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, session_id, status, "
        "requested_at, expires_at, why, evidence) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
        action_id,
        tool_name,
        tool_args,
        summary,
        None,  # session_id not available at this layer
        "pending",
        now,
        expires_at,
        why,
        evidence if evidence is not None else [],
    )
    return action_id


async def _upsert_fact(
    conn: asyncpg.Connection,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
    src: str,
    conf: float,
    last_seen: datetime | None,
    weight: int | None,
    verified: bool,
    primary: bool | None,
) -> AssertResult:
    """Perform the idempotency / supersession logic on *conn*.

    Callers are responsible for wrapping this in a transaction when they need
    the supersession read-then-write pair to be atomic.
    """
    # 1. Check for an existing active row with the same (subject, predicate, object).
    existing = await conn.fetchrow(
        """
        SELECT id, src, conf, verified, last_seen
        FROM relationship.entity_facts
        WHERE subject   = $1
          AND predicate = $2
          AND object    = $3
          AND validity  = 'active'
        """,
        subject,
        predicate,
        object,
    )

    if existing is not None:
        old_id: uuid.UUID = existing["id"]

        # 2. Compare provenance fields to detect supersession.
        prov_changed = (
            existing["src"] != src
            or existing["conf"] != conf
            or bool(existing["verified"]) != verified
            or existing["last_seen"] != last_seen
        )

        if not prov_changed:
            # Idempotent: same identity + same provenance → no write.
            return AssertResult(outcome=AssertOutcome.unchanged, fact_id=old_id)

        # 3. Supersession: mark old row as superseded, insert new active row.
        await conn.execute(
            """
            UPDATE relationship.entity_facts
            SET validity   = 'superseded',
                updated_at = now()
            WHERE id = $1
            """,
            old_id,
        )
        # Keep the replacement insert conflict-safe as well: overlapping
        # reconciler runs can supersede the same old active row concurrently.
        new_id = await conn.fetchval(
            """
            INSERT INTO relationship.entity_facts (
                id, subject, predicate, object, object_kind,
                src, conf, last_seen, weight, verified, "primary",
                validity, created_at, updated_at
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, $4,
                $5, $6, $7, $8, $9, $10,
                'active', now(), now()
            )
            ON CONFLICT (subject, predicate, object) WHERE validity = 'active'
            DO UPDATE
                SET src        = EXCLUDED.src,
                    conf       = EXCLUDED.conf,
                    last_seen  = EXCLUDED.last_seen,
                    weight     = EXCLUDED.weight,
                    verified   = EXCLUDED.verified,
                    "primary"  = EXCLUDED."primary",
                    updated_at = now()
            RETURNING id
            """,
            subject,
            predicate,
            object,
            object_kind,
            src,
            conf,
            last_seen,
            weight,
            verified,
            primary,
        )
        return AssertResult(outcome=AssertOutcome.superseded, fact_id=new_id)

    # 4. No existing active row → insert.
    # The ON CONFLICT clause guards against a race between the read above and
    # this insert (e.g. concurrent calls from the reconciler).
    new_id = await conn.fetchval(
        """
        INSERT INTO relationship.entity_facts (
            id, subject, predicate, object, object_kind,
            src, conf, last_seen, weight, verified, "primary",
            validity, created_at, updated_at
        )
        VALUES (
            gen_random_uuid(), $1, $2, $3, $4,
            $5, $6, $7, $8, $9, $10,
            'active', now(), now()
        )
        ON CONFLICT (subject, predicate, object) WHERE validity = 'active'
        DO UPDATE
            SET src        = EXCLUDED.src,
                conf       = EXCLUDED.conf,
                last_seen  = EXCLUDED.last_seen,
                weight     = EXCLUDED.weight,
                verified   = EXCLUDED.verified,
                "primary"  = EXCLUDED."primary",
                updated_at = now()
        RETURNING id
        """,
        subject,
        predicate,
        object,
        object_kind,
        src,
        conf,
        last_seen,
        weight,
        verified,
        primary,
    )
    return AssertResult(outcome=AssertOutcome.inserted, fact_id=new_id)


async def _assert_on_conn(
    conn: asyncpg.Connection,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
    src: str,
    conf: float,
    last_seen: datetime | None,
    weight: int | None,
    verified: bool,
    primary: bool | None,
    wrap_transaction: bool,
    why: str | None = None,
    evidence: list[str] | None = None,
) -> AssertResult:
    """Execute the full assert logic on *conn*.

    Parameters
    ----------
    wrap_transaction:
        When True, wraps the upsert in ``conn.transaction()`` for atomic
        supersession.  Set to False when the caller is already inside a
        transaction to avoid nested-transaction errors.
    why, evidence:
        Forwarded to the pending_actions row on owner carve-out so the
        Dispatch dossier UI can render a rationale instead of a blank cell.
    """
    # Predicate validation (fast indexed lookup, runs on every call).
    await _validate_predicate(conn, predicate)

    # Owner carve-out (RFC 0017 §2.3).
    if await _is_owner_entity(conn, subject):
        tool_args: dict[str, Any] = {
            "subject": str(subject),
            "predicate": predicate,
            "object": object,
            "object_kind": object_kind,
            "src": src,
            "conf": conf,
            "verified": verified,
        }
        if last_seen is not None:
            tool_args["last_seen"] = last_seen.isoformat()
        if weight is not None:
            tool_args["weight"] = weight
        if primary is not None:
            tool_args["primary"] = primary

        # Dedup probe: any pending row whose tool_args JSONB contains the same
        # identity triple is the same approval request. Without this, a job
        # like contact_info_reconciler that re-runs every 30 min creates a new
        # pending row each tick until the owner acts.
        dedup_match: dict[str, Any] = {
            "subject": str(subject),
            "predicate": predicate,
            "object": object,
            "object_kind": object_kind,
        }

        summary = f"relationship_assert_fact: assert ({predicate}) on owner entity {subject}"
        # Default why/evidence when caller didn't supply richer context — keeps
        # the dossier non-blank even for direct (non-reconciler) callers.
        effective_why = why or (
            f"Approve to record `{predicate} = {object}` on your own entity "
            f"(source: {src}, confidence: {conf:g}). Rejecting leaves the "
            "fact unrecorded; you can also approve once and create a standing "
            "rule for this source."
        )
        effective_evidence: list[str] = (
            list(evidence)
            if evidence
            else [
                f"subject={subject}",
                f"predicate={predicate}",
                f"object={object}",
                f"src={src}",
            ]
        )

        action_id = await _create_pending_action(
            conn,
            "relationship_assert_fact",
            tool_args,
            summary,
            dedup_match=dedup_match,
            why=effective_why,
            evidence=effective_evidence,
        )
        logger.warning(
            "relationship_assert_fact: owner-entity mutation blocked; "
            "parked as pending_action %s (subject=%s, predicate=%s)",
            action_id,
            subject,
            predicate,
        )
        return AssertResult(
            outcome=AssertOutcome.pending_approval,
            fact_id=None,
            action_id=action_id,
        )

    # Non-owner path.
    kwargs: dict[str, Any] = dict(
        subject=subject,
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        last_seen=last_seen,
        weight=weight,
        verified=verified,
        primary=primary,
    )
    if wrap_transaction:
        async with conn.transaction():
            return await _upsert_fact(conn, **kwargs)
    else:
        return await _upsert_fact(conn, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def relationship_assert_fact(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    *,
    src: str,
    object_kind: str = "literal",
    conf: float = 1.0,
    last_seen: datetime | None = None,
    weight: int | None = None,
    verified: bool = False,
    primary: bool | None = None,
    conn: asyncpg.Connection | None = None,
    why: str | None = None,
    evidence: list[str] | None = None,
) -> AssertResult:
    """Assert a fact triple in ``relationship.entity_facts``.

    This is the SINGLE authoritative ingress point for all writes to
    ``relationship.entity_facts``.  All endpoints that need to write a triple
    (contacts CRUD, entity API, merge, archive, promote-tier, queue/dismiss,
    dual-write shim, backfill) MUST call this function.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  Used when *conn* is None.
    subject:
        UUID of the subject entity (FK to ``public.entities.id``).
    predicate:
        Predicate identifier.  Must exist in ``relationship.entity_predicate_registry``.
    object:
        Object value: a literal string for contact predicates, or an entity
        UUID coerced to text for relational predicates.
    src:
        Authoring butler slug (e.g. ``'relationship'``, ``'migration'``).
    object_kind:
        ``'literal'`` (default) or ``'entity'``.
    conf:
        Confidence in [0.0, 1.0] (default 1.0).
    last_seen:
        Timestamp of the most recent observation (nullable).
    weight:
        Relational aggregation weight (nullable).
    verified:
        Owner-confirmed flag (default False).
    primary:
        Primary-of-kind flag for multi-valued contact predicates (nullable).
    conn:
        Optional open ``asyncpg.Connection``.  Pass this when calling from
        inside an existing transaction to avoid nested-transaction deadlocks.
        When omitted, the writer acquires its own connection from *pool* and
        manages its own transaction.
    why:
        Human-readable rationale shown to the owner in the approvals UI when
        the owner carve-out fires.  Falls back to a generated sentence.
    evidence:
        Ordered list of evidence strings shown to the owner in the approvals
        UI under the rationale.  Falls back to a minimal identity summary.

    Returns
    -------
    AssertResult
        Outcome discriminant plus ``fact_id`` (or ``action_id`` on
        ``pending_approval``).

    Raises
    ------
    ValueError
        When *predicate* is not registered, or *conf* is outside [0, 1], or
        *object_kind* is not ``'literal'`` or ``'entity'``.
    """
    # --- Input validation (cheap; runs before any DB access) ---
    if object_kind not in ("literal", "entity"):
        raise ValueError(f"Invalid object_kind {object_kind!r}: must be 'literal' or 'entity'.")
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"conf must be in [0.0, 1.0]; got {conf!r}.")

    kwargs: dict[str, Any] = dict(
        subject=subject,
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        last_seen=last_seen,
        weight=weight,
        verified=verified,
        primary=primary,
        why=why,
        evidence=evidence,
    )

    if conn is not None:
        # Caller owns the connection (and likely an open transaction).
        # Do NOT open another transaction — that would deadlock.
        return await _assert_on_conn(conn, wrap_transaction=False, **kwargs)

    # No caller-supplied connection: acquire one from the pool and manage the
    # transaction ourselves.
    async with pool.acquire() as acquired_conn:
        return await _assert_on_conn(acquired_conn, wrap_transaction=True, **kwargs)


# ---------------------------------------------------------------------------
# Retraction helper
# ---------------------------------------------------------------------------


async def retract_contact_info_fact(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    ci_type: str,
    ci_value: str,
    *,
    conn: asyncpg.Connection | None = None,
) -> uuid.UUID | None:
    """Retract the active ``has-*`` fact matching *(subject, ci_type, ci_value)*.

    Mirrors the retraction performed by
    ``delete_entity_contact`` (``DELETE /entities/{id}/contacts/{pred}/{hash}``):
    marks the matching row ``validity = 'retracted'``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.  Used when *conn* is None.
    subject:
        UUID of the subject entity (FK to ``public.entities.id``).
    ci_type:
        ``public.contact_info.type`` value (e.g. ``'email'``, ``'phone'``,
        ``'telegram'``).  Used to derive the predicate via
        :func:`contact_info_type_to_predicate`.
    ci_value:
        The channel value (object string) of the fact to retract.
    conn:
        Optional open ``asyncpg.Connection``.  Pass when calling from inside
        an existing transaction to avoid nested-transaction errors.

    Returns
    -------
    uuid.UUID | None
        The ``id`` of the retracted row, or ``None`` when no active fact
        matching ``(subject, predicate, ci_value)`` was found (already
        retracted or never asserted).

    Notes
    -----
    - Types that have no registered predicate mapping (e.g.
      ``'telegram_chat_id'``, ``'address'``) are silently skipped — the
      function returns ``None`` without touching the DB.
    - The caller is responsible for supplying the correct ``ci_value``; the
      retraction is keyed on the exact string stored in ``object``.
    """
    predicate = contact_info_type_to_predicate(ci_type)
    if predicate is None:
        # No triple for this channel type — nothing to retract.
        return None

    async def _retract(c: asyncpg.Connection) -> uuid.UUID | None:
        row = await c.fetchrow(
            """
            SELECT id
            FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = $2
              AND object    = $3
              AND validity  = 'active'
            """,
            subject,
            predicate,
            ci_value,
        )
        if row is None:
            return None

        fact_id: uuid.UUID = row["id"]
        await c.execute(
            """
            UPDATE relationship.entity_facts
            SET validity   = 'retracted',
                updated_at = now()
            WHERE id = $1
            """,
            fact_id,
        )
        logger.info(
            "retract_contact_info_fact: retracted fact %s (subject=%s, predicate=%s, type=%s)",
            fact_id,
            subject,
            predicate,
            ci_type,
        )
        return fact_id

    if conn is not None:
        return await _retract(conn)

    async with pool.acquire() as acquired_conn:
        return await _retract(acquired_conn)
