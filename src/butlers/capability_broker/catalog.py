"""Current published fleet authority and strict final-registration projection."""

from collections.abc import Mapping

from butlers.core.catalog_reader import PublishedCatalogProvider
from butlers.core.tool_catalog import (
    RecipientParameters,
    ToolCatalog,
    declared_approval_mode,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS

from .contracts import BrokerRefused, Capability, PolicyEvidence, Principal, Reason
from .domains import Catalog
from .recipients import resolve_recipient_binding


class PublishedAuthorities:
    """Read actual M1 publications on the broker's locked connection.

    The admitted goal pins the complete fleet's definition digest, not a local
    registration generation or the fleet's process identities. Every listing
    and call rereads the current complete fleet; no cached fallback is kept.

    No-party reads need no recipient evidence. Native core.notify declarations
    additionally consume the existing same-transaction identity resolver. This
    supplies no selection, standing-rule, rendering, risk or effect-commit facts;
    other policy queries still refuse without their actual producers.
    """

    async def catalog(self, connection, principal: Principal) -> Catalog:
        try:
            snapshot = await PublishedCatalogProvider().snapshot(connection)
            capabilities = tuple(
                capability
                for registration in snapshot.registrations
                for capability in project_catalog(
                    registration.catalog, domain=registration.domain
                ).capabilities
            )
            # BrokerLedger compares this exact digest with the locked goal.
            return Catalog(snapshot.digest, capabilities)
        except Exception:
            # Database/decoder failures are content-blind refusals; they never
            # select an older snapshot. Cancellation still propagates.
            raise BrokerRefused(Reason.CATALOG) from None

    async def selection(self, connection, principal, capability, grant) -> bool:
        raise BrokerRefused(Reason.SELECTION)

    async def policy(self, connection, principal, capability, args) -> PolicyEvidence:
        if not connection.is_in_transaction():
            raise BrokerRefused(Reason.POLICY)
        parties = capability.recipient_params
        if (
            (
                capability.recipient_resolver == "calendar_response"
                and capability.name == "calendar_respond"
                and capability.effect_class == "communicate"
                and capability.always_per_event
                and parties.argument_names == ("entry_id",)
                and parties.undeterminable
            )
            or (
                capability.recipient_resolver == "native_email"
                and capability.identity == "messenger__email_send_message"
                and capability.effect_class == "communicate"
                and parties == RecipientParameters(("to",))
            )
            or (
                capability.recipient_resolver == "core_notify"
                and capability.name == "notify"
                and capability.effect_class == "communicate"
                and set(parties.argument_names) == {"recipient", "entity_id", "request_context"}
            )
        ):
            binding = await resolve_recipient_binding(connection, capability, args)
            return PolicyEvidence("none", recipient_binding=binding)
        if (
            capability.effect_class not in {"read", "record"}
            or parties.argument_names
            or parties.undeterminable
            or parties.default_owner_condition
            or parties.undeterminable_condition
        ):
            raise BrokerRefused(Reason.POLICY)
        return PolicyEvidence("none")


def _mutable_json(value):
    """Detach schema mappings and frozen sequences from the source snapshot."""
    if isinstance(value, Mapping):
        return {key: _mutable_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_mutable_json(item) for item in value]
    return value


def project_catalog(registration: ToolCatalog, *, domain: str) -> Catalog:
    """Preserve explicit mode, actual configured gate and resource marks.

    Absent or legacy metadata is refused as a complete snapshot, never dropped
    to produce a smaller apparently valid catalog. Private definitions are not
    projected. Declaration validation does not prove the owning gate enforces
    always-per-event behavior or that the owning receipt producer exists.
    """
    try:
        if (
            not isinstance(registration, ToolCatalog)
            or not registration.classification_complete
            or type(domain) is not str
            or not domain
            or any(d.owner_domain != domain for d in registration.descriptors)
        ):
            raise ValueError("verified_complete_domain_registration_required")
        validate_authorization_catalog(registration, store_writers=STORE_WRITERS)
        capabilities = []
        for descriptor in registration.descriptors:
            if not descriptor.llm_presentable:
                continue
            policy = descriptor.approval_policy
            mode = declared_approval_mode(policy)
            # These booleans are already checked by authorization validation.
            available = policy["enabled"]
            native_notify = (
                descriptor.canonical_name == "notify"
                and descriptor.module_name == "core"
                and descriptor.namespace == "core.notifications"
                and descriptor.effect_class == "communicate"
                and set(descriptor.recipient_params.argument_names)
                == {"recipient", "entity_id", "request_context"}
            )
            native_calendar = (
                descriptor.canonical_name == "calendar_respond"
                and descriptor.module_name == "calendar"
                and descriptor.namespace == "calendar.response"
                and descriptor.group_name == "response"
                and descriptor.effect_class == "communicate"
                and mode == "always_per_event"
                and policy["configured_gate"]
                and descriptor.resource_params == ("entry_id",)
                and descriptor.recipient_params.argument_names == ("entry_id",)
                and descriptor.recipient_params.undeterminable
                and descriptor.idempotency == "key_required"
            )
            native_email = (
                domain == "messenger"
                and descriptor.canonical_name == "email_send_message"
                and descriptor.module_name == "email"
                and descriptor.namespace == "email.write"
                and descriptor.group_name == "write"
                and descriptor.effect_class == "communicate"
                and descriptor.output_trust == "untrusted"
                and descriptor.resource_params == ("to",)
                and dict(descriptor.selection_bound) == {"to": False}
                and descriptor.recipient_params == RecipientParameters(("to",))
                and descriptor.safety_args == ("to",)
                and not descriptor.owner_authority
                and descriptor.idempotency == "none"
                and mode in {"default", "always_per_event"}
                and policy["configured_gate"]
            )
            capabilities.append(
                Capability(
                    domain=domain,
                    name=descriptor.canonical_name,
                    effect_class=descriptor.effect_class,
                    output_trust=descriptor.output_trust,
                    selection_bound=tuple(sorted(descriptor.selection_bound.items())),
                    recipient_params=descriptor.recipient_params,
                    resource_params=descriptor.resource_params,
                    owner_authority=descriptor.owner_authority,
                    presentable=descriptor.llm_presentable,
                    always_per_event=mode == "always_per_event",
                    approvals_available=available,
                    approval_required=available and policy["configured_gate"],
                    idempotency=descriptor.idempotency,
                    recipient_resolver=(
                        "core_notify"
                        if native_notify
                        else "calendar_response"
                        if native_calendar
                        else "native_email"
                        if native_email
                        else "none"
                    ),
                    description=descriptor.description,
                    input_schema=_mutable_json(descriptor.input_schema),
                )
            )
        return Catalog(registration.generation_digest, tuple(capabilities))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BrokerRefused(Reason.CATALOG) from None
