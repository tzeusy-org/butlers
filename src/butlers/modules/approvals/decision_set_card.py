"""The Telegram card of one decision set, and the selections its two buttons stand for.

The card offers exactly Approve, Reject and Open (bu-jv1moa.27 P2). Approve decides the *default
selection*: every exact plain item, never an ``explicit_only`` (administer) item. Reject rejects
every item. Per-item unticking lives on the dashboard submit route only, so no tick state and no
toggle callback exists on Telegram.

Neither selection is carried in a callback. They are recomputed from the set at redeem time, and
:func:`action_digest` binds the digests the card showed: a set that changed since (another version,
an item that stopped being exact) redeems as ``action_changed``. A set that cannot be shown exactly
and completely in one message offers Open only; nothing is truncated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from uuid import UUID

from butlers.core.owner_delivery import digest_of
from butlers.modules.approvals.decision_set_view import ItemView, SetView
from butlers.modules.approvals.notifications import (
    CARD_FIT_BUDGET,
    _claim_span,
    _has_unsafe_chars,
    _span,
    _utf16_units,
)
from butlers.modules.approvals.previews import defang_urls

#: What the card says an unticked item comes to, by the producer's ``UNTICKED_OUTCOME``. The
#: owner decision on that value is open (Q3), so nothing here assumes one; an unknown value reads
#: as the neutral "not approved" rather than a promise.
_UNTICKED_PHRASE = {
    "left": "stay waiting for your decision",
    "rejected": "are rejected",
}
_NOT_OFFERED = "Approve and Reject are not offered."
_OPEN_ONLY_WHY = {
    "state": "This set is no longer open",
    "expired": "This set has expired",
    "items": "A call in this set cannot be shown exactly here",
}


@dataclass(frozen=True, slots=True)
class SetCard:
    """A rendered set card and whether it may offer one-tap Approve/Reject."""

    message: str
    offer_decision: bool


def approve_selection(view: SetView) -> dict[UUID, str] | None:
    """The default selection Approve decides: effect id -> item digest, or ``None`` if none.

    Exact plain items only. ``None`` when the set is not decidable or has nothing to approve
    without an item's own tick.
    """
    if not view.decidable:
        return None
    picked = {item.effect_id: item.item_digest for item in view.items if not item.explicit_only}
    return picked or None


def reject_selection(view: SetView) -> tuple[dict[UUID, str], dict[UUID, str]] | None:
    """What Reject decides: ``(plain, explicit_only)`` maps of every item, or ``None``."""
    if not view.decidable:
        return None
    plain = {item.effect_id: item.item_digest for item in view.items if not item.explicit_only}
    explicit = {item.effect_id: item.item_digest for item in view.items if item.explicit_only}
    return plain, explicit


def action_digest(view: SetView) -> str:
    """The digest a card token binds: the exact set version and its default selection.

    An undecidable view digests differently from any decidable one, so a token minted for a
    set that has since stopped being exact is refused ``action_changed``.
    """
    chosen = approve_selection(view)
    return digest_of(
        {
            "kind": "decision_set",
            "set": str(view.set_id),
            "version": view.version,
            "digest": view.digest,
            "decidable": view.decidable,
            "approve": sorted([str(e), d] for e, d in (chosen or {}).items()),
        }
    )


def _label(item: ItemView) -> str:
    return f"{item.position + 1}"


def _shown_exactly(items: tuple[ItemView, ...]) -> bool:
    for item in items:
        values = [f.value for f in item.facts]
        values += [f.value for f in item.lineage.facts] if item.lineage is not None else []
        if any("`" in v or _has_unsafe_chars(v) for v in values):
            return False
    return True


def _item_lines(item: ItemView) -> list[str]:
    lines = [f"{_label(item)}. {_claim_span(item.title)}"]
    for fact in item.facts:
        value = defang_urls(fact.value)
        if "\n" in value:
            lines.extend((f"   {fact.label}:", _span(value)))
        else:
            lines.append(f"   {fact.label}: {_span(value)}")
    if item.lineage is not None:
        lines.append("   Parked by a goal (server record):")
        lines.extend(f"     {f.label}: {_span(f.value)}" for f in item.lineage.facts)
    if item.claims:
        lines.append("   Assistant says (claims, not verified):")
        lines.extend(f"     {c.label}: {_claim_span(c.value)}" for c in item.claims)
    return lines


def render_set_card(view: SetView) -> SetCard:
    """Every item's exact stored call, what Approve covers, and what happens to the rest."""
    count = len(view.items)
    header = f"Decision set: {count} call{'s' if count != 1 else ''} waiting"
    chosen = approve_selection(view)
    if chosen is None:
        reason = _OPEN_ONLY_WHY.get(view.reason or "", "No call here can be approved from a card")
        return _open_only(header, reason)
    if not _shown_exactly(view.items):
        return _open_only(header, "Cannot be shown exactly here")
    covered = [_label(i) for i in view.items if i.effect_id in chosen]
    own_tick = [_label(i) for i in view.items if i.explicit_only]
    phrase = _UNTICKED_PHRASE.get(view.unticked_outcome, "are not approved")
    lines = [header]
    for item in view.items:
        lines.extend(_item_lines(item))
    lines.append(f"Approve covers: {', '.join(covered)}")
    if own_tick:
        lines.append(f"Needs your own tick on the dashboard: {', '.join(own_tick)}")
    lines.append(f"Items Approve does not cover {phrase}.")
    lines.append(f"Reject rejects all {count}.")
    lines.append(f"Expires: {view.expires_at.astimezone(UTC).isoformat()}")
    lines.append("Review or untick single items: use the dashboard button.")
    message = "\n".join(lines)
    if _utf16_units(message) > CARD_FIT_BUDGET:
        return _open_only(header, "Too long to show here")
    return SetCard(message=message, offer_decision=True)


def _open_only(header: str, reason: str) -> SetCard:
    return SetCard(
        message="\n".join(
            (
                header,
                f"{reason}; review each call on the dashboard before deciding. {_NOT_OFFERED}",
            )
        ),
        offer_decision=False,
    )


__all__ = [
    "SetCard",
    "action_digest",
    "approve_selection",
    "reject_selection",
    "render_set_card",
]
