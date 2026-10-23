"""Standing-rule selection for an already verified, registered owning goal call.

The catalog and grant are trusted infrastructure inputs, not request fields or
proof of D7 verification. Missing inputs preserve pending-only admission. This
module neither dispatches nor turns a selected rule into an effect receipt.
"""

from butlers.capability_broker.catalog import project_catalog
from butlers.capability_broker.contracts import PolicyEvidence
from butlers.capability_broker.policy import bind_recipient_policy
from butlers.capability_broker.recipients import resolve_recipient_binding
from butlers.core.capability_json import canonical_bytes
from butlers.core.goal_admission import Grant
from butlers.core.tool_catalog import ToolCatalog
from butlers.modules.approvals.rules import (
    constraint_pins_value,
    match_rules_from_list,
)


def _fully_pinned(descriptor, constraints, arguments):
    """Prove the exact-value branch only, never infer typed-template authority.

    Require every declared input, including optional content/defaults, and every
    supplied input to be fixed. Patterns and free-text slots are not exact pins.
    """
    properties = descriptor.input_schema.get("properties", {})
    if not properties or set(arguments) != set(properties):
        return False
    for name in properties:
        constraint = constraints.get(name)
        if (
            type(constraint) is not dict
            or set(constraint) != {"type", "value"}
            or constraint["type"] != "exact"
            or canonical_bytes({"value": constraint["value"]})
            != canonical_bytes({"value": arguments[name]})
        ):
            return False
    return True


async def matching_goal_rule(connection, request, catalog, grant):
    """Lock actual rule rows after the goal gate, then use existing precedence.

    No callback, boolean override, caller owner enum, or descriptive catalog
    condition supplies authority. Only native core-notify has a real same-TX
    recipient resolver here; other exposed calls need complete exact pins.
    """
    if catalog is None or grant is None:
        return None
    if type(catalog) is not ToolCatalog or type(grant) is not Grant:
        raise ValueError("registered_goal_rule_context_required")
    lineage = request.goal_lineage
    if grant.capability != lineage.goal_capability:
        raise ValueError("goal_rule_grant_mismatch")
    capabilities = project_catalog(catalog, domain=request.origin_butler).capabilities
    capability = next((c for c in capabilities if c.name == request.tool_name), None)
    if capability is None:
        raise ValueError("goal_rule_registration_missing")
    if capability.effect_class == "transact":
        raise ValueError("goal_transact_refused")
    if (
        grant.park_only
        or capability.always_per_event
        or not capability.approvals_available
        or lineage.goal_origin == "third_party"
        or (lineage.goal_depth == 1 and capability.effect_class == "administer")
        or (lineage.goal_exposed and capability.effect_class != "communicate")
    ):
        return None
    descriptor = next(d for d in catalog.descriptors if d.canonical_name == request.tool_name)
    owner_recipient = False
    if lineage.goal_exposed and capability.recipient_resolver == "core_notify":
        binding = await resolve_recipient_binding(connection, capability, request.tool_args)
        evidence = bind_recipient_policy(
            capability,
            request.tool_args,
            lineage.goal_args_digest,
            PolicyEvidence("none", recipient_binding=binding),
        )
        owner_recipient = evidence.recipient == "owner"
    rows = await connection.fetch(
        "SELECT * FROM approval_rules WHERE tool_name=$1 AND active=true ORDER BY id FOR UPDATE",
        request.tool_name,
    )
    eligible = []
    for row in rows:
        constraints = row["arg_constraints"]
        if type(constraints) is not dict:
            continue
        if any(not constraint_pins_value(constraints.get(n)) for n in descriptor.safety_args):
            continue
        if lineage.goal_exposed and not (
            owner_recipient or _fully_pinned(descriptor, constraints, request.tool_args)
        ):
            continue
        eligible.append(row)
    return match_rules_from_list(request.tool_name, request.tool_args, eligible)
