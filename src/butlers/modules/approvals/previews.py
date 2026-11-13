"""Deterministic exact-call previews for approval cards (security.md § Approval Gates).

An approval executes the exact stored call the owner saw.  This module turns the
stored, unredacted ``pending_actions.tool_args`` of a gated tool into the *fact*
lines of an approval card (recipients, subject, full body) and binds the call to
a digest, so a tap on a card whose stored call has since changed executes
nothing.

Nothing here calls a model.  A tool earns the Approve/Reject affordances only
when it has a registered preview, the preview shows every argument the call
carries, and the effect class permits one-tap approval.  Everything else (no
preview, renderer error, an argument the card cannot show, ``administer`` and
protected or consequential actuation) fails closed to Open only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Effect classes that never get one-tap approval, whatever the preview says.
OPEN_ONLY_EFFECT_CLASSES = frozenset({"administer", "actuate_protected", "actuate_consequential"})


class PreviewError(ValueError):
    """The stored call cannot be shown exactly; the card fails closed to Open only."""


@dataclass(frozen=True, slots=True)
class PreviewFact:
    """One labelled line (or block) of the stored call."""

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class CallPreview:
    """The stored call as the owner will see it."""

    title: str
    facts: tuple[PreviewFact, ...]


PreviewRenderer = Callable[[Mapping[str, Any]], CallPreview]


@dataclass(frozen=True, slots=True)
class PreviewRegistration:
    renderer: PreviewRenderer
    effect_class: str = "send"


_REGISTRY: dict[str, PreviewRegistration] = {}


def register_preview(
    tool_name: str,
    renderer: PreviewRenderer,
    *,
    effect_class: str = "send",
) -> None:
    """Register (or replace) the preview renderer for a gated tool."""
    _REGISTRY[tool_name] = PreviewRegistration(renderer=renderer, effect_class=effect_class)


def unregister_preview(tool_name: str) -> None:
    _REGISTRY.pop(tool_name, None)


def args_digest(tool_args: Mapping[str, Any] | None) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no whitespace) of the stored call."""
    canonical = json.dumps(
        tool_args if tool_args is not None else {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_call_preview(tool_name: str, tool_args: Any) -> CallPreview | None:
    """Return the exact-call preview, or ``None`` when the card must be Open only.

    A tool whose catalog descriptor declares ``content_params`` is previewed from the catalog
    alone: a failure there stays Open only and never falls back to a hand-written renderer.
    Every other tool uses the S1 registry.
    """
    descriptor = _catalog_descriptor(tool_name)
    if descriptor is not None and descriptor.content_params is not None:
        return _catalog_preview(tool_name, descriptor, tool_args)
    registration = _REGISTRY.get(tool_name)
    if registration is None or registration.effect_class in OPEN_ONLY_EFFECT_CLASSES:
        return None
    if not isinstance(tool_args, Mapping):
        return None
    try:
        return registration.renderer(tool_args)
    except PreviewError as exc:
        logger.info("approval preview for %s fails closed: %s", tool_name, exc)
    except Exception:  # noqa: BLE001 - a renderer bug must never offer a blind Approve
        logger.warning("approval preview for %s raised; failing closed", tool_name, exc_info=True)
    return None


def dashboard_call_preview(tool_name: str, tool_args: Any) -> dict[str, Any] | None:
    """Return only complete registered facts; oversized/unshowable calls are Open-only.

    This has no model text, secret-field fallback or raw-argument dump. The
    dashboard renders every fact as inert plain text before using the snapshot.
    """
    preview = resolve_call_preview(tool_name, tool_args)
    if preview is None:
        return None
    result = {
        "title": preview.title,
        "facts": [{"label": fact.label, "value": fact.value} for fact in preview.facts],
        "displayed_call": {"tool_name": tool_name, "args_digest": args_digest(tool_args)},
    }
    # Keep an exact, fully shown preview rather than truncating an approved call.
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16_384:
        return None
    return result


# ---------------------------------------------------------------------------
# Catalog-driven previews (RFC 0038 D19)
# ---------------------------------------------------------------------------

_CATALOG_PREVIEW_EFFECT_CLASSES = frozenset({"communicate"})
_LABELS = {
    "to": "To",
    "recipient": "To",
    "chat_id": "Chat",
    "chat_jid": "Chat",
    "message_id": "Message ID",
    "text": "Message",
    "message": "Message",
    "subject": "Subject",
    "body": "Body",
}


def _catalog_descriptor(tool_name: str) -> Any:
    """The checked-in presentation descriptor of a canonical tool name, or ``None``."""
    from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

    return TOOL_PRESENTATION_BY_NAME.get(tool_name)


def _label(argument: str) -> str:
    return _LABELS.get(argument) or argument.replace("_", " ").capitalize()


def _catalog_value(args: Mapping[str, Any], name: str, *, content: bool) -> str:
    value = args.get(name)
    if isinstance(value, bool):
        raise PreviewError(f"{name} is not shown")
    if isinstance(value, int):
        if value <= 0:
            raise PreviewError(f"{name} is not a positive integer")
        return str(value)
    if isinstance(value, str):
        if not value.strip():
            raise PreviewError(f"{name} is blank")
        return value
    if not content and isinstance(value, list):
        return _recipients(args, name, required=True) or ""
    raise PreviewError(f"{name} is missing or not text")


def _catalog_preview(tool_name: str, descriptor: Any, tool_args: Any) -> CallPreview | None:
    """Rebuild the complete stored call from ``recipient_params`` plus ``content_params``."""
    try:
        if not isinstance(tool_args, Mapping):
            return None
        if descriptor.effect_class not in _CATALOG_PREVIEW_EFFECT_CLASSES:
            return None
        recipients = descriptor.recipient_params
        recipient_names = tuple(getattr(recipients, "argument_names", ()) or ())
        content_names = tuple(descriptor.content_params or ())
        if not recipient_names or not content_names:
            raise PreviewError("descriptor does not name both recipients and content")
        # Every non-empty argument must be one the descriptor names, and so shown.
        _reject_unshown(tool_args, frozenset(recipient_names) | frozenset(content_names))
        facts = [
            PreviewFact(_label(name), _catalog_value(tool_args, name, content=False))
            for name in recipient_names
            if name in tool_args
        ]
        if not facts or facts[0].label == _label("message_id") and len(facts) == 1:
            raise PreviewError("no recipient is shown")
        facts += [
            PreviewFact(_label(name), _catalog_value(tool_args, name, content=True))
            for name in content_names
        ]
        return CallPreview(title=tool_name.replace("_", " ").capitalize(), facts=tuple(facts))
    except PreviewError as exc:
        logger.info("catalog approval preview for %s fails closed: %s", tool_name, exc)
    except Exception:  # noqa: BLE001 - a renderer bug must never offer a blind Approve
        logger.warning(
            "catalog approval preview for %s raised; failing closed", tool_name, exc_info=True
        )
    return None


# ---------------------------------------------------------------------------
# Renderer helpers
# ---------------------------------------------------------------------------


def _require_text(args: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = args.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise PreviewError(f"{key} is missing or not text")
    return value


def _optional_text(args: Mapping[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise PreviewError(f"{key} is not text")
    return value


def _recipients(args: Mapping[str, Any], key: str, *, required: bool) -> str | None:
    """Enumerate every recipient in ``args[key]`` or fail closed."""
    value = args.get(key)
    if value is None or value == [] or value == "":
        if required:
            raise PreviewError(f"{key} recipients are not enumerable")
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) and i.strip() for i in items):
        raise PreviewError(f"{key} recipients are not enumerable")
    return ", ".join(i.strip() for i in items)


# Gate metadata (the model-written dossier) is rendered separately as claims.
_DOSSIER_KEYS = frozenset({"_why", "_blast_radius", "_reversibility", "_evidence"})


def _reject_unshown(
    args: Mapping[str, Any], shown: frozenset[str], ignorable: frozenset[str] = frozenset()
) -> None:
    """Every non-empty argument must be shown (or provably irrelevant to the effect)."""
    for key, value in args.items():
        if key in shown or key in ignorable or key in _DOSSIER_KEYS:
            continue
        if value not in (None, "", [], {}):
            raise PreviewError(f"argument {key!r} is not shown by this preview")


def _email_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(args, frozenset({"to", "cc", "bcc", "subject", "body"}))
    facts = [PreviewFact("To", _recipients(args, "to", required=True) or "")]
    for key in ("cc", "bcc"):
        recipients = _recipients(args, key, required=False)
        if recipients:
            facts.append(PreviewFact(key.capitalize(), recipients))
    facts.append(PreviewFact("Subject", _require_text(args, "subject", allow_empty=True)))
    facts.append(PreviewFact("Body", _require_text(args, "body", allow_empty=True)))
    return CallPreview(title="Send email", facts=tuple(facts))


def _telegram_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(args, frozenset({"chat_id", "text"}))
    return CallPreview(
        title="Send Telegram message",
        facts=(
            PreviewFact("To", _require_text(args, "chat_id")),
            PreviewFact("Message", _require_text(args, "text")),
        ),
    )


def _positive_message_id(args: Mapping[str, Any]) -> str:
    message_id = args.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        raise PreviewError("message_id is missing or not a positive integer")
    return str(message_id)


def _telegram_reply_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(args, frozenset({"chat_id", "message_id", "text"}))
    return CallPreview(
        title="Reply to Telegram message",
        facts=(
            PreviewFact("To", _require_text(args, "chat_id")),
            PreviewFact("In reply to", _positive_message_id(args)),
            PreviewFact("Message", _require_text(args, "text")),
        ),
    )


def _email_reply_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(args, frozenset({"to", "thread_id", "subject", "body"}))
    facts = [
        PreviewFact("To", _recipients(args, "to", required=True) or ""),
        PreviewFact("Thread", _require_text(args, "thread_id")),
    ]
    subject = _optional_text(args, "subject")
    if subject is not None:
        facts.append(PreviewFact("Subject", subject))
    facts.append(PreviewFact("Body", _require_text(args, "body", allow_empty=True)))
    return CallPreview(title="Reply to email thread", facts=tuple(facts))


def _telegram_reaction_preview(args: Mapping[str, Any]) -> CallPreview:
    if set(args) - {"chat_id", "message_id", "emoji"} - _DOSSIER_KEYS:
        raise PreviewError("reaction has unshown arguments")
    message_id = args.get("message_id")
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
        raise PreviewError("message_id is missing or not a positive integer")
    return CallPreview(
        title="React to Telegram message",
        facts=(
            PreviewFact("Chat", _require_text(args, "chat_id")),
            PreviewFact("Message ID", str(message_id)),
            PreviewFact("Emoji", _require_text(args, "emoji")),
        ),
    )


def _whatsapp_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(args, frozenset({"recipient", "text"}))
    return CallPreview(
        title="Send WhatsApp message",
        facts=(
            PreviewFact("To", _require_text(args, "recipient")),
            PreviewFact("Message", _require_text(args, "text")),
        ),
    )


def _notify_preview(args: Mapping[str, Any]) -> CallPreview:
    _reject_unshown(
        args,
        frozenset({"channel", "recipient", "subject", "message"}),
        ignorable=frozenset({"intent", "priority", "emoji"}),
    )
    intent = args.get("intent")
    if intent not in (None, "send"):
        raise PreviewError("only send intent has a preview")
    channel = _optional_text(args, "channel") or "telegram"
    facts = [
        PreviewFact("Channel", channel),
        PreviewFact("To", _require_text(args, "recipient")),
    ]
    subject = _optional_text(args, "subject")
    if subject:
        facts.append(PreviewFact("Subject", subject))
    facts.append(PreviewFact("Message", _require_text(args, "message")))
    return CallPreview(title="Send notification", facts=tuple(facts))


register_preview("email_send_message", _email_preview)
register_preview("telegram_send_message", _telegram_preview)
register_preview("telegram_reply_to_message", _telegram_reply_preview)
register_preview("email_reply_to_thread", _email_reply_preview)
register_preview(
    "telegram_react_to_message", _telegram_reaction_preview, effect_class="communicate"
)
register_preview("whatsapp_send_message", _whatsapp_preview)
register_preview("notify", _notify_preview)


# ---------------------------------------------------------------------------
# Span safety
# ---------------------------------------------------------------------------

_DEFANGED_SCHEMES = {"http": "hxxp", "https": "hxxps", "ftp": "fxp"}
_URL_RE = re.compile(r"(?i)\b(https?|ftp)://[^\s<>\"'`]+")


def defang_urls(text: str) -> str:
    """Make URLs inert: ``https://a.b/c`` becomes ``hxxps://a[.]b/c``."""

    def _defang(match: re.Match[str]) -> str:
        scheme, _, rest = match.group(0).partition("://")
        host, sep, tail = rest.partition("/")
        return f"{_DEFANGED_SCHEMES[scheme.lower()]}://{host.replace('.', '[.]')}{sep}{tail}"

    return _URL_RE.sub(_defang, text)


__all__ = [
    "OPEN_ONLY_EFFECT_CLASSES",
    "CallPreview",
    "PreviewError",
    "PreviewFact",
    "PreviewRegistration",
    "args_digest",
    "defang_urls",
    "register_preview",
    "resolve_call_preview",
    "unregister_preview",
]
