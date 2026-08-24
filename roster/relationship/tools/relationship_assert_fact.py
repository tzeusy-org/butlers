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
   mirroring ``channel.py::channel_add``.

   **Self-identity exemption (bu-oluyt.4)** — when ``src`` is a trusted
   owner-self source (``"owner-bootstrap"`` or ``"owner-self"``), the owner is
   registering their own channel handles.  These writes bypass ``pending_actions``
   and go directly to ``entity_facts``.  Third-party assertions about the owner
   (any other ``src``) still park for approval.

   **Security (bu-vj46x)** — trusted sources are reachable ONLY from internal
   daemon/bootstrap code paths.  The MCP tool wrapper removes ``src`` from its
   public signature (hardcoded to ``"relationship"``), and the dashboard API
   models reject trusted sources via a Pydantic field validator, so neither
   LLM sessions nor HTTP callers can spoof them.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import asyncpg

from butlers.core.approvals_hooks import park_pending_action
from butlers.core.tool_call_capture import (
    get_current_approval_push_runtime,
    get_current_runtime_session_id,
)
from butlers.tools.relationship.fact_coverage import record_coverage
from butlers.tools.relationship.fact_evidence import (
    EvidencePacket,
    carry_evidence_forward,
    coerce_session_id,
    normalize_evidence,
    persist_evidence,
)
from butlers.tools.relationship.fact_evidence import (
    EvidenceReference as _EvidenceReference,
)
from butlers.tools.relationship.fact_evidence import (
    validate_evidence as _validate_typed_evidence,
)

logger = logging.getLogger(__name__)

# Pending actions expire after 72 hours (mirrors contact_info.py).
_PENDING_ACTION_EXPIRY_HOURS = 72
# Every pending_actions row this writer parks belongs to the relationship
# butler -- it never runs cross-butler (bu-g27ib).
_ORIGIN_BUTLER = "relationship"

# ``pending_actions.status`` values from which an approved write may execute.
# ``approved`` is the normal dispatch state; ``executed`` admits an operator
# retry of an action whose first dispatch failed after the status flip.
_EXECUTABLE_ACTION_STATUSES = frozenset({"approved", "executed"})


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
#     email             → has-email
#     phone             → has-phone
#     telegram          → has-handle   (scoped handle)
#     telegram_user_id  → has-handle   (numeric Telegram user ID, same predicate)
#     telegram_username → has-handle   (Telegram @username, same predicate)
#     telegram_chat_id  → has-handle   (group/channel routing key — non-secret handle)
#     linkedin          → has-handle
#     twitter           → has-handle
#     website           → has-website
#     other             → has-handle
#
# RFC 0004 Amendment 3 (bu-oluyt.1): telegram_chat_id is a non-secret routing
# handle whose canonical home is a has-handle triple in entity_facts (prefixed
# 'telegram:<id>').  It was previously documented as "intentionally unmapped"
# on the incorrect grounds that it was a group key rather than a contact
# identifier.  The split axis is SENSITIVITY, not TYPE — non-secret identifiers
# belong in entity_facts regardless of whether they identify a person or a group.
#
# Intentionally unmapped (no triple predicate home):
#     google_health      — OAuth routing/credential identifier (bu-k9ylx note)
#     home_assistant_url — service URL, not a personal contact channel
_CI_TYPE_TO_PREDICATE: dict[str, str] = {
    "email": "has-email",
    "phone": "has-phone",
    "telegram": "has-handle",
    "telegram_user_id": "has-handle",
    "telegram_username": "has-handle",
    "telegram_chat_id": "has-handle",  # non-secret routing handle → entity_facts
    "linkedin": "has-handle",
    "twitter": "has-handle",
    "website": "has-website",
    "other": "has-handle",
}


# ---------------------------------------------------------------------------
# Predicate alias map — normalises legacy underscore names to canonical
# hyphenated forms before registry lookup.  The registry stays hyphenated;
# this only normalises inbound names at the assert boundary.
# ---------------------------------------------------------------------------
#
# Two many-to-one mappings:
#   sibling_of  → family-of    (sibling is a family relationship)
#   married_to  → partner-of   (marriage is a partner relationship)
_PREDICATE_ALIAS_MAP: dict[str, str] = {
    # Original underscore→hyphen aliases (relational-edges-single-home, bu-i0pgi).
    "works_at": "works-at",
    "friend_of": "friend-of",
    "child_of": "child-of",
    "parent_of": "parent-of",
    "colleague_of": "colleague-of",
    "family_of": "family-of",
    "partner_of": "partner-of",
    "member_of": "member-of",
    # Many-to-one aliases: collapsed into broader categories.
    "sibling_of": "family-of",
    "married_to": "partner-of",
    # Long-tail relational predicates (bu-kgh8g; seeded in rel_026).
    # "manages" has no word-separator and maps to itself — no alias entry needed.
    "managed_by": "managed-by",
    "manages_property": "manages-property",
    "participant_of": "participant-of",
    "invited_by": "invited-by",
    "rental_agent": "rental-agent",
    "rental_location": "rental-location",
}


# ---------------------------------------------------------------------------
# Family confidence gate (bu-u0m00)
# ---------------------------------------------------------------------------
#
# Kinship predicates (parent-of, child-of, family-of) are prone to LLM
# mis-extraction when the model *infers* a relationship from context rather
# than reading an explicit statement.  Live example: "has a son" was
# extracted as a parent-of edge when the owner has no son.
#
# Gate: for non-owner entities, if the predicate is a kinship type and
# ``conf < _FAMILY_GATE_CONF``, the call is routed to ``pending_approval``
# (same mechanism as the owner carve-out) so the owner can confirm before a
# hard entity-to-entity edge is written.
#
# Threshold of 0.8 divides:
#   conf ≥ 0.8  — explicit statement ("X is Y's mother") → direct write
#   conf < 0.8  — inferred / ambiguous mention → pending for human review
#
# Owner-entity subjects are already gated by the owner carve-out (RFC 0017
# §2.3) regardless of predicate or conf, so this gate only fires on the
# non-owner path.

_FAMILY_GATE_PREDICATES: frozenset[str] = frozenset({"parent-of", "child-of", "family-of"})
_FAMILY_GATE_CONF: float = 0.8


