"""Owner rule 2 (owner decision 9): classify who a call communicates with.

Each party named in the capability's ``recipient_params`` is resolved with the
core-notify owner-resolution rule: a party is the owner only when it resolves
uniquely to the one live, unmerged, undeleted owner entity. Everything else,
including unknown, ambiguous, malformed and lookup-error parties and any party
the arguments cannot name, is ``non_owner``. Prose conditions in the catalog
are descriptions, never evaluated; only the closed ``owner_notify_branch``
marker lets a call that names nobody count as owner communication.

The classification can only over-park: a party form this module does not
recognise is ``non_owner``, which parks for owner approval.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from butlers.core.tool_catalog import OwnerNotifyBranch
from butlers.identity import (
    ROUTED_COMMUNICATION_CHANNEL_IDENTITY_TYPES,
    owner_channel_authorization_candidates,
    resolve_owner_channel_via_definer,
)

from .contracts import Capability, Recipient

_LIVE_OWNERS_SQL = (
    "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) "
    "AND metadata ->> 'merged_into' IS NULL AND metadata ->> 'deleted_at' IS NULL LIMIT 2"
)
_OWNER_DEFAULT_INTENTS = (None, "send", "insight")
_RESOLVE_OWNER_SQL = "SELECT entity_id FROM public.resolve_owner_triple($1, $2)"
_PHONE = "phone-digits:"


def _present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _owner_default_branch(capability: Capability, args: dict[str, Any]) -> bool:
    """Telegram send/insight to nobody: notify's declared owner default."""
    return (
        capability.recipient_params.owner_notify_branch
        is OwnerNotifyBranch.TELEGRAM_SEND_INSIGHT_WITHOUT_PARTY
        and args.get("channel") in (None, "telegram")
        and args.get("intent") in _OWNER_DEFAULT_INTENTS
    )


async def _live_owner(connection) -> UUID | None:
    async with connection.transaction():  # A failed read must not abort the call's transaction.
        rows = await connection.fetch(_LIVE_OWNERS_SQL)
    return rows[0]["id"] if len(rows) == 1 else None


def _channel(capability: Capability, args: dict[str, Any]) -> str | None:
    """The notify channel whose identifiers this call's parties are, if known."""
    if capability.name == "notify":
        channel = args.get("channel")
        if channel is None and not _present(args.get("entity_id")):
            return "telegram"  # notify's documented default without an entity
        return channel if type(channel) is str else None
    for channel in ROUTED_COMMUNICATION_CHANNEL_IDENTITY_TYPES:
        if capability.name.startswith(f"{channel}_"):
            return channel  # A transport module's own tool names its identifiers.
    return None


async def _is_owner(connection, value: Any, owner: UUID | None, channel: str | None) -> bool:
    if type(value) is not str:
        return False
    try:
        return owner is not None and UUID(value) == owner and str(UUID(value)) == value.lower()
    except ValueError:
        pass
    if channel is None and "@" in value:
        channel = "email"  # A party list outside any transport (e.g. invitees).
    channel_type = ROUTED_COMMUNICATION_CHANNEL_IDENTITY_TYPES.get(channel or "")
    if channel_type is None:
        return False
    try:
        async with connection.transaction():
            resolved = await resolve_owner_channel_via_definer(
                connection, channel_type, value, raise_on_error=True
            )
        if owner is None or resolved is None or resolved[0].entity_id != owner:
            return False
        return await _exactly_the_owner(connection, owner, channel_type, value)
    except Exception:
        return False


async def _resolves_to_owner(connection, owner: UUID, predicate: str, candidates) -> bool:
    if not candidates:
        return False
    async with connection.transaction():
        row = await connection.fetchrow(_RESOLVE_OWNER_SQL, predicate, list(candidates))
    return row is not None and row["entity_id"] is not None and UUID(str(row["entity_id"])) == owner


async def _exactly_the_owner(connection, owner: UUID, channel_type: str, value: str) -> bool:
    """Broker authorization needs an exact identifier, not the custody phone rule.

    The shared definer matches a WhatsApp JID's digits against stored phones by
    suffix (lengths within two), so a lookalike number resolves to the owner.
    Core notify keeps that rule. The broker also requires the exact JID, or the
    full number as stored, to be the owner's; anything fuzzier is non-owner and
    parks. The joint lookup above still guards against cross-entity ambiguity.
    """
    candidates = owner_channel_authorization_candidates(channel_type, value)
    phones = [c.removeprefix(_PHONE) for c in candidates if c.startswith(_PHONE)]
    if not phones:
        return True  # Email and handle candidates already matched exactly.
    exact = [c for c in candidates if not c.startswith(_PHONE)]
    if await _resolves_to_owner(connection, owner, "owner-channel", exact):
        return True
    numbers = [form for digits in phones for form in (f"+{digits}", digits)]
    return await _resolves_to_owner(connection, owner, "has-phone", numbers)


async def classify_recipients(
    connection, capability: Capability, args: dict[str, Any]
) -> Recipient:
    params = capability.recipient_params
    if not (params.argument_names or params.undeterminable or params.default_owner_condition):
        # A communication with no declared party cannot be shown to reach the owner.
        return "non_owner" if capability.effect_class == "communicate" else "none"
    if params.undeterminable and params.undeterminable_condition is None:
        return "non_owner"
    parties = [args[name] for name in params.argument_names if _present(args.get(name))]
    if not parties:
        if _owner_default_branch(capability, args):
            return "owner"
        if capability.effect_class == "communicate" or params.undeterminable:
            return "non_owner"
        return "none"
    if params.undeterminable and not _owner_default_branch(capability, args):
        # A conditional undeterminable branch (reply, react, inherited thread)
        # cannot be ruled out from prose, so only the declared owner branch
        # with explicitly named owner parties escapes it.
        return "non_owner"
    values = [item for party in parties for item in (party if type(party) is list else [party])]
    try:
        owner = await _live_owner(connection)
    except Exception:
        return "non_owner"
    channel = _channel(capability, args)
    for value in values:
        if not await _is_owner(connection, value, owner, channel):
            return "non_owner"
    return "owner"
