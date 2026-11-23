"""Owner policy models (REQ-capability-authorization-020, bu-dhloi1.10, bu-dhloi1.29).

See ``src/butlers/core/owner_policy.py`` for the reader/writer and
``roster/switchboard/migrations/059_owner_policy.py`` for the tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SelectorKind = Literal["capability", "region", "effect_class"]
Constraint = Literal["never", "always_ask"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OwnerPolicyTermCreate(_Strict):
    """A new term. ``owner_action_id`` makes a retried submit the same change."""

    owner_action_id: UUID
    selector_kind: SelectorKind
    selector: str = Field(min_length=1, max_length=192)
    term: Constraint
    resources: dict[str, list[str]] = Field(default_factory=dict, max_length=8)
    expires_at: datetime | None = None


class OwnerPolicyTermTighten(_Strict):
    """Replace a term's constraint and expiry at ``expected_version``; tighten only."""

    owner_action_id: UUID
    expected_version: int = Field(ge=1)
    term: Constraint
    expires_at: datetime | None = None


class OwnerPolicyTermLoosen(OwnerPolicyTermTighten):
    """Loosen a term at ``expected_version``; needs a ``policy_loosen`` step-up.

    An earlier expiry is a loosening; an expiry at or before now lapses the term.
    """


class OwnerPolicyLoosenOptions(OwnerPolicyTermLoosen):
    """The exact loosening the owner is asked to confirm with a passkey."""

    term_id: UUID


class OwnerPolicyTermRef(BaseModel):
    """The typed holding term a receipt, owner surface and /status all show."""

    id: str
    version: int
    term: Constraint
    selector_kind: SelectorKind
    selector: str


class OwnerPolicyTerm(OwnerPolicyTermRef):
    resources: dict[str, list[str]]
    expires_at: str | None
    active: bool


class OwnerPolicy(BaseModel):
    generation: int
    terms: list[OwnerPolicyTerm]


class OwnerPolicyReceipt(BaseModel):
    owner_action_id: str
    term_id: str
    action: Literal["create", "tighten", "loosen"]
    from_version: int | None
    to_version: int
    generation: int


class OwnerLawReceipt(BaseModel):
    """The typed owner-law receipt; the broker's ``PolicyHold.receipt()`` exactly."""

    stage: Literal["owner_law"]
    constraint: Constraint
    generation: int
    terms: list[OwnerPolicyTermRef]


class OwnerPolicyHold(BaseModel):
    """One call the owner law held: refused or parked, with its typed receipt.

    ``effect_id`` is also the parked pending action's id.
    """

    disposition: Literal["refused", "parked"]
    capability: str
    invocation_id: str
    effect_id: str | None
    refusal_id: str | None
    receipt: OwnerLawReceipt
    created_at: str
