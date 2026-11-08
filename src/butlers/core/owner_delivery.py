"""Canonical owner delivery: one subject, many presentations, single-use action tokens.

RFC 0038 S6, REQ-owner-delivery-001/002 (bu-dhloi1.13). Tables: ``core_317``.

- A **subject** references exactly one existing authority row (an approval action, which
  also decides a parked goal effect; a goal answer or a pending ask). It is never another
  approval authority: an approve/reject still runs the owning schema's decision in the same
  transaction, through the ``decide`` callback :func:`redeem` is given.
- A **presentation** is one surface's rendering of a subject at one generation. Its retry
  identity is ``(subject, surface, generation)``. Tokens are minted only while it is still
  ``pending`` (never handed to a provider); once a send has begun it is ``unknown`` until the
  provider confirms or fails it, and is never blindly retried as a new success.
- An **action token** is ``od1:`` plus an unpredictable 256-bit nonce. Only its SHA-256
  verifier is stored, with every binding: subject, action, exact action digest, owner,
  surface, generation and expiry. A token is not authentication; callers authenticate the
  owner and route first.

Any winning action (decision or acknowledgement) bumps the subject generation in the same
transaction, so every competing token is stale. Retraction of the other presentations is
then attempted by the surface owner; a failed retraction is receipted and leaves only
harmless stale UI. Tokens, nonces and provider ids never enter logs or receipts.

Nothing here reads model-supplied surface text: a presentation's route comes from the
caller's trusted owner identity and route resolution.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID, uuid4

TOKEN_PREFIX: Final = "od1"
#: ``od1:`` plus 43 base64url characters: 47 bytes, inside Telegram's 64-byte callback limit.
TOKEN_NONCE_BYTES: Final = 32
SURFACES: Final = frozenset({"dashboard", "telegram"})
ACTIONS: Final = frozenset({"approve", "reject", "ack"})
DECISIONS: Final = frozenset({"approve", "reject"})
SUBJECT_KINDS: Final = frozenset({"approval", "goal_answer", "pending_ask"})
RESOLVED: Final = frozenset({"decided", "cancelled", "expired"})
RETRACTION_MAX_FAILED_ATTEMPTS: Final = 5
RETRACTION_RETRY_AFTER_S: Final = 900

Surface = Literal["dashboard", "telegram"]
Action = Literal["approve", "reject", "ack"]
SendOutcome = Literal["sent", "failed", "unknown"]


class OwnerDeliveryRefused(Exception):
    """A fixed, content-free refusal reason; the transaction it is raised in rolls back."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def mint_token() -> tuple[str, str]:
    """A fresh ``(token, verifier)``; persist only the verifier."""
    nonce = secrets.token_bytes(TOKEN_NONCE_BYTES)
    encoded = base64.urlsafe_b64encode(nonce).rstrip(b"=").decode("ascii")
    return f"{TOKEN_PREFIX}:{encoded}", hashlib.sha256(nonce).hexdigest()


def token_verifier(token: object) -> str | None:
    """The verifier of a well-formed token, or ``None`` for anything else."""
    if not isinstance(token, str) or len(token) != len(TOKEN_PREFIX) + 1 + 43:
        return None
    prefix, separator, encoded = token.partition(":")
    if prefix != TOKEN_PREFIX or not separator:
        return None
    try:
        nonce = base64.urlsafe_b64decode(encoded + "=")
    except (ValueError, TypeError):
        return None
    if len(nonce) != TOKEN_NONCE_BYTES:
        return None
    # Reject non-canonical encodings so one nonce has exactly one token spelling.
    if base64.urlsafe_b64encode(nonce).rstrip(b"=").decode("ascii") != encoded:
        return None
    return hashlib.sha256(nonce).hexdigest()


