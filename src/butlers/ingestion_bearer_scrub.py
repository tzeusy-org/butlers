"""Deterministic bearer-material scrubber for the ingest boundary.

One-time codes, password-reset links, magic links and Telegram login codes are
account-takeover keys.  ``about/heart-and-soul/security.md`` bars credentials
from session logs and payloads, so the ingest boundary keeps *that* an auth
artifact arrived (kind, provider domain, observed time) and never the artifact
itself.

The scrubber is pure and idempotent: scrubbing already-scrubbed text changes
nothing, because placeholders are protected from every rule.  Callers (the
Switchboard ``ingest`` tool, the Gmail and Telegram user-client connectors, and
the historical cleanup migration) share this single detector.

False positives remove a number or a URL, never a message.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

#: Telegram's service account that delivers login codes.
TELEGRAM_SERVICE_SENDER = "777000"

KIND_AUTH_CODE = "auth_code"
KIND_RESET_LINK = "reset_link"
KIND_MAGIC_LINK = "magic_link"

_PLACEHOLDER_LABELS = {
    KIND_AUTH_CODE: "auth-code",
    KIND_RESET_LINK: "reset-link",
    KIND_MAGIC_LINK: "magic-link",
}
_LABEL_KINDS = {label: kind for kind, label in _PLACEHOLDER_LABELS.items()}

_PLACEHOLDER = re.compile(r"\[(auth-code|reset-link|magic-link) withheld: ([^\]\s]*)\]")

_OTP_KEYWORD = (
    r"(?:one[- ]time[- ](?:code|password|passcode|pin)|verification code|security code"
    r"|login code|log[- ]?in code|sign[- ]?in code|auth(?:entication)? code"
    r"|confirmation code|passcode|otp|code|pin)"
)
_OTP_VALUE = r"(?:[A-Z]{1,3}-)?(?:\d{4,8}|\d{3}[- ]\d{3}|\d{4}[- ]\d{4})"
_OTP_FORWARD = re.compile(
    rf"(?<!zip )(?<!postal )(?<!post )\b{_OTP_KEYWORD}\b[^\n\d]{{0,40}}?\b({_OTP_VALUE})\b",
    re.IGNORECASE,
)
_OTP_REVERSE = re.compile(
    r"\b(\d{4,8})\b(?=\s+(?:is|as)\s+your\b[^\n]{0,40}?\b(?:code|passcode|otp|pin)\b)",
    re.IGNORECASE,
)
# Aggressive mode skips date/time-shaped runs (2026-10-03, 12:00:00) inside message content.
_ANY_CODE = re.compile(r"(?<![\d:-])\b\d{4,8}\b(?![-:]\d)")

_URL = re.compile(r"https?://[^\s<>\"'\\)\]]+", re.IGNORECASE)
_URL_TRAILING = ".,;:!?"
_RESET_WORDS = re.compile(r"reset|recover|forgot|passw(?:or)?d", re.IGNORECASE)
_MAGIC_WORDS = re.compile(
    r"magic|verif|confirm|activate|sign-?in|log-?in|auth|otp|passwordless|token",
    re.IGNORECASE,
)
_SECRET_PARAMS = frozenset(
    {"token", "code", "key", "sig", "signature", "otp", "ticket", "auth", "t", "k", "hash"}
)
_OPAQUE_SEGMENT = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_-]{20,}$")

_EXPIRES = re.compile(
    r"expires?\s+(?:in|within)\s+(\d{1,3})\s*(minute|min|hour|hr|day)s?\b", re.IGNORECASE
)


@dataclass(frozen=True)
class BearerArtifact:
    """Typed record of one withheld auth artifact (never the artifact itself)."""

    kind: str
    provider_domain: str
    expires_hint: str | None = None

    def as_dict(self) -> dict[str, str]:
        record = {"kind": self.kind, "provider_domain": self.provider_domain}
        if self.expires_hint:
            record["expires_hint"] = self.expires_hint
        return record


def placeholder(kind: str, provider_domain: str) -> str:
    """Return the text placeholder for ``kind`` issued by ``provider_domain``."""
    domain = re.sub(r"[\s\]]", "", provider_domain) or "unknown"
    return f"[{_PLACEHOLDER_LABELS[kind]} withheld: {domain}]"


def placeholder_artifacts(text: str) -> list[BearerArtifact]:
    """Parse placeholders already present in ``text`` into typed artifact records."""
    return [
        BearerArtifact(kind=_LABEL_KINDS[m.group(1)], provider_domain=m.group(2))
        for m in _PLACEHOLDER.finditer(text)
    ]


def provider_domain_for(sender_identity: str | None, provider: str | None) -> str:
    """Best provider label: the sender's email domain, else the source provider."""
    if sender_identity and "@" in sender_identity:
        domain = sender_identity.rsplit("@", 1)[1].strip(" >").lower()
        if domain:
            return domain
    return (provider or "unknown").lower()


