"""Owning SQL transactions for durable goal approval state.

These internal methods consume infrastructure-bound identities, not model/HTTP
claims. No listener, key, domain verifier or profile is enabled here. PARK must
be called only after the separately owned authentic ingress binds its lineage.
LOOKUP absence is UNKNOWN; only a committed CANCEL creates an absent fence.
External effects are never inferred from generic approval/execution results.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments
from butlers.core.goal_stop import CancelIdentity
from butlers.modules.approvals.goal_receipts import owning_receipt, stored_receipt
from butlers.modules.approvals.models import ActionStatus, PendingAction
from butlers.modules.approvals.park import (
    ParkRequest,
    _admit,
    _park_without_delivery,
    _validate_request,
)

# Exact installed prosrc; change only with the owning migration, never a permissive fallback.
_FUNCTION_DIGESTS = {
    "guard_approval_goal_effect": "6d5a1c3269b722f44b5093e8220aee89",
    "guard_approval_goal_receipt": "8e39bcaa85c2b376afa4768cc1b2c03f",
    "guard_pending_goal_action": "46bf1a1efdc84728b7e07f366ac74010",
    "validate_approval_goal_final": "a8c50106b56748beabb5b02b0b01b063",
}


class GoalApprovalsUnavailable(ValueError):
    """Content-blind refusal, including uninstalled or unhealthy owning schema."""


class OwningGoalApprovals:
    def __init__(self, pool, domain: str, *, timeout: float = 5):
        if (
            type(domain) is not str
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", domain)
            or type(timeout) not in (int, float)
            or not 0 < timeout <= 5
        ):
            raise ValueError("finite_owning_goal_configuration_required")
        self.pool, self.domain, self.timeout = pool, domain, timeout

    async def _verify(self, conn):
        role = await conn.fetchrow(
            "SELECT pg_catalog.current_schema() AS schema,current_user AS role,"
            "pg_catalog.current_schemas(true) AS schemas,"
            "(rolsuper OR rolbypassrls) AS unsafe FROM pg_catalog.pg_roles "
            "WHERE rolname=current_user"
        )
        if (
            role is None
            or role["schema"] != self.domain
            or role["schemas"] != ["pg_catalog", self.domain, "public"]
            or (role["role"] != f"butler_{self.domain}_rw" or role["unsafe"] is not False)
        ):
            raise GoalApprovalsUnavailable("owning_goal_role_unavailable")
        healthy = await conn.fetchval(
            """
            SELECT count(*)=2 AND bool_and(c.relrowsecurity AND NOT c.relforcerowsecurity
              AND NOT pg_has_role(current_user,c.relowner,'MEMBER')
              AND NOT has_table_privilege(current_user,c.oid,'TRIGGER')
              AND NOT has_table_privilege(current_user,c.oid,'DELETE')
              AND NOT has_table_privilege(current_user,c.oid,'TRUNCATE')
              AND has_table_privilege(current_user,c.oid,'SELECT')
              AND has_table_privilege(current_user,c.oid,'INSERT')
              AND CASE c.relname WHEN 'approval_goal_effects' THEN
                    has_table_privilege(current_user,c.oid,'UPDATE')
                  ELSE NOT has_table_privilege(current_user,c.oid,'UPDATE') END
              AND (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid)=
                  CASE c.relname WHEN 'approval_goal_effects' THEN 3 ELSE 2 END
              AND NOT EXISTS(SELECT FROM pg_policy p WHERE p.polrelid=c.oid AND NOT (
                p.polpermissive AND p.polroles=ARRAY[(SELECT oid FROM pg_roles
                                                     WHERE rolname=current_user)]
                AND p.polcmd IN ('r','a','w')
                AND CASE WHEN p.polcmd='a' THEN p.polqual IS NULL ELSE
                  lower(regexp_replace(pg_get_expr(p.polqual,p.polrelid),
                      '[()[:space:]]|::(name|text)','','g'))
                  = 'current_user=''' || current_user || '''' END
                AND CASE WHEN p.polcmd='r' THEN p.polwithcheck IS NULL ELSE
                  lower(regexp_replace(pg_get_expr(p.polwithcheck,p.polrelid),
                      '[()[:space:]]|::(name|text)','','g'))
                  = 'current_user=''' || current_user || '''' END)))
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema()
              AND c.relname IN ('approval_goal_effects','approval_goal_receipts')
            """
        )
        if healthy is not True:
            raise GoalApprovalsUnavailable("owning_goal_schema_unavailable")
        functions = await conn.fetchval(
            """
            SELECT count(*)=4 AND bool_and(md5(p.prosrc)=e.value AND NOT p.prosecdef
              AND NOT pg_has_role(current_user,p.proowner,'MEMBER')
              AND p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']
              AND NOT has_function_privilege(current_user,p.oid,'EXECUTE'))
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            JOIN jsonb_each_text($1::jsonb) e ON e.key=p.proname
            WHERE n.nspname=current_schema() AND p.pronargs=0
            """,
            _FUNCTION_DIGESTS,
        )
        triggers = await conn.fetchval(
            """
            SELECT count(*)=9 AND bool_and(t.tgenabled='O' AND t.tgqual IS NULL
              AND NOT t.tgisinternal AND p.proname=expected.function
              AND t.tgtype=CASE
                WHEN expected.trigger LIKE '%no_truncate' THEN 34
                WHEN expected.trigger IN ('approval_goal_final','pending_goal_final') THEN 21
                WHEN expected.trigger='pending_goal_action_guard' THEN 31
                ELSE 29 END
              AND t.tgdeferrable=(expected.trigger IN ('approval_goal_final','pending_goal_final'))
              AND t.tginitdeferred=
                  (expected.trigger IN ('approval_goal_final','pending_goal_final')))
            FROM (VALUES
              ('approval_goal_effects','approval_goal_effect_guard','guard_approval_goal_effect'),
              ('approval_goal_effects','approval_goal_effect_no_truncate','guard_approval_goal_effect'),
              ('approval_goal_effects','approval_goal_final','validate_approval_goal_final'),
              ('approval_goal_receipts','approval_goal_receipt_guard','guard_approval_goal_receipt'),
              ('approval_goal_receipts','approval_goal_receipt_no_truncate','guard_approval_goal_receipt'),
              ('pending_actions','pending_goal_action_guard','guard_pending_goal_action'),
              ('pending_actions','pending_goal_action_final_guard','guard_pending_goal_action'),
              ('pending_actions','pending_goal_action_no_truncate','guard_pending_goal_action'),
              ('pending_actions','pending_goal_final','validate_approval_goal_final')
            ) expected(relation,trigger,function)
            JOIN pg_namespace n ON n.nspname=current_schema()
            JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=expected.relation
            JOIN pg_trigger t ON t.tgrelid=c.oid AND t.tgname=expected.trigger
            JOIN pg_proc p ON p.oid=t.tgfoid AND p.pronamespace=n.oid
            """
        )
        if functions is not True or triggers is not True:
            raise GoalApprovalsUnavailable("owning_goal_guards_unavailable")

    @asynccontextmanager
    async def _transaction(self):
        deadline = time.monotonic() + self.timeout
        source = getattr(self.pool, "pool", None) or self.pool
        async with asyncio.timeout(self.timeout):
            if hasattr(source, "acquire"):
                async with source.acquire() as conn, conn.transaction():
                    await self._begin(conn, deadline)
                    yield conn
            else:
                async with source.transaction():
                    await self._begin(source, deadline)
                    yield source

    async def _begin(self, conn, deadline):
        await conn.execute(
            "SELECT pg_catalog.set_config('lock_timeout',$1,true),"
            "pg_catalog.set_config('statement_timeout',$1,true)",
            f"{max(1, int((deadline - time.monotonic()) * 1000))}ms",
        )
        await self._verify(conn)
        # Unlike the ordinary rolling-schema helper, absence/refusal is not
        # converted into an OFF success. No second connection is acquired.
        rollout = await conn.fetchrow(
            "SELECT admission_enabled,worker_enabled FROM approval_delivery_rollout "
            "WHERE singleton IS TRUE FOR SHARE"
        )
        if rollout is None or any(type(rollout[k]) is not bool for k in rollout):
            raise GoalApprovalsUnavailable("owning_rollout_unavailable")
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext('approval-delivery:' || current_schema()))"
        )

    def _identity(self, identity):
        if type(identity) is not CancelIdentity or identity.owning_domain != self.domain:
            raise ValueError("goal_identity_mismatch")

    async def _gate(self, conn, identity, *, create=False, parked=False):
        self._identity(identity)
        if create:
            await conn.execute(
                "INSERT INTO approval_goal_effects "
                "(effect_id,action_id,goal_id,owning_domain,capability,args_digest,phase,"
                "pending_action_id,stop_fenced) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT(effect_id) DO NOTHING",
                identity.effect_id,
                identity.action_id,
                identity.goal_id,
                self.domain,
                identity.capability,
                identity.args_digest,
                "pending" if parked else "absent",
                identity.action_id if parked else None,
                not parked,
            )
        row = await conn.fetchrow(
            "SELECT * FROM approval_goal_effects WHERE effect_id=$1 FOR UPDATE", identity.effect_id
        )
        if row is not None and any(
            row[k] != getattr(identity, k)
            for k in (
                "effect_id",
                "action_id",
                "goal_id",
                "owning_domain",
                "capability",
                "args_digest",
            )
        ):
            raise ValueError("goal_identity_mismatch")
        return row

    async def _read_receipt(self, conn, gate, identity, outcome):
        row = await conn.fetchrow(
            "SELECT * FROM approval_goal_receipts WHERE effect_id=$1 AND state_version=$2 "
            "AND outcome=$3",
            identity.effect_id,
            gate["state_version"],
            outcome,
        )
        if row is None:
            raise GoalApprovalsUnavailable("current_goal_receipt_missing")
        return stored_receipt(row, identity, outcome)

    async def _receipt(self, conn, gate, identity, outcome):
        value = owning_receipt(uuid4(), identity, outcome, gate["pending_action_id"])
        await conn.execute(
            "INSERT INTO approval_goal_receipts "
            "(receipt_id,effect_id,state_version,goal_id,owning_domain,action_id,capability,"
            "args_digest,outcome,pending_action_id,digest) "
            "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            # UUID conversion happens at this typed producer, not asyncpg's wire codec.
            UUID(value["receipt_id"]),
            identity.effect_id,
            gate["state_version"],
            identity.goal_id,
            self.domain,
            identity.action_id,
            identity.capability,
            identity.args_digest,
            outcome,
            gate["pending_action_id"],
            value["digest"],
        )
        return value

    async def park(self, request: ParkRequest) -> tuple[object, dict]:
        """Commit the actual action/delivery/gate and two stable receipt aliases.

        This is an internal producer, not a verifier of caller-asserted lineage.
        Standing-rule auto-approval is not supplied by this pending-only core;
        activation must not claim the broader producer coverage from it.
        """
        _validate_request(request)
        lineage = request.goal_lineage
        if lineage is None:
            raise ValueError("exact_goal_park_required")
        identity = CancelIdentity(
            lineage.goal_id,
            lineage.goal_effect_id,
            self.domain,
            request.action_id,
            lineage.goal_capability,
            lineage.goal_args_digest,
        )
        snapshot = parse_arguments(canonical_bytes(request.tool_args))
        if arguments_digest(snapshot) != identity.args_digest:
            raise ValueError("goal_argument_mismatch")
        from dataclasses import replace

        request = replace(request, tool_args=snapshot)
        async with self._transaction() as conn:
            try:
                gate = await self._gate(conn, identity)
            except ValueError as exc:
                if str(exc) != "goal_identity_mismatch":
                    raise
                raise ValueError("goal_duplicate_binding_mismatch") from None
            fresh = gate is None
            if fresh:
                gate = await self._gate(conn, identity, create=True, parked=True)
            if gate["stop_fenced"]:
                raise ValueError("goal_stop_fenced")
            if gate["dispatch_intent_id"] is not None or gate["phase"] not in {"absent", "pending"}:
                raise ValueError("goal_park_no_longer_pending")
            rollout = await conn.fetchrow(
                "SELECT admission_enabled,worker_enabled FROM approval_delivery_rollout "
                "WHERE singleton IS TRUE FOR SHARE"
            )
            admission = await (
                _admit(conn, request)
                if rollout["admission_enabled"]
                else _park_without_delivery(conn, request)
            )
            if admission.action_id != identity.action_id:
                raise ValueError("goal_park_reference_mismatch")
            if fresh:
                await self._receipt(conn, gate, identity, "pending")
                value = await self._receipt(conn, gate, identity, "parked")
            else:
                value = await self._read_receipt(conn, gate, identity, "parked")
        return admission, value

    async def lookup(self, identity: CancelIdentity) -> dict | None:
        """Return only current committed evidence. Absence never creates a gate."""
        async with self._transaction() as conn:
            gate = await self._gate(conn, identity)
            if gate is None or gate["dispatch_intent_id"] is not None:
                return None
            outcome = {"pending": "pending", "approved": "approved"}.get(gate["phase"])
            if gate["stop_fenced"]:
                outcome = "fenced_absent" if gate["pending_action_id"] is None else "cancelled"
            if outcome is None:
                return None
            value = await self._read_receipt(conn, gate, identity, outcome)
        return value

    async def transition(
        self,
        identity: CancelIdentity,
        *,
        target_status: ActionStatus,
        decided_by: str,
        event_actor: str,
        event_reason: str,
    ):
        """Owning decision core for an already authenticated owner operation.

        Actor strings are audit data, not authentication. Existing public owner
        routes must retain their own admission/displayed-call checks and enter
        this core before taking an action lock; this is no alternate endpoint.
        The real delivery lifecycle, stored arguments and expiry choose the
        decision. Its gate/receipt changes commit on that same connection.
        """
        from butlers.modules.approvals.delivery_lifecycle import (
            emit_lifecycle_success,
            transition_pending_action,
        )

        if target_status not in {
            ActionStatus.APPROVED,
            ActionStatus.REJECTED,
            ActionStatus.EXPIRED,
        }:
            raise ValueError("unsupported_goal_decision")
        async with self._transaction() as conn:
            gate = await self._gate(conn, identity)
            if gate is None or gate["stop_fenced"] or gate["dispatch_intent_id"] is not None:
                raise ValueError("goal_decision_fenced")
            result = await transition_pending_action(
                conn,
                action_id=identity.action_id,
                target_status=target_status,
                decided_by=decided_by,
                event_actor=event_actor,
                event_reason=event_reason,
                _already_in_transaction=True,
            )
            if result.changed:
                action = result.action
                gate = await conn.fetchrow(
                    "UPDATE approval_goal_effects SET phase=$2,approved_args_digest=$3,"
                    "state_version=state_version+1,updated_at=clock_timestamp() "
                    "WHERE effect_id=$1 RETURNING *",
                    identity.effect_id,
                    "approved" if action.status is ActionStatus.APPROVED else "negative_terminal",
                    action.goal_approved_args_digest,
                )
                if action.status is ActionStatus.APPROVED:
                    await self._receipt(conn, gate, identity, "approved")
        # Do not publish success observations before the actual transaction commits.
        emit_lifecycle_success(result.success_observation)
        return result

    async def cancel(self, identity: CancelIdentity) -> dict | None:
        """Commit no-ever-dispatch fencing; never turn a witness into undo proof."""
        async with self._transaction() as conn:
            gate = await self._gate(conn, identity)
            if gate is None:
                existing = await conn.fetchrow(
                    "SELECT * FROM pending_actions WHERE id=$1 FOR UPDATE", identity.action_id
                )
                if existing is not None:
                    raise ValueError("goal_unassociated_action")
                gate = await self._gate(conn, identity, create=True)
                return await self._receipt(conn, gate, identity, "fenced_absent")
            if gate["dispatch_intent_id"] is not None:
                return None
            if gate["stop_fenced"]:
                return await self._read_receipt(
                    conn,
                    gate,
                    identity,
                    "fenced_absent" if gate["pending_action_id"] is None else "cancelled",
                )
            row = await conn.fetchrow(
                "SELECT * FROM pending_actions WHERE id=$1 FOR UPDATE",
                identity.action_id,
            )
            if row is None and gate["pending_action_id"] is not None:
                raise GoalApprovalsUnavailable("goal_action_missing")
            if row is not None:
                action = PendingAction.from_row(row)
                lineage = action.goal_lineage()
                if (
                    lineage is None
                    or lineage.goal_id != identity.goal_id
                    or (
                        lineage.goal_effect_id != identity.effect_id
                        or lineage.goal_args_digest != identity.args_digest
                        or lineage.goal_capability != identity.capability
                    )
                ):
                    raise ValueError("goal_identity_mismatch")
                if action.status not in {
                    ActionStatus.PENDING,
                    ActionStatus.APPROVED,
                    ActionStatus.REJECTED,
                    ActionStatus.EXPIRED,
                    ActionStatus.ABANDONED,
                    ActionStatus.CANCELLED,
                }:
                    return None
            gate = await conn.fetchrow(
                "UPDATE approval_goal_effects SET stop_fenced=true,phase=$2,"
                "state_version=state_version+1,updated_at=clock_timestamp() "
                "WHERE effect_id=$1 RETURNING *",
                identity.effect_id,
                "absent" if row is None else "cancelled",
            )
            if row is not None:
                await self._cancel_action(conn, action)
            value = await self._receipt(
                conn, gate, identity, "fenced_absent" if row is None else "cancelled"
            )
        return value

    async def _cancel_action(self, conn, action):
        from butlers.modules.approvals.delivery_lifecycle import (
            _cancel_empty_cohort_before_handoff,
            _lock_delivery_root,
            _mark_cohort_membership_ineligible,
        )
        from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

        # Negative terminal decisions retain their status/history. A Stop fence
        # is a new no-forward fact, never a rewrite to manufacture cancellation.
        if action.status in {ActionStatus.PENDING, ActionStatus.APPROVED}:
            await conn.execute(
                "UPDATE pending_actions SET status='cancelled',decided_by='system:goal_stop',"
                "decided_at=clock_timestamp() WHERE id=$1",
                action.id,
            )
        intent = await _lock_delivery_root(conn, action.id)
        if intent is not None:
            await conn.execute(
                "UPDATE approval_delivery_presentations SET state='cancelled',"
                "last_reason_code='action_cancelled',next_attempt_at=NULL,claim_token=NULL,"
                "claim_expires_at=NULL,updated_at=clock_timestamp() WHERE intent_id=$1 "
                "AND state IN ('ready','claimed','handoff_started','retry_wait')",
                intent["id"],
            )
            cohort, _ = await _mark_cohort_membership_ineligible(conn, intent_id=intent["id"])
            if cohort is not None:
                await _cancel_empty_cohort_before_handoff(conn, cohort)
        await record_approval_event(
            conn,
            ApprovalEventType.ACTION_CANCELLED,
            actor="system:goal_stop",
            action_id=action.id,
            reason="goal_stopped",
            metadata={"historical_status": action.status.value},
        )
