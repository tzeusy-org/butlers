"""Value types at the broker's trusted boundaries (RFC 0038 D4, D7, D11).

Nothing here is a request model. A model reaches the broker only with a bearer
token, a tool name and raw arguments; principal, capability metadata, recipient
classification and approval authority all come from the ledger, the published
catalog and the owning domain.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from butlers.capability_health.unavailable import CapabilityUnavailable
from butlers.core.goal_admission import Grant
from butlers.core.goal_labels import CATEGORIES, Label
from butlers.core.owner_policy import PolicyHold
from butlers.core.tool_catalog import RecipientParameters, declared_approval_mode

Origin = Literal["owner", "obligation", "third_party", "staffer"]
EffectClass = Literal["read", "record", "communicate", "transact", "actuate", "administer"]
Recipient = Literal["none", "owner", "non_owner"]
ActuationRisk = Literal["safe", "consequential", "protected", "unknown"]


class Reason(StrEnum):
    """Closed, content-blind refusal reasons returned to the session."""

    AUTHENTICATION = "authentication_refused"
    GOAL = "goal_not_running"
    EPOCH = "lead_epoch_revoked"
    CAPABILITY = "capability_not_granted"
    CATALOG = "catalog_changed"
    RESOURCE = "resource_outside_grant"
    POLICY = "policy_refused"
    # An owner always_ask term on a read: a read cannot park yet, so it refuses.
    ASK_ON_READ = "owner_policy_ask_on_read"
    BUDGET = "budget_exhausted"
    DUPLICATE = "effect_already_recorded"
    UNAVAILABLE = "broker_unavailable"
    # RFC 0038 S6: a dependency the capability needs is known down, unconfigured
    # or held. Only ever raised from published health; it never grants or hides.
    CAPABILITY_UNAVAILABLE = "capability_unavailable"


class BrokerRefused(RuntimeError):
    """A refusal; never carries a token, arguments or a downstream error."""

    def __init__(
        self,
        reason: Reason,
        *,
        effect_id: UUID | None = None,
        hold: PolicyHold | None = None,
        unavailable: CapabilityUnavailable | None = None,
    ):
        super().__init__(reason.value)
        self.reason = reason
        self.effect_id = effect_id
        # The typed S6 result the session sees alongside the closed reason.
        self.unavailable = unavailable
        # The owner-law terms that refused it (S1 typed receipt); never sent
        # to the session, which sees only the closed reason.
        self.hold = hold


_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def token_hash(token: str) -> str:
    """SHA-256 of exactly 256 decoded bits; any other spelling is refused."""
    if type(token) is not str or not _TOKEN.fullmatch(token):
        raise BrokerRefused(Reason.AUTHENTICATION)
    raw = base64.urlsafe_b64decode(token + "=")
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != token:
        raise BrokerRefused(Reason.AUTHENTICATION)
    return hashlib.sha256(raw).hexdigest()


def mint_token() -> tuple[str, str]:
    """Return (plaintext, hash). Only the hash is ever persisted."""
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    return token, token_hash(token)


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated invocation, read from the ledger under the goal lock."""

    goal_id: UUID
    invocation_id: UUID
    depth: Literal[0, 1]
    origin: Origin
    exposed: bool
    catalog_digest: str
    grants: tuple[Grant, ...]
    # The invocation's S1 label as read under the goal lock.
    label: Label
    # Why the invocation first became exposed (closed S1 enum), read under the goal lock.
    # None: unexposed, or a row that never recorded one.
    exposure_cause: str | None = None

    def grant_for(self, capability: str) -> Grant | None:
        return next((g for g in self.grants if g.capability == capability), None)


def declared_categories(descriptor: Any) -> frozenset[str]:
    """The S1 categories a descriptor's output may carry, exactly as declared.

    Missing metadata is ungrantable (S1): an undeclared, unknown, duplicated or
    non-tuple declaration raises rather than defaulting, so a descriptor never
    reaches the broker on an inferred category set. D4 ``output_sensitivity``
    is not consulted.
    """
    declared = getattr(descriptor, "categories", None)
    if (
        not isinstance(declared, tuple)
        or any(not isinstance(item, str) or item not in CATEGORIES for item in declared)
        or len(declared) != len(set(declared))
    ):
        raise ValueError("descriptor categories are missing or unknown")
    return frozenset(declared)


