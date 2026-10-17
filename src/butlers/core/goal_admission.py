"""RFC0038 deterministic admission, deliberately unwired from every live caller.

Only trusted infrastructure implements EvidenceProvider. This is not a request
model, MCP capability, authentication adapter, runner or approval endpoint.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

import asyncpg
from opentelemetry import trace

from butlers.core.runtime_config import read_goal_admission_config

Origin = Literal["owner", "obligation", "third_party", "staffer"]
SourceKind = Literal["message", "occurrence", "feed", "domain", "staffer"]


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class EvidenceRefused(ValueError):
    """A fixed, content-blind admission reason, safe to persist."""


class AdmissionUnavailable(RuntimeError):
    """No durable decision can be asserted, including commit uncertainty."""


@dataclass(frozen=True)
class Grant:
    capability: str
    resources: tuple[tuple[str, tuple[str, ...]], ...] = ()
    park_only: bool = False

    def __post_init__(self):
        if (
            type(self.park_only) is not bool
            or type(self.resources) is not tuple
            or not self.capability
            or any(c in self.capability for c in "*?[]")
        ):
            raise ValueError("capability IDs must be exact")
        if any(
            type(item) is not tuple or len(item) != 2 or type(item[1]) is not tuple
            for item in self.resources
        ):
            raise ValueError("resource constraints must be immutable exact sets")
        keys = [key for key, _ in self.resources]
        if len(keys) != len(set(keys)) or any(
            not key or not values for key, values in self.resources
        ):
            raise ValueError("resource constraints must be unique nonempty exact sets")
        if any(
            not isinstance(v, str) or not v or any(c in v for c in "*?[]")
            for _, values in self.resources
            for v in values
        ):
            raise ValueError("resource values must be exact identifiers")
        if any(len(set(values)) != len(values) for _, values in self.resources):
            raise ValueError("resource sets must not repeat identifiers")
        object.__setattr__(
            self,
            "resources",
            tuple(sorted((key, tuple(sorted(values))) for key, values in self.resources)),
        )


def encode_grants(grants: tuple[Grant, ...]) -> list[dict]:
    return [
        {
            "capability": g.capability,
            "resources": {k: list(v) for k, v in g.resources},
            "park_only": g.park_only,
        }
        for g in sorted(grants, key=lambda g: g.capability)
    ]


def decode_grants(values: list[dict]) -> tuple[Grant, ...]:
    return tuple(
        Grant(
            v["capability"],
            tuple((k, tuple(ids)) for k, ids in sorted(v["resources"].items())),
            v["park_only"],
        )
        for v in values
    )


def intersect(left: tuple[Grant, ...], right: tuple[Grant, ...]) -> tuple[Grant, ...]:
    """Missing resource restrictions mean unrestricted; intersection only narrows."""
    out = []
    by_name = {g.capability: g for g in right}
    for requested in left:
        parent = by_name.get(requested.capability)
        if parent is None:
            continue
        a, b = dict(requested.resources), dict(parent.resources)
        resources = []
        for name in sorted(a.keys() | b.keys()):
            allowed = (
                set(a[name]) & set(b[name])
                if name in a and name in b
                else set(a.get(name, b.get(name, ())))
            )
            if not allowed:
                break
            resources.append((name, tuple(sorted(allowed))))
        else:
            out.append(
                Grant(
                    requested.capability, tuple(resources), requested.park_only or parent.park_only
                )
            )
    return tuple(sorted(out, key=lambda g: g.capability))


@dataclass(frozen=True)
class Capability:
    id: str
    owner_domain: str
    effect_class: Literal["read", "record", "communicate", "transact", "actuate", "administer"]
    owner_authority: bool = False
    owner_default: bool = False
    presentable: bool = True

    def __post_init__(self):
        if (
            not self.id
            or not self.owner_domain
            or self.effect_class
            not in {"read", "record", "communicate", "transact", "actuate", "administer"}
            or any(
                type(v) is not bool
                for v in (self.owner_authority, self.owner_default, self.presentable)
            )
        ):
            raise EvidenceRefused("catalog_unavailable")
        Grant(self.id)


@dataclass(frozen=True)
class CatalogView:
    """Pinned immutable view; actual final-registry publication is a later owner."""

    digest: str
    capabilities: tuple[Capability, ...]

    def __post_init__(self):
        if (
            not self.digest
            or type(self.capabilities) is not tuple
            or len({c.id for c in self.capabilities}) != len(self.capabilities)
        ):
            raise EvidenceRefused("catalog_unavailable")


class CatalogProvider(Protocol):
    async def load(self, connection: asyncpg.Connection) -> CatalogView:
        """Return a verified current immutable publication or raise EvidenceRefused.

        The publisher owns completeness, exclusions and freshness verification.
        This dark slice provides no production catalog publisher/adapter.
        """
        ...


@dataclass(frozen=True)
class StafferContract:
    staffer: str
    owner_domain: str
    digest: str
    capabilities: tuple[Grant, ...]
    catalog_digest: str
    version: int = 1


@dataclass(frozen=True)
class Obligation:
    owner_domain: str
    digest: str
    capabilities: tuple[Grant, ...] | None = None
    creator_invocation: UUID | None = None
    created_exposed: bool | None = None


@dataclass(frozen=True)
class SourceRef:
    kind: SourceKind
    id: str
    occurrence: str | None = None

    def __post_init__(self):
        if self.kind not in {"message", "occurrence", "feed", "domain", "staffer"}:
            raise ValueError("invalid source kind")
        if (
            not isinstance(self.id, str)
            or not self.id
            or len(self.id) > 256
            or (self.kind == "occurrence" and not self.occurrence)
            or (self.kind != "occurrence" and self.occurrence is not None)
        ):
            raise ValueError("durable source identity required")

    @property
    def key(self) -> str:
        return digest([self.kind, self.id, self.occurrence])


@dataclass(frozen=True)
class Evidence:
    """Verified source-reader output, never deserialized from a model/envelope body.

    owner means RFC0017 channel authorization or authenticated dashboard owner,
    not sender text. untainted_owner_stamp must be established by the publishing
    domain's real stamp verifier. Unavailable lookup raises EvidenceRefused.
    """

    kind: SourceKind
    source_partition: str
    owner: bool = False
    forwarded: bool = False
    outside_content: bool = False
    untainted_owner_stamp: bool | None = None
    obligation: Obligation | None = None
    staffer: StafferContract | None = None
    caused_by_goal: UUID | None = None
    caused_by_effect: UUID | None = None
    reply_delivery: str | None = None
    owner_entity: UUID | None = None
    owner_channel: str | None = None

    def __post_init__(self):
        if (
            self.kind not in {"message", "occurrence", "feed", "domain", "staffer"}
            or (self.kind != "message" and self.owner)
            or any(type(v) is not bool for v in (self.owner, self.forwarded, self.outside_content))
            or (
                self.untainted_owner_stamp is not None
                and type(self.untainted_owner_stamp) is not bool
            )
        ):
            raise EvidenceRefused("source_context_invalid")


class EvidenceProvider(Protocol):
    async def load(self, connection: asyncpg.Connection, source: SourceRef) -> Evidence:
        """Load verified source context; no public/model adapter is provided here."""
        ...


@dataclass(frozen=True)
class AdmissionPlan:
    origin: Origin
    exposed: bool
    ceiling: tuple[Grant, ...]
    contract_digest: str | None = None


def derive_admission(evidence: Evidence, catalog: CatalogView) -> AdmissionPlan:
    eligible = {
        c.id: c for c in catalog.capabilities if c.presentable and c.effect_class != "transact"
    }

    def checked(grants):
        if len({g.capability for g in grants}) != len(grants) or any(
            g.capability not in eligible for g in grants
        ):
            raise EvidenceRefused("invalid_contract_capabilities")
        return tuple(sorted(grants, key=lambda g: g.capability))

    if evidence.kind == "message":
        if evidence.owner:
            return AdmissionPlan(
                "owner",
                evidence.forwarded or evidence.outside_content,
                tuple(Grant(c) for c in sorted(eligible)),
            )
        grants = tuple(
            Grant(c.id, park_only=c.effect_class == "communicate" and not c.owner_default)
            for c in eligible.values()
            if c.effect_class == "read"
            or (c.effect_class == "record" and not c.owner_authority)
            or c.effect_class == "communicate"
        )
        return AdmissionPlan("third_party", True, tuple(sorted(grants, key=lambda g: g.capability)))
    if evidence.kind == "staffer":
        contract = evidence.staffer
        if (
            not contract
            or type(contract.version) is not int
            or contract.version != 1
            or not contract.digest
            or not contract.staffer
            or not contract.owner_domain
            or contract.catalog_digest != catalog.digest
        ):
            raise EvidenceRefused("staffer_contract_unavailable")
        return AdmissionPlan(
            "staffer",
            contract.staffer == "qa" or evidence.outside_content,
            checked(contract.capabilities),
            contract.digest,
        )
    obligation = evidence.obligation
    if not obligation or not obligation.digest or not obligation.owner_domain:
        raise EvidenceRefused("declared_subscription_required")
    grants = obligation.capabilities
    if grants is None:
        grants = tuple(
            Grant(c.id)
            for c in eligible.values()
            if (c.owner_domain == obligation.owner_domain and c.effect_class in {"read", "record"})
            or (c.effect_class == "communicate" and c.owner_default)
        )
    exposed = obligation.created_exposed is not False or evidence.outside_content
    if evidence.kind in {"domain", "feed"}:
        exposed |= evidence.untainted_owner_stamp is not True
    return AdmissionPlan("obligation", exposed, checked(grants), obligation.digest)


@dataclass(frozen=True)
class AdmissionResult:
    decision_id: UUID
    goal_id: UUID | None
    result: str
    reason: str


class GoalAdmission:
    def __init__(self, pool: asyncpg.Pool, evidence: EvidenceProvider, catalog: CatalogProvider):
        self.pool, self.evidence, self.catalog = pool, evidence, catalog

    async def admit_submission(self, source: SourceRef, attempt_id: UUID) -> AdmissionResult:
        """Atomically return a durable admission; commit uncertainty raises unavailable."""
        # Do not automatically record exception text/stack from source readers or
        # database drivers. The span contains only identifiers and fixed outcomes.
        with trace.get_tracer(__name__).start_as_current_span(
            "goal.admit_submission",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("source_kind", source.kind)
            span.set_attribute("source_key_digest", source.key)
            result = await self._transaction(source, attempt_id)
            span.set_attribute("decision_id", str(result.decision_id))
            span.set_attribute("result", result.result)
            span.set_attribute("reason", result.reason)
            if result.goal_id is not None:
                span.set_attribute("goal_id", str(result.goal_id))
            return result

    async def _transaction(self, source: SourceRef, attempt_id: UUID) -> AdmissionResult:
        from butlers.core.goal_ledger import GoalLedger

        try:
            async with self.pool.acquire() as connection, connection.transaction():
                ledger = GoalLedger(connection)
                await ledger.verify_role()
                await ledger.lock_keys(["attempt:" + str(attempt_id), "source:" + source.key])
                old = await ledger.existing(source.key, attempt_id)
                if old:
                    return old
                now = await connection.fetchval("SELECT clock_timestamp()")
                try:
                    facts = await self.evidence.load(connection, source)
                    if (
                        facts.kind != source.kind
                        or not facts.source_partition
                        or len(facts.source_partition) > 256
                    ):
                        raise EvidenceRefused("source_context_invalid")
                    catalog = await self.catalog.load(connection)
                    plan = derive_admission(facts, catalog)
                except EvidenceRefused:
                    return await ledger.decide(
                        source, attempt_id, "refused", "source_evidence_unavailable"
                    )
                # SQL failures propagate: a failed transaction cannot persist a refusal.
                try:
                    config = await read_goal_admission_config(connection)
                except ValueError:
                    return await ledger.decide(source, attempt_id, "refused", "limits_unavailable")
                config_data = config.model_dump(mode="json")
                config_digest = digest(config_data)
                limits = next(entry for entry in config.origins if entry.origin == plan.origin)
                window = datetime.fromtimestamp(
                    int(now.timestamp()) // limits.window_seconds * limits.window_seconds, UTC
                )
                retry_after = window + timedelta(seconds=limits.window_seconds)
                await ledger.context(config_digest, catalog.digest, facts, window)
                return await self._admit(
                    ledger, source, attempt_id, facts, plan, config, limits, now, retry_after
                )
        except AdmissionUnavailable:
            raise
        except Exception as exc:
            # Never label an unconfirmed commit or failed decision persistence as success.
            raise AdmissionUnavailable(
                "admission transaction unavailable; retry durable source identity"
            ) from exc

    async def _admit(
        self, ledger, source, attempt_id, facts, plan, config, limits, now, retry_after
    ):
        cause_ids = (facts.caused_by_goal, facts.caused_by_effect)
        if (cause_ids[0] is None) != (cause_ids[1] is None):
            return await ledger.decide(source, attempt_id, "refused", "cause_invalid")
        if facts.caused_by_effect:
            hint = await ledger.effect_hint(facts.caused_by_effect)
            if not hint or hint["goal_id"] != facts.caused_by_goal:
                return await ledger.decide(source, attempt_id, "refused", "cause_invalid")
        reply = (
            await ledger.reply_hint(facts.reply_delivery)
            if facts.owner and facts.reply_delivery
            else None
        )
        goal_ids = {g for g in (facts.caused_by_goal, reply["goal_id"] if reply else None) if g}
        creator = None
        if facts.obligation and facts.obligation.creator_invocation:
            creator = await ledger.creator_hint(facts.obligation.creator_invocation)
            if not creator:
                return await ledger.decide(source, attempt_id, "refused", "creator_unavailable")
            goal_ids.add(creator["goal_id"])
        goals = await ledger.lock_goals(goal_ids)
        ceiling, exposed, chain_depth = plan.ceiling, plan.exposed, 0
        if creator:
            ceiling = intersect(ceiling, decode_grants(creator["grant_snapshot"]))
            # Created-under-exposure is a source-owned immutable obligation stamp.
            exposed |= facts.obligation.created_exposed is not False
        if facts.caused_by_goal:
            cause = goals.get(facts.caused_by_goal)
            effect = await ledger.effect(facts.caused_by_effect)
            if not cause or not effect or effect["goal_id"] != cause["id"] or not facts.obligation:
                return await ledger.decide(source, attempt_id, "refused", "cause_invalid")
            ledger.valid_cause = (cause["id"], effect["id"])
            chain_depth = cause["chain_depth"] + 1
            if chain_depth > config.chain_limit:
                return await ledger.decide(source, attempt_id, "refused", "causal_chain_limit")
            exposed |= cause["exposed"]
            ceiling = intersect(ceiling, decode_grants(cause["ceiling"]))
        if reply:
            binding = await ledger.effect(reply["id"])
            goal = goals[reply["goal_id"]]
            # Recheck expiry after lock waits, retaining the captured quota window.
            prompt_now = await ledger.connection.fetchval("SELECT clock_timestamp()")
            matches = bool(
                binding
                and binding["delivery_ref"] == facts.reply_delivery
                and binding["recipient_entity"] == facts.owner_entity
                and binding["recipient_channel"] == facts.owner_channel
                and facts.owner_entity is not None
                and facts.owner_channel
            )
            ledger.reply_effect = binding["id"] if matches else None
            if (
                matches
                and binding["prompt_accepts_input"]
                and binding["prompt_expires_at"] is not None
                and binding["prompt_expires_at"] > prompt_now
                and goal["status"]
                in {"admitted", "running", "settling", "awaiting_approval", "recovering"}
            ):
                await ledger.increase_exposure(goal["id"], exposed)
                return await ledger.decide(source, attempt_id, "joined", "reply_joined", goal["id"])
        await ledger.lock_keys(
            [
                "quota:origin:" + plan.origin + ":" + ledger.window.isoformat(),
                "quota:source:"
                + plan.origin
                + ":"
                + facts.source_partition
                + ":"
                + ledger.window.isoformat(),
            ]
        )
        origin_count, source_count = await ledger.quota_counts(plan.origin, facts.source_partition)
        if origin_count >= limits.new_goals or source_count >= limits.per_source:
            return await ledger.decide(
                source, attempt_id, "refused", "quota_exhausted", retry_after=retry_after
            )
        goal_id = uuid4()
        await ledger.create_goal(
            goal_id,
            source,
            plan.origin,
            exposed,
            ceiling,
            plan.contract_digest,
            facts,
            chain_depth,
            limits.budget,
            now,
        )
        return await ledger.decide(source, attempt_id, "admitted", "new_goal", goal_id)
