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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

logger = logging.getLogger(__name__)

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_PEOPLE_API_CONNECTIONS_URL = "https://people.googleapis.com/v1/people/me/connections"
GOOGLE_CONTACT_GROUPS_URL = "https://people.googleapis.com/v1/contactGroups"

DEFAULT_GOOGLE_PERSON_FIELDS = (
    "names,emailAddresses,phoneNumbers,addresses,birthdays,events,organizations,"
    "biographies,urls,memberships,photos,userDefined,metadata"
)
DEFAULT_GOOGLE_PAGE_SIZE = 500
SYNC_STATE_KEY_PREFIX = "contacts::sync::"
DEFAULT_INCREMENTAL_SYNC_INTERVAL_MINUTES = 15
DEFAULT_FORCED_FULL_SYNC_DAYS = 6

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


class ContactAddress(BaseModel):
    """Canonical postal address."""

    model_config = ConfigDict(extra="forbid")

    street: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    label: str | None = None
    primary: bool = False


class ContactOrganization(BaseModel):
    """Canonical organization/employer entry."""

    model_config = ConfigDict(extra="forbid")

    company: str | None = None
    title: str | None = None
    department: str | None = None


class ContactDate(BaseModel):
    """Canonical date entry (birthday, anniversary, etc.)."""

    model_config = ConfigDict(extra="forbid")

    year: int | None = None
    month: int | None = None
    day: int | None = None
    label: str | None = None


class ContactUrl(BaseModel):
    """Canonical URL entry."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    label: str | None = None


class ContactUsername(BaseModel):
    """Canonical username/handle entry for a specific service."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    service: str | None = None