def is_auth_service_sender(sender: Mapping[str, Any] | None) -> bool:
    """True when the sender (or any batch participant) is Telegram's login-code service."""
    if not sender:
        return False
    if str(sender.get("identity", "")) == TELEGRAM_SERVICE_SENDER:
        return True
    participants = sender.get("participants")
    if isinstance(participants, Mapping):
        return TELEGRAM_SERVICE_SENDER in {str(key) for key in participants}
    if isinstance(participants, (list, tuple, set, frozenset)):
        return TELEGRAM_SERVICE_SENDER in {str(item) for item in participants}
    return False


def _expires_hint(text: str) -> str | None:
    match = _EXPIRES.search(text)
    if not match:
        return None
    unit = match.group(2).lower()[0]  # m / h / d
    return f"{match.group(1)}{unit}"


def _classify_url(url: str) -> tuple[str, str] | None:
    """Return ``(kind, host)`` when ``url`` is a bearer link, else ``None``."""
    try:
        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    keys = {k.lower() for k, _ in query}
    segments = [s for s in parts.path.split("/") if s]
    has_secret = bool(keys & _SECRET_PARAMS) or any(_OPAQUE_SEGMENT.match(s) for s in segments)
    if not has_secret:
        return None
    haystack = " ".join([parts.path, *keys])
    if _RESET_WORDS.search(haystack):
        return KIND_RESET_LINK, host
    if _MAGIC_WORDS.search(haystack):
        return KIND_MAGIC_LINK, host
    return None


def _scrub_segment(
    segment: str, provider_domain: str, aggressive: bool, found: list[BearerArtifact]
) -> str:
    hint = _expires_hint(segment)

    def code_repl(match: re.Match[str]) -> str:
        found.append(BearerArtifact(KIND_AUTH_CODE, provider_domain, hint))
        return placeholder(KIND_AUTH_CODE, provider_domain)

    def forward_repl(match: re.Match[str]) -> str:
        start = match.start(1) - match.start(0)
        return match.group(0)[:start] + code_repl(match)

    def url_repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw.rstrip(_URL_TRAILING)
        classified = _classify_url(url)
        if classified is None:
            return raw
        kind, host = classified
        found.append(BearerArtifact(kind, host, hint))
        return placeholder(kind, host) + raw[len(url) :]

    segment = _URL.sub(url_repl, segment)
    if aggressive:
        return _ANY_CODE.sub(code_repl, segment)
    segment = _OTP_FORWARD.sub(forward_repl, segment)
    return _OTP_REVERSE.sub(code_repl, segment)


def scrub_text(
    text: str, *, provider_domain: str, aggressive: bool = False
) -> tuple[str, list[BearerArtifact]]:
    """Replace bearer material in ``text`` with typed placeholders.

    ``aggressive`` treats every 4-8 digit run as a code; use it for sources that
    only ever carry auth traffic (the Telegram 777000 service account).
    """
    found: list[BearerArtifact] = []
    out: list[str] = []
    cursor = 0
    for hold in _PLACEHOLDER.finditer(text):
        out.append(_scrub_segment(text[cursor : hold.start()], provider_domain, aggressive, found))
        out.append(hold.group(0))
        cursor = hold.end()
    out.append(_scrub_segment(text[cursor:], provider_domain, aggressive, found))
    return "".join(out), found


