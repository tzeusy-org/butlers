"""Metadata-only transport shared by fixed owning copy runtimes.

Only constructor-registered Switchboard discovery selects a remote endpoint.
Bodies stay on the adopted routed tool path; this bounded channel carries
challenge/generation metadata, without credentials or authority from locators.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from butlers.chronicler.location_policy import PolicyUnavailableError


async def registered_endpoint(runtime, name: str, *, control: bool = True) -> str:
    if not runtime.active or runtime.registry is None:
        raise PolicyUnavailableError("Catalog runtime lifetime ended")
    from butlers.chronicler.location_catalog_copies import _PATH
    from butlers.connectors.mcp_client import CachedMCPClient

    rows = CachedMCPClient._parse_result(
        await runtime.registry.call_tool("list_butlers", {}), "list_butlers"
    )
    if isinstance(rows, dict):
        rows = rows.get("butlers")
    matches = [row for row in rows or () if row.get("name") == name]
    if len(matches) != 1 or matches[0].get("eligibility_state") != "active":
        raise PolicyUnavailableError("Registered catalog endpoint is unavailable")
    from butlers.core.mcp_urls import (
        canonical_runtime_mcp_url,
        resolve_cross_container_mcp_url,
    )

    endpoint = resolve_cross_container_mcp_url(
        canonical_runtime_mcp_url(matches[0]["endpoint_url"])
    )
    url = urlsplit(endpoint)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise PolicyUnavailableError("Registered catalog endpoint differs")
    if not control:
        return endpoint
    return f"{url.scheme}://{url.netloc}{_PATH}"


async def exchange_metadata(runtime, endpoint: str, token: str, body: dict) -> dict:
    # No redirects, credentials, caller endpoint or body-bearing logs.
    from butlers.chronicler.location_catalog_copies import _HEADER, _unique_object

    async with asyncio.timeout(5):
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            async with client.stream(
                "POST", endpoint, json=body, headers={_HEADER: token}
            ) as response:
                if response.status_code != 200:
                    raise PolicyUnavailableError("Catalog control is unavailable")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        raise PolicyUnavailableError("Catalog control is oversized")
                try:
                    value = json.loads(raw, object_pairs_hook=_unique_object)
                except (ValueError, TypeError):
                    raise PolicyUnavailableError("Catalog control differs") from None
    if not isinstance(value, dict):
        raise PolicyUnavailableError("Catalog control differs")
    return value