class ContactPhoto(BaseModel):
    """Canonical photo entry."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    primary: bool = False


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
    addresses: list[ContactAddress] = Field(default_factory=list)
    organizations: list[ContactOrganization] = Field(default_factory=list)
    birthdays: list[ContactDate] = Field(default_factory=list)
    anniversaries: list[ContactDate] = Field(default_factory=list)
    urls: list[ContactUrl] = Field(default_factory=list)
    usernames: list[ContactUsername] = Field(default_factory=list)
    photos: list[ContactPhoto] = Field(default_factory=list)
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


class CanonicalGroup(BaseModel):
    """Provider-neutral canonical contact group/label shape."""

    model_config = ConfigDict(extra="forbid")

    external_id: str = Field(min_length=1)
    name: str
    group_type: str | None = None
    member_count: int | None = None

    @field_validator("external_id")
    @classmethod
    def _normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_id must be a non-empty string")
        return normalized


class GroupBatch(BaseModel):
    """One provider group-list page."""

    model_config = ConfigDict(extra="forbid")

    groups: list[CanonicalGroup] = Field(default_factory=list)
    next_page_token: str | None = None

    @field_validator("next_page_token")
    @classmethod
    def _normalize_page_token(cls, value: str | None) -> str | None:
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
    async def validate_credentials(self) -> None:
        """Verify provider credentials are valid.

        Raises:
            ContactsTokenRefreshError: if credentials cannot be validated.
        """
        ...

    @abc.abstractmethod
    async def list_groups(
        self,
        *,
        account_id: str,
        page_token: str | None = None,
    ) -> GroupBatch:
        """Fetch one page of contact groups/labels from the provider."""
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

    async def validate_credentials(self) -> None:
        """Verify Google OAuth credentials by refreshing the access token
        and making a lightweight People API call.

        Raises:
            ContactsTokenRefreshError: if the token refresh fails.
            ContactsRequestError: if the lightweight People API call fails.
        """
        # Force a token refresh to confirm credentials are valid
        await self._oauth.get_access_token(force_refresh=True)
        # Make a lightweight API call to verify API access
        params: dict[str, Any] = {
            "personFields": "names",
            "pageSize": 1,
        }
        await self._request_connections(params)

    async def list_groups(
        self,
        *,
        account_id: str,
        page_token: str | None = None,
    ) -> GroupBatch:
        """Fetch one page of contact groups from the Google People API.

        Calls ``contactGroups.list`` with optional pagination.
        """
        del account_id
        params: dict[str, Any] = {"pageSize": 1000}
        if page_token is not None:
            params["pageToken"] = page_token
        payload = await self._request_contact_groups(params)
        return _parse_google_group_batch(payload)

    async def shutdown(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _authenticated_get_json(
        self,
        url: str,
        params: dict[str, Any],
        *,
        api_label: str = "Google",
    ) -> dict[str, Any]:
        """Make an authenticated GET request with automatic 401 token-refresh retry.

        Raises ContactsRequestError on non-2xx responses or invalid payloads.
        Does not handle endpoint-specific status codes (e.g. 410 sync-token expiry);
        callers requiring special treatment of specific codes should use this method
        as a post-auth building block or inline their own logic.
        """
        token = await self._oauth.get_access_token()
        response = await self._http_client.get(
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

        if response.status_code == 401:
            token = await self._oauth.get_access_token(force_refresh=True)
            response = await self._http_client.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )

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
                message=f"Invalid JSON payload from {api_label} API",
            ) from exc

        if not isinstance(payload, dict):
            raise ContactsRequestError(
                status_code=response.status_code,
                message=f"{api_label} API payload must be a JSON object",
            )
        return payload

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

    async def _request_contact_groups(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self._authenticated_get_json(
            GOOGLE_CONTACT_GROUPS_URL,
            params,
            api_label="Google contactGroups",
        )


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


def _parse_google_group_batch(payload: dict[str, Any]) -> GroupBatch:
    raw_groups = payload.get("contactGroups")
    groups: list[CanonicalGroup] = []
    if isinstance(raw_groups, list):
        for item in raw_groups:
            if not isinstance(item, dict):
                continue
            resource_name = _as_non_empty_string(item.get("resourceName"))
            if resource_name is None:
                continue
            name = _as_non_empty_string(item.get("name")) or resource_name
            group_type = _as_non_empty_string(item.get("groupType"))
            member_count_raw = item.get("memberCount")
            is_numeric = isinstance(member_count_raw, int | float) and not isinstance(
                member_count_raw, bool
            )
            member_count = int(member_count_raw) if is_numeric else None
            groups.append(
                CanonicalGroup(
                    external_id=resource_name,
                    name=name,
                    group_type=group_type,
                    member_count=member_count,
                )
            )

    return GroupBatch(
        groups=groups,
        next_page_token=_as_non_empty_string(payload.get("nextPageToken")),
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

    addresses = _parse_addresses(payload.get("addresses"))
    organizations = _parse_organizations(payload.get("organizations"))
    birthdays, anniversaries = _parse_birthdays_and_events(
        payload.get("birthdays"), payload.get("events")
    )
    urls = _parse_urls(payload.get("urls"))
    photos = _parse_photos(payload.get("photos"))

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
        addresses=addresses,
        organizations=organizations,
        birthdays=birthdays,
        anniversaries=anniversaries,
        urls=urls,
        photos=photos,
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


def _parse_addresses(raw: Any) -> list[ContactAddress]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactAddress] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        street = _as_non_empty_string(item.get("streetAddress"))
        city = _as_non_empty_string(item.get("city"))
        region = _as_non_empty_string(item.get("region"))
        postal_code = _as_non_empty_string(item.get("postalCode"))
        country = _as_non_empty_string(item.get("country"))
        label = _as_non_empty_string(item.get("formattedType"))
        if not any((street, city, region, postal_code, country, label)):
            continue
        metadata = item.get("metadata")
        primary = bool(metadata.get("primary")) if isinstance(metadata, dict) else False
        parsed.append(
            ContactAddress(
                street=street,
                city=city,
                region=region,
                postal_code=postal_code,
                country=country,
                label=label,
                primary=primary,
            )
        )
    return parsed


def _parse_organizations(raw: Any) -> list[ContactOrganization]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactOrganization] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        company = _as_non_empty_string(item.get("name"))
        title = _as_non_empty_string(item.get("title"))
        department = _as_non_empty_string(item.get("department"))
        if company is None and title is None and department is None:
            continue
        parsed.append(
            ContactOrganization(
                company=company,
                title=title,
                department=department,
            )
        )
    return parsed


def _parse_date_entry(item: dict[str, Any], label: str | None) -> ContactDate | None:
    date_obj = item.get("date")
    if not isinstance(date_obj, dict):
        return None
    year_raw = date_obj.get("year")
    month_raw = date_obj.get("month")
    day_raw = date_obj.get("day")
    year = year_raw if isinstance(year_raw, int) and year_raw != 0 else None
    month = month_raw if isinstance(month_raw, int) and month_raw != 0 else None
    day = day_raw if isinstance(day_raw, int) and day_raw != 0 else None
    if year is None and month is None and day is None:
        return None
    return ContactDate(year=year, month=month, day=day, label=label)


def _parse_birthdays_and_events(
    birthdays_raw: Any, events_raw: Any
) -> tuple[list[ContactDate], list[ContactDate]]:
    birthdays: list[ContactDate] = []
    if isinstance(birthdays_raw, list):
        for item in birthdays_raw:
            if not isinstance(item, dict):
                continue
            entry = _parse_date_entry(item, label="birthday")
            if entry is not None:
                birthdays.append(entry)

    anniversaries: list[ContactDate] = []
    if isinstance(events_raw, list):
        for item in events_raw:
            if not isinstance(item, dict):
                continue
            label = _as_non_empty_string(item.get("formattedType"))
            entry = _parse_date_entry(item, label=label)
            if entry is not None:
                anniversaries.append(entry)

    return birthdays, anniversaries


def _parse_urls(raw: Any) -> list[ContactUrl]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactUrl] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        value = _as_non_empty_string(item.get("value"))
        if value is None:
            continue
        parsed.append(
            ContactUrl(
                value=value,
                label=_as_non_empty_string(item.get("formattedType")),
            )
        )
    return parsed


def _parse_photos(raw: Any) -> list[ContactPhoto]:
    if not isinstance(raw, list):
        return []
    parsed: list[ContactPhoto] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = _as_non_empty_string(item.get("url"))
        if url is None:
            continue
        metadata = item.get("metadata")
        primary = bool(metadata.get("primary")) if isinstance(metadata, dict) else False
        parsed.append(ContactPhoto(url=url, primary=primary))
    return parsed


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
    """Table-backed sync state repository using ``contacts_sync_state``.

    Reads and writes sync state via asyncpg directly to the
    ``contacts_sync_state`` table created by the contacts module migration.
    The ``key()`` helper is retained for backward-compatibility but is no
    longer used for persistence.
    """

    def __init__(self, pool: Any, *, key_prefix: str = SYNC_STATE_KEY_PREFIX) -> None:
        self._pool = pool
        self._key_prefix = key_prefix

    def key(self, *, provider: str, account_id: str) -> str:
        """Return the legacy KV key for this (provider, account_id) pair.

        Retained for backward-compatibility.  Persistence now uses the
        ``contacts_sync_state`` relational table instead of the KV store.
        """
        provider_key = provider.strip().lower()
        account_key = account_id.strip()
        return f"{self._key_prefix}{provider_key}::{account_key}"

    async def load(self, *, provider: str, account_id: str) -> ContactsSyncState:
        """Load sync state from the ``contacts_sync_state`` table.

        Returns an empty :class:`ContactsSyncState` if no row exists yet.
        """
        row = await self._pool.fetchrow(
            """
            SELECT
                sync_cursor,
                cursor_issued_at,
                last_full_sync_at,
                last_incremental_sync_at,
                last_success_at,
                last_error,
                contact_versions
            FROM contacts_sync_state
            WHERE provider = $1 AND account_id = $2
            """,
            provider.strip().lower(),
            account_id.strip(),
        )
        if row is None:
            return ContactsSyncState()
        return ContactsSyncState(
            sync_cursor=row["sync_cursor"],
            cursor_issued_at=(
                row["cursor_issued_at"].isoformat() if row["cursor_issued_at"] else None
            ),
            last_full_sync_at=(
                row["last_full_sync_at"].isoformat() if row["last_full_sync_at"] else None
            ),
            last_incremental_sync_at=(
                row["last_incremental_sync_at"].isoformat()
                if row["last_incremental_sync_at"]
                else None
            ),
            last_success_at=(
                row["last_success_at"].isoformat() if row["last_success_at"] else None
            ),
            last_error=row["last_error"],
            contact_versions=dict(row["contact_versions"]) if row["contact_versions"] else {},
        )

    async def save(
        self,
        *,
        provider: str,
        account_id: str,
        state: ContactsSyncState,
    ) -> None:
        """Upsert sync state into the ``contacts_sync_state`` table."""
        provider_key = provider.strip().lower()
        account_key = account_id.strip()
        await self._pool.execute(
            """
            INSERT INTO contacts_sync_state (
                provider,
                account_id,
                sync_cursor,
                cursor_issued_at,
                last_full_sync_at,
                last_incremental_sync_at,
                last_success_at,
                last_error,
                contact_versions
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (provider, account_id) DO UPDATE SET
                sync_cursor = EXCLUDED.sync_cursor,
                cursor_issued_at = EXCLUDED.cursor_issued_at,
                last_full_sync_at = EXCLUDED.last_full_sync_at,
                last_incremental_sync_at = EXCLUDED.last_incremental_sync_at,
                last_success_at = EXCLUDED.last_success_at,
                last_error = EXCLUDED.last_error,
                contact_versions = EXCLUDED.contact_versions
            """,
            provider_key,
            account_key,
            state.sync_cursor,
            _parse_iso_timestamp(state.cursor_issued_at),
            _parse_iso_timestamp(state.last_full_sync_at),
            _parse_iso_timestamp(state.last_incremental_sync_at),
            _parse_iso_timestamp(state.last_success_at),
            state.last_error,
            json.dumps(state.contact_versions),
        )


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime.

    Returns None if the value is None or empty.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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


def _parse_iso_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class ContactsSyncRuntime:
    """Module-internal polling runtime for recurring contacts sync.

    Contract:
    - Run one sync immediately when started.
    - Continue incremental polling on a fixed cadence (default 15 minutes).
    - Force periodic full sync before Google token-expiry risk (default 6 days).
    - Allow immediate out-of-band trigger requests.
    """

    def __init__(
        self,
        *,
        sync_engine: ContactsSyncEngine,
        state_store: ContactsSyncStateRepository,
        provider_name: str,
        account_id: str,
        incremental_interval: timedelta | None = None,
        forced_full_interval: timedelta | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._sync_engine = sync_engine
        self._state_store = state_store
        self._provider_name = provider_name.strip().lower()
        self._account_id = account_id.strip()
        self._incremental_interval = incremental_interval or timedelta(
            minutes=DEFAULT_INCREMENTAL_SYNC_INTERVAL_MINUTES
        )
        self._forced_full_interval = forced_full_interval or timedelta(
            days=DEFAULT_FORCED_FULL_SYNC_DAYS
        )
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._force_sync_event = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start background polling loop."""
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop(), name="contacts-sync-poller")

    async def stop(self) -> None:
        """Stop background polling loop and wait for shutdown."""
        self._stopping.set()
        self._force_sync_event.set()
        if self._task is None:
            return
        if not self._task.done():
            self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def trigger_immediate_sync(self) -> None:
        """Wake the poller to run sync immediately."""
        self._force_sync_event.set()

    async def run_sync_cycle(self) -> ContactsSyncResult:
        """Run one sync cycle with mode selected from persisted sync state."""
        mode = await self._next_mode()
        return await self._sync_engine.sync(account_id=self._account_id, mode=mode)

    async def _run_loop(self) -> None:
        interval_seconds = max(self._incremental_interval.total_seconds(), 1.0)

        while not self._stopping.is_set():
            try:
                await self.run_sync_cycle()
            except Exception as exc:  # pragma: no cover - caller observes via logs/state
                logger.warning("Contacts sync poll cycle failed: %s", exc, exc_info=True)

            if self._stopping.is_set():
                break

            try:
                await asyncio.wait_for(self._force_sync_event.wait(), timeout=interval_seconds)
                self._force_sync_event.clear()
            except TimeoutError:
                continue

    async def _next_mode(self) -> ContactsSyncMode:
        state = await self._state_store.load(
            provider=self._provider_name,
            account_id=self._account_id,
        )

        if state.sync_cursor is None:
            return "full"

        reference_time = _parse_iso_utc(state.last_full_sync_at) or _parse_iso_utc(
            state.cursor_issued_at
        )
        if reference_time is None:
            return "full"

        if self._now_utc() - reference_time >= self._forced_full_interval:
            return "full"

        return "incremental"

    def _now_utc(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
