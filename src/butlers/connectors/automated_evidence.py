"""Typed, bound evidence from an automated email reply or bounce (bu-s11n0s.14, slice S2a).

``detect_automated_reply`` (``gmail_policy``) classifies a message from headers the sender
wrote, so ``automated_kind`` is a hint: anyone can forge ``From: MAILER-DAEMON`` or
``Auto-Submitted``. This module turns that hint into evidence a later writer may act on, without
an LLM and without copying third-party text anywhere:

- **Binding.** The message must answer mail the owner actually sent. The connector looks up the
  referenced original in the owner's SENT mailbox by its Message-ID; an auto-reply's sender must
  have been a recipient of that original, and a bounce reports only addresses it was sent to.
  A DSN for mail the owner never sent (backscatter) binds nothing.
- **Authentication.** Gmail's own ``Authentication-Results`` (authserv-id ``mx.google.com``,
  the topmost header) must show ``dmarc=pass``, or ``dkim=pass`` for the From domain.
- **Permanence.** Only a recipient whose ``Action`` is ``failed`` with a ``5.x.x`` status counts
  as failed. ``delayed``, ``delivered``, ``relayed``, ``expanded`` and ``4.x.x`` never do.
- **Return date.** An auto-reply's subject and plain body are untrusted text. The parser reads
  only a calendar date that follows a return phrase (``until``, ``back on``, ``returning``…),
  within 180 days of receipt. An ambiguous or conflicting date yields none. Only the typed date
  leaves this module.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.utils import getaddresses, parseaddr
from typing import Any

from butlers.account_security import VERIFICATION_AUTHENTICATED, verify_sender_domain

#: Return dates further out than this are refused: an auto-reply is not a sabbatical plan.
MAX_AWAY_DAYS = 180
#: A DSN with more recipients than this is malformed or abusive; the rest are ignored.
MAX_DSN_RECIPIENTS = 16
#: Only Gmail's own receiving server's verdict counts, never one a sender wrote.
TRUSTED_AUTHSERV_ID = "mx.google.com"
_MAX_PART_DEPTH = 8
_MAX_PART_BYTES = 64 * 1024


@dataclass(frozen=True)
class DsnRecipient:
    """One per-recipient block of an RFC 3464 ``message/delivery-status`` part."""

    final_recipient: str
    action: str
    status: str

    @property
    def permanently_failed(self) -> bool:
        return self.action == "failed" and self.status.startswith("5.")


@dataclass(frozen=True)
class DeliveryStatus:
    recipients: tuple[DsnRecipient, ...] = ()
    original_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutomatedEvidence:
    """What the connector verified about an automated message. Carries no third-party text."""

    original_sent: bool
    """The message names, by Message-ID, mail found in the owner's SENT mailbox."""
    sender_was_recipient: bool
    """The sender was a To/Cc/Bcc recipient of that original (auto-replies)."""
    sender_authenticated: bool
    """Gmail's own Authentication-Results passed DMARC, or DKIM for the From domain."""
    away_until: date | None = None
    """Auto-reply return date; only set when bound to sent mail and authenticated."""
    failed_recipients: tuple[str, ...] = field(default_factory=tuple)
    """Bounce: addresses the original was sent to that permanently failed."""

    def to_control(self) -> dict[str, Any]:
        return {
            "original_sent": self.original_sent,
            "sender_was_recipient": self.sender_was_recipient,
            "sender_authenticated": self.sender_authenticated,
            "away_until": self.away_until.isoformat() if self.away_until else None,
            "failed_recipients": list(self.failed_recipients),
        }


# --------------------------------------------------------------------------- addresses


def normalize_address(value: str) -> str:
    """Bare lowercased address, or ``""`` when the value holds none."""
    address = parseaddr(value or "")[1].strip().lower()
    return address if "@" in address else ""


def bracketed_message_id(value: str) -> str:
    bare = (value or "").strip().strip("<>").strip()
    return f"<{bare}>" if bare and " " not in bare else ""


def _domain(address: str) -> str:
    return address.rpartition("@")[2]


# --------------------------------------------------------------------------- headers


def _header_pairs(headers: Any) -> list[tuple[str, str]]:
    if isinstance(headers, Mapping):
        items: Iterable[Any] = headers.items()
    elif isinstance(headers, list):
        items = ((h.get("name"), h.get("value")) for h in headers if isinstance(h, Mapping))
    else:
        return []
    return [
        (name.strip().lower(), value)
        for name, value in items
        if isinstance(name, str) and isinstance(value, str)
    ]


def _first(headers: Any, name: str) -> str:
    for key, value in _header_pairs(headers):
        if key == name:
            return value
    return ""


