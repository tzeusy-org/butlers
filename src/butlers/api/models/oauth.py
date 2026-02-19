"""Pydantic models for Google OAuth bootstrap endpoints.

Provides request/response models for the OAuth start and callback flows.
"""

from __future__ import annotations

from pydantic import BaseModel


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
