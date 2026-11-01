"""Calendar oauth implementation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)

from butlers.oauth_token_payload import OAuthTokenValidationError, validate_oauth_token_payload

from .constants import GOOGLE_OAUTH_TOKEN_URL, logger
from .errors import (
    CalendarCredentialError,
    CalendarTokenRefreshError,
    _google_error_code,
    _safe_google_error_message,
)


class _GoogleOAuthCredentials(BaseModel):
    """OAuth client credentials required for refresh-token exchange."""

    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)

    @field_validator("client_id", "client_secret", "refresh_token")
    @classmethod
    def _normalize_non_empty(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @classmethod
    def from_json(cls, raw_value: str) -> _GoogleOAuthCredentials:
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise CalendarCredentialError(f"Credential JSON must be valid JSON: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise CalendarCredentialError("Credential JSON must decode to a JSON object")

        credential_data = {
            "client_id": _extract_google_credential_value(payload, "client_id"),
            "client_secret": _extract_google_credential_value(payload, "client_secret"),
            "refresh_token": _extract_google_credential_value(payload, "refresh_token"),
        }

        missing = sorted(key for key, value in credential_data.items() if value is None)
        if missing:
            field_list = ", ".join(missing)
            raise CalendarCredentialError(
                f"Credential JSON is missing required field(s): {field_list}"
            )

        invalid = sorted(
            key
            for key, value in credential_data.items()
            if not isinstance(value, str) or not value.strip()
        )
        if invalid:
            field_list = ", ".join(invalid)
            raise CalendarCredentialError(
                f"Credential JSON must contain non-empty string field(s): {field_list}"
            )

        return cls(
            client_id=str(credential_data["client_id"]),
            client_secret=str(credential_data["client_secret"]),
            refresh_token=str(credential_data["refresh_token"]),
        )


class _GoogleOAuthClient:
    """Refresh-token OAuth helper with lightweight access-token caching."""

    def __init__(
        self,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient,
        on_token_refreshed: Callable[[], Coroutine[Any, Any, None]] | None = None,
        on_token_revoked: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()
        self._on_token_refreshed = on_token_refreshed
        self._on_token_revoked = on_token_revoked

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._token_is_fresh():
            assert self._access_token is not None
            return self._access_token

        async with self._refresh_lock:
            if not force_refresh and self._token_is_fresh():
                assert self._access_token is not None
                return self._access_token

            await self._refresh_access_token()
            assert self._access_token is not None
            return self._access_token

    def _token_is_fresh(self) -> bool:
        if self._access_token is None or self._access_token_expires_at is None:
            return False
        return datetime.now(UTC) < self._access_token_expires_at

    async def _refresh_access_token(self) -> None:
        max_retries = 2
        last_exc: CalendarTokenRefreshError | None = None

        for attempt in range(max_retries + 1):
            try:
                response = await self._http_client.post(
                    GOOGLE_OAUTH_TOKEN_URL,
                    data={
                        "client_id": self._credentials.client_id,
                        "client_secret": self._credentials.client_secret,
                        "refresh_token": self._credentials.refresh_token,
                        "grant_type": "refresh_token",
                    },
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as exc:
                raise CalendarTokenRefreshError(
                    f"Google OAuth token refresh request failed: {exc}"
                ) from exc

            if response.status_code < 200 or response.status_code >= 300:
                error_code = _google_error_code(response)
                last_exc = CalendarTokenRefreshError(
                    "Google OAuth token refresh failed "
                    f"({response.status_code}): {_safe_google_error_message(response)}"
                )
                if error_code == "invalid_grant":
                    await self._notify_token_revoked()
                    raise last_exc
                if attempt < max_retries:
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "CalendarModule: token refresh attempt %d/%d failed (%d), retrying in %ds",
                        attempt + 1,
                        max_retries + 1,
                        response.status_code,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise last_exc

            break

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarTokenRefreshError(
                "Google OAuth token endpoint returned invalid JSON"
            ) from exc

        try:
            token = validate_oauth_token_payload(payload)
        except OAuthTokenValidationError as exc:
            raise CalendarTokenRefreshError(
                "Google OAuth token endpoint returned an invalid token payload"
            ) from exc

        # Refresh early to avoid edge-of-expiration failures.
        refresh_ttl_seconds = max(token.expires_in - 60, 30)

        self._access_token = token.access_token
        self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=refresh_ttl_seconds)

        # Notify caller (e.g. to update last_token_refresh_at in google_accounts).
        if self._on_token_refreshed is not None:
            try:
                await self._on_token_refreshed()
            except Exception:
                logger.debug(
                    "CalendarModule: on_token_refreshed callback failed (best-effort)",
                    exc_info=True,
                )

    async def _notify_token_revoked(self) -> None:
        if self._on_token_revoked is None:
            return
        try:
            await self._on_token_revoked()
        except Exception:
            logger.debug(
                "CalendarModule: on_token_revoked callback failed (best-effort)",
                exc_info=True,
            )


def _extract_google_credential_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload[key]

    for nested_key in ("installed", "web"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None
