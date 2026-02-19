"""Pydantic models for the secrets CRUD API.

These models enforce the no-value-in-response security contract: secret values
are accepted as input (PUT body) but never returned in any response.  Responses
carry only metadata plus an ``is_set`` boolean that indicates whether the secret
currently has a non-empty value.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Response models — values NEVER included
# ---------------------------------------------------------------------------


class SecretEntry(BaseModel):
    """Metadata for a single secret.  Raw values are never exposed.

    Attributes
    ----------
    key:
        The unique secret name (e.g. ``"BUTLER_TELEGRAM_TOKEN"``).
    category:
        Grouping label used by the dashboard (e.g. ``"telegram"``).
    description:
        Optional human-readable label for display in the dashboard.
    is_sensitive:
        When ``True``, the raw value must be masked in UI and log output.
    is_set:
        ``True`` if the secret has a non-empty value stored.  The raw value
        is never included — callers can only learn *whether* it is set.
    created_at:
        When the secret was first stored.
    updated_at:
        When the secret was last updated.
    expires_at:
        Optional expiry timestamp.  ``None`` means the secret never expires.
    source:
        Where the value was resolved from: ``"database"`` or ``"environment"``.
    """

    key: str
    category: str = "general"
    description: str | None = None
    is_sensitive: bool = True
    is_set: bool
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    source: str = "database"


# ---------------------------------------------------------------------------
# Request models — value accepted on write, never echoed back
# ---------------------------------------------------------------------------

_UNSET = object()  # sentinel for "field not provided in request"


class SecretUpsertRequest(BaseModel):
    """Request body for creating or updating a secret (PUT).

    The ``value`` field is required for creating/updating a secret.  It is
    write-only: it is accepted here but never included in any response model.

    All other fields are optional.  When omitted on an *update*, the existing
    stored values are preserved — the PUT does **not** reset unspecified fields
    to their defaults.  When creating a new secret, omitted fields use their
    documented defaults (``category="general"``, ``is_sensitive=True``, etc.).
    """

    value: str = Field(
        ...,
        description="The secret value.  Write-only — never returned in responses.",
        min_length=1,
    )
    category: str | None = Field(
        default=None,
        description="Grouping label for dashboard display. Preserved on update when omitted.",
    )
    description: str | None = Field(
        default=None,
        description="Human-readable label.  Preserved from existing record when omitted.",
    )
    is_sensitive: bool | None = Field(
        default=None,
        description="When true, mask the value in UI and logs. Preserved on update when omitted.",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiry time.  None means the secret never expires.",
    )
