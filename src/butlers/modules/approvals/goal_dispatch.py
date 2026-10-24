"""Fixed authenticated owning approval operations; no model-facing tool or runner.

PARK binds signed central selection on the real owning connection. CLAIM commits
an exact stored-call witness; EXECUTE irrevocably consumes it before invoking a
captured registered original. A readback is never a second execution permit.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import UUID

from butlers.capability_broker.catalog import project_catalog
from butlers.capability_broker.handoff import registration_digest, verify_handoff
from butlers.capability_broker.transport.protocol import identity as parse_identity
from butlers.core.capability_json import arguments_digest
from butlers.modules.approvals.goal_calls import stored_goal_call
from butlers.modules.approvals.goal_control import GoalApprovalsUnavailable, OwningGoalApprovals
from butlers.modules.approvals.models import GoalCallLineage
from butlers.modules.approvals.park import ParkRequest


def call_wire(call):
    from dataclasses import asdict

    lineage = asdict(call.lineage)
    return {
        "approval_ref": str(call.approval_ref),
        "tool_name": call.tool_name,
        "args": call.args,
        "lineage": {k: str(v) if isinstance(v, UUID) else v for k, v in lineage.items()},
    }


def decode_call(value):
    from butlers.capability_broker.transport.protocol import exact, uuid_text
    from butlers.modules.approvals.goal_calls import StoredGoalCall

    exact(value, ("approval_ref", "tool_name", "args", "lineage"))
    fields = (
        "goal_id",
        "invocation_id",
        "goal_depth",
        "goal_exposed",
        "goal_origin",
        "goal_effect_id",
        "goal_capability",
        "goal_args_digest",
        "goal_approved_args_digest",
    )
    line = dict(exact(value["lineage"], fields))
    for key in ("goal_id", "invocation_id", "goal_effect_id"):
        line[key] = uuid_text(line[key])
    call = StoredGoalCall(
        uuid_text(value["approval_ref"]), value["tool_name"], value["args"], GoalCallLineage(**line)
    )
    if arguments_digest(call.args) != call.lineage.goal_approved_args_digest:
        raise ValueError("stored_snapshot_digest")
    return call


class GoalDispatch:
    """Constructed only from the owning daemon's final catalog and originals."""

    def __init__(self, pool, domain, catalog, handlers, keys, *, clock=time.time, preparers=None):
        from butlers.core.tool_catalog import ToolCatalog

        if type(catalog) is not ToolCatalog or not keys:
            raise ValueError("owning_catalog_and_keys_required")
        self.owner = OwningGoalApprovals(pool, domain, catalog=catalog)
        self.domain, self.catalog = domain, catalog
        self.handlers, self.keys = MappingProxyType(dict(handlers)), MappingProxyType(dict(keys))
        self.clock = clock
        self.preparers = MappingProxyType(dict(preparers or {}))

    def _verify(self, envelope, peer):
        if peer.role != "broker-control" or peer.domain != self.domain:
            raise GoalApprovalsUnavailable("owning_peer_required")
        purpose = envelope["operation"]
        if envelope["protocol"] != "broker-approvals-v2":
            raise GoalApprovalsUnavailable("owning_authority_required")
        proof = verify_handoff(
            envelope["handoff"],
            keys=self.keys,
            audience=self.domain,
            purpose=purpose,
            now=int(self.clock()),
        )
        p, target = proof.payload, parse_identity(envelope["identity"])
        d = p["d7"]
        if (
            target.goal_id != UUID(d["goal_id"])
            or target.effect_id != UUID(d["effect_id"])
            or target.action_id != target.effect_id
            or target.capability != proof.grant.capability
            or target.args_digest != p["original_args_digest"]
        ):
            raise GoalApprovalsUnavailable("owning_identity_changed")
        cap = next(
            (
                c
                for c in project_catalog(self.catalog, domain=self.domain).capabilities
                if c.identity == target.capability
            ),
            None,
        )
        if (
            cap is None
            or registration_digest(cap) != p["registration_digest"]
            or p["approval_mode"] != ("always_per_event" if cap.always_per_event else "default")
        ):
            raise GoalApprovalsUnavailable("owning_registration_changed")
        if proof.central_rule_match and (proof.grant.park_only or cap.always_per_event):
            raise GoalApprovalsUnavailable("owning_rule_forbidden")
        return proof, target

    async def __call__(self, value, peer, assertion=None):
        # Legacy Stop LOOKUP/CANCEL remain purpose-only mTLS operations. Legacy
        # unsigned PARK is never enabled by this production implementation.
        op = value["operation"]
        if value["protocol"] == "broker-approvals-v1" and op in {"LOOKUP", "CANCEL"}:
            if peer.role != "broker-control" or peer.domain != self.domain or assertion is not None:
                raise GoalApprovalsUnavailable("owning_peer_required")
            identity = parse_identity(value["identity"])
            receipt = await (
                self.owner.lookup(identity) if op == "LOOKUP" else self.owner.cancel(identity)
            )
            outcome = "unknown" if receipt is None else receipt["outcome"]
        else:
            if assertion is not None:
                raise GoalApprovalsUnavailable("unexpected_ordinary_assertion")
            proof, identity = self._verify(value, peer)
            if op == "PARK":
                d = proof.payload["d7"]
                now = datetime.fromtimestamp(self.clock(), UTC)
                request = ParkRequest(
                    action_id=identity.action_id,
                    tool_name=d["capability"],
                    tool_args=value["arguments"],
                    agent_summary=None,
                    requested_at=now,
                    expires_at=now + timedelta(days=1),
                    origin_butler=self.domain,
                    goal_lineage=GoalCallLineage(
                        identity.goal_id,
                        UUID(d["invocation_id"]),
                        d["depth"],
                        d["exposed"],
                        d["origin"],
                        identity.effect_id,
                        identity.capability,
                        identity.args_digest,
                    ),
                )
                if arguments_digest(request.tool_args) != proof.args_digest:
                    raise GoalApprovalsUnavailable("owning_arguments_changed")
                preparer = self.preparers.get(request.tool_name)
                prepared = None
                if request.tool_name == "calendar_respond":
                    from butlers.modules.calendar_response import CalendarResponseCoordinator

                    if type(preparer) is not CalendarResponseCoordinator:
                        raise GoalApprovalsUnavailable("canonical_preparer_unavailable")
                    prepared = await preparer.prepare_public(request.tool_args, identity.action_id)
                _, receipt = await self.owner.park(
                    request,
                    grant=proof.grant,
                    authorization=proof,
                    prepared=prepared,
                    preparer=preparer,
                )
                outcome = "parked"
            elif op == "SNAPSHOT":
                receipt = await self._snapshot(identity)
                outcome = "approved"
            elif op == "CLAIM":
                receipt = await self._claim(identity, proof)
                outcome = "claimed"
            elif op == "WITNESS":
                receipt = await self._witness(identity, proof)
                outcome = "claimed"
            elif op == "EXECUTE":
                await self._execute(identity, proof)
                receipt, outcome = None, "unknown"  # Handler return is NOT external effect commit.
            else:
                raise GoalApprovalsUnavailable("owning_operation_required")
        return {
            **{k: value[k] for k in ("protocol", "operation", "request_id", "identity")},
            "outcome": outcome,
            "receipt": receipt,
        }

    async def _stored(self, conn, identity, *, dispatched=False):
        gate = await self.owner._gate(conn, identity)
        if (
            gate is None
            or gate["stop_fenced"]
            or gate.get("park_authorization") is None
            or gate["phase"] != ("dispatch_intent" if dispatched else "approved")
        ):
            raise GoalApprovalsUnavailable("owning_snapshot_fenced")
        row = await conn.fetchrow(
            "SELECT * FROM pending_actions WHERE id=$1 FOR UPDATE", identity.action_id
        )
        call = stored_goal_call(row)
        if (
            call.lineage.goal_id != identity.goal_id
            or call.lineage.goal_effect_id != identity.effect_id
            or call.lineage.goal_capability != identity.capability
            or call.lineage.goal_args_digest != identity.args_digest
        ):
            raise GoalApprovalsUnavailable("owning_snapshot_changed")
        return gate, row, call

    async def _snapshot(self, identity):
        async with self.owner._transaction(identity) as conn:
            gate, _, call = await self._stored(conn, identity)
            return {"version": gate["state_version"], "call": call_wire(call)}

    def _match(self, gate, call, proof):
        p, d = proof.payload, proof.payload["d7"]
        original = gate["park_authorization"]
        if (
            d["args_digest"] != call.lineage.goal_approved_args_digest
            or d["invocation_id"] != str(call.lineage.invocation_id)
            or d["depth"] != call.lineage.goal_depth
            or d["origin"] != call.lineage.goal_origin
            or (call.lineage.goal_exposed and not d["exposed"])
            or p["grant"] != original["grant"]
            or p["catalog_digest"] != original["catalog_digest"]
            or p["registration_digest"] != original["registration_digest"]
            or p["approval_mode"] != original["approval_mode"]
        ):
            raise GoalApprovalsUnavailable("owning_witness_binding_changed")

    async def _claim(self, identity, proof):
        async with self.owner._transaction(identity) as conn:
            gate, _, call = await self._stored(conn, identity)
            self._match(gate, call, proof)
            if gate["state_version"] != proof.payload["snapshot_version"]:
                raise GoalApprovalsUnavailable("owning_snapshot_stale")
            snapshot = arguments_digest(call_wire(call))
            updated = await conn.fetchrow(
                "UPDATE approval_goal_effects SET phase='dispatch_intent',dispatch_intent_id=$2,"
                "dispatch_snapshot_digest=$3,state_version=state_version+1,"
                "updated_at=clock_timestamp() "
                "WHERE effect_id=$1 AND dispatch_intent_id IS NULL AND NOT stop_fenced RETURNING *",
                identity.effect_id,
                UUID(proof.payload["d7"]["call_id"]),
                snapshot,
            )
            if updated is None:
                raise GoalApprovalsUnavailable("owning_witness_already_claimed")
        # Only a confirmed commit permits the caller to request consumption.
        return {"intent_id": str(updated["dispatch_intent_id"]), "snapshot_digest": snapshot}

    async def _witness(self, identity, proof):
        async with self.owner._transaction(identity) as conn:
            gate, _, call = await self._stored(conn, identity, dispatched=True)
            self._match(gate, call, proof)
            if (
                gate["dispatch_intent_id"] != UUID(proof.payload["d7"]["call_id"])
                or gate["dispatch_snapshot_digest"] != arguments_digest(call_wire(call))
                or gate["dispatch_consumed_at"] is not None
                or gate["state_version"] != proof.payload["snapshot_version"] + 1
            ):
                raise GoalApprovalsUnavailable("owning_witness_unavailable")
            return {
                "intent_id": str(gate["dispatch_intent_id"]),
                "snapshot_digest": gate["dispatch_snapshot_digest"],
            }

    async def _execute(self, identity, proof):
        async with self.owner._transaction(identity) as conn:
            gate, row, call = await self._stored(conn, identity, dispatched=True)
            self._match(gate, call, proof)
            if (
                gate["dispatch_intent_id"] != UUID(proof.payload["d7"]["call_id"])
                or gate["state_version"] != proof.payload["snapshot_version"] + 1
                or gate["dispatch_snapshot_digest"] != arguments_digest(call_wire(call))
            ):
                raise GoalApprovalsUnavailable("owning_execution_binding_changed")
            fn = self.handlers.get(call.tool_name)
            if not callable(fn):
                raise GoalApprovalsUnavailable("owning_registered_handler_unavailable")
            consumed = await conn.fetchrow(
                "UPDATE approval_goal_effects SET dispatch_consumed_at=clock_timestamp(),"
                "state_version=state_version+1,updated_at=clock_timestamp() "
                "WHERE effect_id=$1 AND dispatch_intent_id=$2 AND dispatch_consumed_at IS NULL "
                "AND NOT stop_fenced RETURNING effect_id",
                identity.effect_id,
                gate["dispatch_intent_id"],
            )
            if consumed is None:
                raise GoalApprovalsUnavailable("owning_execution_already_consumed")
        # This statement MUST remain after successful __aexit__/COMMIT. Neither
        # durable witness readback nor an uncertain commit grants another call.
        from butlers.modules.approvals.executor import _invoke_approved_handler

        await _invoke_approved_handler(row, call.approval_ref, call.tool_name, call.args, fn)
