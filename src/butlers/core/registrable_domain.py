"""Registrable domain (eTLD+1) under the vendored Public Suffix List.

Vendor-door verification compares the sender's registrable domain with the link host's
(REQ-vendor-doors-002), so this is an authorization input, not a display helper. One function
computes it for both. The ICANN and private sections of the list both count, from the pinned
snapshot ``src/butlers/data/public_suffix_list.dat`` (refreshed only by a reviewed PR, together
with a migration that reloads ``public.public_suffix_rules``). The SQL function
``public.registrable_domain`` implements the same algorithm over that table; a parity test pins
them together.

Anything that cannot be computed returns ``None`` (fails closed): a name that is itself a public
suffix, a top-level domain the list does not know, an IP literal, a non-ASCII or punycode label,
an empty label, an over-long name or label, or stray characters. The older display-only helper in
``roster/finance/tools/cancellation_door.py`` is a two-label heuristic and must never be used for a
decision.
"""

from __future__ import annotations

import re
from functools import cache
from importlib.resources import files

_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MAX_NAME = 253


@cache
def suffix_rules() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return ``(normal, wildcard_parents, exceptions)`` from the vendored list."""
    normal: set[str] = set()
    wildcard: set[str] = set()
    exceptions: set[str] = set()
    text = files("butlers").joinpath("data/public_suffix_list.dat").read_text("utf-8")
    for raw in text.splitlines():
        line = raw.split()[0] if raw.split() else ""
        if not line or line.startswith("//"):
            continue
        if line.startswith("!"):
            exceptions.add(line[1:])
        elif line.startswith("*."):
            wildcard.add(line[2:])
        else:
            normal.add(line)
    return frozenset(normal), frozenset(wildcard), frozenset(exceptions)


def rule_lines() -> list[str]:
    """The list's rules as written, for loading ``public.public_suffix_rules``."""
    normal, wildcard, exceptions = suffix_rules()
    return sorted(normal) + sorted("*." + w for w in wildcard) + sorted("!" + e for e in exceptions)


def registrable_domain(name: str) -> str | None:
    """Return the eTLD+1 of an ASCII host name, or ``None`` when it cannot be computed."""
    # ASCII is checked before lower(): the Kelvin sign lowercases to an ASCII "k".
    if not isinstance(name, str) or not name or len(name) > _MAX_NAME or not name.isascii():
        return None
    labels = name.lower().split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return None
    if any(label.startswith("xn--") for label in labels):
        return None
    if all(label.isdigit() for label in labels):
        return None
    normal, wildcard, exceptions = suffix_rules()
    count = len(labels)
    suffix_len: int | None = None
    for start in range(count):
        if ".".join(labels[start:]) in exceptions:
            suffix_len = count - start - 1
            break
    if suffix_len is None:
        for start in range(count):
            candidate = ".".join(labels[start:])
            parent = ".".join(labels[start + 1 :])
            if candidate in normal or (parent and parent in wildcard):
                suffix_len = count - start
                break
    if suffix_len is None or suffix_len >= count:
        return None
    return ".".join(labels[count - suffix_len - 1 :])


def sender_registrable_domain(address: str) -> str | None:
    """Registrable domain of one bare ``local@domain`` address, else ``None``."""
    if not isinstance(address, str):
        return None
    local, sep, domain = address.strip().partition("@")
    if not sep or not local or "@" in domain or any(ch in local for ch in " <>,;"):
        return None
    return registrable_domain(domain)
