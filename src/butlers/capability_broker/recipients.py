"""Same-transaction recipient evidence for the registered core notify handler.

This does not publish a catalog, authorize a model's claimed identity, match a
standing rule, or promise inert rendering. The catalog adapter must bind the
native resolver tag from the verified final core.notifications descriptor.
Unimplemented/inherited selectors conservatively remain non-owner.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_arguments
from butlers.identity import resolve_owner_channel_via_definer

from .contracts import BrokerRefused, Capability, Reason, RecipientBinding
from .domains import Authorities


def _uuid(value):
    try:
        # Raw wire JSON supplies text, never an identity-bearing Python object.
        return UUID(value) if type(value) is str and str(UUID(value)) == value else None
    except ValueError:
        return None


async def resolve_recipient_binding(
    connection, capability: Capability, args: dict
) -> RecipientBinding:
    """Resolve the exact call on its existing transaction, never another pool.

    The native tag is supplied only by the verified catalog projection. This
    helper supplies identity evidence, not policy exemptions or effect receipts.
    """
    if not connection.is_in_transaction():
        raise BrokerRefused(Reason.POLICY)
    snapshot = parse_arguments(canonical_bytes(args))
    r = capability.recipient_params
    try:
        owners = await connection.fetch(
            "SELECT id FROM public.entities WHERE 'owner'=ANY(roles) "
            "AND metadata->>'merged_into' IS NULL AND metadata->>'deleted_at' IS NULL "
            "ORDER BY id LIMIT 2 FOR SHARE"
        )
    except Exception:
        raise BrokerRefused(Reason.POLICY) from None
    owner = owners[0]["id"] if len(owners) == 1 else None
    # A driver UUID subclass is a typed DB result, never raw model authority.
    if not isinstance(owner, UUID):
        owner = None
    present = tuple(n for n in r.argument_names if snapshot.get(n) not in (None, "", []))
    native = (
        capability.recipient_resolver == "core_notify"
        and capability.name == "notify"
        and capability.effect_class == "communicate"
        and set(r.argument_names) == {"recipient", "entity_id", "request_context"}
    )
    targets, default_owner, unknown = [], False, not native
    if capability.recipient_resolver == "calendar_response":
        # entry_id is an event locator, never an owner identity. Inherited
        # provider recipients remain unknown and therefore per-event PARK.
        targets = [None for _ in present]
    elif native:
        channel = snapshot.get("channel") or "telegram"
        intent = snapshot.get("intent", "send")
        # Context can select an inherited endpoint. Caller text, even an
        # owner-looking field, cannot authenticate it or erase that party.
        unknown = intent not in {"send", "insight"} or bool(snapshot.get("request_context"))
        unknown |= channel not in {"telegram", "email"}
        if "entity_id" in present:
            target = _uuid(snapshot["entity_id"])
            row = None
            if target is not None:
                row = await connection.fetchrow(
                    "SELECT id FROM public.entities WHERE id=$1 "
                    "AND metadata->>'merged_into' IS NULL AND metadata->>'deleted_at' IS NULL "
                    "FOR SHARE",
                    target,
                )
            targets.append(row["id"] if row is not None else None)
        if "recipient" in present:
            target = snapshot["recipient"]
            resolved = None
            if type(target) is str and channel in {"telegram", "email"}:
                resolved = await resolve_owner_channel_via_definer(connection, channel, target)
            targets.append(resolved[0].entity_id if resolved is not None else None)
        if "request_context" in present:
            targets.append(None)
        if not present:
            default_owner = channel == "telegram" and intent in {"send", "insight"}
            unknown |= not default_owner
    else:
        targets = [None for _ in present]
    binding = RecipientBinding(
        capability.identity,
        arguments_digest(snapshot),
        owner,
        present,
        tuple(targets),
        default_owner=default_owner,
        undeterminable=unknown,
    )
    return binding


class RecipientAuthorities:
    """Add independent identity reads to a real catalog/selection/policy adapter.

    The delegate still owns verified current registration, resource-selection
    association, real standing rules and actuation risk. This wrapper cannot
    turn absent adapters into authority. It never acquires another connection
    or performs a network call while the broker owns its goal transaction.
    """

    def __init__(self, delegate: Authorities):
        self.delegate = delegate

    async def catalog(self, connection, principal):
        return await self.delegate.catalog(connection, principal)

    async def selection(self, connection, principal, capability, grant):
        return await self.delegate.selection(connection, principal, capability, grant)

    async def policy(self, connection, principal, capability: Capability, args: dict):
        if not connection.is_in_transaction():
            raise BrokerRefused(Reason.POLICY)
        snapshot = parse_arguments(canonical_bytes(args))
        evidence = await self.delegate.policy(connection, principal, capability, snapshot)
        r = capability.recipient_params
        if not (r.argument_names or r.undeterminable or r.default_owner_condition):
            return evidence
        binding = await resolve_recipient_binding(connection, capability, snapshot)
        # Owner resolution does not prove link inertness or a standing-rule match.
        return replace(evidence, recipient_binding=binding)
