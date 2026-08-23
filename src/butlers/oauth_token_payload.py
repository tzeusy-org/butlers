"""Shared validation for successful OAuth token-endpoint payloads.

A ``200`` from a token endpoint only proves the transport succeeded. The body
is still attacker-shaped input: it can carry a non-string access token, a
whitespace-only refresh token, or an ``expires_in`` that is a string, a bool,
a float, negative, or absurdly large. Assigning those values straight into
connector state or a credential store turns a malformed response into a
persistent runtime fault.

This module is the one place that decides what a usable token payload looks
like, so every extraction site enforces the same contract:

* ``access_token`` is required and must be a non-empty string (stripped),
* ``refresh_token`` and ``scope``, when present, must be non-empty strings,
* ``expires_in``, when present, must be a strict ``int`` inside a sane range,
  and defaults to :data:`DEFAULT_EXPIRES_IN_S` when absent.

Rejection raises :class:`OAuthTokenValidationError` with fixed local text.
No provider-supplied value is ever interpolated into the message, so a hostile
response body cannot reach a log line, an error surface, or an audit note.

``src/butlers/spotify_credentials.py`` keeps a Spotify-specific parser because
Spotify's authorization-code exchange additionally requires a refresh token,
scope, and a Bearer token type; this module is the provider-neutral contract
shared by the Google connectors and the generic provider callback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_EXPIRES_IN_S = 3600
"""Lifetime assumed when a token response omits ``expires_in`` entirely."""

MAX_EXPIRES_IN_S = 366 * 24 * 60 * 60
"""Upper bound on an accepted ``expires_in``; anything larger is nonsense."""


class OAuthTokenValidationError(ValueError):
    """A successful OAuth token response is malformed or unsafe to use."""


@dataclass(frozen=True)
class OAuthTokenPayload:
    """Validated fields from an OAuth token endpoint success response."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str | None


def validate_oauth_token_payload(payload: object) -> OAuthTokenPayload:
    """Validate a token success payload before any value reaches runtime state.

    Call this *before* assigning any field of the result, so a rejected payload
    cannot leave a connector or credential store half-updated.

    Raises
    ------
    OAuthTokenValidationError
        If the payload is not a JSON object, the access token is missing or not
        a non-empty string, an optional string field is present but unusable, or
        ``expires_in`` is present and is not a strict positive ``int`` within
        :data:`MAX_EXPIRES_IN_S`.
    """
    if not isinstance(payload, dict):
        raise OAuthTokenValidationError("OAuth token response is not a JSON object.")

    access_token = _required_nonempty_string(payload, "access_token")
    refresh_token = _optional_nonempty_string(payload, "refresh_token")
    scope = _optional_nonempty_string(payload, "scope")

    if "expires_in" in payload:
        expires_in = payload["expires_in"]
        # ``type(...) is int`` rather than isinstance: bool is an int subclass,
        # and ``True`` is not a lifetime.
        if type(expires_in) is not int or not 0 < expires_in <= MAX_EXPIRES_IN_S:
            raise OAuthTokenValidationError("OAuth token response has an invalid expires_in.")
    else:
        expires_in = DEFAULT_EXPIRES_IN_S

    return OAuthTokenPayload(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scope=scope,
    )


def _required_nonempty_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OAuthTokenValidationError(f"OAuth token response has an invalid {field}.")
    return value.strip()


def _optional_nonempty_string(payload: dict[str, Any], field: str) -> str | None:
    if field not in payload:
        return None
    return _required_nonempty_string(payload, field)