def referenced_message_ids(headers: Any) -> tuple[str, ...]:
    """In-Reply-To first, then References newest first, bracketed and de-duplicated."""
    found: list[str] = []
    in_reply_to = _first(headers, "in-reply-to")
    references = _first(headers, "references")
    for raw in (
        *re.findall(r"<[^<>\s]+>", in_reply_to),
        *reversed(re.findall(r"<[^<>\s]+>", references)),
    ):
        mid = bracketed_message_id(raw)
        if mid and mid not in found:
            found.append(mid)
    return tuple(found[:8])


_AUTHSERV_ID = re.compile(r"\s*([^\s;()\"]+)\s*;")


def sender_authenticated(headers: Any) -> bool:
    """Gmail's own verdict on the From domain is ``authenticated``.

    Only the topmost ``Authentication-Results`` header counts, and only when its authserv-id is
    Gmail's receiving server: headers further down were written by whoever sent the message.
    The verdict is the shared ingest one (:func:`butlers.account_security.verify_sender_domain`):
    its RFC 8601 parser is quote-, comment- and literal-aware, a method counts only at the start
    of its own clause, a ``dmarc=pass`` counts only for its own ``header.from`` and a
    ``dkim=pass`` only for an aligned ``header.d``, any ``dmarc`` fail wins, and a header it
    cannot parse is never authenticated.
    """
    results = _first(headers, "authentication-results")
    authserv = _AUTHSERV_ID.match(results)
    if authserv is None or authserv.group(1).lower() != TRUSTED_AUTHSERV_ID:
        return False
    from_domain = _domain(normalize_address(_first(headers, "from")))
    if not from_domain:
        return False
    return verify_sender_domain(results, from_domain) == VERIFICATION_AUTHENTICATED


# --------------------------------------------------------------------------- DSN parsing


def _iter_parts(payload: Any) -> Iterable[dict[str, Any]]:
    stack = [(payload, 0)]
    while stack:
        part, depth = stack.pop()
        if not isinstance(part, dict) or depth > _MAX_PART_DEPTH:
            continue
        yield part
        children = part.get("parts")
        if isinstance(children, list):
            stack.extend((child, depth + 1) for child in reversed(children))


def _part_text(part: dict[str, Any]) -> str:
    body = part.get("body")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, str) or not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    return raw[:_MAX_PART_BYTES].decode("utf-8", errors="replace")


def _field_blocks(text: str) -> list[dict[str, str]]:
    """RFC 822-style field groups separated by blank lines, folding unwrapped."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    last = ""
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if current:
                blocks.append(current)
            current, last = {}, ""
            continue
        if line[0] in " \t" and last:
            current[last] += " " + line.strip()
            continue
        name, sep, value = line.partition(":")
        if not sep:
            continue
        last = name.strip().lower()
        current.setdefault(last, value.strip())
    if current:
        blocks.append(current)
    return blocks


def parse_delivery_status(payload: Any) -> DeliveryStatus:
    """Recipients and original Message-IDs from a bounce's MIME tree; malformed means empty."""
    recipients: list[DsnRecipient] = []
    originals: list[str] = []
    for part in _iter_parts(payload):
        mime = str(part.get("mimeType", "")).lower()
        if mime == "message/delivery-status":
            for block in _field_blocks(_part_text(part)):
                final = block.get("final-recipient", "")
                _type, _, address = final.partition(";")
                address = normalize_address(address.strip())
                if not address or len(recipients) >= MAX_DSN_RECIPIENTS:
                    continue
                status = block.get("status", "").split()[0] if block.get("status") else ""
                recipients.append(
                    DsnRecipient(
                        final_recipient=address,
                        action=block.get("action", "").strip().lower(),
                        status=status,
                    )
                )
        elif mime in ("text/rfc822-headers", "message/rfc822"):
            for block in _field_blocks(_part_text(part))[:1]:
                mid = bracketed_message_id(block.get("message-id", ""))
                if mid and mid not in originals:
                    originals.append(mid)
            # Gmail exposes an attached message's own headers on its first child part.
            children = part.get("parts") if mime == "message/rfc822" else None
            inner = children[0] if isinstance(children, list) and children else {}
            if isinstance(inner, dict):
                mid = bracketed_message_id(_first(inner.get("headers"), "message-id"))
                if mid and mid not in originals:
                    originals.append(mid)
    return DeliveryStatus(tuple(recipients), tuple(originals))


