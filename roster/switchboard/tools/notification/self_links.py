"""Fixed dashboard self routes and private deterministic delivery rendering.

The producer has no URL authority. Both the Switchboard sender and Messenger
receiver derive each live control from the reference and server configuration.
The wire's label, host and provenance are presentation, never authentication.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from butlers.tools.switchboard.contracts import (
    _RESOLVED_LINK_CONTEXT,
    NotifyRequestV1,
    SelfLinkReferenceV1,
    _ResolvedOpenLinkV1,
    parse_notify_request,
)

# These templates are checked against the actual phone entry route registry.
SELF_ROUTES: Mapping[str, str] = MappingProxyType(
    {
        "approval_detail": "/approvals/{action_id}",
        "approvals_queue": "/approvals",
    }
)
_UNVERIFIED = "[link not verified]"
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z", re.ASCII)


def _dashboard_origin() -> tuple[str, str] | None:
    raw = os.environ.get("DASHBOARD_URL", "")
    try:
        if not raw.isascii() or any(ord(char) <= 32 for char in raw) or "\\" in raw:
            return None
        parsed = urlsplit(raw)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not host.isascii()
            or len(host) > 253
        ):
            return None
        labels = host.split(".")
        if any(not _HOST_LABEL.fullmatch(label) or label.startswith("xn--") for label in labels):
            return None
        if all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in labels):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        return "https://" + host, host
    except ValueError:
        return None


def _resolve(reference: SelfLinkReferenceV1) -> dict[str, Any] | None:
    origin = _dashboard_origin()
    template = SELF_ROUTES.get(reference.route_key)
    if origin is None or template is None:
        return None
    if reference.route_key == "approval_detail":
        if set(reference.params) != {"action_id"}:
            return None
        action_id = reference.params["action_id"]
        if not isinstance(action_id, str):
            return None
        try:
            canonical = str(UUID(action_id))
        except ValueError:
            return None
        if canonical != action_id:
            return None
        path = template.format(action_id=canonical)
    elif reference.params:
        return None
    else:
        path = template
    base, host = origin
    url = base + path
    if len(url) > 300:
        return None
    return {
        "verb": "open_link",
        "reference": reference.model_dump(mode="json"),
        "label": f"Open {host} (dashboard)",
        "url": url,
        "host": host,
        "provenance": "dashboard",
    }


def resolve_self_links(request: NotifyRequestV1) -> NotifyRequestV1:
    """Mint output only from an already admitted public notification."""
    if not request.actions:
        return request
    payload = request.model_dump(mode="json", exclude_none=True)
    unresolved = False
    for index, action in enumerate(request.actions):
        if action.verb not in ("open_dashboard", "open_link"):
            continue
        resolved = _resolve(action.reference)
        if resolved is None:
            # Never retain even a previously resolved URL when configuration
            # or a typed route reference can no longer be verified.
            payload["actions"][index] = {
                "verb": "open_dashboard",
                "reference": action.reference.model_dump(mode="json"),
            }
            unresolved = True
        else:
            payload["actions"][index] = resolved
    if unresolved and _UNVERIFIED not in payload["delivery"]["message"]:
        payload["delivery"]["message"] += "\n" + _UNVERIFIED
    return NotifyRequestV1.model_validate(payload, context=_RESOLVED_LINK_CONTEXT)


def parse_delivered_notify_request(payload: Mapping[str, Any]) -> NotifyRequestV1:
    """Reconstruct public references, validate, and independently resolve.

    No claimed server flag or wire URL grants link authority. Even a forged
    private-output envelope can only name the same fixed self routes as public
    input; caller-supplied label/URL/host are discarded before resolution.
    Existing route/owner/recovery admission still gates delivery itself.
    """
    public = dict(payload)
    if isinstance(public.get("actions"), (list, tuple)):
        actions = []
        for action in public["actions"]:
            if isinstance(action, dict) and action.get("verb") == "open_link":
                resolved = _ResolvedOpenLinkV1.model_validate(action)
                action = {
                    "verb": "open_dashboard",
                    "reference": resolved.reference.model_dump(mode="json"),
                }
            actions.append(action)
        public["actions"] = actions
    return resolve_self_links(parse_notify_request(public))
