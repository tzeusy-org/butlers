"""Immutable source identities and creator bounds for dark obligations.

These are value checks, not authentication. They are deliberately unwired from
runtime creators until the broker-owned verified invocation interface and the
source owner's transactional repository are installed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from butlers.core.goal_admission import Grant, SourceRef, digest, encode_grants, intersect
from butlers.core.obligations import DeclarationError, ObligationDeclaration

SourceFamily = Literal["schedule", "deadline", "chain", "subscription", "model_job"]
_DOMAIN = re.compile(r"[a-z][a-z0-9_-]*\Z")
_REVISION = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class RuntimeCreationSnapshot:
    """A copy of server-held creation facts, never a public request schema."""

    invocation_id: UUID
    ceiling: tuple[Grant, ...]
    exposed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.invocation_id, UUID)
            or type(self.exposed) is not bool
            or type(self.ceiling) is not tuple
            or any(not isinstance(grant, Grant) for grant in self.ceiling)
            or len({grant.capability for grant in self.ceiling}) != len(self.ceiling)
        ):
            raise DeclarationError("invalid_creator_snapshot")
        object.__setattr__(self, "ceiling", tuple(sorted(self.ceiling, key=lambda g: g.capability)))

    def check(self, declaration: ObligationDeclaration) -> None:
        """Refuse widening instead of silently clipping a requested declaration."""
        narrowed = intersect(declaration.capabilities, self.ceiling)
        if encode_grants(narrowed) != encode_grants(declaration.capabilities):
            raise DeclarationError("creator_ceiling_exceeded")


@dataclass(frozen=True)
class ObligationSource:
    """Content-free identity bound to a material source definition revision.

    The owning repository must derive revision from the entire effective
    definition, including prompt/context and declaration, and commit it with the
    source row. A caller-provided revision is not authoritative verification.
    """

    owner_domain: str
    family: SourceFamily
    source_id: UUID
    revision: str
    declaration: ObligationDeclaration
    creator: RuntimeCreationSnapshot | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.owner_domain, str)
            or not _DOMAIN.fullmatch(self.owner_domain)
            or self.family not in {"schedule", "deadline", "chain", "subscription", "model_job"}
            or not isinstance(self.source_id, UUID)
            or not isinstance(self.revision, str)
            or not _REVISION.fullmatch(self.revision)
            or not isinstance(self.declaration, ObligationDeclaration)
            or (self.creator is not None and not isinstance(self.creator, RuntimeCreationSnapshot))
        ):
            raise DeclarationError("invalid_obligation_source")
        if self.creator is not None:
            self.creator.check(self.declaration)

    def occurrence(self, slot: str) -> SourceRef:
        """Use the persisted nominal slot/event ID, never a fresh retry clock.

        The infrastructure source adapter owns slot derivation and verification.
        Revision and domain are included so edits and equally-named sources in
        other schemas cannot borrow an occurrence's identity.
        """
        if (
            self.family == "subscription"
            or not isinstance(slot, str)
            or not slot
            or len(slot) > 256
        ):
            raise DeclarationError("invalid_occurrence")
        return SourceRef(
            "occurrence",
            f"{self.owner_domain}:{self.family}:{self.source_id}",
            digest([self.revision, slot]),
        )

    def event(self, event_id: UUID, *, feed: bool) -> SourceRef:
        """Identity for one persisted event under a declared subscription.

        The registered ingress adapter, not an envelope field, determines feed.
        The source repository must still revalidate subscription activation,
        event ownership and actual cause assertions before admission.
        """
        if (
            self.family != "subscription"
            or not isinstance(event_id, UUID)
            or type(feed) is not bool
        ):
            raise DeclarationError("invalid_subscription_event")
        identity = digest([self.owner_domain, str(self.source_id), self.revision, str(event_id)])
        return SourceRef("feed" if feed else "domain", identity)
