"""Explicit trusted boundaries for the dark RFC0038 broker.

These values are infrastructure inputs, never request models. In particular a
model cannot supply recipient, standing-rule, selection or approval authority.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import UUID

from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import RecipientParameters


class Reason(StrEnum):
    AUTHENTICATION = "authentication_refused"
    GOAL = "goal_not_running"
    EPOCH = "lead_epoch_revoked"
    CAPABILITY = "capability_not_granted"
    CATALOG = "catalog_unavailable_or_changed"
    RESOURCE = "resource_outside_grant"
    SELECTION = "selection_unavailable_or_revoked"
    POLICY = "policy_refused"
    BUDGET = "budget_exhausted"
    DUPLICATE = "effect_already_recorded"
    UNAVAILABLE = "broker_unavailable"


class BrokerRefused(RuntimeError):
    """Content-blind reason; never embed token, arguments or downstream errors."""

    def __init__(self, reason: Reason, *, effect_id: UUID | None = None):
        super().__init__(reason.value)
        self.reason = reason
        self.effect_id = effect_id


def token_hash(token: str) -> str:
    """Hash exactly 256 decoded bits; reject padding/alternate spellings."""
    if type(token) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise BrokerRefused(Reason.AUTHENTICATION)
    raw = base64.urlsafe_b64decode(token + "=")
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != token:
        raise BrokerRefused(Reason.AUTHENTICATION)
    return hashlib.sha256(raw).hexdigest()


def mint_token() -> tuple[str, str]:
    """Runner receives plaintext once; persistence receives only the hash."""
    token = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    return token, token_hash(token)


@dataclass(frozen=True, slots=True)
class Principal:
    goal_id: UUID
    invocation_id: UUID
    depth: Literal[0, 1]
    origin: Literal["owner", "obligation", "third_party", "staffer"]
    exposed: bool
    catalog_digest: str
    grants: tuple[Grant, ...]

    def __post_init__(self):
        if (
            not all(isinstance(v, UUID) for v in (self.goal_id, self.invocation_id))
            or type(self.depth) is not int
            or self.depth not in (0, 1)
            or type(self.origin) is not str
            or self.origin not in {"owner", "obligation", "third_party", "staffer"}
            or type(self.exposed) is not bool
            or type(self.catalog_digest) is not str
            or not self.catalog_digest
            or type(self.grants) is not tuple
            or any(not isinstance(g, Grant) for g in self.grants)
            or len({g.capability for g in self.grants}) != len(self.grants)
        ):
            raise ValueError("closed_principal_required")


@dataclass(frozen=True, slots=True)
class Capability:
    domain: str
    name: str
    effect_class: Literal["read", "record", "communicate", "transact", "actuate", "administer"]
    output_trust: Literal["trusted", "untrusted", "stamped"]
    # Exact metadata is required even for tools with no resources. None is not false.
    selection_bound: tuple[tuple[str, bool], ...]
    # No default: a missing declaration is not evidence of no recipients.
    recipient_params: RecipientParameters = field(kw_only=True)
    resource_params: tuple[str, ...] = ()
    owner_authority: bool = False
    presentable: bool = True
    always_per_event: bool = False
    approvals_available: bool = False
    approval_required: bool = False
    idempotency: Literal["none", "key_required"] = "none"
    # Internal native-handler provenance; set only by the verified catalog adapter.
    recipient_resolver: Literal["none", "core_notify"] = "none"
    description: str = ""
    input_schema: dict = field(default_factory=lambda: {"type": "object"}, repr=False)

    def __post_init__(self):
        if (
            self.effect_class
            not in {"read", "record", "communicate", "transact", "actuate", "administer"}
            or self.output_trust not in {"trusted", "untrusted", "stamped"}
            or self.idempotency not in {"none", "key_required"}
            or self.recipient_resolver not in {"none", "core_notify"}
            or any(
                type(value) is not bool
                for value in (
                    self.owner_authority,
                    self.presentable,
                    self.always_per_event,
                    self.approvals_available,
                    self.approval_required,
                )
            )
        ):
            raise ValueError("closed_authorization_metadata_required")
        if not all(
            type(v) is str and re.fullmatch(r"[a-zA-Z0-9_.-]+", v) for v in (self.domain, self.name)
        ):
            raise ValueError("invalid_capability_identity")
        if "__" in self.domain or "__" in self.name:
            raise ValueError("ambiguous_capability_identity")
        if (
            type(self.resource_params) is not tuple
            or any(type(v) is not str or not v for v in self.resource_params)
            or type(self.selection_bound) is not tuple
            or any(
                type(v) is not tuple or len(v) != 2 or type(v[0]) is not str or not v[0]
                for v in self.selection_bound
            )
        ):
            raise ValueError("complete_selection_metadata_required")
        if (
            len(set(self.resource_params)) != len(self.resource_params)
            or len(dict(self.selection_bound)) != len(self.selection_bound)
            or set(dict(self.selection_bound)) != set(self.resource_params)
            or any(type(bound) is not bool for _, bound in self.selection_bound)
        ):
            raise ValueError("complete_selection_metadata_required")
        r = self.recipient_params
        if (
            not isinstance(r, RecipientParameters)
            or type(r.undeterminable) is not bool
            or any(type(n) is not str or not n for n in r.argument_names)
            or len(set(r.argument_names)) != len(r.argument_names)
            or any(
                v is not None and (type(v) is not str or not v.strip())
                for v in (r.default_owner_condition, r.undeterminable_condition)
            )
            or (
                self.effect_class == "communicate"
                and not (r.argument_names or r.undeterminable or r.default_owner_condition)
            )
        ):
            raise ValueError("complete_recipient_metadata_required")
        if self.always_per_event and not self.approvals_available:
            raise ValueError("always_per_event_requires_gate")

    @property
    def identity(self) -> str:
        return f"{self.domain}__{self.name}"

    @property
    def mcp_name(self) -> str:
        return f"{self.domain}__{self.name}"


class Disposition(StrEnum):
    DISPATCH = "dispatch"
    PARK = "park"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    """Produced by the owning-domain policy bridge under the ledger transaction.

    `standing_rule` means an owner-created exact match, not a caller rule ID.
    Template proof must cover every content-bearing/unclassified text argument.
    """

    recipient: Literal["none", "owner", "non_owner"]
    standing_rule: bool = False
    template_pinned: bool = False
    inert_owner_notify: bool = False
    actuation_risk: Literal["safe", "consequential", "protected", "unknown"] = "unknown"
    recipient_binding: RecipientBinding | None = None

    def __post_init__(self):
        if (
            self.recipient not in {"none", "owner", "non_owner"}
            or self.actuation_risk not in {"safe", "consequential", "protected", "unknown"}
            or any(
                type(v) is not bool
                for v in (self.standing_rule, self.template_pinned, self.inert_owner_notify)
            )
        ):
            raise ValueError("closed_policy_evidence_required")


@dataclass(frozen=True, slots=True)
class RecipientBinding:
    """Trusted adapter's exact-call resolution, never model-supplied evidence.

    The adapter must read the unique live owner and every named/inherited party
    on the locked connection. A missing/ambiguous target is represented by None.
    This value does not implement that production read or confer a read grant.
    """

    capability: str
    args_digest: str
    owner_id: UUID | None
    argument_names: tuple[str, ...]
    targets: tuple[UUID | None, ...]
    default_owner: bool = False
    undeterminable: bool = False

    def __post_init__(self):
        if (
            type(self.capability) is not str
            or not self.capability
            or type(self.args_digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", self.args_digest)
            or (self.owner_id is not None and not isinstance(self.owner_id, UUID))
            or type(self.argument_names) is not tuple
            or any(type(v) is not str or not v for v in self.argument_names)
            or len(set(self.argument_names)) != len(self.argument_names)
            or type(self.targets) is not tuple
            or any(v is not None and not isinstance(v, UUID) for v in self.targets)
            or any(type(v) is not bool for v in (self.default_owner, self.undeterminable))
        ):
            raise ValueError("closed_recipient_binding_required")


@dataclass(frozen=True, slots=True)
class Decision:
    disposition: Disposition
    allow_standing_rule: bool = False


@dataclass(frozen=True, slots=True)
class Reservation:
    principal: Principal
    capability: Capability
    decision: Decision
    call_id: UUID
    effect_id: UUID | None
    args_digest: str


@dataclass(frozen=True, slots=True)
class BrokerResult:
    status: Literal["returned", "parked", "committed", "failed", "unknown", "not_received"]
    effect_id: UUID | None = None
    approval_ref: str | None = None
    value: object = field(default=None, repr=False)
