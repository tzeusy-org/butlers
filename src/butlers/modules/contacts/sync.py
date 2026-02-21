"""Core Google contacts sync primitives.

This module is intentionally module-agnostic and focuses on:
- provider fetch contracts (full + incremental)
- sync state persistence
- idempotent change application
"""

from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from butlers.core.state import state_get as _state_get
from butlers.core.state import state_set as _state_set

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_PEOPLE_API_CONNECTIONS_URL = "https://people.googleapis.com/v1/people/me/connections"

DEFAULT_GOOGLE_PERSON_FIELDS = (
    "names,emailAddresses,phoneNumbers,addresses,birthdays,events,organizations,"
    "biographies,urls,memberships,photos,userDefined,metadata"
)
DEFAULT_GOOGLE_PAGE_SIZE = 500
SYNC_STATE_KEY_PREFIX = "contacts::sync::"

ContactsSyncMode = Literal["incremental", "full"]


class ContactsSyncError(RuntimeError):
    """Base contacts sync error."""


class ContactsTokenRefreshError(ContactsSyncError):
    """Raised when OAuth refresh-token exchange fails."""


class ContactsRequestError(ContactsSyncError):
    """Raised when Google People API request fails."""

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Google People API request failed ({status_code}): {message}")


class ContactsSyncTokenExpiredError(ContactsSyncError):
    """Raised when an incremental sync cursor is expired/invalid."""


class ContactEmail(BaseModel):
    """Canonical email value."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    label: str | None = None
    primary: bool = False
    normalized_value: str | None = None

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be a non-empty string")
        return normalized


class ContactPhone(BaseModel):
    """Canonical phone value."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    label: str | None = None
    primary: bool = False
    e164_normalized: str | None = None

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be a non-empty string")
        return normalized


class CanonicalContact(BaseModel):
    """Provider-neutral canonical contact shape."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1)
    etag: str | None = None
    display_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    nickname: str | None = None
    emails: list[ContactEmail] = Field(default_factory=list)
    phones: list[ContactPhone] = Field(default_factory=list)
    group_memberships: list[str] = Field(default_factory=list)
    deleted: bool = False
    raw: dict[str, Any] | None = None

    @field_validator("external_id")
    @classmethod
    def _normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_id must be a non-empty string")
        return normalized


class ContactBatch(BaseModel):
    """One provider sync page."""

    model_config = ConfigDict(extra="forbid")

    contacts: list[CanonicalContact] = Field(default_factory=list)
    next_page_token: str | None = None
    next_sync_cursor: str | None = None
    checkpoint: dict[str, Any] | None = None

    @field_validator("next_page_token", "next_sync_cursor")
    @classmethod
    def _normalize_tokens(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ContactsSyncState(BaseModel):
    """Persistent sync state and idempotency index."""

    model_config = ConfigDict(extra="ignore")

    sync_cursor: str | None = None
    cursor_issued_at: str | None = None
    last_full_sync_at: str | None = None
    last_incremental_sync_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    contact_versions: dict[str, str] = Field(default_factory=dict)


class ContactsSyncResult(BaseModel):
    """Outcome summary from one sync cycle."""

    model_config = ConfigDict(extra="forbid")

    mode: ContactsSyncMode
    fetched_contacts: int
    applied_contacts: int
    skipped_contacts: int
    deleted_contacts: int
    next_sync_cursor: str


class ContactsProvider(abc.ABC):
    """Provider contract for contact sync."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable provider name."""
        ...

    @abc.abstractmethod
    async def full_sync(self, *, account_id: str, page_token: str | None = None) -> ContactBatch:
        """Fetch one full-sync page."""
        ...

    @abc.abstractmethod
    async def incremental_sync(
        self,
        *,
        account_id: str,
        cursor: str,
        page_token: str | None = None,
    ) -> ContactBatch:
        """Fetch one incremental-sync page."""
        ...

    @abc.abstractmethod
    async def shutdown(self) -> None:
        """Release provider resources."""
        ...


class _GoogleOAuthCredentials(BaseModel):
    """Google OAuth client credentials for refresh-token exchange."""

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