# --------------------------------------------------------------------------- return date

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = "(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(\d{4}))?"
_WEEKDAY = r"(?:(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\.?,?\s+)?"
_CUE = re.compile(
    r"\b(?:until|till|through|thru|returning(?:\s+on)?|return(?:ing)?\s+on|back(?:\s+in(?:\s+the)?"
    r"\s+office)?(?:\s+on)?|in\s+the\s+office\s+(?:again\s+)?on)\b[\s:,]*",
    re.IGNORECASE,
)
_DATE_FORMS = (
    ("iso", re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("dmy_name", re.compile(_WEEKDAY + _DAY + r"\s+(?:of\s+)?" + _MONTH + _YEAR, re.IGNORECASE)),
    ("mdy_name", re.compile(_WEEKDAY + _MONTH + r"\s+" + _DAY + _YEAR, re.IGNORECASE)),
    # Only "/" separates a numeric date: "until 5.30" and "out until 4.15 today" are times.
    ("numeric", re.compile(_WEEKDAY + r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")),
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve_year(month: int, day: int, year: str | None, received: date) -> date | None:
    if year:
        number = int(year)
        return _safe_date(number + 2000 if number < 100 else number, month, day)
    candidate = _safe_date(received.year, month, day)
    if candidate is not None and candidate < received:
        candidate = _safe_date(received.year + 1, month, day)
    return candidate


def _date_at(text: str, received: date) -> date | None | bool:
    """The date that starts *text*; ``False`` when it is ambiguous (refuse, do not guess)."""
    for form, pattern in _DATE_FORMS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        if form == "iso":
            return _safe_date(int(groups[0]), int(groups[1]), int(groups[2]))
        if form == "dmy_name":
            return _resolve_year(_MONTHS[groups[1].lower()], int(groups[0]), groups[2], received)
        if form == "mdy_name":
            return _resolve_year(_MONTHS[groups[0].lower()], int(groups[1]), groups[2], received)
        first, second, year = int(groups[0]), int(groups[1]), groups[2]
        if first <= 12 and second <= 12 and first != second:
            return False  # 03/04 is March 4th or 3rd April: refuse rather than guess
        day, month = (first, second) if first > 12 else (second, first)
        return _resolve_year(month, day, year, received)
    return None


def parse_return_date(text: str, received: date) -> date | None:
    """The single return date an auto-reply states, or None.

    Reads only a date right after a return cue. A date before receipt or more than
    ``MAX_AWAY_DAYS`` after it, an ambiguous numeric date, or two different dates all yield
    None. Nothing but the date is returned.
    """
    if not text:
        return None
    found: set[date] = set()
    for cue in _CUE.finditer(text[:_MAX_PART_BYTES]):
        value = _date_at(text[cue.end() : cue.end() + 64], received)
        if value is False:
            return None
        if isinstance(value, date):
            found.add(value)
    if len(found) != 1:
        return None
    (away_until,) = found
    if not received <= away_until <= received + timedelta(days=MAX_AWAY_DAYS):
        return None
    return away_until


# --------------------------------------------------------------------------- evidence


def auto_reply_evidence(
    headers: Any,
    *,
    original_recipients: frozenset[str] | None,
    subject: str,
    body_text: str,
    received: date,
) -> AutomatedEvidence:
    """Evidence for an auto-reply; ``original_recipients`` is None when no sent original matched."""
    sender = normalize_address(_first(headers, "from"))
    bound = original_recipients is not None and sender in original_recipients
    authenticated = sender_authenticated(headers)
    away_until = None
    if bound and authenticated:
        away_until = parse_return_date(f"{subject}\n{body_text}", received)
    return AutomatedEvidence(
        original_sent=original_recipients is not None,
        sender_was_recipient=bound,
        sender_authenticated=authenticated,
        away_until=away_until,
    )


def bounce_evidence(
    headers: Any,
    status: DeliveryStatus,
    *,
    original_recipients: frozenset[str] | None,
) -> AutomatedEvidence:
    """Evidence for a bounce: only addresses the owner's original was sent to can fail."""
    failed: tuple[str, ...] = ()
    if original_recipients is not None:
        failed = tuple(
            sorted(
                {
                    r.final_recipient
                    for r in status.recipients
                    if r.permanently_failed and r.final_recipient in original_recipients
                }
            )
        )
    return AutomatedEvidence(
        original_sent=original_recipients is not None,
        sender_was_recipient=False,
        sender_authenticated=sender_authenticated(headers),
        failed_recipients=failed,
    )


def recipients_of(headers: Any) -> frozenset[str]:
    """Every To/Cc/Bcc address of a sent message's headers."""
    values = [v for k, v in _header_pairs(headers) if k in ("to", "cc", "bcc")]
    return frozenset(
        address for _name, raw in getaddresses(values) if (address := normalize_address(raw))
    )


__all__ = [
    "AutomatedEvidence",
    "DeliveryStatus",
    "DsnRecipient",
    "MAX_AWAY_DAYS",
    "auto_reply_evidence",
    "bounce_evidence",
    "bracketed_message_id",
    "normalize_address",
    "parse_delivery_status",
    "parse_return_date",
    "recipients_of",
    "referenced_message_ids",
    "sender_authenticated",
]
