"""Strict value projection of an already verified final registration.

This is not a current-fleet reader or an Authorities implementation. Its caller
must authenticate the complete current M1 registration and bind its digest to
the goal on the broker's locked connection. A local catalog or this projection
cannot establish currentness, selection records, standing rules or receipts.
"""

from collections.abc import Mapping

from butlers.core.tool_catalog import (
    ToolCatalog,
    declared_approval_mode,
    validate_authorization_catalog,
)
from butlers.core.tool_presentation_inventory import STORE_WRITERS

from .contracts import BrokerRefused, Capability, Reason
from .domains import Catalog


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
                    recipient_resolver="core_notify" if native_notify else "none",
                    description=descriptor.description,
                    input_schema=_mutable_json(descriptor.input_schema),
                )
            )
        return Catalog(registration.generation_digest, tuple(capabilities))
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BrokerRefused(Reason.CATALOG) from None
