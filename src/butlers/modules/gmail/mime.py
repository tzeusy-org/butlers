"""Bounded plaintext Gmail messages; no quoting, HTML, Bcc, aliases or attachments."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from typing import Any

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
MESSAGE_ID_RE = re.compile(r"<[^<>\s@]+@[^<>\s@]+>")
BODY_LIMIT = 50_000


class GmailError(Exception):
    """Closed provider/tool error; never contains provider response bodies or credentials."""

    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def address(value: Any) -> str:
    if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
        raise ValueError("invalid address")
    parsed = Address(addr_spec=value)
    if not parsed.username or not parsed.domain or parsed.addr_spec != value:
        raise ValueError("use a bare email address")
    return value


def validate_content(args: dict[str, Any]) -> None:
    to, cc = args.get("to"), args.get("cc") or []
    if not isinstance(to, list) or not to or not isinstance(cc, list):
        raise ValueError("to must be a nonempty address list; cc must be an address list")
    if not 1 <= len(to) + len(cc) <= 20:
        raise ValueError("use 1 to 20 recipients")
    for item in [*to, *cc]:
        address(item)
    subject, body = args.get("subject"), args.get("body")
    if (
        not isinstance(subject, str)
        or not subject.strip()
        or len(subject) > 998
        or any(c in subject for c in "\r\n\x00")
    ):
        raise ValueError("subject must be nonblank, single-line text up to 998 characters")
    if not isinstance(body, str) or len(body) > BODY_LIMIT or "\x00" in body:
        raise ValueError(f"body must be plaintext up to {BODY_LIMIT} characters")
    body.encode("utf-8")
    for key in ("draft_id", "thread_id", "in_reply_to_message_id", "replaces_draft_id"):
        if args.get(key) is not None and not valid_id(args[key]):
            raise ValueError(f"{key} is not a valid Gmail id")
    if bool(args.get("thread_id")) != bool(args.get("in_reply_to_message_id")):
        raise ValueError("thread_id and in_reply_to_message_id must be supplied together")


def headers(message: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for h in (message.get("payload") or {}).get("headers") or []:
        if not isinstance(h, dict) or not isinstance(h.get("name"), str):
            continue
        name, value = h["name"].lower(), h.get("value")
        if name not in {"subject", "message-id", "in-reply-to", "references", "from", "date"}:
            continue
        if name in result or not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
            raise ValueError("ambiguous or unsafe reply headers")
        result[name] = value
    return result


def reply_headers(message: dict[str, Any], args: dict[str, Any]) -> dict[str, str]:
    if (
        message.get("id") != args["in_reply_to_message_id"]
        or message.get("threadId") != args["thread_id"]
    ):
        raise ValueError("the replied-to message does not belong to this thread")
    h = headers(message)
    target = h.get("message-id", "")
    if not MESSAGE_ID_RE.fullmatch(target):
        raise ValueError("the replied-to message has no usable Message-ID")

    def normalize(s):
        return re.sub(r"^(?:(?:re|fwd?)\s*:\s*)+", "", s.strip(), flags=re.I).strip()

    if normalize(args["subject"]) != normalize(h.get("subject", "")):
        raise ValueError("the subject does not match the replied-to message")
    for arg, header in (("in_reply_to_sender", "from"), ("in_reply_to_date", "date")):
        if arg in args and (not h.get(header) or args[arg] != h[header]):
            raise ValueError("the pinned reply sender/date does not match")
    references = h.get("references") or h.get("in-reply-to") or ""
    if references and not re.fullmatch(r"(?:<[^<>\s@]+@[^<>\s@]+>\s*)+", references):
        raise ValueError("the replied-to message has unsafe threading headers")
    return {"In-Reply-To": target, "References": " ".join([*references.split(), target])}


def content_digest(to: list[str], cc: list[str], subject: str, body: str) -> str:
    return hashlib.sha256(
        json.dumps([to, cc, subject, body], ensure_ascii=False).encode()
    ).hexdigest()


def build_message(
    sender: str, args: dict[str, Any], threading: dict[str, str], message_id: str | None = None
) -> dict[str, str]:
    message = EmailMessage(policy=SMTP)
    message["From"] = address(sender)
    message["To"] = ", ".join(args["to"])
    if args.get("cc"):
        message["Cc"] = ", ".join(args["cc"])
    message["Subject"] = args["subject"]
    for name, value in threading.items():
        message[name] = value
    if message_id:
        message["Message-ID"] = message_id
    message["Date"] = formatdate(localtime=False, usegmt=True)
    message.set_content(args["body"], subtype="plain", charset="utf-8", cte="base64")
    # set_content adds a trailing newline; the approved body is the entire payload, unchanged.
    message.set_payload(base64.b64encode(args["body"].encode("utf-8")).decode("ascii"))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return {"raw": raw, **({"threadId": args["thread_id"]} if args.get("thread_id") else {})}