@dataclass(frozen=True, slots=True)
class Capability:
    """The broker's projection of one published, grantable D4 descriptor."""

    domain: str
    name: str
    effect_class: EffectClass
    output_trust: Literal["trusted", "untrusted", "stamped"]
    recipient_params: RecipientParameters
    resource_params: tuple[str, ...] = ()
    selection_bound: frozenset[str] = frozenset()
    # Resource params whose identifiers have no alias; only these can narrow
    # an owner-law term (any other narrowing selects every call).
    alias_free_params: frozenset[str] = frozenset()
    # Aliasable resource params the domain resolves before owner law, as
    # sorted (param, kind) pairs (``aliases``); they narrow on canonical ids.
    alias_resolvers: tuple[tuple[str, str], ...] = ()
    safety_args: tuple[str, ...] = ()
    owner_authority: bool = False
    always_per_event: bool = False
    # The owning butler runs an enabled approvals module, so it can park.
    approvals_enabled: bool = False
    # ...and has an approvals gate configured for this tool (approval policy).
    approval_gate: bool = False
    idempotency: Literal["none", "key_required"] = "none"
    # S1 sensitivity categories its output may carry; a test double that
    # omits them is all six. Real descriptors must declare them.
    categories: frozenset[str] = CATEGORIES
    # RFC 0038 S6 declared dependency IDs; health only ever refuses on these.
    depends_on: tuple[str, ...] = ()
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"}, repr=False)

    @property
    def alias_resolved(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.alias_resolvers)

    @property
    def identity(self) -> str:
        """Namespaced ``<domain>__<tool>``: the grant key and the MCP tool name."""
        return f"{self.domain}__{self.name}"

    @classmethod
    def from_descriptor(cls, domain: str, descriptor: Any, input_schema: dict) -> Capability:
        policy = descriptor.approval_policy or {}
        return cls(
            domain=domain,
            name=descriptor.canonical_name,
            effect_class=descriptor.effect_class,
            output_trust=descriptor.output_trust,
            recipient_params=descriptor.recipient_params,
            resource_params=tuple(descriptor.resource_params or ()),
            selection_bound=frozenset(
                name for name, bound in (descriptor.selection_bound or {}).items() if bound
            ),
            alias_free_params=frozenset(getattr(descriptor, "alias_free_params", None) or ()),
            alias_resolvers=tuple(
                sorted(dict(getattr(descriptor, "alias_resolvers", None) or {}).items())
            ),
            safety_args=tuple(descriptor.safety_args or ()),
            owner_authority=bool(descriptor.owner_authority),
            always_per_event=declared_approval_mode(policy) == "always_per_event",
            approvals_enabled=policy.get("enabled") is True,
            approval_gate=policy.get("enabled") is True and policy.get("configured_gate") is True,
            idempotency=descriptor.idempotency,
            categories=declared_categories(descriptor),
            depends_on=tuple(getattr(descriptor, "depends_on", None) or ()),
            description=descriptor.description,
            input_schema=input_schema,
        )


@dataclass(frozen=True, slots=True)
class Catalog:
    """Current published capabilities and the fleet digest they came from."""

    digest: str
    capabilities: tuple[Capability, ...]

    def get(self, identity: str) -> Capability | None:
        return next((c for c in self.capabilities if c.identity == identity), None)


class Disposition(StrEnum):
    DISPATCH = "dispatch"
    PARK = "park"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    # Whether the owning approvals module may match a standing rule at park
    # time. False for park-only grants, always_per_event tools and exposed
    # calls the exposure limits exclude; never authority to skip a park.
    allow_standing_rule: bool = False


@dataclass(frozen=True, slots=True)
class Reservation:
    """A call that passed the per-call transaction and committed its effect row."""

    principal: Principal
    capability: Capability
    decision: Decision
    call_id: UUID
    effect_id: UUID | None
    args_digest: str
    recipient: Recipient = "none"


@dataclass(frozen=True, slots=True)
class BrokerResult:
    status: Literal["returned", "parked", "committed", "failed", "unknown", "refused"]
    effect_id: UUID | None = None
    approval_ref: str | None = None
    value: object = field(default=None, repr=False)
