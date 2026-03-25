"""Spotify credential key constants for use with CredentialStore.

Defines the canonical butler_secrets key names for Spotify OAuth tokens and
app credentials.  All Spotify credentials are stored with ``category="spotify"``
and ``is_sensitive=True`` (the default).

Resolution order (via ``CredentialStore.resolve()``)::

    access_token = await store.resolve(SPOTIFY_ACCESS_TOKEN)
    if access_token is None:
        raise RuntimeError("Spotify access token is not configured")

Key constants
-------------
SPOTIFY_CLIENT_ID
    The Spotify application client ID (32-character hex string).
SPOTIFY_ACCESS_TOKEN
    The short-lived OAuth access token for Spotify Web API calls.
SPOTIFY_REFRESH_TOKEN
    The long-lived OAuth refresh token for obtaining new access tokens.
SPOTIFY_TOKEN_EXPIRES_AT
    ISO-8601 UTC timestamp string indicating when the access token expires,
    used for proactive refresh (refresh 5 minutes before expiry).

See tasks 2.1-2.2 in openspec/changes/connector-spotify/tasks.md.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# butler_secrets key names for Spotify OAuth credentials
# ---------------------------------------------------------------------------

SPOTIFY_CLIENT_ID = "SPOTIFY_CLIENT_ID"
"""Spotify application client ID (32-character hex string)."""

SPOTIFY_ACCESS_TOKEN = "SPOTIFY_ACCESS_TOKEN"
"""Short-lived Spotify OAuth access token for Web API calls."""

SPOTIFY_REFRESH_TOKEN = "SPOTIFY_REFRESH_TOKEN"
"""Long-lived Spotify OAuth refresh token for obtaining new access tokens."""

SPOTIFY_TOKEN_EXPIRES_AT = "SPOTIFY_TOKEN_EXPIRES_AT"
"""ISO-8601 UTC timestamp string for proactive access token refresh."""

SPOTIFY_CATEGORY = "spotify"
"""Category label used when storing Spotify credentials in butler_secrets."""

# Backwards-compatibility alias for code importing the private name.
_SPOTIFY_CATEGORY = SPOTIFY_CATEGORY
