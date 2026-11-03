"""Inactive standing-reply template drafts: typed grammar, owner authoring, preview.

A standing-reply template draft (bu-jv1moa.10) is the owner's typed description of
a reply they may later let go out without a per-event tap.  This slice stores and
previews drafts only.  A draft never auto-approves or executes anything:

- drafts live in ``standing_reply_template_drafts``, which the approval gate never
  reads (it matches only active ``approval_rules``);
- there is no activation path, no promotion into ``approval_rules`` and no
  caller-controlled active flag;
- every preview result carries ``authority: "none"``.

Activation, broker enforcement, per-window limits and receipts belong to
bu-jv1moa.28 and must be separately adopted before any of this gains authority.

Grammar (``REQ-capability-authorization-006``): each content-bearing argument of
the send tool gets one owner-authored template matched against the whole value.
A template is fixed text plus typed slots written ``{name:type}``, where type is
one of :data:`SLOT_TYPES`.  There are no free-text, identifier, URL or recipient
slots, adjacent slots are refused (their split would be ambiguous), and every
recipient is pinned to a resolved entity.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

#: The only slot types a template may declare.
SLOT_TYPES: frozenset[str] = frozenset({"date", "time", "datetime", "duration", "amount", "count"})

#: Value grammar per slot type.  A captured value must also parse (see _slot_value_ok).
_SLOT_PATTERNS: dict[str, str] = {
    "date": r"\d{4}-\d{2}-\d{2}",
    "time": r"\d{2}:\d{2}(?::\d{2})?",
    "datetime": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?",
    "duration": r"\d{1,4} (?:minute|minutes|hour|hours|day|days|week|weeks)",
    "amount": r"\d{1,12}(?:\.\d{2})?",
    "count": r"\d{1,6}",
}

_SLOT_RE = re.compile(r"\{([^{}]*)\}")
_SLOT_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_TEMPLATE_LENGTH = 2000
MAX_RECIPIENTS = 20


@dataclass(frozen=True)
class SendToolShape:
    """Which argument names the recipient and which arguments carry content."""

    recipient_arg: str
    content_args: tuple[str, ...]


#: Send tools a draft may describe.  Anything else is refused.
SUPPORTED_SEND_TOOLS: dict[str, SendToolShape] = {
    "telegram_send_message": SendToolShape(recipient_arg="chat_id", content_args=("text",)),
    "email_send_message": SendToolShape(recipient_arg="to", content_args=("subject", "body")),
}

#: Owner decision bu-bup68v: never through personal WhatsApp or Telegram user-client accounts.
PERSONAL_ACCOUNT_SEND_PREFIXES: tuple[str, ...] = ("whatsapp_", "telegram_user_")


class TemplateDraftError(Exception):
    """Base error; ``code`` is the stable machine-readable reason."""

    status_code = 400

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class TemplateDraftInvalid(TemplateDraftError):
    status_code = 400


class TemplateDraftNotFound(TemplateDraftError):
    status_code = 404


class TemplateDraftConflict(TemplateDraftError):
    status_code = 409


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------


def parse_template(source: str) -> list[dict[str, str]]:
    """Parse a template into canonical segments or raise :class:`TemplateDraftInvalid`.

    Segments are ``{"text": ...}`` or ``{"slot": name, "type": type}``.
    """
    if not isinstance(source, str) or not source.strip():
        raise TemplateDraftInvalid("template_empty")
    if len(source) > MAX_TEMPLATE_LENGTH:
        raise TemplateDraftInvalid("template_too_long")

    segments: list[dict[str, str]] = []
    names: set[str] = set()
    cursor = 0
    for found in _SLOT_RE.finditer(source):
        text = source[cursor : found.start()]
        if text:
            segments.append({"text": text})
        elif segments and "slot" in segments[-1]:
            raise TemplateDraftInvalid("adjacent_slots")
        name, sep, slot_type = found.group(1).partition(":")
        if not sep or not _SLOT_NAME_RE.fullmatch(name):
            raise TemplateDraftInvalid("slot_malformed")
        if slot_type not in SLOT_TYPES:
            raise TemplateDraftInvalid("slot_type_forbidden", f"slot type {slot_type!r}")
        if name in names:
            raise TemplateDraftInvalid("slot_duplicate")
        names.add(name)
        segments.append({"slot": name, "type": slot_type})
        cursor = found.end()
    tail = source[cursor:]
    if tail:
        segments.append({"text": tail})

    fixed = "".join(s["text"] for s in segments if "text" in s)
    if "{" in fixed or "}" in fixed:
        raise TemplateDraftInvalid("slot_malformed")
    if not fixed.strip():
        raise TemplateDraftInvalid("template_without_fixed_text")
    return segments


def render_template(segments: list[dict[str, str]]) -> str:
    """Render canonical segments back to template source."""
    return "".join(
        s["text"] if "text" in s else "{" + s["slot"] + ":" + s["type"] + "}" for s in segments
    )


def _slot_value_ok(slot_type: str, value: str) -> bool:
    try:
        if slot_type == "date":
            date.fromisoformat(value)
        elif slot_type == "time":
            time.fromisoformat(value)
        elif slot_type == "datetime":
            datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def match_template(segments: list[dict[str, str]], value: Any) -> dict[str, str] | None:
    """Match *value* against the whole template; return slot values or ``None``."""
    if not isinstance(value, str):
        return None
    pattern = "".join(
        re.escape(s["text"]) if "text" in s else f"(?P<{s['slot']}>{_SLOT_PATTERNS[s['type']]})"
        for s in segments
    )
    # re.ASCII: ``\d`` must not admit non-ASCII digits (Arabic-Indic, fullwidth, ...).
    found = re.fullmatch(pattern, value, flags=re.ASCII)
    if found is None:
        return None
    slots = found.groupdict()
    types = {s["slot"]: s["type"] for s in segments if "slot" in s}
    if not all(_slot_value_ok(types[name], slot) for name, slot in slots.items()):
        return None
    return slots


def send_tool_shape(tool_name: str) -> SendToolShape:
    """Return the supported shape for *tool_name* or refuse it."""
    if tool_name.startswith(PERSONAL_ACCOUNT_SEND_PREFIXES):
        raise TemplateDraftInvalid("personal_account_send_forbidden")
    shape = SUPPORTED_SEND_TOOLS.get(tool_name)
    if shape is None:
        raise TemplateDraftInvalid("unsupported_send_tool")
    return shape


def canonicalize_draft(
    *,
    tool_name: str,
    templates: dict[str, str],
    recipient_entity_ids: list[uuid.UUID],
) -> tuple[dict[str, list[dict[str, str]]], list[uuid.UUID]]:
    """Validate a draft's tool, templates and recipients; return canonical forms."""
    shape = send_tool_shape(tool_name)
    if set(templates) != set(shape.content_args):
        raise TemplateDraftInvalid("content_arguments_mismatch")
    canonical = {arg: parse_template(templates[arg]) for arg in shape.content_args}
    recipients = list(dict.fromkeys(recipient_entity_ids))
    if not recipients:
        raise TemplateDraftInvalid("recipients_required")
    if len(recipients) > MAX_RECIPIENTS:
        raise TemplateDraftInvalid("too_many_recipients")
    return canonical, recipients


