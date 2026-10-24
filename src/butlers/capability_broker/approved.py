"""Central half of stored-approved dispatch, serialized with real Stop SQL.

This is not an approval ingress. StoredGoalCall is data, not authentication.
The separately required owning producer must authenticate its snapshot, commit
its matching local dispatch witness after this transaction, and refuse changes
or uncertainty before forwarding. Nothing here starts that producer, signs a
call, forwards a request, or claims an owning receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments
from butlers.core.goal_admission import decode_grants, digest, encode_grants, intersect
from butlers.core.goal_ledger import GoalLedger
from butlers.modules.approvals.goal_calls import StoredGoalCall
from butlers.modules.approvals.models import GoalCallLineage

from .contracts import BrokerRefused, Decision, Disposition, Principal, Reason, Reservation
from .domains import Authorities, DomainReceipt
from .ledger import BrokerLedger


@dataclass(frozen=True, slots=True)
class ApprovedDispatch:
    """Confirmed central intent, not proof of local approval/dispatch/commit.

    Keep the original immutable effect digest separate from the owner's edited
    executed digest. The latter is used in D7; settlement must still bind the
    original effect and this one-time dispatch intent. Raw args stay immutable.
    """

    reservation: Reservation
    original_args_digest: str
    approval_ref: str
    wire_arguments: bytes = field(repr=False)


async def prepare_approved(connection, call: StoredGoalCall, authorities: Authorities):
    """Run inside one bounded caller-owned central transaction.

    D13 allows an owner decision after the invocation/token ended and while the
    goal settles. It does not mint/revive a bearer token or reserve another
    budget unit. The original invocation/ceiling, current catalog, live resource
    selection and monotonic exposure still constrain the edited stored call.
    """
    if not isinstance(call, StoredGoalCall) or not isinstance(call.lineage, GoalCallLineage):
        raise BrokerRefused(Reason.AUTHENTICATION)
    lineage = call.lineage
    wire = canonical_bytes(call.args)
    arguments = parse_arguments(wire)
    approved_digest = arguments_digest(arguments)
    if (
        call.approval_ref != lineage.goal_effect_id
        or lineage.goal_capability.split("__")[1] != call.tool_name
        or approved_digest != lineage.goal_approved_args_digest
    ):
        raise BrokerRefused(Reason.AUTHENTICATION)
    await GoalLedger(connection).verify_role()
    # The caller's actual sw048 transaction supplies remaining-time SQL limits;
    # do not refresh those waits after pool/schema/role admission consumed time.
    goal = await connection.fetchrow(
        "SELECT id,status,origin,exposed,catalog_digest,ceiling FROM switchboard.goals "
        "WHERE id=$1 FOR UPDATE OF goals",
        lineage.goal_id,
    )
    if goal is None or goal["status"] not in {"running", "settling"}:
        raise BrokerRefused(Reason.GOAL)
    invocation = await connection.fetchrow(
        "SELECT id,depth,exposed,grant_snapshot,grant_digest FROM switchboard.invocations "
        "WHERE goal_id=$1 AND id=$2 FOR UPDATE",
        lineage.goal_id,
        lineage.invocation_id,
    )
    if invocation is None:
        raise BrokerRefused(Reason.AUTHENTICATION)
    try:
        snapshot = invocation["grant_snapshot"]
        grants = decode_grants(snapshot)
        ceiling = decode_grants(goal["ceiling"])
        if (
            encode_grants(grants) != snapshot
            or encode_grants(ceiling) != goal["ceiling"]
            or digest(snapshot) != invocation["grant_digest"]
            or intersect(grants, ceiling) != grants
            or invocation["depth"] != lineage.goal_depth
            or goal["origin"] != lineage.goal_origin
            or type(goal["exposed"]) is not bool
            or type(invocation["exposed"]) is not bool
        ):
            raise ValueError("invalid_stored_principal")
        principal = Principal(
            lineage.goal_id,
            lineage.invocation_id,
            lineage.goal_depth,
            goal["origin"],
            goal["exposed"] or invocation["exposed"] or lineage.goal_exposed,
            goal["catalog_digest"],
            grants,
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
    row = await connection.fetchrow(
        "SELECT e.*,a.owning_domain,a.action_id,a.approval_ref AS owning_approval_ref,"
        "a.dispatch_intent_id,EXISTS(SELECT 1 FROM switchboard.goal_approval_cancel_work w "
        "WHERE w.goal_id=e.goal_id AND w.effect_id=e.id) AS cancellation_pending "
        "FROM switchboard.effects e JOIN switchboard.goal_park_associations a "
        "ON (a.goal_id,a.effect_id)=(e.goal_id,e.id) "
        "WHERE e.goal_id=$1 AND e.id=$2 FOR UPDATE OF e,a",
        lineage.goal_id,
        lineage.goal_effect_id,
    )
    reference = str(call.approval_ref)
    if row is None or (
        row["goal_id"] != lineage.goal_id
        or row["invocation_id"] != lineage.invocation_id
        or row["capability"] != lineage.goal_capability
        or row["args_digest"] != lineage.goal_args_digest
        or row["owning_domain"] != lineage.goal_capability.split("__")[0]
        or row["action_id"] != call.approval_ref
        or row["approval_ref"] not in (None, reference)
        or row["owning_approval_ref"] not in (None, reference)
        or row["cancellation_pending"] is not False
    ):
        raise BrokerRefused(Reason.AUTHENTICATION)
    if row["dispatch_intent_id"] is not None or row["status"] not in {
        "requested",
        "parked",
        "approved",
        "unknown",
    }:
        raise BrokerRefused(Reason.DUPLICATE, effect_id=lineage.goal_effect_id)
    capability, _ = await BrokerLedger(connection).resolve_call(
        principal, lineage.goal_capability, arguments, approved_digest, authorities
    )
    # The original PARK already consumed the call budget. Check its existing
    # accounting rather than charging twice or creating another effect.
    budget = await connection.fetchrow(
        "SELECT *,deadline>now() AS deadline_valid FROM switchboard.budgets "
        "WHERE goal_id=$1 FOR UPDATE",
        lineage.goal_id,
    )
    if (
        budget is None
        or not budget["deadline_valid"]
        or not (
            0 < budget["used_calls"] <= budget["max_calls"]
            and budget["used_calls"] + budget["reserved_calls"] <= budget["max_calls"]
        )
    ):
        raise BrokerRefused(Reason.BUDGET)
    intent = uuid4()
    # The central FK/Stop triggers enforce the same goal-first order. Recovering
    # a lost PARK response can attach its exact owning reference, but cannot
    # erase any dispatched/committed/unknown-forward provenance.
    updated = await connection.fetchrow(
        "UPDATE switchboard.effects SET status='approved',approval_ref=$3,updated_at=now() "
        "WHERE goal_id=$1 AND id=$2 AND status IN ('requested','parked','approved','unknown') "
        "RETURNING id",
        lineage.goal_id,
        lineage.goal_effect_id,
        reference,
    )
    if updated is None:
        raise BrokerRefused(Reason.UNAVAILABLE)
    updated = await connection.fetchrow(
        "UPDATE switchboard.goal_park_associations SET approval_ref=$4,dispatch_intent_id=$3 "
        "WHERE goal_id=$1 AND effect_id=$2 AND dispatch_intent_id IS NULL "
        "AND (approval_ref IS NULL OR approval_ref=$4) "
        "AND NOT EXISTS(SELECT 1 FROM switchboard.goal_approval_cancel_work w "
        "WHERE w.goal_id=$1 AND w.effect_id=$2) RETURNING effect_id",
        lineage.goal_id,
        lineage.goal_effect_id,
        intent,
        reference,
    )
    if updated is None:
        raise BrokerRefused(Reason.DUPLICATE, effect_id=lineage.goal_effect_id)
    updated = await connection.fetchrow(
        "UPDATE switchboard.effects SET status='dispatched',updated_at=now() "
        "WHERE goal_id=$1 AND id=$2 AND status='approved' RETURNING id",
        lineage.goal_id,
        lineage.goal_effect_id,
    )
    if updated is None:
        raise BrokerRefused(Reason.UNAVAILABLE)
    return ApprovedDispatch(
        Reservation(
            principal,
            capability,
            Decision(Disposition.DISPATCH),
            intent,
            lineage.goal_effect_id,
            approved_digest,
            next(g for g in principal.grants if g.capability == capability.identity),
        ),
        lineage.goal_args_digest,
        reference,
        wire,
    )


async def settle_approved(connection, prepared: ApprovedDispatch, receipt: DomainReceipt):
    """Settle only the exact committed central intent; no owning-proof inference.

    The authenticated owning result producer is still required. This has no
    retry/forward side effect. A stopped goal may settle an earlier dispatched
    call, but cannot turn it into cancelled or erase its immutable original args.
    """
    if type(prepared) is not ApprovedDispatch or type(receipt) is not DomainReceipt:
        raise BrokerRefused(Reason.AUTHENTICATION)
    reservation = prepared.reservation
    if (
        arguments_digest(parse_arguments(prepared.wire_arguments)) != reservation.args_digest
        or reservation.effect_id is None
        or reservation.decision.disposition is not Disposition.DISPATCH
    ):
        raise BrokerRefused(Reason.AUTHENTICATION)
    goal = await connection.fetchrow(
        "SELECT id FROM switchboard.goals WHERE id=$1 FOR UPDATE OF goals",
        reservation.principal.goal_id,
    )
    if goal is None:
        raise BrokerRefused(Reason.GOAL)
    status = "refused" if receipt.status == "not_received" else receipt.status
    updated = await connection.fetchrow(
        "UPDATE switchboard.effects e SET status=$3,receipt_ref=$4,updated_at=now() "
        "FROM switchboard.goal_park_associations a "
        "WHERE (a.goal_id,a.effect_id)=(e.goal_id,e.id) "
        "AND e.goal_id=$1 AND e.id=$2 AND e.status='dispatched' "
        "AND e.invocation_id=$5 AND e.capability=$6 AND e.args_digest=$7 "
        "AND e.approval_ref=$8 AND a.approval_ref=$8 "
        "AND a.owning_domain=$9 AND a.action_id=e.id "
        "AND a.dispatch_intent_id=$10 AND a.dispatch_committed_at IS NOT NULL RETURNING e.id",
        reservation.principal.goal_id,
        reservation.effect_id,
        status,
        receipt.receipt_ref,
        reservation.principal.invocation_id,
        reservation.capability.identity,
        prepared.original_args_digest,
        prepared.approval_ref,
        reservation.capability.domain,
        reservation.call_id,
    )
    if updated is None:
        raise BrokerRefused(Reason.DUPLICATE, effect_id=reservation.effect_id)
    expose = reservation.capability.output_trust == "untrusted" or (
        reservation.capability.output_trust == "stamped" and not receipt.stamped_owner_untainted
    )
    if expose:
        await connection.execute(
            "UPDATE switchboard.goals SET exposed=true,updated_at=now() WHERE id=$1",
            reservation.principal.goal_id,
        )
        await connection.execute(
            "UPDATE switchboard.invocations SET exposed=true,updated_at=now() "
            "WHERE goal_id=$1 AND id=$2",
            reservation.principal.goal_id,
            reservation.principal.invocation_id,
        )


async def parked_identity(connection, goal_id, effect_id):
    """Lock the original undispatched PARK tuple without granting execution."""
    from uuid import UUID

    from butlers.core.goal_stop import CancelIdentity

    if not isinstance(goal_id, UUID) or not isinstance(effect_id, UUID):
        raise BrokerRefused(Reason.AUTHENTICATION)
    goal = await connection.fetchrow(
        "SELECT id,status FROM switchboard.goals WHERE id=$1 FOR UPDATE OF goals", goal_id
    )
    if goal is None or goal["status"] not in {"running", "awaiting_approval", "settling"}:
        raise BrokerRefused(Reason.GOAL)
    row = await connection.fetchrow(
        "SELECT e.*,a.owning_domain,a.action_id,a.approval_ref AS owning_approval_ref,"
        "a.dispatch_intent_id FROM switchboard.effects e "
        "JOIN switchboard.goal_park_associations a ON (a.goal_id,a.effect_id)=(e.goal_id,e.id) "
        "WHERE e.goal_id=$1 AND e.id=$2 FOR UPDATE OF e,a",
        goal_id,
        effect_id,
    )
    if (
        row is None
        or row["goal_id"] != goal_id
        or row["id"] != effect_id
        or row["dispatch_intent_id"] is not None
        or row["status"] not in {"requested", "parked", "unknown", "rejected", "expired"}
        or row["owning_approval_ref"] not in {None, str(effect_id)}
    ):
        raise BrokerRefused(Reason.DUPLICATE)
    try:
        identity = CancelIdentity(
            goal_id,
            effect_id,
            row["owning_domain"],
            row["action_id"],
            row["capability"],
            row["args_digest"],
        )
    except (ValueError, TypeError):
        raise BrokerRefused(Reason.AUTHENTICATION) from None
    return identity, row["status"]


async def settle_negative(connection, identity, receipt):
    """Persist only a validated current owning negative, never a dispatch/Stop ACK."""
    from .transport.protocol import receipt_digest

    outcome = receipt["outcome"]
    if (
        outcome not in {"rejected", "expired"}
        or receipt["approval_ref"] != str(identity.action_id)
        or receipt["identity"]
        != {
            key: str(getattr(identity, key))
            for key in (
                "goal_id",
                "effect_id",
                "owning_domain",
                "action_id",
                "capability",
                "args_digest",
            )
        }
        or receipt_digest(receipt) != receipt["digest"]
    ):
        raise BrokerRefused(Reason.AUTHENTICATION)
    current, status = await parked_identity(connection, identity.goal_id, identity.effect_id)
    if current != identity or (status in {"rejected", "expired"} and status != outcome):
        raise BrokerRefused(Reason.DUPLICATE)
    if status == outcome:
        return
    row = await connection.fetchrow(
        "UPDATE switchboard.effects SET status=$3,updated_at=now() "
        "WHERE goal_id=$1 AND id=$2 AND status IN ('requested','parked','unknown') RETURNING id",
        identity.goal_id,
        identity.effect_id,
        outcome,
    )
    if row is None:
        raise BrokerRefused(Reason.DUPLICATE)


async def parked_reservation(connection, goal_id, effect_id, authorities):
    """Select immutable original association/grant for a signed snapshot request."""
    from uuid import UUID

    if not isinstance(goal_id, UUID) or not isinstance(effect_id, UUID):
        raise BrokerRefused(Reason.AUTHENTICATION)
    goal = await connection.fetchrow(
        "SELECT id,status,origin,exposed,catalog_digest,ceiling FROM switchboard.goals "
        "WHERE id=$1 FOR UPDATE OF goals",
        goal_id,
    )
    if goal is None or goal["status"] not in {"running", "settling"}:
        raise BrokerRefused(Reason.GOAL)
    row = await connection.fetchrow(
        "SELECT e.*,a.owning_domain,a.action_id,a.park_call_id,a.selected_grant,"
        "a.catalog_digest AS parked_catalog_digest,a.allow_standing_rule,a.dispatch_intent_id "
        "FROM switchboard.effects e JOIN switchboard.goal_park_associations a "
        "ON (a.goal_id,a.effect_id)=(e.goal_id,e.id) "
        "WHERE e.goal_id=$1 AND e.id=$2 FOR UPDATE OF e,a",
        goal_id,
        effect_id,
    )
    if row is None or row["dispatch_intent_id"] is not None or row["park_call_id"] is None:
        raise BrokerRefused(Reason.DUPLICATE)
    invocation = await connection.fetchrow(
        "SELECT id,depth,exposed,grant_snapshot,grant_digest FROM switchboard.invocations "
        "WHERE goal_id=$1 AND id=$2 FOR UPDATE",
        goal_id,
        row["invocation_id"],
    )
    try:
        grants = decode_grants(invocation["grant_snapshot"])
        selected = decode_grants([row["selected_grant"]])[0]
        if (
            selected not in grants
            or intersect(grants, decode_grants(goal["ceiling"])) != grants
            or digest(invocation["grant_snapshot"]) != invocation["grant_digest"]
            or row["parked_catalog_digest"] != goal["catalog_digest"]
        ):
            raise ValueError("parked_principal_changed")
        principal = Principal(
            goal_id,
            row["invocation_id"],
            invocation["depth"],
            goal["origin"],
            goal["exposed"] or invocation["exposed"],
            goal["catalog_digest"],
            grants,
        )
        catalog = await authorities.catalog(connection, principal)
        capability = next(c for c in catalog.capabilities if c.identity == selected.capability)
        if catalog.digest != principal.catalog_digest or row["capability"] != selected.capability:
            raise ValueError("parked_catalog_changed")
        return Reservation(
            principal,
            capability,
            Decision(Disposition.PARK, row["allow_standing_rule"]),
            row["park_call_id"],
            effect_id,
            row["args_digest"],
            selected,
        )
    except (ValueError, TypeError, KeyError, StopIteration):
        raise BrokerRefused(Reason.AUTHENTICATION) from None


async def attach_owning_witness(connection, prepared, witness):
    reservation = prepared.reservation
    if witness["intent_id"] != str(reservation.call_id):
        raise BrokerRefused(Reason.AUTHENTICATION)
    goal = await connection.fetchrow(
        "SELECT id,status FROM switchboard.goals WHERE id=$1 FOR UPDATE OF goals",
        reservation.principal.goal_id,
    )
    if goal is None or goal["status"] not in {"running", "settling"}:
        raise BrokerRefused(Reason.GOAL)
    row = await connection.fetchrow(
        "UPDATE switchboard.goal_park_associations SET owning_witness_digest=$4 "
        "WHERE goal_id=$1 AND effect_id=$2 AND dispatch_intent_id=$3 "
        "AND (owning_witness_digest IS NULL OR owning_witness_digest=$4) RETURNING effect_id",
        reservation.principal.goal_id,
        reservation.effect_id,
        reservation.call_id,
        witness["snapshot_digest"],
    )
    if row is None:
        raise BrokerRefused(Reason.AUTHENTICATION)
