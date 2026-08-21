"""Canonical Spotify credential authority identifiers.

The application client id is Tier 1 system configuration. OAuth tokens are
Tier 2 owner credentials and must only be resolved from ``entity_info``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# butler_secrets key names for Spotify OAuth credentials
# ---------------------------------------------------------------------------

SPOTIFY_CLIENT_ID = "SPOTIFY_CLIENT_ID"
"""Spotify application client ID (32-character hex string)."""

SPOTIFY_OAUTH_ACCESS = "spotify_oauth_access"
SPOTIFY_OAUTH_REFRESH = "spotify_oauth_refresh"
SPOTIFY_OAUTH_EXPIRES_AT = "spotify_oauth_expires_at"

SPOTIFY_MANAGED_ENTITY_INFO_TYPES: frozenset[str] = frozenset(
    {SPOTIFY_OAUTH_ACCESS, SPOTIFY_OAUTH_REFRESH, SPOTIFY_OAUTH_EXPIRES_AT}
)

# Compatibility names retained until the separately-owned generic OAuth
# Spotify provider is removed. Connector and module code must not use these.
SPOTIFY_ACCESS_TOKEN = "SPOTIFY_ACCESS_TOKEN"
SPOTIFY_REFRESH_TOKEN = "SPOTIFY_REFRESH_TOKEN"
SPOTIFY_TOKEN_EXPIRES_AT = "SPOTIFY_TOKEN_EXPIRES_AT"

SPOTIFY_CATEGORY = "spotify"
"""Category label used when storing Spotify credentials in butler_secrets."""


class SpotifyTokenResponseError(ValueError):
    """A successful Spotify token response is malformed or unsafe to use."""


@dataclass(frozen=True)
class SpotifyTokenResponse:
    """Validated fields from a Spotify OAuth token endpoint success response."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str | None
    token_type: str | None


_MAX_ACCESS_TOKEN_LIFETIME_S = 366 * 24 * 60 * 60


def parse_spotify_token_response(
    payload: object,
    *,
    require_refresh_token: bool,
    require_scope: bool = False,
    require_token_type: bool = False,
) -> SpotifyTokenResponse:
    """Validate a token success payload before its values reach an authority sink.

    Spotify's authorization-code exchange must include a refresh token, scope,
    and Bearer token type. A refresh response may omit the refresh token when
    it is not rotated, but any value it supplies must still be a usable string.
    """
    if not isinstance(payload, dict):
        raise SpotifyTokenResponseError("Spotify token response is invalid.")

    access_token = _required_nonempty_string(payload, "access_token")
    expires_in = payload.get("expires_in")
    if type(expires_in) is not int or not 0 < expires_in <= _MAX_ACCESS_TOKEN_LIFETIME_S:
        raise SpotifyTokenResponseError("Spotify token response is invalid.")

    refresh_token = _optional_nonempty_string(
        payload,
        "refresh_token",
        required=require_refresh_token,
    )
    scope = _optional_nonempty_string(payload, "scope", required=require_scope)
    token_type = _optional_nonempty_string(payload, "token_type", required=require_token_type)
    if token_type is not None and token_type.casefold() != "bearer":
        raise SpotifyTokenResponseError("Spotify token response is invalid.")

    return SpotifyTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scope=scope,
        token_type=token_type,
    )


def _required_nonempty_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SpotifyTokenResponseError("Spotify token response is invalid.")
    return value


def _optional_nonempty_string(
    payload: dict[str, Any],
    field: str,
    *,
    required: bool,
) -> str | None:
    if field not in payload:
        if required:
            raise SpotifyTokenResponseError("Spotify token response is invalid.")
        return None

    return _required_nonempty_string(payload, field)