def preview_template_draft(
    draft: dict[str, Any],
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    resolved_recipient_entity_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Report whether a call would match *draft*.  The result never carries authority."""
    reasons: list[str] = []
    slots: dict[str, dict[str, str]] = {}
    matched = False
    if draft["status"] != "draft":
        reasons.append("draft_revoked")
    if tool_name != draft["tool_name"]:
        reasons.append("tool_mismatch")
    else:
        shape = SUPPORTED_SEND_TOOLS[tool_name]
        known = {shape.recipient_arg, *shape.content_args}
        if set(tool_args) - known:
            reasons.append("unknown_parameters")
        if resolved_recipient_entity_id is None:
            reasons.append("recipient_unresolved")
        elif resolved_recipient_entity_id not in set(draft["recipient_entity_ids"]):
            reasons.append("recipient_not_pinned")
        if any(arg not in tool_args for arg in shape.content_args):
            reasons.append("content_incomplete")
        else:
            matched = True
            for arg in shape.content_args:
                arg_slots = match_template(draft["content_templates"][arg], tool_args[arg])
                if arg_slots is None:
                    matched = False
                else:
                    slots[arg] = arg_slots
            if not matched:
                reasons.append("content_mismatch")
    return {
        "draft_id": str(draft["id"]),
        "revision": draft["revision"],
        "matched": matched,
        "eligible": not reasons,
        "reasons": reasons,
        "slots": slots if matched else {},
        # Drafts are inactive: a preview is never an approval (bu-jv1moa.28 owns activation).
        "authority": "none",
    }


# ---------------------------------------------------------------------------
# Owner authoring (dashboard API only; no MCP tool, so no runtime session can author)
# ---------------------------------------------------------------------------


def _row_to_draft(row: Any) -> dict[str, Any]:
    draft = dict(row)
    draft["recipient_entity_ids"] = list(draft["recipient_entity_ids"])
    return draft


async def _require_resolved_recipients(conn: Any, recipients: list[uuid.UUID]) -> None:
    found = await conn.fetch(
        "SELECT id FROM public.entities WHERE id = ANY($1::uuid[]) "
        "AND metadata ->> 'merged_into' IS NULL AND metadata ->> 'deleted_at' IS NULL",
        recipients,
    )
    if {r["id"] for r in found} != set(recipients):
        raise TemplateDraftInvalid("recipient_unresolved")


def create_request_digest(
    *,
    tool_name: str,
    content_templates: dict[str, list[dict[str, str]]],
    recipient_entity_ids: list[uuid.UUID],
    description: str,
) -> str:
    """SHA-256 over the canonical create request, persisted to bind its idempotency key."""
    payload = {
        "tool_name": tool_name,
        "content_templates": content_templates,
        "recipient_entity_ids": [str(e) for e in recipient_entity_ids],
        "description": description,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def create_template_draft(
    conn: Any,
    *,
    tool_name: str,
    templates: dict[str, str],
    recipient_entity_ids: list[uuid.UUID],
    description: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Persist a new inactive draft.

    Replaying the same key with the same request returns the draft that key created,
    in its current state (after an edit or revoke too); the same key with a different
    request is a conflict.
    """
    if not idempotency_key.strip():
        raise TemplateDraftInvalid("idempotency_key_required")
    canonical, recipients = canonicalize_draft(
        tool_name=tool_name, templates=templates, recipient_entity_ids=recipient_entity_ids
    )
    await _require_resolved_recipients(conn, recipients)
    digest = create_request_digest(
        tool_name=tool_name,
        content_templates=canonical,
        recipient_entity_ids=recipients,
        description=description,
    )
    row = await conn.fetchrow(
        "INSERT INTO standing_reply_template_drafts "
        "(id, tool_name, content_templates, recipient_entity_ids, description, idempotency_key, "
        "create_request_digest) "
        "VALUES ($1, $2, $3, $4::uuid[], $5, $6, $7) "
        "ON CONFLICT (idempotency_key) DO NOTHING RETURNING *",
        uuid.uuid4(),
        tool_name,
        canonical,
        recipients,
        description,
        idempotency_key,
        digest,
    )
    if row is not None:
        return _row_to_draft(row)
    existing = await conn.fetchrow(
        "SELECT * FROM standing_reply_template_drafts WHERE idempotency_key = $1",
        idempotency_key,
    )
    if existing is None:  # pragma: no cover - the conflicting row cannot vanish (no deletes)
        raise TemplateDraftConflict("idempotency_key_conflict")
    # Compare against the immutable digest of the create that minted this key, never the
    # draft's current (editable) content: a retried create after an edit or revoke is
    # still a replay and returns that same draft, in its current state.
    if existing["create_request_digest"] != digest:
        raise TemplateDraftConflict("idempotency_key_reused")
    return _row_to_draft(existing)


async def get_template_draft(conn: Any, draft_id: uuid.UUID) -> dict[str, Any]:
    row = await conn.fetchrow(
        "SELECT * FROM standing_reply_template_drafts WHERE id = $1", draft_id
    )
    if row is None:
        raise TemplateDraftNotFound("draft_not_found")
    return _row_to_draft(row)


async def list_template_drafts(
    conn: Any, *, limit: int, after: tuple[Any, ...] | None = None
) -> list[dict[str, Any]]:
    """Newest first, keyset-paged on an ``(created_at, id)`` cursor."""
    if after is None:
        rows = await conn.fetch(
            "SELECT * FROM standing_reply_template_drafts "
            "ORDER BY created_at DESC, id DESC LIMIT $1",
            limit,
        )
    else:
        rows = await conn.fetch(
            "SELECT * FROM standing_reply_template_drafts "
            "WHERE (created_at, id) < ($2::timestamptz, $3::uuid) "
            "ORDER BY created_at DESC, id DESC LIMIT $1",
            limit,
            after[0],
            after[1],
        )
    return [_row_to_draft(r) for r in rows]


async def edit_template_draft(
    conn: Any,
    *,
    draft_id: uuid.UUID,
    expected_revision: int,
    templates: dict[str, str],
    recipient_entity_ids: list[uuid.UUID],
    description: str,
) -> dict[str, Any]:
    """Replace a draft's content; a stale ``expected_revision`` is a conflict."""
    current = await get_template_draft(conn, draft_id)
    canonical, recipients = canonicalize_draft(
        tool_name=current["tool_name"],
        templates=templates,
        recipient_entity_ids=recipient_entity_ids,
    )
    await _require_resolved_recipients(conn, recipients)
    row = await conn.fetchrow(
        "UPDATE standing_reply_template_drafts "
        "SET content_templates = $3, recipient_entity_ids = $4::uuid[], description = $5, "
        "revision = revision + 1, updated_at = now() "
        "WHERE id = $1 AND revision = $2 AND status = 'draft' RETURNING *",
        draft_id,
        expected_revision,
        canonical,
        recipients,
        description,
    )
    if row is None:
        latest = await get_template_draft(conn, draft_id)
        if latest["status"] != "draft":
            raise TemplateDraftConflict("draft_revoked")
        raise TemplateDraftConflict("stale_revision")
    return _row_to_draft(row)


async def revoke_template_draft(
    conn: Any, *, draft_id: uuid.UUID, expected_revision: int
) -> dict[str, Any]:
    """Mark a draft unavailable; the row stays as audit history."""
    row = await conn.fetchrow(
        "UPDATE standing_reply_template_drafts "
        "SET status = 'revoked', revoked_at = now(), updated_at = now(), "
        "revision = revision + 1 "
        "WHERE id = $1 AND revision = $2 AND status = 'draft' RETURNING *",
        draft_id,
        expected_revision,
    )
    if row is None:
        latest = await get_template_draft(conn, draft_id)
        if latest["status"] != "draft":
            raise TemplateDraftConflict("draft_revoked")
        raise TemplateDraftConflict("stale_revision")
    return _row_to_draft(row)
