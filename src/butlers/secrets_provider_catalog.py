"""Provider catalog for the secrets passport page.

This module defines the canonical list of credential providers that the
secrets inventory understands.  The catalog is returned as part of the
``GET /api/secrets/inventory`` response so the frontend never needs a
separate round-trip and the FE/BE shapes stay in sync.

The shape mirrors the ``ProviderInfo`` TypeScript interface in
``frontend/src/components/secrets/passport/types.ts`` and the
``PROVIDER_CATALOG`` constant that previously lived in
``frontend/src/hooks/use-secrets-inventory.ts``.

When a new connector is added that requires credential management, add
its entry here.  The frontend hook consumes ``response.providers`` and
falls back to its own copy for one release cycle (see the TODO comment
in ``use-secrets-inventory.ts``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProviderMetadata(BaseModel):
    """Display metadata for a single credential provider.

    Fields match the ``ProviderInfo`` TypeScript interface:
    - id: provider slug (e.g. ``"google"``)
    - label: human-readable name (e.g. ``"Google"``)
    - glyph: single character used as the provider icon
    - kind: credential mechanism
    - authority: canonical issuing domain / service name
    - brief: one-line description of what this credential enables
    - cadence: how often data is fetched / events arrive
    """

    id: str
    label: str
    glyph: str
    kind: Literal["oauth", "token", "apikey", "webhook"]
    authority: str
    brief: str
    cadence: str


# ---------------------------------------------------------------------------
# Canonical provider catalog
# ---------------------------------------------------------------------------
# Order: most-common / most-critical first.
# Add new entries here when a connector requires credential management.
# ---------------------------------------------------------------------------

PROVIDER_CATALOG: dict[str, ProviderMetadata] = {
    "google": ProviderMetadata(
        id="google",
        label="Google",
        glyph="G",
        kind="oauth",
        authority="accounts.google.com",
        brief="Calendar, Gmail, Drive read.",
        cadence="on demand · refreshes hourly",
    ),
    "spotify": ProviderMetadata(
        id="spotify",
        label="Spotify",
        glyph="S",
        kind="oauth",
        authority="accounts.spotify.com",
        brief="Recent listens.",
        cadence="poll · 15m",
    ),
    "homeassistant": ProviderMetadata(
        id="homeassistant",
        label="Home Assistant",
        glyph="H",
        kind="token",
        authority="home.lim.local",
        brief="Smart-home state, sensors.",
        cadence="poll · 30s",
    ),
    "whatsapp": ProviderMetadata(
        id="whatsapp",
        label="WhatsApp",
        glyph="W",
        kind="oauth",
        authority="wa.bridge",
        brief="Inbound messages.",
        cadence="webhook + poll · 5m",
    ),
    "owntracks": ProviderMetadata(
        id="owntracks",
        label="OwnTracks",
        glyph="O",
        kind="webhook",
        authority="self-hosted",
        brief="Location pings via MQTT.",
        cadence="event-driven",
    ),
    "steam": ProviderMetadata(
        id="steam",
        label="Steam",
        glyph="V",
        kind="apikey",
        authority="steamcommunity.com",
        brief="Library, playtime.",
        cadence="poll · 6h",
    ),
    "telegram_bot": ProviderMetadata(
        id="telegram_bot",
        label="Telegram Bot",
        glyph="T",
        kind="token",
        authority="api.telegram.org",
        brief="Bot inbound + outbound.",
        cadence="webhook + poll · 30s",
    ),
    "anthropic": ProviderMetadata(
        id="anthropic",
        label="Anthropic",
        glyph="A",
        kind="apikey",
        authority="api.anthropic.com",
        brief="Claude model calls.",
        cadence="on demand",
    ),
}