class _GoogleOAuthClient:
    """Refresh-token OAuth helper with in-memory access-token cache."""

    def __init__(
        self,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._credentials = credentials
        self._http_client = http_client
        self._access_token: str | None = None
        self._access_token_expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

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
            raise ContactsTokenRefreshError(
                f"Google OAuth token refresh request failed: {exc}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise ContactsTokenRefreshError(
                "Google OAuth token refresh failed "
                f"({response.status_code}): {_safe_google_error_message(response)}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ContactsTokenRefreshError(
                "Google OAuth token endpoint returned invalid JSON"
            ) from exc

        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            raise ContactsTokenRefreshError(
                "Google OAuth token response is missing a non-empty access_token"
            )

        expires_in_raw = payload.get("expires_in") if isinstance(payload, dict) else None
        expires_in_seconds = _coerce_expires_in_seconds(expires_in_raw)
        refresh_ttl_seconds = max(expires_in_seconds - 60, 30)

        self._access_token = access_token.strip()
        self._access_token_expires_at = datetime.now(UTC) + timedelta(seconds=refresh_ttl_seconds)


class GoogleContactsProvider(ContactsProvider):
    """Google People API provider implementation for contacts sync."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        person_fields: str = DEFAULT_GOOGLE_PERSON_FIELDS,
        page_size: int = DEFAULT_GOOGLE_PAGE_SIZE,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = _GoogleOAuthCredentials(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )
        self._person_fields = person_fields.strip() or DEFAULT_GOOGLE_PERSON_FIELDS
        self._page_size = max(1, int(page_size))
        self._owns_http_client = http_client is None
        self._http_client = (
            http_client
            if http_client is not None
            else httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        )
        self._oauth = _GoogleOAuthClient(self._credentials, self._http_client)

    @property
    def name(self) -> str:
        return "google"

    async def full_sync(self, *, account_id: str, page_token: str | None = None) -> ContactBatch:
        del account_id
        params: dict[str, Any] = {
            "personFields": self._person_fields,
            "pageSize": self._page_size,
            "requestSyncToken": "true",
        }
        if page_token is not None:
            params["pageToken"] = page_token
        payload = await self._request_connections(params)
        return _parse_google_batch(payload)

    async def incremental_sync(
        self,
        *,
        account_id: str,
        cursor: str,
        page_token: str | None = None,
    ) -> ContactBatch:
        del account_id
        params: dict[str, Any] = {
            "personFields": self._person_fields,
            "pageSize": self._page_size,
            "syncToken": cursor,
        }
        if page_token is not None:
            params["pageToken"] = page_token
        payload = await self._request_connections(params, has_sync_token=True)
        return _parse_google_batch(payload)

    async def shutdown(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _request_connections(
        self,
        params: dict[str, Any],
        *,
        has_sync_token: bool = False,
    ) -> dict[str, Any]:
        token = await self._oauth.get_access_token()
        response = await self._http_client.get(
            GOOGLE_PEOPLE_API_CONNECTIONS_URL,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if response.status_code == 401:
            token = await self._oauth.get_access_token(force_refresh=True)
            response = await self._http_client.get(
                GOOGLE_PEOPLE_API_CONNECTIONS_URL,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )

        if response.status_code == 410 and has_sync_token:
            raise ContactsSyncTokenExpiredError("Google People sync token expired/invalid")

        if response.status_code < 200 or response.status_code >= 300:
            raise ContactsRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ContactsRequestError(
                status_code=response.status_code,
                message="Invalid JSON payload from Google People API",
            ) from exc

        if not isinstance(payload, dict):
            raise ContactsRequestError(
                status_code=response.status_code,
                message="Google People API payload must be a JSON object",
            )
        return payload


def _safe_google_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return " ".join(message.split())[:200]
        message = payload.get("error_description") or payload.get("message")
        if isinstance(message, str) and message.strip():
            return " ".join(message.split())[:200]

    text = response.text.strip()
    if text:
        return " ".join(text.split())[:200]
    return "unknown error"


def _coerce_expires_in_seconds(value: Any) -> int:
    if isinstance(value, bool):
        return 3600
    if isinstance(value, int | float):
        return int(value) if value > 0 else 3600
    return 3600


def _as_non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_google_batch(payload: dict[str, Any]) -> ContactBatch:
    raw_people = payload.get("connections")
    contacts: list[CanonicalContact] = []
    if isinstance(raw_people, list):
        for item in raw_people:
            if not isinstance(item, dict):
                continue
            parsed = _parse_google_contact(item)
            if parsed is not None:
                contacts.append(parsed)

    return ContactBatch(
        contacts=contacts,
        next_page_token=_as_non_empty_string(payload.get("nextPageToken")),
        next_sync_cursor=_as_non_empty_string(payload.get("nextSyncToken")),
        checkpoint={"total_people": len(contacts)},
    )


def _parse_google_contact(payload: dict[str, Any]) -> CanonicalContact | None:
    external_id = _as_non_empty_string(payload.get("resourceName"))
    if external_id is None:
        logger.warning("Skipping Google contact without resourceName")
        return None

    metadata = payload.get("metadata")
    deleted = bool(metadata.get("deleted")) if isinstance(metadata, dict) else False

    names = payload.get("names")
    primary_name = _pick_primary_entry(names) if isinstance(names, list) else None
    display_name = (
        _as_non_empty_string(primary_name.get("displayName"))
        if isinstance(primary_name, dict)
        else None
    )
    first_name = (
        _as_non_empty_string(primary_name.get("givenName"))
        if isinstance(primary_name, dict)
        else None
    )
    last_name = (
        _as_non_empty_string(primary_name.get("familyName"))
        if isinstance(primary_name, dict)
        else None
    )
    middle_name = (
        _as_non_empty_string(primary_name.get("middleName"))
        if isinstance(primary_name, dict)
        else None
    )

    emails = _parse_emails(payload.get("emailAddresses"))
    phones = _parse_phones(payload.get("phoneNumbers"))

    nicknames = payload.get("nicknames")
    nickname = None
    if isinstance(nicknames, list):
        primary_nickname = _pick_primary_entry(nicknames)
        if isinstance(primary_nickname, dict):
            nickname = _as_non_empty_string(primary_nickname.get("value"))

    memberships = payload.get("memberships")
    group_memberships = _parse_group_memberships(memberships)

    return CanonicalContact(
        external_id=external_id,
        etag=_as_non_empty_string(payload.get("etag")),
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        middle_name=middle_name,
        nickname=nickname,
        emails=emails,
        phones=phones,
        group_memberships=group_memberships,
        deleted=deleted,
        raw=payload,
    )


def _pick_primary_entry(values: list[Any]) -> dict[str, Any] | None:
    first_dict: dict[str, Any] | None = None
    for item in values:
        if not isinstance(item, dict):
            continue
        if first_dict is None:
            first_dict = item
        metadata = item.get("metadata")
        if isinstance(metadata, dict) and metadata.get("primary") is True:
            return item
    return first_dict


def _parse_emails(raw: Any) -> list[ContactEmail]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactEmail] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = _as_non_empty_string(item.get("value"))
        if value is None:
            continue
        metadata = item.get("metadata")
        primary = bool(metadata.get("primary")) if isinstance(metadata, dict) else False
        parsed.append(
            ContactEmail(
                value=value,
                label=_as_non_empty_string(item.get("type")),
                primary=primary,
                normalized_value=value.lower(),
            )
        )
    return parsed


def _parse_phones(raw: Any) -> list[ContactPhone]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactPhone] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = _as_non_empty_string(item.get("value"))
        if value is None:
            continue
        metadata = item.get("metadata")
        primary = bool(metadata.get("primary")) if isinstance(metadata, dict) else False
        canonical = _as_non_empty_string(item.get("canonicalForm"))
        parsed.append(
            ContactPhone(
                value=value,
                label=_as_non_empty_string(item.get("type")),
                primary=primary,
                e164_normalized=canonical,
            )
        )
    return parsed


def _parse_group_memberships(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    groups: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        group_ref = item.get("contactGroupMembership")
        if not isinstance(group_ref, dict):
            continue
        resource = _as_non_empty_string(group_ref.get("contactGroupResourceName"))
        if resource is not None:
            groups.append(resource)
    return groups


class ContactsSyncStateRepository(Protocol):
    """Persistence contract for provider/account sync state."""

    async def load(self, *, provider: str, account_id: str) -> ContactsSyncState:
        """Load state for provider/account."""
        ...

    async def save(
        self,
        *,
        provider: str,
        account_id: str,
        state: ContactsSyncState,
    ) -> None:
        """Persist state for provider/account."""
        ...


class ContactsSyncStateStore:
    """KV-backed sync state repository using ``core.state``."""

    def __init__(self, pool: Any, *, key_prefix: str = SYNC_STATE_KEY_PREFIX) -> None:
        self._pool = pool
        self._key_prefix = key_prefix

    def key(self, *, provider: str, account_id: str) -> str:
        provider_key = provider.strip().lower()
        account_key = account_id.strip()
        return f"{self._key_prefix}{provider_key}::{account_key}"

    async def load(self, *, provider: str, account_id: str) -> ContactsSyncState:
        raw = await _state_get(self._pool, self.key(provider=provider, account_id=account_id))
        if not isinstance(raw, dict):
            return ContactsSyncState()
        return ContactsSyncState(**raw)

    async def save(
        self,
        *,
        provider: str,
        account_id: str,
        state: ContactsSyncState,
    ) -> None:
        await _state_set(
            self._pool,
            self.key(provider=provider, account_id=account_id),
            state.model_dump(),
        )


class ContactApplyFn(Protocol):
    """Callback invoked for each non-idempotent contact update."""

    async def __call__(self, contact: CanonicalContact) -> None:
        """Apply one canonical contact update."""
        ...


class ContactsSyncEngine:
    """Runs full/incremental sync cycles with idempotent application."""

    def __init__(
        self,
        *,
        provider: ContactsProvider,
        state_store: ContactsSyncStateRepository,
        apply_contact: ContactApplyFn,
    ) -> None:
        self._provider = provider
        self._state_store = state_store
        self._apply_contact = apply_contact

    async def sync(
        self,
        *,
        account_id: str,
        mode: ContactsSyncMode = "incremental",
    ) -> ContactsSyncResult:
        provider_name = self._provider.name
        state = await self._state_store.load(provider=provider_name, account_id=account_id)
        effective_mode = "full" if mode == "full" or state.sync_cursor is None else "incremental"
        cursor = state.sync_cursor

        if mode == "incremental" and cursor is None:
            logger.info(
                "Contacts sync: no saved cursor for provider=%s account=%s; running full sync",
                provider_name,
                account_id,
            )

        try:
            if effective_mode == "incremental":
                try:
                    contacts, next_cursor = await self._collect_incremental(
                        account_id=account_id,
                        cursor=cursor,
                    )
                except ContactsSyncTokenExpiredError:
                    logger.warning(
                        "Contacts sync token expired for provider=%s account=%s; "
                        "falling back to full sync",
                        provider_name,
                        account_id,
                    )
                    effective_mode = "full"
                    state.sync_cursor = None
                    contacts, next_cursor = await self._collect_full(account_id=account_id)
            else:
                contacts, next_cursor = await self._collect_full(account_id=account_id)

            result = await self._apply_changes(
                contacts=contacts,
                state=state,
                next_cursor=next_cursor,
                mode=effective_mode,
            )
        except Exception as exc:
            state.last_error = str(exc)[:300]
            await self._state_store.save(provider=provider_name, account_id=account_id, state=state)
            raise

        await self._state_store.save(provider=provider_name, account_id=account_id, state=state)
        return result

    async def _collect_full(self, *, account_id: str) -> tuple[list[CanonicalContact], str]:
        page_token: str | None = None
        contacts: list[CanonicalContact] = []
        next_cursor: str | None = None

        while True:
            batch = await self._provider.full_sync(account_id=account_id, page_token=page_token)
            contacts.extend(batch.contacts)
            if batch.next_sync_cursor is not None:
                next_cursor = batch.next_sync_cursor
            page_token = batch.next_page_token
            if page_token is None:
                break

        if next_cursor is None:
            raise ContactsSyncError("Provider full sync did not return next_sync_cursor")
        return contacts, next_cursor

    async def _collect_incremental(
        self,
        *,
        account_id: str,
        cursor: str | None,
    ) -> tuple[list[CanonicalContact], str]:
        if cursor is None:
            raise ContactsSyncError("Incremental sync requires a non-null cursor")

        page_token: str | None = None
        contacts: list[CanonicalContact] = []
        next_cursor: str | None = None

        while True:
            batch = await self._provider.incremental_sync(
                account_id=account_id,
                cursor=cursor,
                page_token=page_token,
            )
            contacts.extend(batch.contacts)
            if batch.next_sync_cursor is not None:
                next_cursor = batch.next_sync_cursor
            page_token = batch.next_page_token
            if page_token is None:
                break

        if next_cursor is None:
            raise ContactsSyncError("Provider incremental sync did not return next_sync_cursor")
        return contacts, next_cursor

    async def _apply_changes(
        self,
        *,
        contacts: list[CanonicalContact],
        state: ContactsSyncState,
        next_cursor: str,
        mode: ContactsSyncMode,
    ) -> ContactsSyncResult:
        versions = dict(state.contact_versions)
        applied = 0
        skipped = 0
        deleted = 0

        for contact in contacts:
            version = _contact_version(contact)
            previous = versions.get(contact.external_id)
            if previous == version:
                skipped += 1
                continue

            await self._apply_contact(contact)
            versions[contact.external_id] = version
            applied += 1
            if contact.deleted:
                deleted += 1

        now_iso = datetime.now(UTC).isoformat()
        state.sync_cursor = next_cursor
        state.cursor_issued_at = now_iso
        state.last_success_at = now_iso
        state.last_error = None
        state.contact_versions = versions
        if mode == "full":
            state.last_full_sync_at = now_iso
        else:
            state.last_incremental_sync_at = now_iso

        return ContactsSyncResult(
            mode=mode,
            fetched_contacts=len(contacts),
            applied_contacts=applied,
            skipped_contacts=skipped,
            deleted_contacts=deleted,
            next_sync_cursor=next_cursor,
        )


def _contact_version(contact: CanonicalContact) -> str:
    if contact.etag is not None:
        return f"etag:{contact.etag}:deleted={int(contact.deleted)}"
    raw_payload: Any = contact.raw if contact.raw is not None else contact.model_dump(mode="json")
    serialized = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"hash:{digest}:deleted={int(contact.deleted)}"
