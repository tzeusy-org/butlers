"""Inert rendering of subscription cancellation links (bu-svqpzo.10).

A cancellation link written by a session that handled third-party content is never
shown to the owner as a live URL.  The owner message names only the link's
registrable domain as plain text and says the link is unverified.

There is no public-suffix library in the dependency set, so the registrable domain
is derived offline: the last two labels, or the last three under a short list of
common multi-label public suffixes and shared hosting suffixes.  The result is a
display label for an already-unverified link, never an authorization input.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "net.au",
        "org.au",
        "co.nz",
        "co.jp",
        "ne.jp",
        "or.jp",
        "co.kr",
        "co.in",
        "co.za",
        "com.br",
        "com.cn",
        "com.hk",
        "com.mx",
        "com.sg",
        "com.tr",
        # Shared hosting: the tenant, not the platform, is the registrable name.
        "github.io",
        "gitlab.io",
        "vercel.app",
        "netlify.app",
        "pages.dev",
        "workers.dev",
        "web.app",
        "firebaseapp.com",
        "herokuapp.com",
        "blogspot.com",
        "azurewebsites.net",
        "cloudfront.net",
    }
)

_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


def registrable_domain(url: str | None) -> str | None:
    """Return the registrable domain of an http(s) URL, or None when it has none."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not host:
        return None
    host = host.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if not _HOST.fullmatch(host):
        return None
    labels = host.split(".")
    if len(labels) < 2 or any(not label for label in labels):
        return None
    keep = 3 if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES else 2
    return ".".join(labels[-keep:])


def defanged_domain(domain: str) -> str:
    """Render a domain so no chat client auto-links it (``example.com`` -> ``example[.]com``)."""
    return domain.replace(".", "[.]")