def _same_digest(left: object, right: object) -> bool:
    """Constant-time equality of two hex digests; anything else is unequal."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(left.encode("ascii", "replace"), right.encode("ascii", "replace"))


def digest_of(value: object) -> str:
    """SHA-256 of canonical JSON, the digest form every subject binding uses."""
    canonical = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Subject:
    id: UUID
    kind: str
    owning_schema: str
    source_ref: str
    owner_entity: UUID
    action_digest: str
    generation: int
    state: str

    @classmethod
    def from_row(cls, row: Any) -> Subject:
        return cls(
            row["id"],
            row["kind"],
            row["owning_schema"],
            row["source_ref"],
            row["owner_entity"],
            row["action_digest"],
            row["generation"],
            row["state"],
        )


@dataclass(frozen=True, slots=True)
class Presentation:
    id: UUID
    subject_id: UUID
    generation: int
    surface: str
    state: str
    #: Fresh tokens by action; empty when the presentation was already handed off.
    tokens: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class RetractTarget:
    """A presentation whose actions are now stale; its surface owner removes them."""

    presentation_id: UUID
    surface: str
    route_ref: str
    provider_ref: str | None
    #: The authority row behind the card, so its surface owner can say what it was about.
    kind: str | None = None
    source_ref: str | None = None
    owning_schema: str | None = None


@dataclass(frozen=True, slots=True)
class Redemption:
    outcome: Literal["won", "duplicate", "refused"]
    reason: str | None = None
    action: str | None = None
    subject_id: UUID | None = None
    source_ref: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    retract: tuple[RetractTarget, ...] = ()


#: Runs the owning authority's decision on the redemption's own connection. It must raise
#: (rolling everything back) when the authority refuses; its return value is the outcome.
Decide = Callable[[Any, str], Awaitable[dict[str, Any]]]


async def rollout_enabled(conn: Any) -> bool:
    """Whether the canonical owner-delivery path is switched on (default off).

    A schema migrated without the core ledger (core_317) is simply off.
    """
    if await conn.fetchval("SELECT to_regclass('public.owner_delivery_rollout')") is None:
        return False
    value = await conn.fetchval("SELECT enabled FROM public.owner_delivery_rollout WHERE singleton")
    return value is True


async def append_receipt(
    conn: Any,
    subject_id: UUID,
    kind: str,
    generation: int,
    *,
    presentation_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO public.owner_delivery_receipts "
        "(subject_id, owning_schema, presentation_id, kind, generation, detail) "
        "SELECT id, owning_schema, $2, $3, $4, ($5::text)::jsonb "
        "FROM public.owner_delivery_subjects WHERE id = $1",
        subject_id,
        presentation_id,
        kind,
        generation,
        json.dumps(detail or {}),
    )


async def open_subject(
    conn: Any,
    *,
    kind: str,
    source_ref: str,
    owner_entity: UUID,
    content_digest: str,
    action_digest: str,
    goal_effect_id: UUID | None = None,
) -> Subject:
    """Open (or return) the one subject for an authority row in the current schema.

    Idempotent per ``(schema, kind, source_ref)``; a different digest or owner for the same
    source refuses rather than silently re-binding the subject.
    """
    if kind not in SUBJECT_KINDS:
        raise OwnerDeliveryRefused("subject_kind_invalid")
    inserted = await conn.fetchrow(
        "INSERT INTO public.owner_delivery_subjects "
        "(id, kind, owning_schema, source_ref, goal_effect_id, owner_entity, "
        " content_digest, action_digest) "
        "VALUES ($1, $2, current_schema(), $3, $4, $5, $6, $7) "
        "ON CONFLICT (owning_schema, kind, source_ref) DO NOTHING RETURNING *",
        uuid4(),
        kind,
        source_ref,
        goal_effect_id,
        owner_entity,
        content_digest,
        action_digest,
    )
    if inserted is not None:
        await append_receipt(conn, inserted["id"], "opened", inserted["generation"])
        return Subject.from_row(inserted)
    row = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_subjects "
        "WHERE owning_schema = current_schema() AND kind = $1 AND source_ref = $2",
        kind,
        source_ref,
    )
    if row is None:
        raise OwnerDeliveryRefused("subject_unavailable")
    if (
        row["owner_entity"] != owner_entity
        or row["content_digest"] != content_digest
        or row["action_digest"] != action_digest
    ):
        raise OwnerDeliveryRefused("subject_binding_changed")
    return Subject.from_row(row)


async def present(
    conn: Any,
    subject_id: UUID,
    *,
    surface: Surface,
    route_ref: str,
    actions: Sequence[Action],
    expires_at: datetime,
    legacy_ref: str | None = None,
) -> Presentation:
    """The subject's presentation on ``surface`` at its current generation, with tokens.

    A crash-retry reuses the same row. Tokens are minted only while the row is still
    ``pending``; a presentation already handed to its provider returns without tokens, so
    a retry can never become a second, unreceipted send.
    """
    if surface not in SURFACES or not actions or not set(actions) <= ACTIONS:
        raise OwnerDeliveryRefused("presentation_invalid")
    subject = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_subjects WHERE id = $1 FOR UPDATE", subject_id
    )
    if subject is None:
        raise OwnerDeliveryRefused("subject_unavailable")
    if subject["state"] in RESOLVED:
        raise OwnerDeliveryRefused("subject_resolved")
    generation = subject["generation"]
    row = await conn.fetchrow(
        "INSERT INTO public.owner_delivery_presentations "
        "(id, subject_id, owning_schema, generation, surface, route_ref, retry_key, legacy_ref) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
        "ON CONFLICT (subject_id, surface, generation) DO NOTHING RETURNING *",
        uuid4(),
        subject_id,
        subject["owning_schema"],
        generation,
        surface,
        route_ref,
        f"{subject_id}:{surface}:{generation}",
        legacy_ref,
    )
    if row is None:
        row = await conn.fetchrow(
            "SELECT * FROM public.owner_delivery_presentations "
            "WHERE subject_id = $1 AND surface = $2 AND generation = $3",
            subject_id,
            surface,
            generation,
        )
        if row is None or row["route_ref"] != route_ref:
            raise OwnerDeliveryRefused("presentation_route_changed")
    else:
        await append_receipt(
            conn,
            subject_id,
            "presented",
            generation,
            presentation_id=row["id"],
            detail={"surface": surface},
        )
    state = row["state"]
    if state == "failed":
        # The provider proved non-delivery: revoke the tokens that never reached the
        # owner, then present again under the same retry identity.
        await conn.execute(
            "UPDATE public.owner_delivery_tokens SET consumed_at = clock_timestamp(), "
            'outcome = \'{"revoked": "send_failed"}\'::jsonb '
            "WHERE presentation_id = $1 AND consumed_at IS NULL",
            row["id"],
        )
        await conn.execute(
            "UPDATE public.owner_delivery_presentations SET state = 'pending' WHERE id = $1",
            row["id"],
        )
        state = "pending"
    # The dashboard renders into the owner's authenticated response itself, so every
    # render is a delivery and carries its own tokens; a provider surface mints only
    # before its send begins, so a retry can never become a second unreceipted send.
    mintable = state in ("pending", "sent") if surface == "dashboard" else state == "pending"
    if not mintable:
        return Presentation(row["id"], subject_id, generation, surface, state)
    if surface == "dashboard" and state == "pending":
        await conn.execute(
            "UPDATE public.owner_delivery_presentations SET state = 'sent' WHERE id = $1",
            row["id"],
        )
        if subject["state"] in ("pending", "unknown"):
            await conn.execute(
                "UPDATE public.owner_delivery_subjects SET state = 'delivered' WHERE id = $1",
                subject_id,
            )
        state = "sent"
    tokens: dict[str, str] = {}
    for action in actions:
        token, verifier = mint_token()
        await conn.execute(
            "INSERT INTO public.owner_delivery_tokens "
            "(verifier, subject_id, owning_schema, presentation_id, action, action_digest, "
            " owner_entity, surface, generation, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            verifier,
            subject_id,
            subject["owning_schema"],
            row["id"],
            action,
            subject["action_digest"],
            subject["owner_entity"],
            surface,
            generation,
            expires_at,
        )
        tokens[action] = token
    return Presentation(row["id"], subject_id, generation, surface, state, tokens)


#: Lock order: the authority row (``pending_actions``) when a path needs it, then the
#: subject row, then the rows below it. The RFC 0023 rows (intent, then delivery rows) and
#: the subject's presentation or token rows are both below the subject but have no fixed
#: order between them: ``close_for_authority`` locks presentations before RFC 0023 rows,
#: while ``complete_handoff`` and ``defer_pending_action`` lock the RFC 0023 rows first
#: (defer takes the intent, RFC rows, then presentations). That is safe because every path
#: that touches both holds the subject (and the action, where it takes one) first, so they
#: queue instead of deadlocking.
#: The one writer that skips the authority row is ``complete_handoff``: the executor holds
#: that row across the tool call, and a handoff result must not wait for it
#: (``tests/core/test_owner_delivery_lock_order.py`` and the PG races in
#: ``tests/integration/test_owner_delivery_ledger.py``).
LOCK_ORDER: Final = ("authority", "subject", "rfc0023|presentation|token")


async def lock_subject_of(conn: Any, presentation_id: UUID) -> bool:
    """Lock the subject of ``presentation_id`` only; ``False`` if there is none.

    For callers that hold the authority row and must take the subject before touching
    another store's rows (the RFC 0023 handoff), keeping :data:`LOCK_ORDER` global.
    """
    subject_id = await conn.fetchval(
        "SELECT subject_id FROM public.owner_delivery_presentations WHERE id = $1",
        presentation_id,
    )
    if not isinstance(subject_id, UUID):
        return False
    locked = await conn.fetchval(
        "SELECT id FROM public.owner_delivery_subjects WHERE id = $1 FOR UPDATE", subject_id
    )
    return locked is not None


async def _lock_subject_then_presentation(conn: Any, presentation_id: UUID) -> tuple[Any, Any]:
    """``(subject, presentation)`` rows, locked in :data:`LOCK_ORDER`, or ``(None, None)``."""
    # subject_id is immutable (trigger-enforced), so reading it unlocked is safe.
    subject_id = await conn.fetchval(
        "SELECT subject_id FROM public.owner_delivery_presentations WHERE id = $1",
        presentation_id,
    )
    if not isinstance(subject_id, UUID):
        return None, None
    subject = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_subjects WHERE id = $1 FOR UPDATE", subject_id
    )
    presentation = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_presentations WHERE id = $1 FOR UPDATE",
        presentation_id,
    )
    if subject is None or presentation is None:
        return None, None
    return subject, presentation


async def begin_send(conn: Any, presentation_id: UUID) -> bool:
    """Mark a presentation as handed to its provider before the call (pending -> unknown).

    From here a crash leaves an honest ``unknown`` rather than an unsent ``pending`` that a
    retry would mint fresh tokens for and send again.
    """
    subject, _ = await _lock_subject_then_presentation(conn, presentation_id)
    if subject is None:
        return False
    tag = await conn.execute(
        "UPDATE public.owner_delivery_presentations SET state = 'unknown' "
        "WHERE id = $1 AND state = 'pending'",
        presentation_id,
    )
    return str(tag).endswith(" 1")


async def record_send(
    conn: Any,
    presentation_id: UUID,
    *,
    outcome: SendOutcome,
    provider_ref: str | None = None,
) -> str:
    """Settle a handed-off presentation; an ambiguous provider result stays ``unknown``."""
    if outcome not in ("sent", "failed", "unknown"):
        raise OwnerDeliveryRefused("send_outcome_invalid")
    subject, row = await _lock_subject_then_presentation(conn, presentation_id)
    if row is None:
        raise OwnerDeliveryRefused("presentation_unavailable")
    kind = {"sent": "sent", "failed": "send_failed", "unknown": "send_unknown"}[outcome]
    if row["state"] == "unknown" and outcome != "unknown":
        await conn.execute(
            "UPDATE public.owner_delivery_presentations "
            "SET state = $2, provider_ref = COALESCE(provider_ref, $3) WHERE id = $1",
            presentation_id,
            outcome,
            provider_ref,
        )
    elif row["state"] == "unknown" and provider_ref is not None and row["provider_ref"] is None:
        await conn.execute(
            "UPDATE public.owner_delivery_presentations SET provider_ref = $2 WHERE id = $1",
            presentation_id,
            provider_ref,
        )
    if outcome == "sent" and subject["state"] in ("pending", "unknown"):
        await conn.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'delivered' WHERE id = $1",
            row["subject_id"],
        )
    await append_receipt(
        conn,
        row["subject_id"],
        kind,
        row["generation"],
        presentation_id=presentation_id,
        detail={"surface": row["surface"]},
    )
    return outcome


async def _retraction_targets(conn: Any, subject_id: UUID) -> tuple[RetractTarget, ...]:
    """Every live presentation of the subject; dashboard rows retract in the ledger itself."""
    rows = await conn.fetch(
        "SELECT id, surface, route_ref, provider_ref, state "
        "FROM public.owner_delivery_presentations "
        "WHERE subject_id = $1 AND state IN ('pending', 'sent', 'unknown') "
        "ORDER BY created_at, id FOR UPDATE",
        subject_id,
    )
    targets: list[RetractTarget] = []
    for row in rows:
        if row["surface"] == "dashboard" or row["state"] == "pending":
            # Nothing outside the ledger renders these: retracting them here is complete.
            await conn.execute(
                "UPDATE public.owner_delivery_presentations SET state = 'retracted' WHERE id = $1",
                row["id"],
            )
            continue
        targets.append(
            RetractTarget(row["id"], row["surface"], row["route_ref"], row["provider_ref"])
        )
    return tuple(targets)


async def _refuse(
    conn: Any, token_row: Any, reason: str, subject_generation: int | None = None
) -> Redemption:
    await append_receipt(
        conn,
        token_row["subject_id"],
        "refused",
        subject_generation or token_row["generation"],
        presentation_id=token_row["presentation_id"],
        detail={"reason": reason, "action": token_row["action"], "surface": token_row["surface"]},
    )
    return Redemption("refused", reason, token_row["action"], token_row["subject_id"])


async def redeem(
    conn: Any,
    token: object,
    *,
    kind: str,
    source_ref: str,
    expected_action: Action,
    owner_entity: UUID,
    surface: Surface,
    current_action_digest: str,
    decide: Decide | None = None,
    now: datetime | None = None,
) -> Redemption:
    """Validate a token against every binding and the current subject fence, then act once.

    Call inside the transaction that holds the lock of the authority row named by
    ``kind``/``source_ref`` in the current schema; a token for any other subject is
    refused before anything else, and so is a token for any action but
    ``expected_action`` (an ack token never reaches approve or deny). Order: lock subject,
    lock token, verify, CAS the
    subject (generation + 1, so every competing token is stale), run ``decide`` for
    approve/reject, consume the token, receipt. A replay of the winning token answers
    ``duplicate`` without acting again. Refusals change no state other than an
    append-only receipt.
    """
    verifier = token_verifier(token)
    if verifier is None:
        return Redemption("refused", "token_malformed")
    # Token bindings are immutable, so its subject id can be read before locking; the
    # locks then follow LOCK_ORDER (subject before token).
    subject_id = await conn.fetchval(
        "SELECT subject_id FROM public.owner_delivery_tokens WHERE verifier = $1", verifier
    )
    if not isinstance(subject_id, UUID):
        return Redemption("refused", "token_unknown")
    subject = await conn.fetchrow(
        "SELECT *, current_schema() AS here FROM public.owner_delivery_subjects "
        "WHERE id = $1 FOR UPDATE",
        subject_id,
    )
    if subject is None:
        return Redemption("refused", "subject_unavailable")
    row = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_tokens WHERE verifier = $1 FOR UPDATE", verifier
    )
    if row is None:
        return Redemption("refused", "token_unknown")
    # Never act on one authority row with another subject's token. A foreign schema's
    # subject is not ours to receipt either (row security would refuse the write).
    if subject["owning_schema"] != subject["here"]:
        return Redemption("refused", "subject_mismatch")
    if (subject["kind"], subject["source_ref"]) != (kind, source_ref):
        return await _refuse(conn, row, "subject_mismatch", subject["generation"])
    # Who and where come before any replay answer: a recorded outcome is never
    # disclosed to a different owner or surface.
    if owner_entity != row["owner_entity"] or owner_entity != subject["owner_entity"]:
        return await _refuse(conn, row, "wrong_owner", subject["generation"])
    if surface != row["surface"]:
        return await _refuse(conn, row, "wrong_surface", subject["generation"])
    if row["action"] != expected_action:
        return await _refuse(conn, row, "action_mismatch", subject["generation"])
    if row["consumed_at"] is not None:
        raw = row["outcome"]
        outcome = dict(raw if isinstance(raw, dict) else json.loads(raw))
        if "revoked" in outcome:
            return await _refuse(conn, row, "token_revoked", subject["generation"])
        # The caller answers a replay from the authority row; nothing acts again.
        return Redemption(
            "duplicate", None, row["action"], row["subject_id"], subject["source_ref"]
        )
    moment = now or await conn.fetchval("SELECT clock_timestamp()")
    if moment >= row["expires_at"]:
        return await _refuse(conn, row, "token_expired", subject["generation"])
    if subject["state"] in RESOLVED:
        return await _refuse(conn, row, "subject_resolved", subject["generation"])
    if row["generation"] != subject["generation"]:
        return await _refuse(conn, row, "stale_generation", subject["generation"])
    if not (
        _same_digest(row["action_digest"], subject["action_digest"])
        and _same_digest(subject["action_digest"], current_action_digest)
    ):
        return await _refuse(conn, row, "action_changed", subject["generation"])
    action = row["action"]
    if action in DECISIONS and (subject["kind"] != "approval" or decide is None):
        return await _refuse(conn, row, "action_unsupported", subject["generation"])

    next_generation = subject["generation"] + 1
    if action == "ack":
        await conn.execute(
            "UPDATE public.owner_delivery_subjects "
            "SET state = 'acknowledged', decided_surface = $2, generation = $3 WHERE id = $1",
            subject["id"],
            surface,
            next_generation,
        )
        result: dict[str, Any] = {}
    else:
        # Subject CAS before the authority decision, in one transaction: the decision's
        # own lifecycle hook then finds the subject already resolved. A refusal from
        # ``decide`` raises and rolls back the CAS with it.
        await conn.execute(
            "UPDATE public.owner_delivery_subjects SET state = 'decided', decision = $2, "
            "decided_surface = $3, resolved_at = $4, generation = $5 WHERE id = $1",
            subject["id"],
            action,
            surface,
            moment,
            next_generation,
        )
        assert decide is not None
        result = await decide(conn, action)
    await conn.execute(
        "UPDATE public.owner_delivery_tokens SET consumed_at = $2, outcome = ($3::text)::jsonb "
        "WHERE verifier = $1",
        verifier,
        moment,
        # Only the closed outcome: the authority row stays the one record of the call.
        json.dumps({"action": action, "generation": row["generation"], "won": True}),
    )
    retract = await _retraction_targets(conn, subject["id"])
    await append_receipt(
        conn,
        subject["id"],
        "acknowledged" if action == "ack" else "decided",
        next_generation,
        presentation_id=row["presentation_id"],
        detail={"action": action, "surface": surface},
    )
    return Redemption("won", None, action, subject["id"], subject["source_ref"], result, retract)


async def close_for_authority(
    conn: Any, *, kind: str, source_ref: str, status: str
) -> tuple[RetractTarget, ...]:
    """Resolve the subject of an authority row that left pending by any path.

    The owning decision calls this inside its own transaction, so a decision taken outside
    a token (legacy callback, expiry, cancellation, broker) still stales every token and
    yields the presentations to retract. A subject already resolved (the token path CASes
    first) or absent is a no-op.
    """
    state, decision = {
        "approved": ("decided", "approve"),
        "rejected": ("decided", "reject"),
        "expired": ("expired", None),
        "cancelled": ("cancelled", None),
    }.get(status, (None, None))
    if state is None:
        return ()
    # Same convention as the RFC 0023 delivery root: a store without the ledger (a
    # pre-core_317 schema, a storage double) has no subject to close.
    if (
        await conn.fetchval("SELECT to_regclass('public.owner_delivery_subjects') IS NOT NULL")
        is not True
    ):
        return ()
    row = await conn.fetchrow(
        # A close outside a token names no winning surface; an earlier acknowledgement's
        # surface would also violate the decided_surface/state CHECK on expiry or cancel.
        "UPDATE public.owner_delivery_subjects SET state = $3, decision = $4, "
        "decided_surface = NULL, resolved_at = clock_timestamp(), "
        "generation = generation + 1 "
        "WHERE owning_schema = current_schema() AND kind = $1 AND source_ref = $2 "
        "AND state NOT IN ('decided', 'cancelled', 'expired') RETURNING id, generation",
        kind,
        source_ref,
        state,
        decision,
    )
    if not row or not isinstance(row["id"], UUID):
        return ()
    retract = await _retraction_targets(conn, row["id"])
    await append_receipt(conn, row["id"], "closed", row["generation"], detail={"status": status})
    return retract


async def lock_open_subject(conn: Any, *, kind: str, source_ref: str) -> UUID | None:
    """Lock the current schema's unresolved subject of one authority row, if it has one.

    A store without the ledger (a pre-core_317 schema) has none, as in
    :func:`close_for_authority`.
    """
    if (
        await conn.fetchval("SELECT to_regclass('public.owner_delivery_subjects') IS NOT NULL")
        is not True
    ):
        return None
    value = await conn.fetchval(
        "SELECT id FROM public.owner_delivery_subjects "
        "WHERE owning_schema = current_schema() AND kind = $1 AND source_ref = $2 "
        "AND state NOT IN ('decided', 'cancelled', 'expired') FOR UPDATE",
        kind,
        source_ref,
    )
    return value if isinstance(value, UUID) else None


async def advance_generation(conn: Any, subject_id: UUID) -> None:
    """Move a subject locked by :func:`lock_open_subject` to a new generation, still open.

    Every token minted so far goes stale, so the next presentation on each surface is a new
    row with fresh tokens and its own provider reference. Sent cards fall to
    :func:`pending_retractions`; dashboard and unsent rows retract here.
    """
    await conn.execute(
        "UPDATE public.owner_delivery_subjects SET generation = generation + 1 WHERE id = $1",
        subject_id,
    )
    await _retraction_targets(conn, subject_id)


async def record_retraction(
    conn: Any, presentation_id: UUID, *, retracted: bool, reason: str | None = None
) -> bool:
    """Receipt a provider retraction attempt; failure leaves only harmless stale UI.

    Only a stale card (subject resolved, or moved past the card's generation) can be marked
    retracted: a live card's buttons are still the owner's way to decide.

    Returns whether this failure used the card's last attempt, once per card, so the caller
    can tell an operator: the card leaves the listing with its buttons still showing.
    """
    subject, row = await _lock_subject_then_presentation(conn, presentation_id)
    if row is None:
        raise OwnerDeliveryRefused("presentation_unavailable")
    if subject["state"] not in RESOLVED and row["generation"] == subject["generation"]:
        raise OwnerDeliveryRefused("presentation_live")
    if retracted and row["state"] in ("sent", "unknown"):
        await conn.execute(
            "UPDATE public.owner_delivery_presentations SET state = 'retracted' WHERE id = $1",
            presentation_id,
        )
    await append_receipt(
        conn,
        row["subject_id"],
        "retracted" if retracted else "retract_failed",
        row["generation"],
        presentation_id=presentation_id,
        detail={"reason": reason} if reason else {},
    )
    if retracted or row["state"] not in ("sent", "unknown"):
        return False
    failures = await conn.fetchval(
        "SELECT count(*) FROM public.owner_delivery_receipts "
        "WHERE subject_id = $1 AND presentation_id = $2 AND kind = 'retract_failed'",
        row["subject_id"],
        presentation_id,
    )
    return failures == RETRACTION_MAX_FAILED_ATTEMPTS


async def retractions_given_up(conn: Any, *, surface: Surface) -> int:
    """How many of this schema's stale cards ran out of attempts and still show their buttons.

    A schema without the ledger (core_317) has none.
    """
    if await conn.fetchval("SELECT to_regclass('public.owner_delivery_receipts')") is None:
        return 0
    count = await conn.fetchval(
        "SELECT count(*) FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id "
        "WHERE p.owning_schema = current_schema() AND p.surface = $1 "
        "AND p.state IN ('sent', 'unknown') "
        "AND (s.state IN ('decided', 'cancelled', 'expired') "
        "     OR p.generation < s.generation) "
        "AND (SELECT count(*) FROM public.owner_delivery_receipts r "
        "     WHERE r.subject_id = p.subject_id AND r.presentation_id = p.id "
        "     AND r.kind = 'retract_failed') >= $2",
        surface,
        RETRACTION_MAX_FAILED_ATTEMPTS,
    )
    return int(count or 0)


async def pending_retractions(
    conn: Any, *, surface: Surface, limit: int = 50
) -> tuple[RetractTarget, ...]:
    """This schema's live provider presentations whose actions are stale, oldest first.

    A presentation is stale once its subject resolved or moved past its generation, by
    whichever path. The surface owner drains this on a timer and reports each attempt
    through :func:`record_retraction`. Every failure is a receipt, so a card that can never
    be retracted must not be retried forever: a failed card comes back only after
    :data:`RETRACTION_RETRY_AFTER_S`, and not at all after
    :data:`RETRACTION_MAX_FAILED_ATTEMPTS` (:func:`retractions_given_up` counts those). Its
    buttons stay harmless, its tokens being stale. A provider outage is not a failed attempt:
    the surface owner reports nothing for it.
    """
    rows = await conn.fetch(
        "SELECT p.id, p.surface, p.route_ref, p.provider_ref, "
        "s.kind, s.source_ref, s.owning_schema "
        "FROM public.owner_delivery_presentations p "
        "JOIN public.owner_delivery_subjects s ON s.id = p.subject_id "
        "LEFT JOIN LATERAL ("
        "  SELECT count(*) AS attempts, max(r.created_at) AS latest "
        "  FROM public.owner_delivery_receipts r "
        "  WHERE r.subject_id = p.subject_id AND r.presentation_id = p.id "
        "  AND r.kind = 'retract_failed'"
        ") failed ON true "
        "WHERE p.owning_schema = current_schema() AND p.surface = $1 "
        "AND p.state IN ('sent', 'unknown') "
        "AND (s.state IN ('decided', 'cancelled', 'expired') "
        "     OR p.generation < s.generation) "
        "AND failed.attempts < $3 "
        "AND (failed.latest IS NULL "
        "     OR failed.latest <= clock_timestamp() - make_interval(secs => $4)) "
        "ORDER BY p.updated_at, p.id LIMIT $2",
        surface,
        limit,
        RETRACTION_MAX_FAILED_ATTEMPTS,
        float(RETRACTION_RETRY_AFTER_S),
    )
    return tuple(
        RetractTarget(
            row["id"],
            row["surface"],
            row["route_ref"],
            row["provider_ref"],
            row["kind"],
            row["source_ref"],
            row["owning_schema"],
        )
        for row in rows
    )


async def subject_for_source(conn: Any, *, kind: str, source_ref: str) -> Subject | None:
    """The current schema's subject for one authority row, if delivery opened one."""
    row = await conn.fetchrow(
        "SELECT * FROM public.owner_delivery_subjects "
        "WHERE owning_schema = current_schema() AND kind = $1 AND source_ref = $2",
        kind,
        source_ref,
    )
    return Subject.from_row(row) if row is not None else None


async def owner_entity_id(conn: Any) -> UUID | None:
    """The one owner entity id, or ``None`` (fail closed) when absent or ambiguous.

    Recipients and token bindings come from this identity, never from model text.
    """
    rows = await conn.fetch("SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 2")
    if len(rows) != 1 or not isinstance(rows[0]["id"], UUID):
        return None
    return rows[0]["id"]


@dataclass(frozen=True, slots=True)
class DeliveryInputs:
    """Stable typed inputs later lanes read; nothing here activates them.

    ``urgency_class`` (bu-dhloi1.22) has no admissible value yet, ``holding_reason`` is
    the broker's owner-policy hold receipt (bu-dhloi1.10), and presence-aware selection,
    Why doors and push (bu-4c7nvp.1.8, bu-dhloi1.24) consume ``surfaces`` without this
    module choosing among them.
    """

    subject_id: UUID
    kind: str
    generation: int
    state: str
    urgency_class: str | None
    holding_reason: dict[str, Any] | None
    surfaces: tuple[tuple[str, str], ...]


async def delivery_inputs(conn: Any, subject_id: UUID) -> DeliveryInputs | None:
    row = await conn.fetchrow(
        "SELECT id, kind, generation, state, urgency_class, holding_reason "
        "FROM public.owner_delivery_subjects WHERE id = $1",
        subject_id,
    )
    if row is None:
        return None
    presentations = await conn.fetch(
        "SELECT surface, state FROM public.owner_delivery_presentations "
        "WHERE subject_id = $1 AND generation = $2 ORDER BY surface",
        subject_id,
        row["generation"],
    )
    holding = row["holding_reason"]
    if isinstance(holding, str):
        holding = json.loads(holding)
    return DeliveryInputs(
        row["id"],
        row["kind"],
        row["generation"],
        row["state"],
        row["urgency_class"],
        holding,
        tuple((p["surface"], p["state"]) for p in presentations),
    )


async def record_holding_reason(conn: Any, subject_id: UUID, receipt: dict[str, Any]) -> None:
    """Attach the broker's typed owner-policy hold to an open subject (display only)."""
    if not (
        isinstance(receipt, dict)
        and isinstance(receipt.get("stage"), str)
        # Exactly int: a bool passes isinstance and then fails the column's number CHECK.
        and type(receipt.get("generation")) is int
        and isinstance(receipt.get("terms"), list)
    ):
        raise OwnerDeliveryRefused("holding_reason_invalid")
    await conn.execute(
        "UPDATE public.owner_delivery_subjects SET holding_reason = ($2::text)::jsonb "
        "WHERE id = $1 AND state NOT IN ('decided', 'cancelled', 'expired')",
        subject_id,
        json.dumps(receipt),
    )


__all__ = [
    "ACTIONS",
    "LOCK_ORDER",
    "advance_generation",
    "lock_open_subject",
    "lock_subject_of",
    "append_receipt",
    "DeliveryInputs",
    "delivery_inputs",
    "owner_entity_id",
    "pending_retractions",
    "record_holding_reason",
    "subject_for_source",
    "TOKEN_PREFIX",
    "OwnerDeliveryRefused",
    "Presentation",
    "Redemption",
    "RetractTarget",
    "Subject",
    "begin_send",
    "close_for_authority",
    "digest_of",
    "mint_token",
    "open_subject",
    "present",
    "record_retraction",
    "retractions_given_up",
    "record_send",
    "redeem",
    "rollout_enabled",
    "token_verifier",
]