# ---------------------------------------------------------------------------
# Owner self-identity sources (bu-oluyt.4)
# ---------------------------------------------------------------------------
#
# When the *subject* is the owner entity, writes normally park in
# ``pending_actions`` for human approval (RFC 0017 §2.3).  The exemption
# below allows the owner to register their OWN identity handles (telegram
# chat-id, email address, phone, etc.) WITHOUT approval by using a
# *trusted source* that originates from daemon/tool code paths — not from
# arbitrary LLM-generated input.
#
# Trusted sources:
#   "owner-bootstrap"  — daemon startup path (_ensure_owner_entity /
#                        lifecycle.run_startup) seeding identity facts on
#                        first boot.
#   "owner-self"       — owner-setup tools where the owner is explicitly
#                        entering their own channel identifiers.
#
# Security guarantee: these source strings MUST be set only by internal code
# paths (daemon startup, owner-setup tools).  They are NOT safe to propagate
# from untrusted external input — ``src`` is a free-form string at the library
# level, so caller discipline is the only gate.  Enforcement layers (bu-vj46x):
#   • MCP tool wrapper — ``src`` is removed from the tool signature and
#     hardcoded to ``"relationship"`` inside the wrapper so an LLM session can
#     never supply a trusted source.
#   • Dashboard API models — ``AddContactRequest`` and ``UpdateContactRequest``
#     reject ``owner-self`` / ``owner-bootstrap`` via a Pydantic field validator
#     so an HTTP caller cannot supply a trusted source.
# Any write whose ``src`` is NOT in this set still goes through the normal
# pending_actions gate.
_OWNER_SELF_SOURCES: frozenset[str] = frozenset({"owner-bootstrap", "owner-self"})

# Trusted INTERNAL-DERIVATION sources.  Unlike _OWNER_SELF_SOURCES (the owner
# registering their own identity handles), these are background jobs that derive
# owner facts SOLELY from the owner's own STRUCTURED data — currently just
# ``interaction_sync``, which mints ``knows`` edges from interaction *counts*.
# Owner-entity writes from these sources auto-apply instead of parking (RFC 0017
# §2.3 parks owner writes only from UNtrusted sources).
#
# Deliberately narrow: prose/text-extraction jobs (e.g. ``memory_curation``'s
# edge promotion) are NOT trusted — a mis-extracted owner edge is exactly the
# RFC 0017 incident class, so those keep parking for owner review.
#
# Same security guarantee as _OWNER_SELF_SOURCES: these source strings MUST be set
# only by internal code paths.  The MCP tool wrapper hardcodes ``src="relationship"``
# (an LLM session can never supply one) and the dashboard API models reject them.
_TRUSTED_INTERNAL_SOURCES: frozenset[str] = frozenset({"interaction_sync"})

# Source strings that bypass the owner-entity approval gate.  External callers
# (LLM / HTTP) must never be able to supply any of these.
_OWNER_AUTO_APPLY_SOURCES: frozenset[str] = _OWNER_SELF_SOURCES | _TRUSTED_INTERNAL_SOURCES


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


async def _validate_predicate(conn: asyncpg.Connection | asyncpg.Pool, predicate: str) -> None:
    """Raise ValueError if *predicate* is not in relationship.entity_predicate_registry.

    Accepts a bare ``Pool`` as well as a ``Connection`` -- both expose the same
    ``fetchval()`` surface (a pool auto-acquires/releases internally), which
    :func:`validate_fact_fields_or_raise` relies on to avoid an explicit
    ``pool.acquire()`` for a single read (bu-g27ib).
    """
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


async def validate_fact_fields_or_raise(
    pool: asyncpg.Pool,
    *,
    predicate: str,
    conf: float,
    object_kind: str,
) -> None:
    """Validate a fact's static fields without writing anything.

    Raises ``ValueError`` with the same messages :func:`relationship_assert_fact`
    would raise for an unregistered predicate, an out-of-range ``conf``, or an
    invalid ``object_kind`` -- but performs no mutation and reads only the
    predicate registry.

    Callers that assert a BATCH of facts inside one outer transaction (e.g.
    ``POST /entities``'s ``initial_facts`` loop) MUST pre-validate every fact
    in the batch with this function *before* starting that transaction.
    Reason (bu-g27ib): :func:`park_pending_action` (the owner-push choke
    point every parking fact routes through, bu-mda0r) writes its
    ``pending_actions`` row -- and fires the owner push -- on its own
    connection acquired from *pool*, independent of any caller-supplied
    ``conn``/transaction, because the push path needs real ``pool.acquire()``
    semantics. If an EARLIER fact in a batch parks (committing that row) and
    a LATER fact in the same batch then raises ``ValueError`` (e.g. an
    unregistered predicate), rolling back the outer transaction does NOT
    undo the earlier park: the result is an orphaned ``pending_actions`` row
    referencing data that was never persisted, and an owner push for a
    mutation that no longer exists. Ruling out every ``ValueError`` up front
    makes that mid-batch rollback structurally unreachable.
    """
    if object_kind not in ("literal", "entity"):
        raise ValueError(f"Invalid object_kind {object_kind!r}: must be 'literal' or 'entity'.")
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"conf must be in [0.0, 1.0]; got {conf!r}.")
    resolved_predicate = _PREDICATE_ALIAS_MAP.get(predicate, predicate)
    # Query *pool* directly (no explicit acquire()) -- a single read has no
    # need to hold a dedicated connection, and this keeps a batch
    # pre-validation pass from consuming an extra connection-pool slot per
    # fact on top of the one the caller's transaction already holds.
    await _validate_predicate(pool, resolved_predicate)


