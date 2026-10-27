"""Public, host-configured browser identity; request headers never configure it."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_DNS = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_DEPLOYMENT = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


@dataclass(frozen=True)
class OwnerAuthConfig:
    origin: str | None
    rp_id: str | None
    api_key: str | None = field(default=None, repr=False)
    deployment: str = "default"
    trusted_proxy_peers: tuple[str, ...] = ()

    @property
    def origin_valid(self) -> bool:
        return bool(self.origin and self.rp_id)

    @property
    def key_generation(self) -> str | None:
        return hashlib.sha256(self.api_key.encode()).hexdigest() if self.api_key else None

    @property
    def owner_cookie(self) -> str:
        return f"__Host-butlers-{self.deployment}-owner"

    @property
    def preauth_cookie(self) -> str:
        return f"__Host-butlers-{self.deployment}-preauth"

    @classmethod
    def from_env(cls, api_key: str | None = None) -> OwnerAuthConfig:
        if api_key is None:
            api_key = os.environ.get("DASHBOARD_API_KEY")
        origin = os.environ.get("DASHBOARD_AUTH_ORIGIN", "")
        rp_id = os.environ.get("DASHBOARD_AUTH_RP_ID", "")
        deployment = os.environ.get("DASHBOARD_AUTH_DEPLOYMENT", "default")
        peers: tuple[str, ...] = ()
        try:
            peers = tuple(
                str(ipaddress.ip_address(peer.strip()))
                for peer in os.environ.get("DASHBOARD_AUTH_TRUSTED_PROXY_PEERS", "").split(",")
                if peer.strip()
            )
            parsed = urlsplit(origin)
            if (
                parsed.scheme != "https"
                or parsed.netloc != parsed.hostname
                or parsed.path
                or parsed.query
                or parsed.fragment
                or not _DNS.fullmatch(parsed.hostname or "")
                or rp_id != parsed.hostname
                or not _DEPLOYMENT.fullmatch(deployment)
            ):
                raise ValueError
        except ValueError:
            origin = rp_id = ""
            deployment = "default"
            peers = ()
        return cls(origin or None, rp_id or None, api_key or None, deployment, peers)
