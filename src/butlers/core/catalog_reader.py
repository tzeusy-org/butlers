"""Same-connection verified D4 fleet snapshot and concrete admission adapter."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from butlers.core.catalog_publication import (
    authorization_digest,
    decode_publication,
    digest,
    verify_role,
)
from butlers.core.goal_admission import Capability, CatalogView, EvidenceRefused
from butlers.core.tool_catalog import OwnerNotifyBranch, RecipientParameters, ToolCatalog

# An omission is not an empty eligible fleet. Membership changes require an
# explicit checked-in migration+reader change, never a publishing caller list.
DOMAINS = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)
EXCLUSIONS = {"calendar": "module, not a standalone roster daemon"}


@dataclass(frozen=True)
class Registration:
    domain: str
    instance_id: UUID
    revision_id: UUID
    epoch: int
    body_digest: str
    authorization_digest: str
    catalog: ToolCatalog


@dataclass(frozen=True)
class FleetSnapshot:
    digest: str
    generation_digest: str
    registrations: tuple[Registration, ...]

    def registration(self, domain: str) -> Registration:
        for item in self.registrations:
            if item.domain == domain:
                return item
        raise EvidenceRefused("catalog_unavailable")


def _owner_notify_eligible(descriptor, domain: str) -> bool:
    """Preserve one declared native branch, not ownership of any actual call.

    The typed marker describes eligibility for a ceiling containing notify.
    Explicit/non-owner/unknown/inherited recipients still need the actual
    broker checks. A tool name, prose condition or marker on another tool
    cannot turn arbitrary communication into an owner-directed capability.
    """
    recipients = descriptor.recipient_params
    return (
        descriptor.canonical_name == "notify"
        and descriptor.module_name == "core"
        and descriptor.group_name == "notifications"
        and descriptor.namespace == "core.notifications"
        and descriptor.owner_domain == domain
        and descriptor.effect_class == "communicate"
        and descriptor.owner_authority is False
        and descriptor.grantable
        and isinstance(recipients, RecipientParameters)
        and recipients.owner_notify_branch is OwnerNotifyBranch.TELEGRAM_SEND_INSIGHT_WITHOUT_PARTY
        and recipients.argument_names == ("recipient", "entity_id", "request_context")
        and recipients.undeterminable is True
    )


class PublishedCatalogProvider:
    async def snapshot(self, connection) -> FleetSnapshot:
        """Read current definitions in the caller's existing transaction.

        No pool acquisition, cached fallback, cross-schema SQL or network I/O.
        One statement binds membership and current publications to one MVCC
        snapshot. It grants no cross-domain UPDATE/row-lock authority.
        """
        if not connection.is_in_transaction():
            raise EvidenceRefused("catalog_unavailable")
        await verify_role(connection, "switchboard")
        # ONE SQL statement / MVCC snapshot, not multiple READ COMMITTED reads.
        # FOR SHARE would require cross-domain UPDATE privileges and RLS policy;
        # this read-only projection needs neither. A concurrent republish gives
        # the next reader a new generation; broker checks it again per call.
        state = await connection.fetchrow(
            "SELECT enabled, "
            "(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.domain) "
            "FROM public.capability_catalog_members m) AS members, "
            "(SELECT jsonb_agg(jsonb_build_object("
            "'domain',c.domain,'instance_id',c.instance_id,'revision_id',c.revision_id,"
            "'epoch',c.epoch,'live',c.active AND c.expires_at>clock_timestamp(),"
            "'body',h.body,'body_digest',h.body_digest,"
            "'authorization_digest',h.authorization_digest) ORDER BY c.domain) "
            "FROM public.capability_catalog_current c "
            "JOIN public.capability_catalog_history h ON "
            "(h.domain,h.epoch,h.revision_id,h.instance_id)="
            "(c.domain,c.epoch,c.revision_id,c.instance_id)) AS registrations "
            "FROM public.capability_catalog_control WHERE singleton"
        )
        if state is None or state["enabled"] is not True:
            raise EvidenceRefused("catalog_unavailable")
        members, rows = state["members"], state["registrations"]
        expected = {name: (True, None) for name in DOMAINS} | {
            name: (False, reason) for name, reason in EXCLUSIONS.items()
        }
        if (
            type(members) is not list
            or len(members) != len(expected)
            or {row["domain"]: (row["included"], row["exclusion_reason"]) for row in members}
            != expected
        ):
            raise EvidenceRefused("catalog_unavailable")
        if (
            type(rows) is not list
            or len(rows) != len(DOMAINS)
            or {row["domain"] for row in rows} != set(DOMAINS)
        ):
            raise EvidenceRefused("catalog_unavailable")
        registrations = []
        for row in rows:
            if row["live"] is not True or type(row["epoch"]) is not int or row["epoch"] <= 0:
                raise EvidenceRefused("catalog_unavailable")
            try:
                instance, revision = UUID(row["instance_id"]), UUID(row["revision_id"])
            except (TypeError, ValueError, AttributeError):
                raise EvidenceRefused("catalog_unavailable") from None
            catalog = decode_publication(row["body"], row["domain"])
            if (
                digest(row["body"]) != row["body_digest"]
                or authorization_digest(catalog) != row["authorization_digest"]
            ):
                raise EvidenceRefused("catalog_unavailable")
            registrations.append(
                Registration(
                    row["domain"],
                    instance,
                    revision,
                    row["epoch"],
                    row["body_digest"],
                    row["authorization_digest"],
                    catalog,
                )
            )
        content = [[item.domain, item.body_digest] for item in registrations]
        identities = [
            [item.domain, str(item.instance_id), str(item.revision_id), item.epoch]
            for item in registrations
        ]
        return FleetSnapshot(digest([expected, content]), digest(identities), tuple(registrations))

    async def load(self, connection) -> CatalogView:
        snapshot = await self.snapshot(connection)
        capabilities = tuple(
            Capability(
                f"{registration.domain}__{item.canonical_name}",
                registration.domain,
                item.effect_class,
                owner_authority=item.owner_authority,
                # Existing admission flag means a conditional branch is eligible,
                # never that explicit or inherited recipients are the owner.
                owner_default=_owner_notify_eligible(item, registration.domain),
                presentable=item.grantable,
            )
            for registration in snapshot.registrations
            for item in registration.catalog.descriptors
            if item.authorization_complete
        )
        return CatalogView(snapshot.digest, capabilities)

    async def require_registration(
        self,
        connection,
        *,
        domain: str,
        revision_id: UUID,
        generation_digest: str,
    ) -> Registration:
        """M2 export: verify the actual current registration, never just a hash.

        Broker adapts this full immutable descriptor set to its own Capability,
        including resource/recipient/approval/provenance metadata. This method
        does not authorize a call or implement that downstream adaptation.
        """
        item = (await self.snapshot(connection)).registration(domain)
        if item.revision_id != revision_id or item.catalog.generation_digest != generation_digest:
            raise EvidenceRefused("catalog_unavailable")
        return item
