"""Owner watch values (RFC 0019 notify-only narrowing; REQ-owner-watches-003/004; dark).

A watch is one evaluator instance (``butlers.conditions``) the owner approved on an exact card.
It has no recipient, URL or free-text field: a spec is a closed set of fields and anything else
refuses. Nothing here reads a clock, a database or a model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from butlers.perception.model import canonical

MAX_ACTIVE = 50
LIFETIME = timedelta(days=90)
RENEWAL_OFFER = timedelta(days=7)
DELIVERY_MODES = frozenset({"digest", "right_away"})
DEFAULT_DELIVERY = "digest"
#: The only fields a watch spec may carry; no recipient, URL, template or action.
SPEC_FIELDS = frozenset({"predicate", "delivery", "categories"})
OWNER = "owner"


class WatchRefused(ValueError):
    """A watch request is outside the owner-authored, notify-only contract."""


class CapExceeded(WatchRefused):
    """The owner already has the maximum number of active watches."""


class WatchConflict(RuntimeError):
    """A concurrent write changed the watch (version CAS lost)."""


class WatchState(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    DELETED = "deleted"


class Action(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    RENEW = "renew"
    DELETE = "delete"


@dataclass(frozen=True)
class Watch:
    id: str
    version: int
    generation: int
    predicate: dict
    predicate_digest: str
    grant_digest: str
    sources: tuple[str, ...]
    categories: tuple[str, ...]
    delivery: str
    state: WatchState
    created_at: datetime
    expires_at: datetime
    approval_digest: str  # the exact card the owner approved for this version

    @property
    def instance_id(self) -> str:
        """The evaluator instance of this generation; a renewal registers the next one."""
        return f"{self.id}:{self.generation}"

    def due_for_renewal(self, now: datetime) -> bool:
        return (
            self.state is WatchState.ACTIVE
            and self.expires_at - RENEWAL_OFFER <= now < self.expires_at
        )


@dataclass(frozen=True)
class WatchCard:
    """What the owner approves. Its digest binds every field that defines the watch."""

    action: Action
    watch_id: str
    target_version: int
    predicate: dict
    grant_digest: str
    sources: tuple[str, ...]
    categories: tuple[str, ...]
    delivery: str
    # A create or renew card binds the lifetime (expiry is that long after approval, REQ-004);
    # an update or delete card binds the watch's current expiry and carries no lifetime.
    expires_at: datetime | None
    lifetime: timedelta | None = None
    resume: bool = False  # an update that also re-activates a paused watch

    @property
    def body(self) -> dict:
        return {
            "action": str(self.action),
            "watch_id": self.watch_id,
            "target_version": self.target_version,
            "predicate": self.predicate,
            "grant_digest": self.grant_digest,
            "sources": list(self.sources),
            "categories": list(self.categories),
            "delivery": self.delivery,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "lifetime_days": self.lifetime.days if self.lifetime else None,
            "resume": self.resume,
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical(self.body).encode()).hexdigest()


@dataclass(frozen=True)
class OwnerApproval:
    """The owner's approval of one exact card, built only by the trusted approval boundary."""

    card_digest: str
    origin: str  # required: no default may stand in for the owner
    approved_at: datetime | None = None
