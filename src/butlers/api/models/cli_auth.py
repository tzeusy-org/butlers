"""Pydantic models for CLI auth device-code flow endpoints.

Provides request/response models for starting and polling CLI tool
authentication flows (OpenCode, Codex) via device code authorization.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CLIAuthSessionState(StrEnum):
    """State of a CLI auth session."""

    starting = "starting"
    """Subprocess launched, waiting for device code to appear in stdout."""

    awaiting_auth = "awaiting_auth"
    """Device code parsed; waiting for user to authorize in browser."""

    success = "success"
    """Authentication completed successfully."""

    failed = "failed"
    """Authentication failed (process exited non-zero or timed out)."""

    expired = "expired"
    """Session timed out before user completed authorization."""


class CLIAuthHealthState(StrEnum):
    """Health state of a CLI auth provider."""

    authenticated = "authenticated"
    not_authenticated = "not_authenticated"
    unavailable = "unavailable"
    probe_failed = "probe_failed"


class CLIAuthProvider(BaseModel):
    """Summary of a CLI auth provider and its current status."""

    name: str
    """Provider identifier (e.g. ``"opencode-openai"``, ``"codex"``)."""

    display_name: str
    """Human-readable label (e.g. ``"OpenCode (OpenAI)"``).`"""

    runtime: str
    """Which butler runtime adapter this provides auth for."""

    auth_mode: str = "device_code"
    """Auth mode: ``"device_code"`` or ``"api_key"``."""

    authenticated: bool
    """Whether valid credentials currently exist on disk."""

    health: CLIAuthHealthState | None = None
    """Result of the live health probe (None if not yet probed)."""

    health_detail: str | None = None
    """Human-readable detail from the health probe."""

    token_path: str | None = None
    """Path to the credential file (masked for display, not secret)."""

    env_var: str | None = None
    """Environment variable name for API key providers."""


class CLIAuthStartResponse(BaseModel):
    """Response from POST /api/cli-auth/{provider}/start."""

    session_id: str
    """Unique session identifier for polling."""

    state: CLIAuthSessionState
    """Current session state."""

    auth_url: str | None = None
    """URL the user should visit to authorize."""

    device_code: str | None = None
    """One-time code the user enters at the auth URL."""

    message: str | None = None
    """Human-readable status message."""


class CLIAuthSessionResponse(BaseModel):
    """Response from GET /api/cli-auth/sessions/{session_id}."""

    session_id: str
    state: CLIAuthSessionState
    auth_url: str | None = None
    device_code: str | None = None
    message: str | None = None
    provider: str | None = None


class CLIAuthApiKeyRequest(BaseModel):
    """Request body for PUT /api/cli-auth/{provider}/api-key."""

    api_key: str
    """The API key value to store."""


class CLIAuthApiKeyResponse(BaseModel):
    """Response from PUT /api/cli-auth/{provider}/api-key."""

    provider: str
    stored: bool
    message: str | None = None


class CLIAuthTestResponse(BaseModel):
    """Response from POST /api/cli-auth/{provider}/test."""

    provider: str
    success: bool
    detail: str | None = None
