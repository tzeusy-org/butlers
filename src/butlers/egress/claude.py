"""Supported native Claude API-base routing; no native binary or tool replacement."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from butlers.egress import gateway as protocol
from butlers.egress.http_transport import GatewayConfig

_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Keep native auth/tools intact and map only the supported API-base path.

    API-key inference and the actual bare-mode tool inventory need native
    evidence separately. This setting is not a whole-native-network hook.
    """
    result = dict(environment)
    gateway = GatewayConfig.from_environment(environment)
    if gateway is None:
        return result
    logical = environment.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    try:
        origin = urlsplit(logical)
        port = origin.port if origin.port is not None else 443
        host = origin.hostname
    except ValueError:
        raise ValueError("invalid Claude logical API base") from None
    if (
        origin.scheme != "https"
        or not host
        or not host.isascii()
        or origin.username is not None
        or origin.password is not None
        or origin.query
        or origin.fragment
        or "\\" in logical
        or any(ord(char) <= 32 or ord(char) == 127 for char in logical)
        or not 1 <= port <= 65535
    ):
        raise ValueError("invalid Claude logical API base")
    headers = []
    for line in environment.get("ANTHROPIC_CUSTOM_HEADERS", "").split("\n"):
        if "\r" in line:
            raise ValueError("invalid Claude custom header")
        if not line.strip():
            continue
        name, separator, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if (
            not separator
            or not _HEADER_NAME.fullmatch(name)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid Claude custom header")
        lowered = name.lower()
        if lowered == "host":
            authority = host if port == 443 else f"{host}:{port}"
            if value.lower() != authority:
                raise ValueError("Claude custom Host differs from logical API base")
            continue
        if lowered == "connection":
            continue
        if (
            lowered == "forwarded"
            or lowered.encode() in protocol._HOP_HEADERS
            or lowered.startswith(("x-forwarded-", "x-egress-", "x-butlers-"))
        ):
            raise ValueError("unsupported Claude internal-hop header")
        headers.append(f"{name}: {value}")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        result.pop(key, None)
        result.pop(key.lower(), None)
    result["ANTHROPIC_BASE_URL"] = gateway.base_url(host, port) + origin.path.rstrip("/")
    result["ANTHROPIC_CUSTOM_HEADERS"] = "\n".join([*headers, "Connection: close"])
    return result
