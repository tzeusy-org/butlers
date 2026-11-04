"""The one place owner-facing (Telegram) dashboard links are built.

A link is only worth sending if a phone can open it. When ``DASHBOARD_URL`` is
unset there is no reachable origin (``localhost`` is not one), so the helpers
return ``None`` and log one warning; callers send the message without a link.

Every route a builder passes here must be listed in
``frontend/tests/e2e/telegram-minted-routes.json``; a test enforces that, and
the Playwright phone spec holds each listed route to the phone contract.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_warned_unset = False


def dashboard_base_url(override: str | None = None) -> str | None:
    """Return the public dashboard origin, or ``None`` when none is configured."""
    global _warned_unset
    value = (override or os.environ.get("DASHBOARD_URL") or "").strip().rstrip("/")
    if value:
        return value
    if not _warned_unset:
        _warned_unset = True
        logger.warning(
            "DASHBOARD_URL is unset; owner-facing messages are sent without dashboard links"
        )
    return None


def dashboard_link(path: str, *, base: str | None = None) -> str | None:
    """Return the absolute URL for a dashboard-relative ``path``, or ``None``."""
    origin = dashboard_base_url(base)
    if origin is None:
        return None
    return f"{origin}/{path.lstrip('/')}"