async def _create_pending_action(
    pool: asyncpg.Pool,
    tool_name: str,
    tool_args: dict[str, Any],
    summary: str,
    *,
    src: str,
    observed_at: datetime,
    dedup_match: dict[str, Any] | None = None,
    why: str | None = None,
    evidence: list[_EvidenceReference] | None = None,
) -> uuid.UUID:
    """Insert a pending_actions row (or return an existing pending match).

    When *dedup_match* is provided, the writer first looks for an existing
    ``status='pending'`` row with the same ``tool_name`` whose ``tool_args``
    JSONB-contains *dedup_match*.  If found, the existing ``action_id`` is
    returned and no new row is created.  This prevents reconciler-driven
    duplicate approvals when the same (subject, predicate, object) fact is
    re-asserted on successive sweeps before the owner has acted on the prior
    request.

    *why* and typed *evidence* populate the ``pending_actions`` dossier columns
    so the approvals UI can render a human-readable rationale and inspectable
    references rather than an untyped evidence string list.

    Takes *pool* (not a caller-supplied ``conn``) because the actual insert
    routes through :func:`butlers.core.approvals_hooks.park_pending_action`,
    the single choke point for PENDING inserts (bu-mda0r): it writes the row
    AND attempts the owner-facing push in one call, and the push path needs
    to acquire its own connection from a real pool (bu-g27ib). The dedup read
    has no transactional dependency on the caller's in-flight entity_facts
    write, so reading it from *pool* instead of the caller's ``conn`` is safe.

    *src* and *observed_at* are recorded in ``relationship.fact_approval_context``
    rather than in *tool_args*. Approval dispatch replays ``tool_args`` by
    splatting it into the MCP tool, so every key there must also be a tool
    parameter -- and ``src`` selects the owner carve-out's trusted-source
    exemption, which is exactly the value an LLM session must never be able to
    supply (bu-vj46x). Keeping it server-side makes the replay read the source
    from a row only this function ever wrote.
    """
    if dedup_match is not None:
        existing = await pool.fetchval(
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

    # Stamp the action's own id into the stored arguments. Approval dispatch
    # replays ``tool_args`` through the MCP tool, and ``approval_action_id`` is
    # what tells that replay it is executing an already-approved write rather
    # than proposing a new one -- without it the replay re-parks forever. The
    # dedup probe matches on the identity quadruple only, so the extra key
    # cannot break dedup.
    tool_args = {**tool_args, "approval_action_id": str(action_id)}

    # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast) --
    # asyncpg's registered jsonb codec already serializes once; pre-
    # serializing double-encodes into a jsonb-typed STRING (bu-cymc4/bu-bstqu).
    safe_tool_args = json.loads(json.dumps(tool_args, default=str))

    await _record_approval_context(pool, action_id=action_id, src=src, observed_at=observed_at)

    await park_pending_action(
        pool,
        action_id=action_id,
        tool_name=tool_name,
        tool_args=safe_tool_args,
        agent_summary=summary,
        requested_at=now,
        expires_at=expires_at,
        session_id=get_current_runtime_session_id(),
        why=why,
        evidence=evidence if evidence is not None else [],
        origin_butler=_ORIGIN_BUTLER,
        approval_push_runtime=get_current_approval_push_runtime(),
    )
    return action_id


async def _record_approval_context(
    pool: asyncpg.Pool,
    *,
    action_id: uuid.UUID,
    src: str,
    observed_at: datetime,
) -> None:
    """Record the server-written provenance of one parked fact write.

    Written before :func:`park_pending_action` so the context is already durable
    by the time the owner can possibly approve the action. Idempotent: a retry
    of the same action id keeps the first recorded provenance.
    """
    await pool.execute(
        """
        INSERT INTO relationship.fact_approval_context (action_id, src, observed_at)
        VALUES ($1, $2, $3)
        ON CONFLICT (action_id) DO NOTHING
        """,
        action_id,
        src,
        observed_at,
    )


@dataclass(frozen=True, slots=True)
class _ApprovedAction:
    """A ``pending_actions`` row the owner approved for exactly this triple.

    Everything here is server-written: ``src`` and ``observed_at`` were recorded
    in ``relationship.fact_approval_context`` when the writer parked the action,
    ``evidence`` in the dossier column, and ``session_id`` by
    :func:`park_pending_action`. The approved write therefore replays the
    owner's decision instead of trusting whatever the dispatch caller passed.
    """

    action_id: uuid.UUID
    src: str
    observed_at: datetime | None
    evidence: list[_EvidenceReference]
    session_id: uuid.UUID | None


async def _resolve_approved_action(
    conn: asyncpg.Connection,
    action_id: uuid.UUID,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
) -> _ApprovedAction:
    """Load and verify the approved action authorising this write.

    The ``approval_action_id`` argument travels through an LLM-reachable tool
    surface, so it is treated as a claim to be checked, never as authority in
    itself. The claim only survives if a real ``pending_actions`` row exists for
    THIS writer, is in an executable status, and its stored identity quadruple
    matches the triple being written. That makes escalation structurally
    unreachable: the only ``approval_action_id`` a caller can get accepted is one
    the owner already approved for precisely this triple, and the ``src`` /
    evidence used come from that row rather than from the caller (bu-vj46x).

    Raises ``ValueError`` on any mismatch -- failing loudly beats writing a fact
    under provenance we cannot substantiate.
    """
    row = await conn.fetchrow(
        """
        SELECT pa.tool_name, pa.tool_args, pa.status, pa.evidence, pa.session_id,
               ctx.src AS ctx_src, ctx.observed_at AS ctx_observed_at
        FROM pending_actions pa
        LEFT JOIN relationship.fact_approval_context ctx ON ctx.action_id = pa.id
        WHERE pa.id = $1
        """,
        action_id,
    )
    if row is None:
        raise ValueError(f"approval_action_id {action_id} does not identify a pending action.")
    if row["tool_name"] != "relationship_assert_fact":
        raise ValueError(
            f"approval_action_id {action_id} belongs to a different tool; "
            "it cannot authorise a fact write."
        )
    if row["status"] not in _EXECUTABLE_ACTION_STATUSES:
        raise ValueError(
            f"approval_action_id {action_id} is in status {row['status']!r}; "
            "only an approved action may execute a fact write."
        )

    stored_args = _as_json_object(row["tool_args"])
    claimed = (str(subject), predicate, object, object_kind)
    approved = (
        stored_args.get("subject"),
        stored_args.get("predicate"),
        stored_args.get("object"),
        stored_args.get("object_kind"),
    )
    if claimed != approved:
        raise ValueError(
            f"approval_action_id {action_id} was approved for a different triple; "
            "the write does not match what the owner approved."
        )

    stored_src = row["ctx_src"]
    if not isinstance(stored_src, str) or not stored_src:
        raise ValueError(
            f"approval_action_id {action_id} has no recorded source; "
            "the write cannot be attributed."
        )

    return _ApprovedAction(
        action_id=action_id,
        src=stored_src,
        observed_at=row["ctx_observed_at"],
        evidence=normalize_evidence(_as_json_value(row["evidence"])),
        session_id=coerce_session_id(row["session_id"]),
    )


def _as_json_value(value: Any) -> Any:
    """Decode a JSONB column that asyncpg may hand back as text."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _as_json_object(value: Any) -> dict[str, Any]:
    """Decode a JSONB column expected to hold an object; empty dict otherwise."""
    decoded = _as_json_value(value)
    return dict(decoded) if isinstance(decoded, dict) else {}


# Bounded retry budget for the concurrent-writer race. Each attempt re-reads
# the current active row and routes through supersession; a non-zero budget only
# matters when a competing writer keeps replacing the active row between our read
# and our insert, which is self-limiting in practice.
_MAX_UPSERT_ATTEMPTS = 5


async def _insert_active_fact(
    conn: asyncpg.Connection,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
    src: str,
    conf: float,
    last_seen: datetime | None,
    observed_at: datetime,
    weight: int | None,
    verified: bool,
    primary: bool | None,
    packet: EvidencePacket,
) -> uuid.UUID | None:
    """Insert a new ACTIVE row, returning its id, or ``None`` on conflict.

    Uses ``ON CONFLICT ... DO NOTHING`` so a concurrent writer that already holds
    the active (subject, predicate, object) slot is NEVER mutated in place —
    crucially, ``conf`` and ``observed_at`` on the existing active row are left
    untouched (spec: conf is immutable, superseded rows keep their observed_at).
    A ``None`` return signals the caller to re-read and route the collision
    through normal supersession.

    *packet* supplies the assertion provenance stamped onto the row
    (``assert_origin``/``assert_session_id``/``assert_action_id``): how this row
    came to be active, which runtime session authored it, and which approved
    action authorised it. It is written with the row, not after it, so a fact can
    never exist with its provenance missing.
    """
    return await conn.fetchval(
        """
        INSERT INTO relationship.entity_facts (
            id, subject, predicate, object, object_kind,
            src, conf, last_seen, observed_at, weight, verified, "primary",
            validity, created_at, updated_at,
            assert_origin, assert_session_id, assert_action_id
        )
        VALUES (
            gen_random_uuid(), $1, $2, $3, $4,
            $5, $6, $7, $8, $9, $10, $11,
            'active', now(), now(),
            $12, $13, $14
        )
        ON CONFLICT (subject, predicate, object) WHERE validity = 'active'
        DO NOTHING
        RETURNING id
        """,
        subject,
        predicate,
        object,
        object_kind,
        src,
        conf,
        last_seen,
        observed_at,
        weight,
        verified,
        primary,
        packet.origin,
        packet.session_id,
        packet.action_id,
    )


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
    observed_at: datetime,
    weight: int | None,
    verified: bool,
    primary: bool | None,
    packet: EvidencePacket,
) -> AssertResult:
    """Perform the idempotency / supersession logic on *conn*.

    Callers are responsible for wrapping this in a transaction when they need
    the supersession read-then-write pair to be atomic.

    *packet* is persisted on the SAME connection as the fact write, so evidence
    and fact commit together or not at all. On supersession the prior row's
    evidence is copied onto the replacement (``carried_from``) — the superseded
    row itself is never touched, so "why is this true" survives re-assertion
    without any ledger row being rewritten.

    ``observed_at`` is stamped onto the new active row.  On supersession the
    superseded row KEEPS its own ``observed_at`` (this function never rewrites
    it — supersession only flips ``validity`` and ``updated_at``).

    Concurrency contract (spec: conf immutable, observed_at preserved): every
    write goes through ``INSERT ... ON CONFLICT DO NOTHING``. We NEVER issue an
    in-place ``DO UPDATE`` that would overwrite ``conf``/``observed_at`` on an
    existing active row. When the unique active-slot index rejects our insert
    (another writer holds the slot), we re-read and retry, so the collision is
    resolved by normal supersession — the prior active row is marked
    ``superseded`` (keeping its own ``conf``/``observed_at``) before a fresh
    active row is inserted.
    """
    for _ in range(_MAX_UPSERT_ATTEMPTS):
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
                # Idempotent on the FACT: same identity + same provenance, so no
                # new row. The evidence packet is still appended — a second
                # source citing a reason for an already-known fact is new
                # knowledge even when the triple is not. The ledger's unique
                # (fact_id, kind, ref) index absorbs a literal repeat.
                await persist_evidence(conn, fact_id=old_id, packet=packet)
                return AssertResult(outcome=AssertOutcome.unchanged, fact_id=old_id)

            # 3. Supersession: retract the specific old row we just read, then
            #    insert the replacement. The UPDATE is guarded on the row id AND
            #    validity='active' so a racing supersession of the same row only
            #    succeeds once; if we lose that race, rowcount is 0 and we retry.
            status = await conn.execute(
                """
                UPDATE relationship.entity_facts
                SET validity   = 'superseded',
                    updated_at = now()
                WHERE id = $1
                  AND validity = 'active'
                """,
                old_id,
            )
            if status == "UPDATE 0":
                # Another writer already superseded this row out from under us.
                # Re-read and start over so we supersede the current active row.
                continue

            new_id = await _insert_active_fact(
                conn,
                subject=subject,
                predicate=predicate,
                object=object,
                object_kind=object_kind,
                src=src,
                conf=conf,
                last_seen=last_seen,
                observed_at=observed_at,
                weight=weight,
                verified=verified,
                primary=primary,
                packet=packet,
            )
            if new_id is None:
                # A competing writer slipped a new active row into the slot
                # between our UPDATE and our INSERT. We have already correctly
                # superseded the row we observed; loop to supersede theirs too
                # rather than overwriting it in place.
                continue
            await carry_evidence_forward(conn, from_fact_id=old_id, to_fact_id=new_id)
            await persist_evidence(conn, fact_id=new_id, packet=packet)
            return AssertResult(outcome=AssertOutcome.superseded, fact_id=new_id)

        # 4. No existing active row → insert. DO NOTHING (never DO UPDATE) so a
        #    concurrent writer's active row is never mutated in place; on conflict
        #    we re-read and route the collision through supersession above.
        new_id = await _insert_active_fact(
            conn,
            subject=subject,
            predicate=predicate,
            object=object,
            object_kind=object_kind,
            src=src,
            conf=conf,
            last_seen=last_seen,
            observed_at=observed_at,
            weight=weight,
            verified=verified,
            primary=primary,
            packet=packet,
        )
        if new_id is None:
            # Lost the insert race: an active row now exists. Re-read so we either
            # report `unchanged` (identical provenance) or supersede it.
            continue
        await persist_evidence(conn, fact_id=new_id, packet=packet)
        return AssertResult(outcome=AssertOutcome.inserted, fact_id=new_id)

    raise RuntimeError(
        "relationship_assert_fact: exhausted supersession retries under contention "
        f"for (subject={subject}, predicate={predicate}, object={object})."
    )


async def _assert_on_conn(
    conn: asyncpg.Connection,
    pool: asyncpg.Pool,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
    src: str,
    conf: float,
    last_seen: datetime | None,
    observed_at: datetime,
    weight: int | None,
    verified: bool,
    primary: bool | None,
    wrap_transaction: bool,
    why: str | None = None,
    evidence: list[_EvidenceReference] | None = None,
    approval_action_id: uuid.UUID | None = None,
) -> AssertResult:
    """Execute the full assert logic on *conn*.

    Parameters
    ----------
    pool:
        The caller's asyncpg pool. Used ONLY for the owner-carve-out /
        family-gate park paths below (:func:`_create_pending_action`), which
        need real pool semantics (``pool.acquire()``) for the owner push --
        every entity_facts read/write in this function still goes through
        *conn*.
    wrap_transaction:
        When True, wraps the upsert in ``conn.transaction()`` for atomic
        supersession.  Set to False when the caller is already inside a
        transaction to avoid nested-transaction errors.
    why, evidence:
        Forwarded to the pending_actions row on owner carve-out so the
        Dispatch dossier UI can render a rationale and typed evidence instead
        of a blank cell.
    approval_action_id:
        Set when this call is the EXECUTION of an owner-approved action rather
        than a fresh proposal. Verified against ``pending_actions`` before it
        does anything (:func:`_resolve_approved_action`); once verified it both
        skips the gates the owner already cleared and supplies ``src``,
        ``observed_at``, evidence and session from the parked row.
    """
    # Predicate validation (fast indexed lookup, runs on every call).
    await _validate_predicate(conn, predicate)

    approved: _ApprovedAction | None = None
    if approval_action_id is not None:
        approved = await _resolve_approved_action(
            conn,
            approval_action_id,
            subject=subject,
            predicate=predicate,
            object=object,
            object_kind=object_kind,
        )
        # The owner approved THIS triple: re-parking it would ask the same
        # question a second time and the fact would never land. Both gates below
        # are proposal-time gates, so an approved execution skips them and goes
        # straight to the write with the parked provenance.
        src = approved.src
        if approved.observed_at is not None:
            # The fact was observed when it was proposed, not when the owner got
            # round to approving it. Staleness bands read observed_at, so taking
            # the approval time here would make every approved fact look fresher
            # than the observation behind it.
            observed_at = approved.observed_at

    # Owner carve-out (RFC 0017 §2.3).
    # Exception: when *src* is a trusted source — either an owner-self source
    # (the owner registering their own identity handles) or a trusted
    # internal-derivation job (interaction_sync, memory_curation,
    # fact_retraction_curation) operating only on the owner's own data — the write
    # bypasses pending_actions and goes directly to entity_facts.
    # Third-party / message-extracted writes about the owner (any other src) still
    # park for approval.
    if (
        approved is None
        and await _is_owner_entity(conn, subject)
        and src not in _OWNER_AUTO_APPLY_SOURCES
    ):
        tool_args: dict[str, Any] = {
            "subject": str(subject),
            "predicate": predicate,
            "object": object,
            "object_kind": object_kind,
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
        effective_evidence: list[_EvidenceReference] = (
            list(evidence)
            if evidence
            else [
                {"type": "entity", "ref": str(subject), "note": "Subject entity."},
                {"type": "fact", "ref": predicate, "note": "Predicate."},
                {"type": "text", "ref": object, "note": "Proposed object value."},
                {"type": "text", "ref": src, "note": "Assertion source."},
            ]
        )

        action_id = await _create_pending_action(
            pool,
            "relationship_assert_fact",
            tool_args,
            summary,
            src=src,
            observed_at=observed_at,
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

    # Family confidence gate (bu-u0m00): low-confidence kinship assertions are
    # routed to pending_approval rather than writing a hard entity-to-entity edge.
    # This prevents inferred mis-extractions (e.g. "has a son" when untrue) from
    # silently writing incorrect parent-of/child-of/family-of edges.
    # High-confidence (conf ≥ 0.8) kinship assertions from explicit statements
    # proceed through the normal upsert path below.
    if approved is None and predicate in _FAMILY_GATE_PREDICATES and conf < _FAMILY_GATE_CONF:
        tool_args_gate: dict[str, Any] = {
            "subject": str(subject),
            "predicate": predicate,
            "object": object,
            "object_kind": object_kind,
            "conf": conf,
            "verified": verified,
        }
        if last_seen is not None:
            tool_args_gate["last_seen"] = last_seen.isoformat()
        if weight is not None:
            tool_args_gate["weight"] = weight
        if primary is not None:
            tool_args_gate["primary"] = primary

        dedup_match_gate: dict[str, Any] = {
            "subject": str(subject),
            "predicate": predicate,
            "object": object,
            "object_kind": object_kind,
        }
        gate_why = why or (
            f"Low-confidence kinship claim: `{predicate}` (conf={conf:g}) must be "
            "confirmed before a hard entity edge is written. Approve if the relationship "
            "is correct; reject if this was a mis-extraction."
        )
        gate_evidence: list[_EvidenceReference] = (
            list(evidence)
            if evidence
            else [
                {"type": "entity", "ref": str(subject), "note": "Subject entity."},
                {"type": "fact", "ref": predicate, "note": "Predicate."},
                {"type": "text", "ref": object, "note": "Proposed object value."},
                {
                    "type": "text",
                    "ref": f"conf={conf:g} (threshold={_FAMILY_GATE_CONF:g})",
                    "note": "Confidence gate evidence.",
                },
                {"type": "text", "ref": src, "note": "Assertion source."},
            ]
        )
        gate_summary = (
            f"relationship_assert_fact: low-confidence kinship {predicate!r} "
            f"(conf={conf:g}) on entity {subject} — gated for confirmation"
        )
        action_id = await _create_pending_action(
            pool,
            "relationship_assert_fact",
            tool_args_gate,
            gate_summary,
            src=src,
            observed_at=observed_at,
            dedup_match=dedup_match_gate,
            why=gate_why,
            evidence=gate_evidence,
        )
        logger.warning(
            "relationship_assert_fact: low-confidence kinship edge blocked by family gate; "
            "parked as pending_action %s (subject=%s, predicate=%s, conf=%g)",
            action_id,
            subject,
            predicate,
            conf,
        )
        return AssertResult(
            outcome=AssertOutcome.pending_approval,
            fact_id=None,
            action_id=action_id,
        )

    if approved is not None:
        packet = EvidencePacket(
            items=tuple(approved.evidence),
            src=approved.src,
            origin="approved",
            session_id=approved.session_id,
            action_id=approved.action_id,
        )
    else:
        packet = EvidencePacket(
            items=tuple(evidence or ()),
            src=src,
            origin="direct",
            session_id=coerce_session_id(get_current_runtime_session_id()),
        )

    kwargs: dict[str, Any] = dict(
        subject=subject,
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        last_seen=last_seen,
        observed_at=observed_at,
        weight=weight,
        verified=verified,
        primary=primary,
        packet=packet,
    )
    if wrap_transaction:
        async with conn.transaction():
            return await _write_fact_with_receipts(conn, kwargs)
    return await _write_fact_with_receipts(conn, kwargs)


async def _write_fact_with_receipts(
    conn: asyncpg.Connection,
    kwargs: dict[str, Any],
) -> AssertResult:
    """Write the fact and its coverage receipt as one unit.

    A successful assert is itself an observation: this source looked at this
    subject and predicate and found a value. Recording that alongside the fact
    is what lets a later read distinguish "nobody ever looked" from "we looked
    and there is nothing", and it must not be able to drift from the fact — so
    it runs on the same connection inside the same transaction.
    """
    result = await _upsert_fact(conn, **kwargs)
    await record_coverage(
        conn,
        subject=kwargs["subject"],
        predicate=kwargs["predicate"],
        src=kwargs["src"],
        outcome="present",
        observed_at=kwargs["observed_at"],
    )
    return result


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
    observed_at: datetime | None = None,
    weight: int | None = None,
    verified: bool = False,
    primary: bool | None = None,
    conn: asyncpg.Connection | None = None,
    why: str | None = None,
    evidence: list[_EvidenceReference] | None = None,
    approval_action_id: uuid.UUID | None = None,
) -> AssertResult:
    """Assert a fact triple in ``relationship.entity_facts``.

    This is the SINGLE authoritative ingress point for all writes to
    ``relationship.entity_facts``.  All endpoints that need to write a triple
    (contacts CRUD, entity API, merge, archive, dunbar-tier, queue/dismiss,
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
    observed_at:
        When the fact was actually observed, as distinct from the assertion
        time. Defaults to ``now()`` when omitted. An explicit value (e.g. a
        backdated import) is honoured verbatim. Stamped onto the new active row;
        on supersession the superseded row keeps its own ``observed_at``.
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
        Ordered typed evidence references (`type`, `ref`, `note`) recorded in
        ``relationship.fact_evidence`` alongside the written fact, and shown to
        the owner in the approvals UI under the rationale when the write parks.
        References only — the ledger never stores copied source content, and
        over-long refs/notes are rejected. Falls back to a minimal identity
        summary on the park paths.
    approval_action_id:
        Identifies the approved ``pending_actions`` row this call is executing.
        Only set by approval dispatch. It is verified against the stored row
        (same tool, executable status, same triple) before it grants anything;
        when it verifies, the owner and family gates are skipped and ``src``,
        ``observed_at``, evidence and the originating session are taken from
        the parked row rather than from the caller — so an approved fact keeps
        the time it was observed, not the time it was approved.

    Returns
    -------
    AssertResult
        Outcome discriminant plus ``fact_id`` (or ``action_id`` on
        ``pending_approval``).

    Raises
    ------
    ValueError
        When *predicate* is not registered, or *conf* is outside [0, 1], or
        *object_kind* is not ``'literal'`` or ``'entity'``, or *evidence* is
        malformed, or *approval_action_id* does not identify an approved action
        for exactly this triple.
    """
    # --- Input validation (cheap; runs before any DB access) ---
    if object_kind not in ("literal", "entity"):
        raise ValueError(f"Invalid object_kind {object_kind!r}: must be 'literal' or 'entity'.")
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"conf must be in [0.0, 1.0]; got {conf!r}.")
    if evidence is not None:
        _validate_typed_evidence(evidence)

    # Normalise legacy underscore predicate aliases to canonical hyphenated names.
    predicate = _PREDICATE_ALIAS_MAP.get(predicate, predicate)

    # Default observed_at to assertion time when the caller did not supply it.
    # An explicit value (e.g. a backdated import) is honoured verbatim.
    resolved_observed_at = observed_at if observed_at is not None else datetime.now(UTC)

    kwargs: dict[str, Any] = dict(
        subject=subject,
        predicate=predicate,
        object=object,
        object_kind=object_kind,
        src=src,
        conf=conf,
        last_seen=last_seen,
        observed_at=resolved_observed_at,
        weight=weight,
        verified=verified,
        primary=primary,
        why=why,
        evidence=evidence,
        approval_action_id=approval_action_id,
    )

    if conn is not None:
        # Caller owns the connection (and likely an open transaction).
        # Do NOT open another transaction — that would deadlock.
        return await _assert_on_conn(conn, pool, wrap_transaction=False, **kwargs)

    # No caller-supplied connection: acquire one from the pool and manage the
    # transaction ourselves.
    async with pool.acquire() as acquired_conn:
        return await _assert_on_conn(acquired_conn, pool, wrap_transaction=True, **kwargs)


# ---------------------------------------------------------------------------
# Deterministic ingress channel-fact hook (entity-v3, bu-hvrt1)
# ---------------------------------------------------------------------------
#
# When the Switchboard routes a message from an unresolved sender, a temporary
# entity is minted (in public.entities/public.contacts) but the sender's channel
# identifier is NOT yet recorded in relationship.entity_facts. That triple is the
# dedup key resolve_contact_by_channel() reads on the *next* message, so without
# it every subsequent message from the same new sender would mint another
# duplicate entity.
#
# The entity-v3 switchboard-identity invariant forbids the Switchboard from
# writing entity_facts itself: fact assertion belongs to the relationship domain.
# This hook is that assertion — owned by the relationship butler (which owns the
# entity_facts writer and the channel→predicate mapping), invoked DETERMINISTICALLY
# from the routing pipeline (code, not the routed LLM session). Running in code
# guarantees the dedup triple lands on exactly the path that minted the temp
# entity, so the dedup invariant cannot regress on an LLM no-op.


async def assert_sender_channel_fact(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    channel_type: str,
    channel_value: str,
    *,
    conn: asyncpg.Connection | None = None,
) -> AssertResult | None:
    """Deterministically record an unresolved sender's channel triple.

    Maps *channel_type* to its contact predicate and asserts
    ``(entity_id, predicate, channel_value)`` as a ``primary`` literal fact via
    the central writer. This is the LLM-independent replacement for the channel
    triple that ``create_temp_contact`` used to write inline; moving it here keeps
    Switchboard ingress free of ``entity_facts`` writes (entity-v3
    switchboard-identity invariant) while preserving the existing-sender dedup
    key ``resolve_contact_by_channel`` depends on.

    Returns the :class:`AssertResult` on success, or ``None`` when the channel
    type has no predicate mapping (nothing to assert) — never raises: an
    assertion failure is logged and swallowed so it cannot break routing.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the relationship schema.
    entity_id:
        UUID of the (temporary) entity the channel identifier belongs to.
    channel_type:
        Source channel type (e.g. ``"telegram"``, ``"email"``).
    channel_value:
        The raw sender identifier observed on the channel.
    conn:
        Optional open connection (pass when inside an existing transaction).
    """
    # Resolve the predicate from the shared channel-type mapping at the identity
    # resolution layer so reads (resolve_contact_by_channel) and this write stay
    # keyed identically.
    from butlers.identity import (
        _CHANNEL_TYPE_TO_PREDICATE,
        channel_value_for_storage,
    )

    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(channel_type)
    if predicate is None:
        logger.debug(
            "assert_sender_channel_fact: no predicate mapping for channel_type=%r; "
            "skipping channel-triple assertion for entity %s",
            channel_type,
            entity_id,
        )
        return None

    # Store telegram handles in the canonical ``telegram:<bare>`` form. The
    # delivery read path (daemon._resolve_entity_channel_identifier) filters
    # has-handle objects on ``LIKE 'telegram:%'``, so an unprefixed object is
    # NON-deliverable via notify(entity_id). Normalising here — keyed identically
    # to the read fallback (resolve_contact_by_channel's _telegram_prefixed_value)
    # — keeps recognition, delivery, and ingress dedup on ONE stored format and
    # removes the need for the read-side prefix tolerance bridge (PR #2465).
    stored_value = channel_value_for_storage(channel_type, channel_value)

    try:
        return await relationship_assert_fact(
            pool,
            entity_id,
            predicate,
            stored_value,
            src="identity",
            object_kind="literal",
            primary=True,
            conn=conn,
        )
    except Exception:  # noqa: BLE001 — never let a fact write break routing
        logger.warning(
            "assert_sender_channel_fact: failed to assert channel triple for entity %s "
            "(channel_type=%r, value=%r) — sender dedup key not written",
            entity_id,
            channel_type,
            channel_value,
            exc_info=True,
        )
        return None


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
        fact_id = await c.fetchval(
            """
            UPDATE relationship.entity_facts
            SET validity   = 'retracted',
                updated_at = now()
            WHERE subject   = $1
              AND predicate = $2
              AND object    = $3
              AND validity  = 'active'
            RETURNING id
            """,
            subject,
            predicate,
            ci_value,
        )
        if fact_id is None:
            return None

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


# ---------------------------------------------------------------------------
# prefers-channel — single-valued preferred-outbound-channel predicate
# ---------------------------------------------------------------------------
#
# entity-keyed-preferred-channel (group 1, bu-ctsgh). ``prefers-channel`` is an
# ``override``-kind, ``object_kind='literal'``, ``cardinality='single'`` predicate
# (seeded by rel_022). It records the channel an entity prefers to be reached on.
# Unlike the generic central writer — which keys idempotency/supersession on
# ``(subject, predicate, object)`` — a single-valued predicate must supersede ANY
# prior active value for the subject when a *different* channel is asserted. The
# dedicated path below enforces that, plus write-time reachability validation and
# retract-on-clear.

#: Canonical predicate name (kebab-case, matches the rel_022 registry seed).
PREFERS_CHANNEL_PREDICATE = "prefers-channel"

#: Channel name → the ``has-*`` predicate that proves reachability on that
#: channel family. Channels not listed here have no clean per-channel proof and
#: are validated via the degraded "any handle" path (see OQ2 resolution below).
#:
#: OQ2 resolution (design.md D2 / Open Question OQ2) — DEGRADE within the handle
#: family. ``has-handle`` objects are channel-prefixed ONLY for telegram
#: (``telegram:<id>``); discord/linkedin/twitter/"other" handles are stored
#: verbatim with no channel prefix (``_ef_channel_helpers.encode_handle_object``
#: prefixes telegram only, and rel_019's own docstring states telegram rows
#: cannot be reliably distinguished from other handles inside entity_facts).
#: Therefore the prefix taxonomy is reliable ONLY for telegram. We validate
#: per-channel where reliable (email→has-email, phone/sms→has-phone,
#: telegram→has-handle:telegram:) and DEGRADE every other handle channel
#: (discord, linkedin, twitter, …) to "subject has ANY active has-handle fact",
#: exactly as design.md sanctions when the taxonomy lacks a clean prefix.
_CHANNEL_REACHABILITY: dict[str, tuple[str, str | None]] = {
    # channel name : (has-* predicate, required object prefix or None)
    "email": ("has-email", None),
    "phone": ("has-phone", None),
    "sms": ("has-phone", None),
    "telegram": ("has-handle", "telegram:"),
}

#: Handle channels with no reliable channel prefix degrade to "any has-handle".
#: This is the family proof predicate used for those channels.
_DEGRADED_HANDLE_PREDICATE = "has-handle"


async def _entity_has_reachability_fact(
    conn: asyncpg.Connection,
    subject: uuid.UUID,
    channel: str,
) -> bool:
    """Return True if *subject* has an active contact fact proving reachability on *channel*.

    See ``_CHANNEL_REACHABILITY`` and the OQ2 resolution note for the per-channel
    vs. degraded-handle distinction.
    """
    mapping = _CHANNEL_REACHABILITY.get(channel)
    if mapping is not None:
        predicate, required_prefix = mapping
        if required_prefix is None:
            return bool(
                await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM relationship.entity_facts
                        WHERE subject     = $1
                          AND predicate   = $2
                          AND validity    = 'active'
                          AND object_kind = 'literal'
                    )
                    """,
                    subject,
                    predicate,
                )
            )
        return bool(
            await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM relationship.entity_facts
                    WHERE subject     = $1
                      AND predicate   = $2
                      AND validity    = 'active'
                      AND object_kind = 'literal'
                      AND object LIKE $3 || '%'
                )
                """,
                subject,
                predicate,
                required_prefix,
            )
        )

    # Degraded handle path: any active has-handle fact proves the entity is
    # reachable on *some* handle channel, which is the best the taxonomy allows.
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM relationship.entity_facts
                WHERE subject     = $1
                  AND predicate   = $2
                  AND validity    = 'active'
                  AND object_kind = 'literal'
            )
            """,
            subject,
            _DEGRADED_HANDLE_PREDICATE,
        )
    )