def _scrub_gmail_body_data(data: str, provider_domain: str, found: list[BearerArtifact]) -> str:
    """Scrub a Gmail ``body.data`` base64url blob; leave undecodable blobs untouched."""
    try:
        padded = data + "=" * (-len(data) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return data
    scrubbed, hits = scrub_text(decoded, provider_domain=provider_domain)
    if not hits:
        return data
    found.extend(hits)
    return base64.urlsafe_b64encode(scrubbed.encode("utf-8")).decode("ascii").rstrip("=")


def scrub_json(
    value: Any, *, provider_domain: str, aggressive: bool = False
) -> tuple[Any, list[BearerArtifact]]:
    """Recursively scrub every string in a JSON-like structure (returns a copy).

    Gmail ``body.data`` blobs are base64url-encoded; they are decoded, scrubbed
    and re-encoded only when something was withheld.
    """
    found: list[BearerArtifact] = []

    def walk(node: Any, *, in_body: bool = False) -> Any:
        if isinstance(node, str):
            out, hits = scrub_text(node, provider_domain=provider_domain, aggressive=aggressive)
            found.extend(hits)
            return out
        if isinstance(node, Mapping):
            return {
                key: (
                    _scrub_gmail_body_data(child, provider_domain, found)
                    if in_body and key == "data" and isinstance(child, str)
                    else walk(child, in_body=(key == "body"))
                )
                for key, child in node.items()
            }
        if isinstance(node, (list, tuple)):
            return [walk(child) for child in node]
        return node

    return walk(value), found


def scrub_stored_record(
    raw_payload: Mapping[str, Any], normalized_text: str
) -> tuple[dict[str, Any], str, list[BearerArtifact]]:
    """Scrub a persisted ``message_inbox`` record; shared by ingest and the sw_040 backfill.

    Only the message-content subtree (``raw_payload["payload"]``) and
    ``normalized_text`` are scrubbed.  ``source``, ``event``, ``sender`` and
    ``control`` (identities, ids, timestamps) are never rewritten, even in
    aggressive mode, which treats every 4-8 digit run as a code.  Known false
    positive: in a 777000 service-sender message any 4-8 digit number in the
    text is withheld.  Returns ``(raw_payload copy, normalized_text, fresh hits)``.
    """
    sender = raw_payload.get("sender") or {}
    source = raw_payload.get("source") or {}
    domain = provider_domain_for(sender.get("identity"), source.get("provider"))
    aggressive = is_auth_service_sender(sender)
    result = dict(raw_payload)
    json_hits: list[BearerArtifact] = []
    if raw_payload.get("payload") is not None:
        result["payload"], json_hits = scrub_json(
            raw_payload["payload"], provider_domain=domain, aggressive=aggressive
        )
    new_text, text_hits = scrub_text(normalized_text, provider_domain=domain, aggressive=aggressive)
    # The payload subtree embeds normalized_text, so one artifact can hit in both;
    # count the text hits (or the raw-only hits) once.
    return result, new_text, text_hits or json_hits


def scrub_filtered_preview(
    preview: str | None,
    *,
    connector_type: str,
    sender_identity: str,
    full_payload: object,
) -> str | None:
    """Withhold bearer text before buffering or repairing a visibility preview.

    Legacy JSONB strings can supply hints, but cannot override the actual row
    sender. A detector failure withholds the preview without logging its text.
    Replay payloads and authentication-artifact production stay separate.
    """
    if preview is None:
        return None
    try:
        if isinstance(full_payload, str):
            try:
                full_payload = json.loads(full_payload)
            except ValueError:
                full_payload = None
        payload = full_payload if isinstance(full_payload, Mapping) else {}
        source = payload.get("source")
        provider = source.get("provider") if isinstance(source, Mapping) else None
        if not isinstance(provider, str):
            provider = connector_type
        sender = payload.get("sender")
        aggressive = is_auth_service_sender({"identity": sender_identity}) or (
            isinstance(sender, Mapping) and is_auth_service_sender(sender)
        )
        text, _ = scrub_text(
            preview,
            provider_domain=provider_domain_for(sender_identity, provider),
            aggressive=aggressive,
        )
        return text
    except Exception:
        return None


def scrub_message_text(text: str, *, source: Mapping[str, Any], sender: Mapping[str, Any]) -> str:
    """Scrub the text handed to a routing session, matching what ingest persisted."""
    domain = provider_domain_for(sender.get("identity"), source.get("provider"))
    return scrub_text(text, provider_domain=domain, aggressive=is_auth_service_sender(sender))[0]


def scrub_envelope(envelope: Mapping[str, Any]) -> tuple[dict[str, Any], list[BearerArtifact]]:
    """Scrub an ``ingest.v1`` envelope dict; sets ``control.bearer_scrubbed`` on a hit.

    Used by connectors before submit so bearer material never reaches their own
    replay queues or the Switchboard.  Policy routing is unaffected: only the
    persisted payload text changes.
    """
    sender = envelope.get("sender") or {}
    source = envelope.get("source") or {}
    domain = provider_domain_for(sender.get("identity"), source.get("provider"))
    aggressive = is_auth_service_sender(sender)

    result = dict(envelope)
    payload = dict(envelope.get("payload") or {})
    found: list[BearerArtifact] = []
    if isinstance(payload.get("normalized_text"), str):
        payload["normalized_text"], hits = scrub_text(
            payload["normalized_text"], provider_domain=domain, aggressive=aggressive
        )
        found.extend(hits)
    if payload.get("raw") is not None:
        payload["raw"], hits = scrub_json(
            payload["raw"], provider_domain=domain, aggressive=aggressive
        )
        found.extend(hits)
    result["payload"] = payload
    if found:
        result["control"] = {**(envelope.get("control") or {}), "bearer_scrubbed": True}
    return result, found
