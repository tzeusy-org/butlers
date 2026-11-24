"""Deterministic approval envelopes and dependencies for durable delivery.

This module renders fixed dossier text and signed actions without model or
provider calls. Durable approval delivery owns admission, reservation and
handoff through the normal Switchboard → Messenger path; legacy push evidence
remains readable but has no sender here.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from butlers.core.approval_callbacks import mint_approval_callback_token
from butlers.modules.approvals.lineage_facts import lineage_facts
from butlers.modules.approvals.previews import defang_urls, resolve_call_preview


@dataclass(frozen=True, slots=True)
class ApprovalPushRuntime:
    """Live daemon dependencies for a deterministic approval push.

    The small injected boundary keeps the approvals module independent of the
    Switchboard client's concrete type and makes every delivery decision easy to
    exercise without an LLM runtime.
    """

    dispatch: Callable[[dict[str, Any]], Awaitable[None]]
    resolve_owner_recipient: Callable[[], Awaitable[str | None]]
    credential_store: Any | None
    dashboard_base_url: str | None = None


def _as_aware_datetime(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"approval push action {field_name} must be timezone-aware datetime")
    return value


def _action_id(action: Mapping[str, Any]) -> uuid.UUID:
    raw = action.get("id")
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("approval push action id must be a UUID") from exc


# Unicode categories that can hide, reorder or forge text: controls (C0/C1), format
# characters (bidi overrides, zero-width joiners, BOM) and lone surrogates.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})


def _unsafe_char(ch: str) -> bool:
    return ch != "\n" and unicodedata.category(ch) in _UNSAFE_CATEGORIES


def _has_unsafe_chars(value: str) -> bool:
    return any(_unsafe_char(ch) for ch in value)


def _utf16_units(text: str) -> int:
    """Telegram measures message length in UTF-16 code units, not code points."""
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _dossier_value(value: Any) -> str:
    if value is None:
        return "unspecified"
    normalized = "".join(ch for ch in str(value) if not _unsafe_char(ch)).strip()
    return normalized or "unspecified"


# Telegram caps a message at 4,096 UTF-16 code units; the card keeps headroom for the
# "[butler]" prefix the Messenger adds and for HTML escaping of the whole text.
CARD_FIT_BUDGET = 3500
_CLAIM_MAX_CHARS = 300


@dataclass(frozen=True, slots=True)
class ApprovalCard:
    """A rendered approval card and whether it may offer one-tap Approve/Reject."""

    message: str
    offer_decision: bool


def _span(value: str) -> str:
    """Wrap untrusted text so Telegram renders it literally.

    The transport HTML-escapes the message and then interprets a few Markdown
    constructs; a code span is inert to both (``<code>``/``<pre>`` content is
    escaped, never parsed).  Multi-line values use a fenced block.
    """
    if "\n" in value:
        return f"```\n{value}\n```"
    return f"`{value or '(empty)'}`"


def _claim_span(value: Any) -> str:
    """A model-written claim: defanged, bounded, and unable to break out of its span."""
    text = defang_urls(_dossier_value(value)).replace("`", "'")
    if len(text) > _CLAIM_MAX_CHARS:
        text = text[: _CLAIM_MAX_CHARS - 1] + "\u2026"
    return _span(text)


def render_approval_card(
    action: Mapping[str, Any],
    *,
    dashboard_base_url: str | None = None,
    origin_butler: str | None = None,
) -> ApprovalCard:
    """Render the exact stored call plus labelled claims, deterministically.

    Facts (recipients, subject, body) come from the stored ``tool_args`` through
    the preview registry.  The model-written dossier fields are *claims* and sit
    under "Assistant says".  When the whole call cannot be shown, the card says
    so and offers Open only (``offer_decision`` is False).
    """
    _action_id(action)
    expires_at = _as_aware_datetime(action.get("expires_at"), field_name="expires_at")
    tool_name = _dossier_value(action.get("tool_name"))
    preview = resolve_call_preview(tool_name, action.get("tool_args"))

    started_by = origin_butler or action.get("origin_butler")
    lineage = f"Started by: {_dossier_value(started_by)} butler"
    if action.get("origin") == "prepared":
        lineage += " (prepared action, not an owner push)"
    goal = lineage_facts(action)
    goal_lines = (
        (
            "Parked by a goal (server record):",
            *(f"  {f.label}: {_span(f.value)}" for f in goal.facts),
        )
        if goal is not None
        else ()
    )
    tail = (
        lineage,
        *goal_lines,
        "Assistant says (claims, not verified):",
        f"  Why: {_claim_span(action.get('why'))}",
        f"  Blast radius: {_claim_span(action.get('blast_radius'))}",
        f"  Reversibility: {_claim_span(action.get('reversibility'))}",
        f"Expires: {expires_at.astimezone(UTC).isoformat()}",
        "Review: use the dashboard button.",
    )

    if preview is not None and any(
        "`" in f.value or _has_unsafe_chars(f.value) for f in preview.facts
    ):
        reason = "Cannot be shown exactly here"
    elif preview is not None:
        lines = [f"Approval needed: {preview.title}"]
        for fact in preview.facts:
            value = defang_urls(fact.value)
            if "\n" in value:
                lines.extend((f"{fact.label}:", _span(value)))
            else:
                lines.append(f"{fact.label}: {_span(value)}")
        message = "\n".join((*lines, *tail))
        if _utf16_units(message) <= CARD_FIT_BUDGET:
            return ApprovalCard(message=message, offer_decision=True)
        reason = "Too long to show here"
    else:
        reason = "No exact preview for this tool"

    message = "\n".join(
        (
            f"Approval needed: {tool_name}",
            f"{reason}; review the full call before deciding. Approve and Reject are not offered.",
            *tail,
        )
    )
    return ApprovalCard(message=message, offer_decision=False)


def format_approval_request_message(
    action: Mapping[str, Any],
    *,
    dashboard_base_url: str | None = None,
    origin_butler: str | None = None,
) -> str:
    """Render the approval card text without LLM involvement."""
    return render_approval_card(
        action, dashboard_base_url=dashboard_base_url, origin_butler=origin_butler
    ).message


def build_approval_request_envelope(
    *,
    action: Mapping[str, Any],
    origin_butler: str,
    owner_recipient: str,
    callback_secret: str,
    dashboard_base_url: str | None = None,
    owner_tokens: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build one owner-only ``approval_request`` envelope for a parked action.

    Approve and Reject are attached only when the card shows the whole stored
    call; otherwise the owner gets Open only. ``owner_tokens`` (canonical owner
    delivery, bu-dhloi1.13) replaces the legacy ``apr1`` HMAC tokens: when given,
    a button is attached only for an action it holds a single-use token for.
    """
    action_id = _action_id(action)
    requested_at = _as_aware_datetime(action.get("requested_at"), field_name="requested_at")
    card = render_approval_card(
        action, dashboard_base_url=dashboard_base_url, origin_butler=origin_butler
    )
    actions: list[dict[str, Any]] = []
    if card.offer_decision and owner_tokens is not None:
        if owner_tokens.get("approve") and owner_tokens.get("reject"):
            for verb in ("approve", "reject"):
                actions.append({"verb": verb, "callback_token": owner_tokens[verb]})
    elif card.offer_decision:
        for verb, token_verb in (("approve", "a"), ("reject", "r")):
            actions.append(
                {
                    "verb": verb,
                    "callback_token": mint_approval_callback_token(
                        action_id=action_id,
                        verb=token_verb,
                        requested_at=requested_at,
                        secret=callback_secret,
                    ),
                }
            )
    actions.append(
        {
            "verb": "open_dashboard",
            "reference": {
                "kind": "self",
                "route_key": "approval_detail",
                "params": {"action_id": str(action_id)},
            },
        }
    )
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": card.message,
            "recipient": owner_recipient,
        },
        "actions": actions,
    }


def build_approval_digest_envelope(
    *,
    pending_count: int,
    origin_butler: str,
    owner_recipient: str,
    dashboard_base_url: str | None = None,
) -> dict[str, Any]:
    """Build the one non-interactive digest used after a park burst starts."""
    return {
        "schema_version": "notify.v1",
        "origin_butler": origin_butler,
        "delivery": {
            "intent": "approval_request",
            "channel": "telegram",
            "message": (
                f"{pending_count} actions awaiting review.\nReview: use the dashboard button."
            ),
            "recipient": owner_recipient,
        },
        "actions": [
            {
                "verb": "open_dashboard",
                "reference": {"kind": "self", "route_key": "approvals_queue", "params": {}},
            }
        ],
    }


__all__ = [
    "CARD_FIT_BUDGET",
    "ApprovalCard",
    "ApprovalPushRuntime",
    "build_approval_digest_envelope",
    "build_approval_request_envelope",
    "format_approval_request_message",
    "render_approval_card",
]