async def _supersede_active_prefers_channel(
    conn: asyncpg.Connection,
    subject: uuid.UUID,
    *,
    validity: str,
) -> int:
    """Mark every active ``prefers-channel`` row for *subject* with *validity*.

    *validity* is ``'superseded'`` (new preference replaces old) or ``'retracted'``
    (preference cleared). Returns the number of rows transitioned. Single-valued:
    after a successful assert exactly one active row remains; after a clear, none.
    """
    status = await conn.execute(
        """
        UPDATE relationship.entity_facts
        SET validity   = $3,
            updated_at = now()
        WHERE subject   = $1
          AND predicate = $2
          AND validity  = 'active'
        """,
        subject,
        PREFERS_CHANNEL_PREDICATE,
        validity,
    )
    # asyncpg execute() returns e.g. "UPDATE 2"; parse the affected-row count.
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):  # pragma: no cover - defensive
        return 0


async def assert_prefers_channel(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    channel: str,
    *,
    src: str = "relationship",
    verified: bool = True,
    conf: float = 1.0,
    conn: asyncpg.Connection | None = None,
) -> AssertResult:
    """Assert *subject*'s preferred outbound *channel* (single-valued supersession).

    Contract (entity-keyed-preferred-channel, relationship-facts spec):

    1. **Reachability validation** — the assertion is rejected with a
       :class:`ValueError` unless *subject* already has an active contact fact
       for *channel* (``has-email`` / ``has-phone`` / ``has-handle`` of the
       matching family). See the OQ2 resolution on ``_CHANNEL_REACHABILITY`` for
       the per-channel vs. degraded-handle behavior.
    2. **Single-valued supersession** — any prior active ``prefers-channel`` row
       for *subject* (regardless of its object) is marked
       ``validity='superseded'`` before the new active row is inserted, so
       exactly one active ``prefers-channel`` triple remains.
    3. **Idempotency** — re-asserting the same channel returns
       :attr:`AssertOutcome.unchanged` without writing.

    Owner carve-out is intentionally NOT applied here: the preferred channel is
    an owner-facing dashboard control (owner setting their own / a contact's
    preference), not an ingestion-driven mutation that needs approval. The
    generic owner-approval path is reserved for ``has-*`` channel-identity writes.

    Parameters mirror :func:`relationship_assert_fact`; *channel* is the bare
    channel name (``"telegram"``, ``"email"``, ``"discord"``, …) stored verbatim
    as the triple ``object``.

    Raises
    ------
    ValueError
        When *channel* is empty, or *subject* has no contact fact proving
        reachability on *channel*.
    """
    if not channel or not channel.strip():
        raise ValueError("prefers-channel requires a non-empty channel name.")
    channel = channel.strip()

    async def _do(c: asyncpg.Connection) -> AssertResult:
        # Predicate must be registered (defensive — rel_022 seeds it).
        await _validate_predicate(c, PREFERS_CHANNEL_PREDICATE)

        # 1. Reachability validation — reject a preference the entity can't honor.
        if not await _entity_has_reachability_fact(c, subject, channel):
            raise ValueError(
                f"Cannot prefer channel {channel!r} for entity {subject}: the entity "
                f"has no active contact fact for that channel "
                f"(expected a has-email / has-phone / has-handle of the {channel!r} "
                f"family). Add the channel identity first, then set the preference."
            )

        # 2. Idempotency — same active channel already set → no write.
        existing = await c.fetchrow(
            """
            SELECT id, src, conf, verified
            FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = $2
              AND object    = $3
              AND validity  = 'active'
            """,
            subject,
            PREFERS_CHANNEL_PREDICATE,
            channel,
        )
        if existing is not None and (
            existing["src"] == src
            and existing["conf"] == conf
            and bool(existing["verified"]) == verified
        ):
            return AssertResult(outcome=AssertOutcome.unchanged, fact_id=existing["id"])

        # 3. Single-valued supersession — retire ALL prior active values (any
        #    object), then insert the new active row.
        superseded = await _supersede_active_prefers_channel(c, subject, validity="superseded")
        new_id = await c.fetchval(
            """
            INSERT INTO relationship.entity_facts (
                id, subject, predicate, object, object_kind,
                src, conf, verified, validity, created_at, updated_at
            )
            VALUES (
                gen_random_uuid(), $1, $2, $3, 'literal',
                $4, $5, $6, 'active', now(), now()
            )
            RETURNING id
            """,
            subject,
            PREFERS_CHANNEL_PREDICATE,
            channel,
            src,
            conf,
            verified,
        )
        outcome = AssertOutcome.superseded if superseded else AssertOutcome.inserted
        return AssertResult(outcome=outcome, fact_id=new_id)

    if conn is not None:
        return await _do(conn)
    async with pool.acquire() as acquired_conn:
        async with acquired_conn.transaction():
            return await _do(acquired_conn)


async def retract_prefers_channel(
    pool: asyncpg.Pool,
    subject: uuid.UUID,
    *,
    conn: asyncpg.Connection | None = None,
) -> int:
    """Clear *subject*'s preferred channel by retracting any active row.

    Marks every active ``prefers-channel`` row for *subject*
    ``validity='retracted'``. Returns the number of rows retracted (0 when no
    preference was set). Idempotent: clearing an already-cleared preference is a
    no-op returning 0.
    """

    async def _do(c: asyncpg.Connection) -> int:
        return await _supersede_active_prefers_channel(c, subject, validity="retracted")

    if conn is not None:
        return await _do(conn)
    async with pool.acquire() as acquired_conn:
        async with acquired_conn.transaction():
            return await _do(acquired_conn)
