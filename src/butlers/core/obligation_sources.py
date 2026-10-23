"""Immutable source identities and creator bounds for dark obligations.

These values and the owning-schema repository are not authentication. Runtime
creators remain unwired until the broker-owned verified invocation interface
exists. Repository calls require the source owner's actual transaction/role;
they are not a request schema or an admission endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from butlers.core.goal_admission import (
    Grant,
    SourceRef,
    decode_grants,
    digest,
    encode_grants,
    intersect,
)
from butlers.core.obligations import DeclarationError, ObligationDeclaration, parse_declaration

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


@dataclass(frozen=True)
class DefinitionRevision:
    """Material definition; its revision is derived here, not a caller stamp.

    material_digest binds the owning source's actual configuration/content. The
    future source adapter must derive it from that record, not request metadata.
    Neither this type nor RuntimeCreationSnapshot verifies the creator.
    """

    owner_domain: str
    family: SourceFamily
    source_id: UUID
    generation: int
    material_digest: str
    declaration: ObligationDeclaration
    origin: Literal["git", "runtime"]
    creator: RuntimeCreationSnapshot | None = None
    state: Literal["active", "disabled", "deleted"] = "active"

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or self.generation < 1
            or not isinstance(self.material_digest, str)
            or not _REVISION.fullmatch(self.material_digest)
            or self.origin not in {"git", "runtime"}
            or self.state not in {"active", "disabled", "deleted"}
            or (self.origin == "runtime") != (self.creator is not None)
        ):
            raise DeclarationError("invalid_definition_revision")
        # Reuse the immutable value contract, including creator ceiling checks.
        ObligationSource(
            self.owner_domain,
            self.family,
            self.source_id,
            "0" * 64,
            self.declaration,
            self.creator,
        )

    @property
    def revision(self) -> str:
        return digest(self.encode())

    @property
    def source(self) -> ObligationSource:
        return ObligationSource(
            self.owner_domain,
            self.family,
            self.source_id,
            self.revision,
            self.declaration,
            self.creator,
        )

    def encode(self) -> dict:
        return {
            "owner_domain": self.owner_domain,
            "family": self.family,
            "source_id": str(self.source_id),
            "generation": self.generation,
            "material_digest": self.material_digest,
            "declaration": {
                "version": 1,
                "capabilities": encode_grants(self.declaration.capabilities),
            },
            "origin": self.origin,
            "state": self.state,
            "creator": None
            if self.creator is None
            else {
                "invocation_id": str(self.creator.invocation_id),
                "ceiling": encode_grants(self.creator.ceiling),
                "exposed": self.creator.exposed,
            },
        }

    @classmethod
    def decode(cls, value: dict) -> DefinitionRevision:
        creator = value["creator"]
        return cls(
            value["owner_domain"],
            value["family"],
            UUID(value["source_id"]),
            value["generation"],
            value["material_digest"],
            parse_declaration(value["declaration"]),
            value["origin"],
            None
            if creator is None
            else RuntimeCreationSnapshot(
                UUID(creator["invocation_id"]),
                decode_grants(creator["ceiling"]),
                creator["exposed"],
            ),
            value["state"],
        )


class SourceConflict(ValueError):
    """Closed stale/replay/refusal reason; never supplied content."""


class ObligationSourceRepository:
    """A staged owning-connection repository, never a cross-domain pool reader.

    Every call requires an existing transaction, own schema and exact runtime
    role. Returned values are staged until the caller's transaction commits.
    Callers must use durable command IDs when reconciling a lost commit reply.
    No method authenticates a model-supplied creator/cause, dispatches work,
    calls GoalAdmission or sends the outbox to a control plane.
    """

    def __init__(self, connection, owner_domain: str):
        if not isinstance(owner_domain, str) or not _DOMAIN.fullmatch(owner_domain):
            raise SourceConflict("invalid_source_owner")
        self.connection, self.owner_domain = connection, owner_domain

    async def _guard(self) -> None:
        if not self.connection.is_in_transaction():
            raise SourceConflict("owning_transaction_required")
        identity = await self.connection.fetchrow(
            """SELECT current_schema() AS schema, current_user AS role,
               EXISTS (SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema()
                   AND relname=ANY($1::text[])) AS shadowed""",
            [
                "obligation_definitions",
                "obligation_revisions",
                "obligation_source_commands",
                "obligation_occurrences",
                "obligation_source_outbox",
            ],
        )
        if (
            identity["shadowed"]
            or identity["schema"] != self.owner_domain
            or identity["role"] != (f"butler_{self.owner_domain}_rw")
        ):
            raise SourceConflict("owning_role_required")

    async def _lock(self, source_id: UUID) -> None:
        if not isinstance(source_id, UUID):
            raise SourceConflict("invalid_source_id")
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"obligation-source:{self.owner_domain}:{source_id}",
        )

    async def lock_source(self, source_id: UUID) -> None:
        """Validated owning-source lock for writers sharing a source-row TX."""
        await self._guard()
        await self._lock(source_id)

    async def lock_git_inventory(self) -> None:
        """Serialize full owning Git reconciliation before command/source locks.

        Relation identity is checked before discovery; no temporary shadow or
        cross-schema shortcut may participate in a source inventory.
        """
        await self._guard()
        row = await self.connection.fetchrow(
            """SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname=$1 AND c.relname='scheduled_tasks' AND c.relkind IN ('r','p'))
               AND NOT EXISTS(SELECT 1 FROM pg_class WHERE relnamespace=pg_my_temp_schema()
                   AND relname='scheduled_tasks') AS scheduled_relation""",
            self.owner_domain,
        )
        if not row or not row["scheduled_relation"]:
            raise SourceConflict("owning_schedule_relation_required")
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"obligation-git-inventory:{self.owner_domain}",
        )

    async def read_command(self, command_id: UUID) -> tuple[str, DefinitionRevision] | None:
        """Lock a command before source/row locks; replay is not current authority.

        The returned immutable historical body must be matched to the exact
        request. This exposes the existing command lock without changing revise.
        """
        await self._guard()
        if not isinstance(command_id, UUID):
            raise SourceConflict("invalid_source_command")
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"obligation-command:{self.owner_domain}:{command_id}",
        )
        row = await self.connection.fetchrow(
            "SELECT request_digest,body FROM obligation_source_commands WHERE id=$1", command_id
        )
        if row is None:
            return None
        value = DefinitionRevision.decode(row["body"])
        if value.owner_domain != self.owner_domain:
            raise SourceConflict("source_binding_invalid")
        return row["request_digest"], value

    async def read(self, source_id: UUID) -> DefinitionRevision | None:
        await self._guard()
        row = await self.connection.fetchrow(
            """SELECT r.body,r.generation AS revision_generation,
               d.id,d.owner_domain,d.family,d.origin,d.creator,
               d.revision,d.generation,d.state FROM obligation_definitions d
               JOIN obligation_revisions r ON r.source_id=d.id AND r.revision=d.revision
               WHERE d.id=$1""",
            source_id,
        )
        if row is None:
            return None
        value = DefinitionRevision.decode(row["body"])
        if (
            value.source_id != row["id"]
            or value.owner_domain != self.owner_domain
            or value.owner_domain != row["owner_domain"]
            or value.family != row["family"]
            or value.origin != row["origin"]
            or value.encode()["creator"] != row["creator"]
            or value.revision != row["revision"]
            or value.generation != row["generation"]
            or value.generation != row["revision_generation"]
            or value.state != row["state"]
        ):
            raise SourceConflict("source_binding_invalid")
        return value

    async def _outbox(self, source_id: UUID, revision: str, kind: str, reference: dict) -> None:
        await self.connection.execute(
            """INSERT INTO obligation_source_outbox
               (event_key,source_id,revision,kind,reference) VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (event_key) DO NOTHING""",
            digest([str(source_id), revision, kind, reference]),
            source_id,
            revision,
            kind,
            reference,
        )

    async def revise(
        self,
        command_id: UUID,
        value: DefinitionRevision,
        *,
        expected_revision: str | None,
    ) -> DefinitionRevision:
        """CAS one material revision and cancellation/outbox in the caller's TX.

        Same command+same request returns the original immutable result even
        after later revisions; different input with that command refuses. The
        caller still revalidates current state before consuming an occurrence.
        """
        await self._guard()
        if not isinstance(command_id, UUID) or value.owner_domain != self.owner_domain:
            raise SourceConflict("invalid_source_command")
        await self.connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"obligation-command:{self.owner_domain}:{command_id}",
        )
        request_digest = digest([expected_revision, value.encode()])
        previous = await self.connection.fetchrow(
            "SELECT request_digest,body FROM obligation_source_commands WHERE id=$1",
            command_id,
        )
        if previous:
            if previous["request_digest"] != request_digest:
                raise SourceConflict("command_binding_conflict")
            return DefinitionRevision.decode(previous["body"])
        await self._lock(value.source_id)
        old = await self.read(value.source_id)
        if old is None:
            if expected_revision is not None or value.generation != 1 or value.state != "active":
                raise SourceConflict("source_revision_conflict")
            await self.connection.execute(
                """INSERT INTO obligation_definitions
                   (id,owner_domain,family,origin,creator,revision,generation,state)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                value.source_id,
                value.owner_domain,
                value.family,
                value.origin,
                value.encode()["creator"],
                value.revision,
                value.generation,
                value.state,
            )
        else:
            if (
                expected_revision != old.revision
                or value.generation != old.generation + 1
                or old.state == "deleted"
            ):
                raise SourceConflict("source_revision_conflict")
            if (value.family, value.origin, value.creator) != (old.family, old.origin, old.creator):
                raise SourceConflict("creation_binding_immutable")
            await self.connection.execute(
                """UPDATE obligation_definitions SET revision=$2,generation=$3,state=$4,
                   updated_at=clock_timestamp() WHERE id=$1""",
                value.source_id,
                value.revision,
                value.generation,
                value.state,
            )
        await self.connection.execute(
            """INSERT INTO obligation_revisions (source_id,revision,generation,body)
               VALUES ($1,$2,$3,$4)""",
            value.source_id,
            value.revision,
            value.generation,
            value.encode(),
        )
        # Every material revision invalidates old pending AND claimed attempts.
        cancelled = await self.connection.fetch(
            """UPDATE obligation_occurrences SET state='cancelled'
               WHERE source_id=$1 AND state IN ('pending','claimed') RETURNING id,revision""",
            value.source_id,
        )
        for row in cancelled:
            await self._outbox(
                value.source_id,
                row["revision"],
                "occurrence_cancelled",
                {"occurrence_id": str(row["id"])},
            )
        await self._outbox(
            value.source_id,
            value.revision,
            "definition_changed",
            {"generation": value.generation, "state": value.state},
        )
        await self.connection.execute(
            """INSERT INTO obligation_source_commands (id,source_id,request_digest,body)
               VALUES ($1,$2,$3,$4)""",
            command_id,
            value.source_id,
            request_digest,
            value.encode(),
        )
        return value

    async def capture(
        self,
        source_id: UUID,
        revision: str,
        *,
        slot: str | None = None,
        event_id: UUID | None = None,
        feed: bool = False,
    ) -> UUID:
        """Capture an already source-validated slot/event; not event verification.

        Subscription activity/event/cause provenance must be checked by the
        actual future source adapter in THIS owning transaction. Payloads do
        not establish those facts. Only fixed references enter this outbox.
        """
        await self._guard()
        await self._lock(source_id)
        value = await self.read(source_id)
        if value is None or value.revision != revision or value.state != "active":
            raise SourceConflict("source_not_current")
        if value.family == "subscription":
            if slot is not None:
                raise SourceConflict("invalid_subscription_identity")
            ref = value.source.event(event_id, feed=feed)
        else:
            if event_id is not None or feed is not False:
                raise SourceConflict("invalid_occurrence_identity")
            ref = value.source.occurrence(slot)
        row = await self.connection.fetchrow(
            """INSERT INTO obligation_occurrences
               (source_id,revision,source_key,source_kind,source_ref,occurrence)
               VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (source_key) DO NOTHING RETURNING id""",
            source_id,
            revision,
            ref.key,
            ref.kind,
            ref.id,
            ref.occurrence,
        )
        if not row:
            row = await self.connection.fetchrow(
                "SELECT id FROM obligation_occurrences WHERE source_key=$1",
                ref.key,
            )
        await self._outbox(
            source_id, revision, "occurrence_captured", {"occurrence_id": str(row["id"])}
        )
        return row["id"]

    async def cancel_occurrence(self, source_id: UUID, occurrence_id: UUID) -> None:
        """Durable idempotent cancellation, including an already claimed row."""
        await self._guard()
        await self._lock(source_id)
        row = await self.connection.fetchrow(
            "SELECT revision,state FROM obligation_occurrences "
            "WHERE id=$1 AND source_id=$2 FOR UPDATE",
            occurrence_id,
            source_id,
        )
        if row is None:
            raise SourceConflict("occurrence_missing")
        if row["state"] != "cancelled":
            await self.connection.execute(
                "UPDATE obligation_occurrences SET state='cancelled' WHERE id=$1",
                occurrence_id,
            )
        await self._outbox(
            source_id,
            row["revision"],
            "occurrence_cancelled",
            {"occurrence_id": str(occurrence_id)},
        )

    async def fence(self, source_id: UUID, occurrence_id: UUID):
        """Lock source before occurrence, shared by claim and final consumption.

        The lock must cover the local staged consumption, not just an earlier
        lookup. A remote provider send needs its separately allocated protocol.
        """
        await self._guard()
        await self._lock(source_id)
        value = await self.read(source_id)
        row = await self.connection.fetchrow(
            "SELECT * FROM obligation_occurrences WHERE id=$1 AND source_id=$2 FOR UPDATE",
            occurrence_id,
            source_id,
        )
        if (
            not value
            or value.state != "active"
            or not row
            or row["revision"] != value.revision
            or row["state"] == "cancelled"
        ):
            raise SourceConflict("source_not_current")
        return value, row

    async def claim(self, source_id: UUID, occurrence_id: UUID, token: UUID) -> bool:
        """No expiry/reassignment inference; retry must retain its own token."""
        if not isinstance(token, UUID):
            raise SourceConflict("invalid_claim")
        _, row = await self.fence(source_id, occurrence_id)
        if row["state"] == "claimed":
            return row["claim_token"] == token
        await self.connection.execute(
            "UPDATE obligation_occurrences SET state='claimed',claim_token=$2 WHERE id=$1",
            occurrence_id,
            token,
        )
        return True
