"""Immutable, explicit transport policy; disabled configuration does no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PeerPolicy:
    role: str
    domain: str
    sans: frozenset[str]
    client_certificate: bool = False

    def __post_init__(self):
        if (
            type(self.role) is not str
            or self.role not in {"broker-control", "owning-service"}
            or type(self.domain) is not str
            or not re.fullmatch(r"[a-zA-Z0-9_.-]+", self.domain)
            or "__" in self.domain
            or type(self.sans) is not frozenset
            or not self.sans
            or any(
                type(s) is not str
                or not s.startswith(("DNS:", "URI:", "IP:"))
                or "*" in s
                or len(s) > 512
                for s in self.sans
            )
            or type(self.client_certificate) is not bool
        ):
            raise ValueError("invalid_peer_policy")


@dataclass(frozen=True)
class TransportConfig:
    endpoint: str
    peer: PeerPolicy

    def __post_init__(self):
        if type(self.endpoint) is not str or type(self.peer) is not PeerPolicy:
            raise ValueError("invalid_private_endpoint")
        u = urlsplit(self.endpoint)
        if (
            u.scheme != "https"
            or not u.hostname
            or not u.port
            or u.path != "/mcp"
            or u.query
            or u.fragment
            or u.username
            or u.password
            or self.peer.role != "owning-service"
            or self.peer.client_certificate
        ):
            raise ValueError("invalid_private_endpoint")

    @classmethod
    def from_mapping(cls, value: dict | None) -> TransportConfig | None:
        # This branch must precede parsing paths or constructing any resources.
        if value is None or (type(value) is dict and value.get("enabled", False) is False):
            return None
        if type(value) is not dict or set(value) != {"enabled", "endpoint", "peer"}:
            raise ValueError("invalid_transport_configuration")
        if value["enabled"] is not True or type(value["peer"]) is not PeerPolicy:
            raise ValueError("invalid_transport_configuration")
        return cls(value["endpoint"], value["peer"])


HEADER_LIMIT = 16_384
REQUEST_LIMIT = 262_144
REPLY_LIMIT = 65_536
OPERATION_SECONDS = 10.0
