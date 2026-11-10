"""Daemon-side client for the named relays (RFC 0038 D7).

A daemon presents its workload's projected relay token and its own daemon
name; it never sends ``source_butler``. Callers use this only while
``BUTLERS_SERVICE_PLANE_AUTH`` is on; otherwise they keep the legacy route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from butlers.core.service_plane.endpoint import RELAY_PATH
from butlers.core.service_plane.relays import RelayRefused
from butlers.core.service_plane.workload_identity import (
    DAEMON_HEADER,
    RELAY_AUDIENCE,
    TOKEN_DIR,
    TOKEN_HEADER,
)


class RelayUnavailable(Exception):
    """The relay could not be reached or answered outside its contract."""


def configured_switchboard_url(switchboard_client: Any) -> str | None:
    """The Switchboard URL a daemon's Switchboard client was built from, or None.

    That client is built from ``config.switchboard_url`` (resolved across
    containers), so a relay goes to the same Switchboard the daemon already uses.
    """
    url = getattr(getattr(switchboard_client, "transport", None), "url", None)
    return url if isinstance(url, str) and url else None


def relay_url(switchboard_url: str, name: str) -> str:
    """The relay URL on the service listener named by Switchboard's MCP URL."""
    parts = urlsplit(switchboard_url)
    return f"{parts.scheme}://{parts.netloc}{RELAY_PATH.format(name=name)}"


async def call_relay(
    name: str,
    args: dict[str, Any],
    *,
    daemon: str,
    switchboard_url: str,
    token_dir: Path = TOKEN_DIR,
    request_timeout_s: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """Call one relay; return the target's result or raise.

    ``RelayRefused`` carries the relay's refusal code; nothing was sent to the
    target. ``RelayUnavailable`` means the outcome is not known to be a refusal.
    """
    token = (token_dir / RELAY_AUDIENCE).read_text(encoding="utf-8").strip()
    headers = {TOKEN_HEADER: token, DAEMON_HEADER: daemon}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=request_timeout_s) as client:
            response = await client.post(
                relay_url(switchboard_url, name), json={"args": args}, headers=headers
            )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RelayUnavailable(type(exc).__name__) from None
    if not isinstance(body, dict):
        raise RelayUnavailable("malformed_reply")
    if body.get("status") == "refused" and isinstance(body.get("code"), str):
        raise RelayRefused(body["code"])
    if response.status_code != 200 or body.get("status") != "ok":
        raise RelayUnavailable(f"http_{response.status_code}")
    return body.get("result")
