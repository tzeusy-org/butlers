"""Internal, monotonic content marking for deterministic notification producers.

These helpers are deliberately absent from MCP tool signatures. A nested sender
cannot turn off an enclosing tainted-content mark; the scope ends with its job.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

_inert_content: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "delivery_content_requires_inert_links", default=False
)


@contextmanager
def inert_delivery_content() -> Iterator[None]:
    """Mark content read from tainted or non-owner-authority records."""
    token = _inert_content.set(True)
    try:
        yield
    finally:
        _inert_content.reset(token)


def delivery_content_requires_inert_links() -> bool:
    """Read the server-held sender mark; no caller argument can clear it."""
    return _inert_content.get()


def defang_delivery_text(text: str) -> str:
    """Neutralise URL/address separators in plain-text mail, including bare hosts.

    Transforming only scheme-prefixed URLs misses bare domains, addresses and
    alternative schemes. Preserve the text with explicit, readable separators
    rather than relying on any mail client's link-detection regular expression.
    """
    return text.translate(str.maketrans({".": "[.]", ":": "[:]", "@": "[@]"}))
