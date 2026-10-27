"""Qualified SQL for the unwired goal-admission transaction; no runtime owner."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import uuid4

from butlers.core.goal_admission import (
    AdmissionResult,
    AdmissionUnavailable,
    digest,
    encode_grants,
)


class GoalLedger:
    def __init__(self, connection):
        self.connection = connection
        self.config_digest = self.catalog_digest = self.facts = self.window = None
        self.origin = None
        self.valid_cause = (None, None)
        self.reply_effect = None

    async def verify_role(self):
        # Check the actual connection, not a caller-supplied role/verified flag.
        role = await self.connection.fetchrow(
            "SELECT current_user AS name, rolsuper, rolbypassrls FROM pg_roles "
            "WHERE rolname=current_user"
        )
        if role["name"] != "butler_switchboard_rw" or role["rolsuper"] or role["rolbypassrls"]:
            raise AdmissionUnavailable("verified Switchboard runtime role required")
        if await self.connection.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='switchboard' AND c.relname=ANY($1::text[]) "
            "AND pg_has_role(current_user,c.relowner,'MEMBER'))",
            [
                "goals",
                "invocations",
                "effects",
                "budgets",
                "admission_decisions",
                "goal_inbox",
                "admission_attempt_bindings",
            ],
        ):
            raise AdmissionUnavailable("runtime must not own or assume the ledger owner")

    async def lock_keys(self, keys):
        for key in sorted(set(keys)):
            lock_id = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], signed=True)
            await self.connection.execute("SELECT pg_advisory_xact_lock($1::bigint)", lock_id)

    async def existing(self, source_key, attempt_id):
        row = await self.connection.fetchrow(
            "SELECT d.* FROM switchboard.admission_attempt_bindings a "
            "JOIN switchboard.admission_decisions d "
            "ON (d.id,d.source_key)=(a.decision_id,a.source_key) WHERE a.attempt_id=$1",
            attempt_id,
        )
        if not row:
            row = await self.connection.fetchrow(
                "SELECT * FROM switchboard.admission_decisions WHERE attempt_id=$1", attempt_id
            )
        if row and row["source_key"] != source_key:
            raise AdmissionUnavailable("attempt identity belongs to another durable source")
        if not row:
            row = await self.connection.fetchrow(
                "SELECT * FROM switchboard.admission_decisions WHERE source_key=$1 "
                "AND (result IN ('admitted','joined') OR "
                "(result='refused' AND reason='recorded_without_goal'))",
                source_key,
            )
        if row:
            # The caller holds both attempt and source transaction locks. Bind
            # every returned attempt before commit, including source-deduped
            # retries which reuse a different attempt's immutable decision.
            await self.bind_attempt(attempt_id, source_key, row["id"])
            return AdmissionResult(row["id"], row["goal_id"], row["result"], row["reason"])
        return None

    async def bind_attempt(self, attempt_id, source_key, decision_id):
        await self.connection.execute(
            "INSERT INTO switchboard.admission_attempt_bindings "
            "(attempt_id,source_key,decision_id) VALUES ($1,$2,$3) "
            "ON CONFLICT (attempt_id) DO NOTHING",
            attempt_id,
            source_key,
            decision_id,
        )

    async def context(self, config_digest, catalog_digest, facts, window):
        self.config_digest, self.catalog_digest = config_digest, catalog_digest
        self.facts, self.window = facts, window

    async def lock_goals(self, ids):
        rows = await self.connection.fetch(
            "SELECT * FROM switchboard.goals WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",
            sorted(ids),
        )
        return {row["id"]: row for row in rows}

    async def creator_hint(self, id):
        # Grant/goal association is protected by immutable invocation lineage.
        return await self.connection.fetchrow(
            "SELECT * FROM switchboard.invocations WHERE id=$1", id
        )

    async def reply_hint(self, delivery_ref):
        return await self.connection.fetchrow(
            "SELECT id,goal_id FROM switchboard.effects WHERE delivery_ref=$1", delivery_ref
        )

    async def effect_hint(self, id):
        return await self.connection.fetchrow(
            "SELECT id,goal_id FROM switchboard.effects WHERE id=$1", id
        )

    async def effect(self, id):
        return await self.connection.fetchrow(
            "SELECT * FROM switchboard.effects WHERE id=$1 FOR UPDATE", id
        )

    async def increase_exposure(self, goal_id, exposed):
        await self.connection.execute(
            "UPDATE switchboard.goals SET exposed=true, updated_at=now() "
            "WHERE id=$1 AND $2 AND NOT exposed",
            goal_id,
            exposed,
        )

    async def deliver(self, decision, source):
        # A joined reply reaches its goal only through this inbox row; the
        # unique source key keeps one delivery per incoming envelope.
        await self.connection.execute(
            "INSERT INTO switchboard.goal_inbox "
            "(id,goal_id,decision_id,source_key,source_ref,delivery_ref,reply_effect_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7)",
            uuid4(),
            decision.goal_id,
            decision.decision_id,
            source.key,
            source.id,
            self.facts.reply_delivery,
            self.reply_effect,
        )

    async def quota_counts(self, origin, source):
        self.origin = origin
        row = await self.connection.fetchrow(
            "SELECT count(*) AS origin_count, "
            "count(*) FILTER (WHERE source_partition=$3) AS source_count "
            "FROM switchboard.admission_decisions WHERE result='admitted' "
            "AND origin=$1 AND quota_window=$2",
            origin,
            self.window,
            source,
        )
        return row["origin_count"], row["source_count"]

    async def create_goal(
        self,
        goal_id,
        source,
        origin,
        exposed,
        ceiling,
        contract_digest,
        facts,
        chain_depth,
        budget,
        now,
    ):
        grants = encode_grants(ceiling)
        await self.connection.execute(
            "INSERT INTO switchboard.goals "
            "(id,source_key,source_kind,source_ref,occurrence_key,origin,"
            "admitted_exposed,exposed,ceiling,ceiling_digest,catalog_digest,contract_digest,config_digest,"
            "caused_by_goal,caused_by_effect,chain_depth) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$7,$8::jsonb,$9,$10,$11,$12,$13,$14,$15) "
            "ON CONFLICT (source_key) DO NOTHING",
            goal_id,
            source.key,
            source.kind,
            source.id,
            source.occurrence,
            origin,
            exposed,
            grants,
            digest(grants),
            self.catalog_digest,
            contract_digest,
            self.config_digest,
            facts.caused_by_goal,
            facts.caused_by_effect,
            chain_depth,
        )
        # The unique source key plus transaction lock is final race authority;
        # an unexpected conflict cannot silently allocate a second budget.
        await self.connection.execute(
            "INSERT INTO switchboard.budgets "
            "(goal_id,max_invocations,max_children,max_calls,max_tokens,"
            "max_spend,deadline,config_snapshot) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            goal_id,
            budget.max_invocations,
            budget.max_children,
            budget.max_calls,
            budget.max_tokens,
            budget.max_spend,
            now + timedelta(seconds=budget.deadline_seconds),
            budget.model_dump(mode="json"),
        )

    async def decide(self, source, attempt_id, result, reason, goal_id=None, retry_after=None):
        id = uuid4()
        facts = self.facts
        # Invalid/missing cause is recorded as a fixed refusal, not an invalid
        # FK that aborts the refusal transaction. The source reference preserves
        # the trusted evidence lookup; no forged association is persisted.
        await self.connection.execute(
            "INSERT INTO switchboard.admission_decisions "
            "(id,attempt_id,source_key,source_ref,source_kind,"
            "goal_id,result,reason,origin,source_partition,quota_window,retry_after,delivery_ref,"
            "catalog_digest,config_digest,caused_by_goal,caused_by_effect,"
            "occurrence_key,reply_effect_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)",
            id,
            attempt_id,
            source.key,
            source.id,
            source.kind,
            goal_id,
            result,
            reason,
            self.origin,
            facts.source_partition if facts else None,
            self.window,
            retry_after,
            facts.reply_delivery if facts else None,
            self.catalog_digest,
            self.config_digest,
            *self.valid_cause,
            source.occurrence,
            self.reply_effect,
        )
        await self.bind_attempt(attempt_id, source.key, id)
        return AdmissionResult(id, goal_id, result, reason)
