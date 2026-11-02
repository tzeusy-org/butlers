"""Immutable informational questions; routing authority is supplied by service composition."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from butlers.core.delivery_rendering import defang_delivery_text


def _text(value: str, limit: int, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must contain 1-{limit} characters")


@dataclass(frozen=True, slots=True)
class ConfirmOption:
    value: str
    label: str

    def __post_init__(self):
        _text(self.value, 64, "option value")
        _text(self.label, 80, "option label")


@dataclass(frozen=True, slots=True)
class Recipient:
    """Resolved by a trusted producer, never accepted from callback JSON."""

    entity_id: UUID
    sender_id: str
    chat_id: str
    owner: bool

    def __post_init__(self):
        if not isinstance(self.entity_id, UUID) or type(self.owner) is not bool:
            raise ValueError("verified recipient entity and owner classification required")
        _text(self.sender_id, 128, "sender identity")
        _text(self.chat_id, 128, "chat identity")


@dataclass(frozen=True, slots=True)
class ConfirmCard:
    facts: tuple[str, ...] = ()
    claims: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()

    def __post_init__(self):
        for group in (self.facts, self.claims, self.quotes):
            if not isinstance(group, tuple) or len(group) > 8:
                raise ValueError("card spans must be bounded immutable tuples")
            for text in group:
                _text(text, 256, "card span")

    def lines(self) -> list[str]:
        return [
            *self.facts,
            *(f"Assistant claim: {' '.join(s.splitlines())}" for s in self.claims),
            *(
                f"Quoted input: {defang_delivery_text(' '.join(s.splitlines()))}"
                for s in self.quotes
            ),
        ]


@dataclass(frozen=True, slots=True)
class ConfirmPrompt:
    question: str
    options: tuple[ConfirmOption, ...]
    recipient: Recipient
    timeout_seconds: int = 600
    on_timeout_value: str | None = None
    card: ConfirmCard = ConfirmCard()

    def __post_init__(self):
        _text(self.question, 512, "question")
        if not isinstance(self.options, tuple) or not 2 <= len(self.options) <= 4:
            raise ValueError("confirm requires 2-4 immutable options")
        if not all(isinstance(o, ConfirmOption) for o in self.options):
            raise ValueError("typed confirm options required")
        values = {o.value for o in self.options}
        if len(values) != len(self.options):
            raise ValueError("confirm option values must be unique")
        if type(self.timeout_seconds) is not int or not 30 <= self.timeout_seconds <= 86400:
            raise ValueError("confirm timeout must be between 30 and 86400 seconds")
        if self.on_timeout_value is not None and self.on_timeout_value not in values:
            raise ValueError("timeout value must reference an offered option")
        if not isinstance(self.recipient, Recipient) or not isinstance(self.card, ConfirmCard):
            raise ValueError("resolved recipient and typed card required")
        if len(self.render_text().encode("utf-16-le")) // 2 > 3000:
            raise ValueError("confirm card exceeds the single-message budget")

    def render_text(self) -> str:
        """Plain text for Telegram (no parse mode) and escaped React text nodes."""
        return "\n".join((self.question, *self.card.lines()))


def effect_digest(producer: str, prompt: ConfirmPrompt) -> str:
    """Exact preauthorized notify arguments; computed by the broker before dispatch.

    Includes route and producer, not just visible text. A delivery reference for
    another question or channel identity is never sufficient authority.
    """
    from dataclasses import asdict

    from butlers.core.goal_admission import digest

    envelope = asdict(prompt)
    envelope["recipient"]["entity_id"] = str(prompt.recipient.entity_id)
    return digest({"intent": "confirm", "producer": producer, "prompt": envelope})
