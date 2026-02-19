"""Pydantic models for Google OAuth bootstrap endpoints.

Provides request/response models for the OAuth start and callback flows,
and the credential status surface.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, computed_field


class OAuthStartResponse(BaseModel):
    """Response from the OAuth start endpoint.

    Returns the authorization URL that the user should visit to grant access.
    """

    authorization_url: str
    state: str


class OAuthCallbackSuccess(BaseModel):
    """Successful OAuth callback payload.

    Returned when the authorization code has been successfully exchanged
    for tokens and the refresh token has been stored.
    """

    success: bool = True
    message: str = "OAuth bootstrap complete. Refresh token stored."
    provider: str = "google"
    scope: str | None = None


class OAuthCallbackError(BaseModel):
    """Error payload returned when the OAuth callback fails.

    Error messages are actionable but do not leak client secrets or raw
    provider error details that could aid an attacker.
    """

    success: bool = False
    error_code: str
    message: str
    provider: str = "google"


# ---------------------------------------------------------------------------
# Status models
# ---------------------------------------------------------------------------


class OAuthCredentialState(StrEnum):
    """Operational state of a set of Google OAuth credentials.

    Values are stable identifiers suitable for use in frontend conditional
    rendering logic (e.g. showing a "Connect Google" button vs. a healthy
    badge).
    """

    connected = "connected"
    """Credentials are present, validated, and believed to be usable."""

    not_configured = "not_configured"
    """Required environment variables are absent; OAuth has never been bootstrapped."""

    expired = "expired"
    """Refresh token has been revoked or has expired; a new bootstrap flow is required."""

    missing_scope = "missing_scope"
    """Credentials are present but lack one or more required OAuth scopes."""

    redirect_uri_mismatch = "redirect_uri_mismatch"
    """The registered redirect URI does not match what Google expects; reconfiguration needed."""

    unapproved_tester = "unapproved_tester"
    """The Google OAuth app is in testing mode and the account has not been added as a tester."""

    unknown_error = "unknown_error"
    """An unclassified error is preventing credential use; see ``remediation`` for guidance."""


class OAuthCredentialStatus(BaseModel):
    """Status of a single OAuth credential set (e.g. Google).

    Provides a machine-readable ``state`` and a human-readable
    ``remediation`` message for actionable frontend UX.
    """

    provider: str = "google"
    """OAuth provider identifier (e.g. ``"google"``)."""

    state: OAuthCredentialState
    """Machine-readable connectivity state."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def connected(self) -> bool:
        """Convenience boolean: ``True`` iff ``state == OAuthCredentialState.connected``."""
        return self.state == OAuthCredentialState.connected

    scopes_granted: list[str] | None = None
    """List of OAuth scopes present on the stored credential, when known."""

    remediation: str | None = None
    """
    Actionable human-readable guidance shown when ``connected`` is ``False``.

    Examples
    --------
    - "Click 'Connect Google' to start the OAuth authorization flow."
    - "Your Google token has expired. Re-run the OAuth flow to reconnect."
    - "Add your Google account as a tester in the Google Cloud Console OAuth consent screen."
    """

    detail: str | None = None
    """
    Optional technical detail for operator use.

    Not shown in end-user UX but included in API responses so developers
    can diagnose credential issues without tailing server logs.
    """


class OAuthStatusResponse(BaseModel):
    """Top-level response for GET /api/oauth/status.

    Aggregates the status of all OAuth credential sets known to the system.
    For v1, only Google credentials are surfaced.
    """

    google: OAuthCredentialStatus
