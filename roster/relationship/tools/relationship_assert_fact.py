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

6. **Effective time (relationship-fact-effective-time)** — six optional
   arguments carry when the relationship held (see
   :mod:`butlers.tools.relationship.fact_temporal`). Omitted and ``None`` are
   the same input. With none of them the call is *ordinary*: it targets the
   default occurrence and preserves whatever packet that row already stores. A
   non-null bound, precision or period id is an *explicit* packet for that
   occurrence; ``corrects_fact_id`` selects a compare-and-swap *correction* of
   one exact active row. Every version the writer inserts copies its packet
   unchanged unless the call is an explicit correction, and ``validity`` never
   derives from it. This is the **transition writer**: its targetless
   ``ON CONFLICT DO NOTHING`` plus locked re-read works with the legacy
   ``uq_ef_spo_active`` index, with both indexes, and with only the occurrence
   index; and while the legacy index exists every temporal intent fails
   ``temporal_cutover_pending`` before approval parking or any write.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import asyncpg

from butlers.core import entity_graph_edges
from butlers.core.approvals_hooks import park_pending_action
from butlers.core.custody_bindings import native_channel_mutation
from butlers.core.tool_call_capture import (
    get_current_approval_push_runtime,
    get_current_runtime_session_id,
)
from butlers.modules.memory.content_authority import resolve_content_authority
from butlers.modules.memory.knowledge_gaps import close_matching_gaps
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
from butlers.tools.relationship.fact_temporal import (
    CORRECTION_REQUIRED,
    CORRECTION_STALE,
    INVALID,
    MUTATOR_UNSUPPORTED,
    OCCURRENCE_AMBIGUOUS,
    ORDINARY,
    PACKET_COLUMNS,
    UNKNOWN,
    RequestMode,
    TemporalError,
    TemporalPacket,
    TemporalRequest,
    normalize_request,
    parked_request,
    require_temporal_admission,
    temporal_bearing_sql,
    wire_values,
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
    Reason (bu-g27ib): :func:`park_pending_action` writes its action and
    delivery intent in an independent transaction acquired from *pool*. If an
    EARLIER fact in a batch parks (committing those rows) and
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
    temporal_mode: RequestMode,
    temporal_base_fact_id: uuid.UUID | None,
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
    the single atomic admission point for PENDING inserts. It acquires its own
    connection from a real pool (bu-g27ib). The dedup read
    has no transactional dependency on the caller's in-flight entity_facts
    write, so reading it from *pool* instead of the caller's ``conn`` is safe.

    *src* and *observed_at* are recorded in ``relationship.fact_approval_context``
    rather than in *tool_args*. Approval dispatch replays ``tool_args`` by
    splatting it into the MCP tool, so every key there must also be a tool
    parameter -- and ``src`` selects the owner carve-out's trusted-source
    exemption, which is exactly the value an LLM session must never be able to
    supply (bu-vj46x). Keeping it server-side makes the replay read the source
    from a row only this function ever wrote.

    *temporal_mode* and *temporal_base_fact_id* freeze how the parked temporal
    packet was resolved (and against which active version) so approved replay
    can refuse a stale one. A pending match must share them: the same canonical
    packet resolved against a different base is a different question.
    """
    if dedup_match is not None:
        existing = await pool.fetchval(
            """
            SELECT pa.id FROM pending_actions pa
              JOIN relationship.fact_approval_context ctx ON ctx.action_id = pa.id
             WHERE pa.tool_name = $1
               AND pa.status   = 'pending'
               AND pa.tool_args @> $2::jsonb
               AND ctx.temporal_request_mode IS NOT DISTINCT FROM $3
               AND ctx.temporal_base_fact_id IS NOT DISTINCT FROM $4
             ORDER BY pa.requested_at ASC
             LIMIT 1
            """,
            tool_name,
            dedup_match,
            temporal_mode.value,
            temporal_base_fact_id,
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

    await _record_approval_context(
        pool,
        action_id=action_id,
        src=src,
        observed_at=observed_at,
        temporal_mode=temporal_mode,
        temporal_base_fact_id=temporal_base_fact_id,
    )

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
    temporal_mode: RequestMode,
    temporal_base_fact_id: uuid.UUID | None,
) -> None:
    """Record the server-written provenance of one parked fact write.

    Written before :func:`park_pending_action` so the context is already durable
    by the time the owner can possibly approve the action. Idempotent: a retry
    of the same action id keeps the first recorded provenance.
    """
    await pool.execute(
        """
        INSERT INTO relationship.fact_approval_context (
            action_id, src, observed_at, temporal_request_mode, temporal_base_fact_id
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (action_id) DO NOTHING
        """,
        action_id,
        src,
        observed_at,
        temporal_mode.value,
        temporal_base_fact_id,
    )


@dataclass(frozen=True, slots=True)
class _FrozenResolution:
    """The active version an approved ordinary request was resolved against.

    ``None`` base means the default occurrence was empty when parked, so replay
    may only create unknown there (or find the identical unknown row).
    """

    base_fact_id: uuid.UUID | None


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
    # The parked canonical temporal request, its exact wire form, and the
    # frozen resolution replay must still observe (None for a pre-temporal
    # action, which keeps plain ordinary semantics).
    temporal: TemporalRequest
    temporal_wire: dict[str, str | None]
    frozen: _FrozenResolution | None


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
               ctx.src AS ctx_src, ctx.observed_at AS ctx_observed_at,
               ctx.temporal_request_mode, ctx.temporal_base_fact_id
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

    mode = row["temporal_request_mode"]
    temporal, temporal_wire = parked_request(stored_args, mode)
    return _ApprovedAction(
        action_id=action_id,
        src=stored_src,
        observed_at=row["ctx_observed_at"],
        evidence=normalize_evidence(_as_json_value(row["evidence"])),
        session_id=coerce_session_id(row["session_id"]),
        temporal=temporal,
        temporal_wire=temporal_wire,
        frozen=None if mode is None else _FrozenResolution(row["temporal_base_fact_id"]),
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


async def _lock_fact_entities(
    conn: asyncpg.Connection,
    subject: uuid.UUID,
    object: str | None = None,
    object_kind: str = "literal",
) -> None:
    """Lock the entity rows a fact write references, before any fact-row lock.

    Lock order (bu-ab0zys): every ``relationship.entity_facts`` insert or
    replacement takes ``public.entities`` rows first, in ascending id order,
    and only then fact rows -- the order ``merge_entity_pair`` and
    ``contact_merge`` already use (entities ``FOR UPDATE``, then facts). Without
    it a replacement insert took the fact row first and the entity second (the
    FK's implicit ``FOR KEY SHARE``), so a concurrent merge could deadlock
    against it.

    ``FOR KEY SHARE`` is the FK's own lock: it waits for a merge holding the
    rows ``FOR UPDATE`` and blocks a merge from starting, but never conflicts
    with another writer. The subject and, for ``object_kind='entity'``, the
    object entity are locked; a missing entity row simply locks nothing.

    Locking is all this does. A write that waits for a merge still proceeds
    against the tombstoned source afterwards (``metadata.merged_into``); whether
    the writer should refuse or follow the merge is bu-gm93xc.

    Retract/verify-only paths write no FK column and take no entity lock, so
    they cannot join the cycle. Must run inside the caller's transaction.
    """
    ids = {subject}
    if object_kind == "entity" and object is not None:
        try:
            ids.add(uuid.UUID(object))
        except ValueError:
            pass  # the insert's own validation reports a malformed object id
    await _lock_fact_entities_batch(conn, ids)


async def _lock_fact_entities_batch(conn: asyncpg.Connection, ids: Iterable[uuid.UUID]) -> None:
    """``FOR KEY SHARE`` every entity row in *ids* at once, in ascending id order.

    One writer call locks only its own subject and object, so a caller that
    makes several writer calls in ONE transaction (``promote_entity``'s
    ``initial_facts`` loop) would otherwise lock their entities in body order
    across calls, which a concurrent merge of two of them can deadlock against
    (bu-7s41je). Such a caller takes every entity it will write against here,
    once, before its first entity or fact lock; each per-call
    :func:`_lock_fact_entities` then re-takes rows it already holds.
    """
    ordered = sorted(set(ids))
    if not ordered:
        return
    await conn.execute(
        """
        SELECT id FROM public.entities
        WHERE id = ANY($1::uuid[])
        ORDER BY id
        FOR KEY SHARE
        """,
        ordered,
    )


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
    temporal: TemporalPacket = UNKNOWN,
) -> uuid.UUID | None:
    """Insert a new ACTIVE row, returning its id, or ``None`` on conflict.

    Uses a TARGETLESS ``ON CONFLICT DO NOTHING`` so a concurrent writer that
    already holds the active slot is NEVER mutated in place — crucially,
    ``conf`` and ``observed_at`` on the existing active row are left untouched
    (spec: conf is immutable, superseded rows keep their observed_at). A
    ``None`` return signals the caller to re-read and route the collision
    through normal supersession.

    Targetless on purpose (relationship-fact-effective-time): it names no
    inference index, so the same statement is valid while the legacy
    ``uq_ef_spo_active`` exists, while both it and the occurrence index exist,
    and after a cutover leaves only the occurrence index.

    *packet* supplies the assertion provenance stamped onto the row
    (``assert_origin``/``assert_session_id``/``assert_action_id``): how this row
    came to be active, which runtime session authored it, and which approved
    action authorised it. It is written with the row, not after it, so a fact can
    never exist with its provenance missing. *temporal* is the row's effective
    packet, likewise immutable once written.
    """
    return await conn.fetchval(
        f"""
        INSERT INTO relationship.entity_facts (
            id, subject, predicate, object, object_kind,
            src, conf, last_seen, observed_at, weight, verified, "primary",
            validity, created_at, updated_at,
            assert_origin, assert_session_id, assert_action_id,
            {PACKET_COLUMNS}
        )
        VALUES (
            gen_random_uuid(), $1, $2, $3, $4,
            $5, $6, $7, $8, $9, $10, $11,
            'active', now(), now(),
            $12, $13, $14,
            $15, $16, $17, $18, $19
        )
        ON CONFLICT DO NOTHING
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
        *temporal.sql_args(),
    )


#: The active row of one effective occurrence (NULL period = default occurrence).
_ACTIVE_OCCURRENCE_SQL = f"""
    SELECT id, src, conf, verified, last_seen, {PACKET_COLUMNS}
    FROM relationship.entity_facts
    WHERE subject   = $1
      AND predicate = $2
      AND object    = $3
      AND validity  = 'active'
      AND effective_period_id IS NOT DISTINCT FROM $4
"""


def _same_assertion_fields(
    row: asyncpg.Record,
    *,
    src: str,
    conf: float,
    verified: bool,
    last_seen: datetime | None,
) -> bool:
    """The non-temporal provenance a re-assertion is compared on."""
    return (
        row["src"] == src
        and row["conf"] == conf
        and bool(row["verified"]) == verified
        and row["last_seen"] == last_seen
    )


async def _active_occurrence(
    conn: asyncpg.Connection,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    period_id: uuid.UUID | None,
) -> asyncpg.Record | None:
    return await conn.fetchrow(_ACTIVE_OCCURRENCE_SQL, subject, predicate, object, period_id)


async def _supersede_and_replace(
    conn: asyncpg.Connection,
    *,
    old_id: uuid.UUID,
    temporal: TemporalPacket,
    write: dict[str, Any],
) -> uuid.UUID | None:
    """Supersede *old_id* and insert its replacement carrying *temporal*.

    Returns the replacement id, ``None`` when *old_id* was no longer active
    (another writer superseded it first), or raises ``_SlotTaken`` when a
    competing writer filled the occurrence between our UPDATE and INSERT.
    """
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
        return None

    if write["object_kind"] == "entity":
        # RFC 0031 (bu-8cdl1.8 Slice 2): the superseded row is no longer
        # current -- its projected edge must go with it in the same
        # transaction, or a supersession would leave two live edges for the
        # same conceptual relationship.
        await entity_graph_edges.delete_entity_graph_edge(
            conn,
            source_schema="relationship",
            source_table="entity_facts",
            source_id=old_id,
        )

    new_id = await _insert_active_fact(conn, **write, temporal=temporal)
    if new_id is None:
        raise _SlotTaken
    await carry_evidence_forward(conn, from_fact_id=old_id, to_fact_id=new_id)
    await persist_evidence(conn, fact_id=new_id, packet=write["packet"])
    await _project(conn, fact_id=new_id, write=write)
    return new_id


class _SlotTaken(Exception):
    """A competing writer filled the occurrence slot we just vacated."""


async def _project(conn: asyncpg.Connection, *, fact_id: uuid.UUID, write: dict[str, Any]) -> None:
    if write["object_kind"] == "entity":
        # RFC 0031 (bu-8cdl1.8 Slice 2): project the entity-to-entity edge
        # in the same transaction as the fact write -- a projection
        # failure here fails this whole write, so the graph can never
        # silently diverge from relationship.entity_facts.
        await entity_graph_edges.project_entity_graph_edge(
            conn,
            source_schema="relationship",
            source_table="entity_facts",
            source_id=fact_id,
            subject_entity_id=write["subject"],
            predicate=write["predicate"],
            object_entity_id=uuid.UUID(write["object"]),
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
    temporal: TemporalRequest = ORDINARY,
    frozen: _FrozenResolution | None = None,
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
    existing active row. When a unique active-slot index rejects our insert
    (another writer holds the slot), we re-read and retry, so the collision is
    resolved by normal supersession — the prior active row is marked
    ``superseded`` (keeping its own ``conf``/``observed_at``) before a fresh
    active row is inserted.

    Effective time: idempotency and supersession are scoped to one effective
    occurrence. An *ordinary* request (no temporal intent) targets the default
    occurrence and resolves its packet from the stored row, so a known packet
    is compared as-is and copied into any provenance replacement, never cleared.
    An *explicit* request whose packet differs from the occupied occurrence is
    refused (``temporal_correction_required``) rather than reinterpreted. A
    *correction* is delegated to :func:`_correct_fact`. *frozen* is set for an
    approved ordinary replay and must still hold (see :class:`_FrozenResolution`).
    """
    # Entity rows before any fact row (see _lock_fact_entities).
    await _lock_fact_entities(conn, subject, object, object_kind)

    write: dict[str, Any] = dict(
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
    if temporal.mode is RequestMode.correction:
        return await _correct_fact(conn, request=temporal, write=write)

    period_id = temporal.packet.period_id
    for _ in range(_MAX_UPSERT_ATTEMPTS):
        # 1. Read the active row of this occurrence, if any.
        existing = await _active_occurrence(
            conn, subject=subject, predicate=predicate, object=object, period_id=period_id
        )
        if frozen is not None and temporal.mode is RequestMode.ordinary:
            _verify_frozen_ordinary(existing, frozen)

        if existing is not None:
            old_id: uuid.UUID = existing["id"]
            stored = TemporalPacket.from_row(existing)
            if temporal.mode is RequestMode.explicit and stored != temporal.packet:
                raise TemporalError(
                    CORRECTION_REQUIRED,
                    f"occurrence already holds a different effective packet on fact {old_id}; "
                    "pass corrects_fact_id to replace it, or a new effective_period_id for "
                    "a repeated period.",
                )

            # 2. Compare provenance fields to detect supersession.
            if _same_assertion_fields(
                existing, src=src, conf=conf, verified=verified, last_seen=last_seen
            ):
                # Idempotent on the FACT: same identity + same provenance, so no
                # new row. The evidence packet is still appended — a second
                # source citing a reason for an already-known fact is new
                # knowledge even when the triple is not. The ledger's unique
                # (fact_id, kind, ref) index absorbs a literal repeat.
                await persist_evidence(conn, fact_id=old_id, packet=packet)
                return AssertResult(outcome=AssertOutcome.unchanged, fact_id=old_id)

            # 3. Supersession: retract the specific old row we just read, then
            #    insert the replacement carrying the STORED effective packet.
            #    The UPDATE is guarded on the row id AND validity='active' so a
            #    racing supersession of the same row only succeeds once; if we
            #    lose that race we re-read and retry.
            try:
                new_id = await _supersede_and_replace(
                    conn, old_id=old_id, temporal=stored, write=write
                )
            except _SlotTaken:
                # A competing writer slipped a new active row into the slot
                # between our UPDATE and our INSERT. We have already correctly
                # superseded the row we observed; loop to supersede theirs too
                # rather than overwriting it in place.
                continue
            if new_id is None:
                continue
            return AssertResult(outcome=AssertOutcome.superseded, fact_id=new_id)

        # 4. No existing active row → insert. DO NOTHING (never DO UPDATE) so a
        #    concurrent writer's active row is never mutated in place; on conflict
        #    we re-read and route the collision through supersession above.
        #    An ordinary request creates the unknown default occurrence.
        new_id = await _insert_active_fact(conn, **write, temporal=temporal.packet)
        if new_id is None:
            # Lost the insert race: an active row now exists. Re-read so we either
            # report `unchanged` (identical provenance) or supersede it.
            continue
        await persist_evidence(conn, fact_id=new_id, packet=packet)
        await _project(conn, fact_id=new_id, write=write)
        return AssertResult(outcome=AssertOutcome.inserted, fact_id=new_id)

    raise RuntimeError(
        "relationship_assert_fact: exhausted supersession retries under contention "
        f"for (subject={subject}, predicate={predicate}, object={object})."
    )


def _verify_frozen_ordinary(existing: asyncpg.Record | None, frozen: _FrozenResolution) -> None:
    """Refuse an approved ordinary replay whose resolved occurrence has moved on."""
    if frozen.base_fact_id is not None:
        if existing is None or existing["id"] != frozen.base_fact_id:
            raise TemporalError(
                CORRECTION_STALE,
                f"the approved write preserved fact {frozen.base_fact_id}, which is no "
                "longer the active default occurrence.",
            )
    elif existing is not None and TemporalPacket.from_row(existing).is_temporal_bearing:
        raise TemporalError(
            CORRECTION_STALE,
            "the approved write expected an empty or unknown default occurrence, but "
            f"fact {existing['id']} now holds a known effective packet.",
        )


async def _correct_fact(
    conn: asyncpg.Connection,
    *,
    request: TemporalRequest,
    write: dict[str, Any],
) -> AssertResult:
    """Compare-and-swap one exact active version to a complete desired packet.

    The named row is locked ``FOR UPDATE``. If it is still active it is
    superseded (keeping its own packet) and one replacement with the desired
    packet, the same occurrence id and the carried-forward evidence becomes
    active. Of two concurrent corrections naming the same row, the one that
    locks it while active and commits first wins; the other re-reads it as
    superseded and fails stale before its first write. An exact retry of an
    already-applied correction returns the matching successor as unchanged.
    """
    target_id = request.corrects_fact_id
    target = await conn.fetchrow(
        f"""
        SELECT id, subject, predicate, object, validity,
               src, conf, verified, last_seen, {PACKET_COLUMNS}
        FROM relationship.entity_facts
        WHERE id = $1
        FOR UPDATE
        """,
        target_id,
    )
    if target is None or (target["subject"], target["predicate"], target["object"]) != (
        write["subject"],
        write["predicate"],
        write["object"],
    ):
        raise TemporalError(
            INVALID,
            f"corrects_fact_id {target_id} does not name a fact of this subject, "
            "predicate and object.",
        )
    desired = _correction_packet(request, target)
    fields = {k: write[k] for k in ("src", "conf", "verified", "last_seen")}

    if target["validity"] == "active":
        if TemporalPacket.from_row(target) == desired and _same_assertion_fields(target, **fields):
            await persist_evidence(conn, fact_id=target_id, packet=write["packet"])
            return AssertResult(outcome=AssertOutcome.unchanged, fact_id=target_id)
        try:
            new_id = await _supersede_and_replace(
                conn, old_id=target_id, temporal=desired, write=write
            )
        except _SlotTaken:
            new_id = None
        if new_id is None:
            raise TemporalError(
                CORRECTION_STALE,
                f"fact {target_id} changed while it was being corrected; re-read and retry.",
            )
        return AssertResult(outcome=AssertOutcome.superseded, fact_id=new_id)

    if target["validity"] == "superseded":
        successor = await _active_occurrence(
            conn,
            subject=write["subject"],
            predicate=write["predicate"],
            object=write["object"],
            period_id=desired.period_id,
        )
        if (
            successor is not None
            and TemporalPacket.from_row(successor) == desired
            and _same_assertion_fields(successor, **fields)
        ):
            await persist_evidence(conn, fact_id=successor["id"], packet=write["packet"])
            return AssertResult(outcome=AssertOutcome.unchanged, fact_id=successor["id"])

    raise TemporalError(
        CORRECTION_STALE,
        f"fact {target_id} is no longer the active version of its occurrence; read the "
        "current active fact and correct that id instead.",
    )


def _correction_packet(request: TemporalRequest, target: asyncpg.Record) -> TemporalPacket:
    """The complete desired replacement: inherit, or match, the target's occurrence."""
    occurrence = target["effective_period_id"]
    supplied = request.packet.period_id
    if supplied is not None and supplied != occurrence:
        raise TemporalError(
            INVALID,
            "effective_period_id must match the corrected fact's occurrence; a different "
            "period is a repeated occurrence, not a correction.",
        )
    return request.packet.with_period(occurrence)


async def _resolve_for_parking(
    conn: asyncpg.Connection,
    request: TemporalRequest,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
) -> tuple[TemporalPacket, uuid.UUID | None]:
    """Resolve a request against current state before it is parked.

    Returns the exact packet the owner will review and the active version it
    was resolved against. An ordinary request over a known default occurrence
    parks that stored packet rather than an all-null wire shape. Requests that
    could never execute (a stale correction target, an explicit packet that
    conflicts with its occupied occurrence) are refused here, before parking.
    """
    if request.mode is RequestMode.correction:
        target = await conn.fetchrow(
            f"""
            SELECT id, subject, predicate, object, validity, {PACKET_COLUMNS}
            FROM relationship.entity_facts WHERE id = $1
            """,
            request.corrects_fact_id,
        )
        if target is None or (target["subject"], target["predicate"], target["object"]) != (
            subject,
            predicate,
            object,
        ):
            raise TemporalError(
                INVALID,
                f"corrects_fact_id {request.corrects_fact_id} does not name a fact of this "
                "subject, predicate and object.",
            )
        if target["validity"] != "active":
            raise TemporalError(
                CORRECTION_STALE, f"fact {request.corrects_fact_id} is no longer active."
            )
        return _correction_packet(request, target), target["id"]

    existing = await _active_occurrence(
        conn,
        subject=subject,
        predicate=predicate,
        object=object,
        period_id=request.packet.period_id,
    )
    if existing is None:
        return request.packet, None
    stored = TemporalPacket.from_row(existing)
    if request.mode is RequestMode.explicit and stored != request.packet:
        raise TemporalError(
            CORRECTION_REQUIRED,
            f"occurrence already holds a different effective packet on fact {existing['id']}; "
            "pass corrects_fact_id to replace it.",
        )
    return stored, existing["id"]


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
    temporal: TemporalRequest | None,
    temporal_wire: dict[str, str | None],
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
    temporal, temporal_wire:
        The normalized temporal request of a fresh call, or ``None`` for an
        approved replay, whose request is the parked canonical one; the replay's
        raw wire values (*temporal_wire*) must equal the parked form exactly.
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
        if temporal_wire != approved.temporal_wire:
            raise ValueError(
                f"approval_action_id {approval_action_id} was approved for a different "
                "effective-time packet; the write does not match what the owner approved."
            )
        temporal = approved.temporal
    assert temporal is not None  # a fresh call always arrives normalized

    if temporal.has_temporal_intent:
        # Transition fence: checked before parking or any write, so no temporal
        # approval can even be queued while the legacy index is live.
        await require_temporal_admission(conn)
        if predicate == PREFERS_CHANNEL_PREDICATE:
            raise TemporalError(
                MUTATOR_UNSUPPORTED,
                "prefers-channel is single-valued and has no period-aware policy yet; "
                "it accepts no effective-time arguments.",
            )

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
        # Dedup probe: any pending row whose tool_args JSONB contains the same
        # identity triple and canonical temporal packet is the same approval
        # request. Without this, a job like contact_info_reconciler that re-runs
        # every 30 min creates a new pending row each tick until the owner acts.
        parked = await _parked_arguments(
            conn,
            temporal,
            subject=subject,
            predicate=predicate,
            object=object,
            object_kind=object_kind,
            conf=conf,
            verified=verified,
            last_seen=last_seen,
            weight=weight,
            primary=primary,
        )

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
            parked.tool_args,
            summary,
            src=src,
            observed_at=observed_at,
            temporal_mode=temporal.mode,
            temporal_base_fact_id=parked.base_fact_id,
            dedup_match=parked.dedup_match,
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
        parked_gate = await _parked_arguments(
            conn,
            temporal,
            subject=subject,
            predicate=predicate,
            object=object,
            object_kind=object_kind,
            conf=conf,
            verified=verified,
            last_seen=last_seen,
            weight=weight,
            primary=primary,
        )
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
            parked_gate.tool_args,
            gate_summary,
            src=src,
            observed_at=observed_at,
            temporal_mode=temporal.mode,
            temporal_base_fact_id=parked_gate.base_fact_id,
            dedup_match=parked_gate.dedup_match,
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
        temporal=temporal,
        frozen=approved.frozen if approved is not None else None,
    )
    # Parking/approval notification above stays outside a newly acquired
    # custody transaction. Enter the current-binding writer BEFORE the actual
    # native entity/fact locks below; conn-aware callers must already own the
    # genuine outer writer if their transaction has started.
    subjects = [subject]
    if object_kind == "entity":
        subjects.append(uuid.UUID(object))
    async with native_channel_mutation(pool, conn, subjects) as current_conn:
        if wrap_transaction:
            async with current_conn.transaction():
                return await _write_fact_with_receipts(current_conn, kwargs)
        return await _write_fact_with_receipts(current_conn, kwargs)


@dataclass(frozen=True, slots=True)
class _ParkedArguments:
    tool_args: dict[str, Any]
    dedup_match: dict[str, Any]
    base_fact_id: uuid.UUID | None


async def _parked_arguments(
    conn: asyncpg.Connection,
    temporal: TemporalRequest,
    *,
    subject: uuid.UUID,
    predicate: str,
    object: str,
    object_kind: str,
    conf: float,
    verified: bool,
    last_seen: datetime | None,
    weight: int | None,
    primary: bool | None,
) -> _ParkedArguments:
    """Build the replayable ``tool_args`` (and dedup probe) of a parked write.

    All six temporal keys are always present in canonical form -- the packet
    resolved against current state, JSON null for unknown/default -- so an
    omitted and an explicit-null proposal park, deduplicate and replay
    identically.
    """
    resolved, base_fact_id = await _resolve_for_parking(
        conn, temporal, subject=subject, predicate=predicate, object=object
    )
    identity: dict[str, Any] = {
        "subject": str(subject),
        "predicate": predicate,
        "object": object,
        "object_kind": object_kind,
        **temporal.tool_args(resolved),
    }
    tool_args: dict[str, Any] = {**identity, "conf": conf, "verified": verified}
    if last_seen is not None:
        tool_args["last_seen"] = last_seen.isoformat()
    if weight is not None:
        tool_args["weight"] = weight
    if primary is not None:
        tool_args["primary"] = primary
    return _ParkedArguments(tool_args=tool_args, dedup_match=identity, base_fact_id=base_fact_id)


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
    if result.outcome is not AssertOutcome.unchanged:
        await _answer_knowledge_gaps(conn, kwargs, result)
    return result


async def _answer_knowledge_gaps(
    conn: asyncpg.Connection,
    kwargs: dict[str, Any],
    result: AssertResult,
) -> None:
    """Move an owner knowledge gap this write answers to ``answerable`` (bu-q7vx1q.9).

    Same connection and transaction as the fact, so the gap never reads answered for a
    write that rolled back.  The authority is derived from the session's routing context
    here, never taken from the caller.
    """

    async def value() -> str | None:
        if kwargs["object_kind"] != "entity":
            return kwargs["object"]
        return await conn.fetchval(
            "SELECT canonical_name FROM public.entities WHERE id = $1::uuid", kwargs["object"]
        )

    await close_matching_gaps(
        conn,
        entity_id=kwargs["subject"],
        predicate=kwargs["predicate"],
        ref=f"entity_fact:{result.fact_id}",
        value=value,
        authority=lambda: resolve_content_authority(conn),
    )


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
    effective_period_id: uuid.UUID | str | None = None,
    effective_from: datetime | str | None = None,
    effective_from_precision: str | None = None,
    effective_to: datetime | str | None = None,
    effective_to_precision: str | None = None,
    corrects_fact_id: uuid.UUID | str | None = None,
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
    effective_period_id, effective_from, effective_from_precision, effective_to, \
effective_to_precision, corrects_fact_id:
        Optional effective-time packet (relationship-fact-effective-time; see
        :mod:`butlers.tools.relationship.fact_temporal`). ``None`` and omission
        are identical. All ``None`` is an ordinary assertion that preserves the
        default occurrence's stored packet. ``instant`` bounds need an explicit
        offset; ``day``/``month``/``year`` bounds are civil ``YYYY-MM-DD`` /
        ``YYYY-MM`` / ``YYYY`` values normalized to a half-open UTC interval.
        ``corrects_fact_id`` replaces exactly that active version with the
        complete supplied packet (all ``None`` = unknown). While the legacy
        ``uq_ef_spo_active`` index exists, any of them non-null fails
        ``temporal_cutover_pending`` before parking or writing.

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
        for exactly this triple and effective-time packet.
    TemporalError
        (a ``ValueError``) with a stable ``code`` for a malformed packet
        (``temporal_invalid``), the transition fence
        (``temporal_cutover_pending``), a conflicting explicit packet
        (``temporal_correction_required``) or a stale correction/replay
        (``temporal_correction_stale``).
    """
    # --- Input validation (cheap; runs before any DB access) ---
    if object_kind not in ("literal", "entity"):
        raise ValueError(f"Invalid object_kind {object_kind!r}: must be 'literal' or 'entity'.")
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"conf must be in [0.0, 1.0]; got {conf!r}.")
    if evidence is not None:
        _validate_typed_evidence(evidence)
    temporal_args: dict[str, Any] = dict(
        effective_period_id=effective_period_id,
        effective_from=effective_from,
        effective_from_precision=effective_from_precision,
        effective_to=effective_to,
        effective_to_precision=effective_to_precision,
        corrects_fact_id=corrects_fact_id,
    )
    # An approved replay carries the PARKED canonical packet, which is compared
    # verbatim against the approval rather than re-normalized as fresh input.
    temporal = None if approval_action_id is not None else normalize_request(**temporal_args)

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
        temporal=temporal,
        temporal_wire=wire_values(**temporal_args),
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
        canonical_identity_channel_type,
        channel_value_for_storage,
    )

    canonical_channel = canonical_identity_channel_type(channel_type)
    predicate = _CHANNEL_TYPE_TO_PREDICATE.get(canonical_channel)
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
    stored_value = channel_value_for_storage(canonical_channel, channel_value)

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
        logger.warning("identity.sender_channel_fact_assertion_failed")
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
    - Types that have no registered predicate mapping (e.g. ``'address'``)
      are silently skipped — the function returns ``None`` without touching
      the DB.  ``'telegram_chat_id'`` is mapped (to ``has-handle``; RFC 0004
      Amendment 3) and retracts like any other handle.
    - The caller is responsible for supplying the correct ``ci_value``; the
      retraction is keyed on the exact string stored in ``object``.
    - The selector names an SPO, not an effective occurrence. It retracts the
      single active occurrence (keeping its effective packet); when more than
      one occurrence is active it raises ``temporal_occurrence_ambiguous``
      before any write rather than choosing one.
    """
    predicate = contact_info_type_to_predicate(ci_type)
    if predicate is None:
        # No triple for this channel type — nothing to retract.
        return None

    async def _retract(c: asyncpg.Connection) -> uuid.UUID | None:
        matches = await c.fetch(
            """
            SELECT id FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = $2
              AND object    = $3
              AND validity  = 'active'
            FOR UPDATE
            """,
            subject,
            predicate,
            ci_value,
        )
        if len(matches) > 1:
            raise TemporalError(
                OCCURRENCE_AMBIGUOUS,
                f"{len(matches)} active effective occurrences match this contact value; "
                "retract one by fact id.",
            )
        if not matches:
            return None
        fact_id = await c.fetchval(
            """
            UPDATE relationship.entity_facts
            SET validity   = 'retracted',
                updated_at = now()
            WHERE id = $1
              AND validity = 'active'
            RETURNING id
            """,
            matches[0]["id"],
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
        async with native_channel_mutation(pool, conn, [subject]) as current_conn:
            return await _retract(current_conn)

    async with pool.acquire() as acquired_conn:
        async with native_channel_mutation(pool, acquired_conn, [subject]) as current_conn:
            async with current_conn.transaction():
                return await _retract(current_conn)


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


async def _fence_prefers_channel(conn: asyncpg.Connection, subject: uuid.UUID) -> None:
    """Refuse a set/clear the legacy single-valued path cannot perform safely.

    ``prefers-channel`` has no period-aware policy yet (``bu-4ss0u``), so its
    predicate-wide supersession may only touch unknown default rows. A
    temporal-bearing current row, or more than one active occurrence of one
    channel, fails ``temporal_mutator_unsupported`` before any write. The rows
    are locked so the check and the write see the same set.
    """
    fence = await conn.fetchrow(
        f"""
        WITH active_rows AS (
            SELECT ef.object, {temporal_bearing_sql("ef")} AS temporal
            FROM relationship.entity_facts ef
            WHERE ef.subject   = $1
              AND ef.predicate = $2
              AND ef.validity  = 'active'
            FOR UPDATE
        )
        SELECT COALESCE(bool_or(temporal), false) AS temporal,
               COALESCE(max(n), 0) AS occurrences
        FROM (
            SELECT object, bool_or(temporal) AS temporal, count(*) AS n
            FROM active_rows GROUP BY object
        ) per_channel
        """,
        subject,
        PREFERS_CHANNEL_PREDICATE,
    )
    if fence["temporal"] or fence["occurrences"] > 1:
        raise TemporalError(
            MUTATOR_UNSUPPORTED,
            f"entity {subject} has a temporal or repeated prefers-channel occurrence; "
            "the single-valued preference path cannot change it yet.",
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

        # Entity rows before the fence's fact-row locks (see _lock_fact_entities).
        await _lock_fact_entities(c, subject)
        await _fence_prefers_channel(c, subject)

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
        async with native_channel_mutation(pool, conn, [subject]) as current_conn:
            return await _do(current_conn)
    async with pool.acquire() as acquired_conn:
        async with native_channel_mutation(pool, acquired_conn, [subject]) as current_conn:
            async with current_conn.transaction():
                return await _do(current_conn)


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
        await _fence_prefers_channel(c, subject)
        return await _supersede_active_prefers_channel(c, subject, validity="retracted")

    if conn is not None:
        async with native_channel_mutation(pool, conn, [subject]) as current_conn:
            return await _do(current_conn)
    async with pool.acquire() as acquired_conn:
        async with native_channel_mutation(pool, acquired_conn, [subject]) as current_conn:
            async with current_conn.transaction():
                return await _do(current_conn)
