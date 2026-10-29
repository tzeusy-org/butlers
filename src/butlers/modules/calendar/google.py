"""Calendar google implementation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from butlers.modules.calendar_response import (
    CalendarResponseError,
    CalendarResponseUncertainError,
    SelfResponseSnapshot,
    verified_response_result,
)

from .conflicts import _merge_busy_windows
from .constants import (
    DEFAULT_SYNC_WINDOW_DAYS,
    GOOGLE_CALENDAR_API_BASE_URL,
    RATE_LIMIT_BASE_BACKOFF_SECONDS,
    RATE_LIMIT_MAX_RETRIES,
    RATE_LIMIT_RETRY_STATUS_CODES,
    logger,
)
from .errors import (
    CalendarAuthError,
    CalendarRequestError,
    CalendarSyncTokenExpiredError,
    _safe_google_error_message,
)
from .google_mapping import (
    _attendee_info_list_to_google,
    _build_google_event_body,
    _build_google_event_patch_body,
    _coerce_zoneinfo,
    _google_event_to_calendar_event,
    _google_rfc3339,
    _parse_google_datetime,
)
from .models import (
    AttendeeInfo,
    BusyWindow,
    CalendarConfig,
    CalendarEvent,
    CalendarEventCreate,
    CalendarEventUpdate,
)
from .oauth import _GoogleOAuthClient, _GoogleOAuthCredentials
from .provider import CalendarProvider


class _GoogleProvider(CalendarProvider):
    """Google provider with OAuth refresh-token and authenticated request helpers."""

    def __init__(
        self,
        config: CalendarConfig,
        credentials: _GoogleOAuthCredentials,
        http_client: httpx.AsyncClient | None = None,
        pool: Any = None,
    ) -> None:
        self._config = config
        self._pool = pool
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=30.0)

        # Wire post-refresh callbacks so account health follows observed OAuth
        # outcomes from this module, not only dashboard-originated changes.
        on_token_refreshed: Callable[[], Coroutine[Any, Any, None]] | None = None
        on_token_revoked: Callable[[], Coroutine[Any, Any, None]] | None = None
        account_email = config.account
        if pool is not None:
            _email = account_email
            _pool = pool

            async def _update_last_token_refresh_at() -> None:
                try:
                    if _email:
                        await _pool.execute(
                            """
                            UPDATE public.google_accounts
                            SET last_token_refresh_at = NOW()
                            WHERE email = $1
                            """,
                            _email,
                        )
                    else:
                        await _pool.execute(
                            """
                            UPDATE public.google_accounts
                            SET last_token_refresh_at = NOW()
                            WHERE is_primary = true
                            """
                        )
                except Exception:
                    logger.debug(
                        "CalendarModule: failed to update last_token_refresh_at",
                        exc_info=True,
                    )

            _module_refresh_token = credentials.refresh_token

            async def _mark_token_revoked() -> None:
                # Guard against stale-daemon clobber: this module caches the
                # refresh token at startup. If the owner re-authorized since
                # (dashboard OAuth stored a NEW token and set status='active'),
                # an invalid_grant on OUR stale token says nothing about the
                # current credential — only mark revoked when the stored token
                # is still the one we hold (observed live: a daemon holding a
                # dead token kept flipping a freshly re-authorized account
                # back to 'revoked').
                try:
                    if _email:
                        stored = await _pool.fetchval(
                            """
                            SELECT ei.value
                            FROM public.google_accounts ga
                            JOIN public.entity_info ei ON ei.entity_id = ga.entity_id
                            WHERE ga.email = $1 AND ei.type = 'google_oauth_refresh'
                            """,
                            _email,
                        )
                    else:
                        stored = await _pool.fetchval(
                            """
                            SELECT ei.value
                            FROM public.google_accounts ga
                            JOIN public.entity_info ei ON ei.entity_id = ga.entity_id
                            WHERE ga.is_primary = true AND ei.type = 'google_oauth_refresh'
                            """
                        )
                    if stored is not None and stored != _module_refresh_token:
                        logger.info(
                            "CalendarModule: stored refresh token differs from this "
                            "module's cached token — skipping revoked-status update "
                            "(account was re-authorized; restart to pick up the new token)."
                        )
                        return
                    if _email:
                        await _pool.execute(
                            """
                            UPDATE public.google_accounts
                            SET status = 'revoked'
                            WHERE email = $1
                            """,
                            _email,
                        )
                    else:
                        await _pool.execute(
                            """
                            UPDATE public.google_accounts
                            SET status = 'revoked'
                            WHERE is_primary = true
                            """
                        )
                except Exception:
                    logger.debug(
                        "CalendarModule: failed to mark Google account revoked",
                        exc_info=True,
                    )

            on_token_refreshed = _update_last_token_refresh_at
            on_token_revoked = _mark_token_revoked

        self._oauth = _GoogleOAuthClient(
            credentials,
            self._http_client,
            on_token_refreshed,
            on_token_revoked,
        )

    @property
    def name(self) -> str:
        return "google"

    async def _request_google_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        response = await self._request_with_bearer(
            method=method,
            path=path,
            params=params,
            json_body=json_body,
            extra_headers=extra_headers,
        )

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        if response.status_code == 204:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarAuthError(
                "Google Calendar API returned invalid JSON for a successful response"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarAuthError("Google Calendar API returned an unexpected JSON payload shape")
        return payload

    async def _request_with_bearer(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        normalized_path = path if path.startswith("/") else f"/{path}"
        url = f"{GOOGLE_CALENDAR_API_BASE_URL}{normalized_path}"

        response = await self._request_once(
            method=method,
            url=url,
            params=params,
            json_body=json_body,
            extra_headers=extra_headers,
            force_refresh=False,
        )

        if response.status_code == 401:
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                extra_headers=extra_headers,
                force_refresh=True,
            )

        # Rate-limit retry: honour Retry-After header on 429, exponential backoff on 503
        # (spec section 14.2).
        retry = 0
        while (
            response.status_code in RATE_LIMIT_RETRY_STATUS_CODES and retry < RATE_LIMIT_MAX_RETRIES
        ):
            backoff = RATE_LIMIT_BASE_BACKOFF_SECONDS * (2**retry)
            # For 429 responses, respect the Retry-After header when present.
            if response.status_code == 429:
                retry_after_header = response.headers.get("Retry-After")
                if retry_after_header is not None:
                    try:
                        backoff = float(retry_after_header)
                    except ValueError:
                        pass
            logger.warning(
                "Calendar API rate-limited (status=%d), retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                backoff,
                retry + 1,
                RATE_LIMIT_MAX_RETRIES,
            )
            await asyncio.sleep(backoff)
            response = await self._request_once(
                method=method,
                url=url,
                params=params,
                json_body=json_body,
                extra_headers=extra_headers,
                force_refresh=False,
            )
            retry += 1

        return response

    async def _request_once(
        self,
        *,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        extra_headers: dict[str, str] | None,
        force_refresh: bool,
    ) -> httpx.Response:
        access_token = await self._oauth.get_access_token(force_refresh=force_refresh)
        headers: dict[str, str] = {"Authorization": f"Bearer {access_token}"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            return await self._http_client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CalendarAuthError(f"Google Calendar request failed: {exc}") from exc

    async def discover_or_create_calendar(self, name: str) -> str:
        """Find a calendar by *name* in the user's calendar list, or create one.

        Paginates through ``calendarList.list`` looking for a calendar whose
        ``summary`` matches *name* exactly.  If none is found after exhausting
        all pages, a new calendar is created via ``calendars.insert`` and its
        ``id`` is returned.
        """
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token is not None:
                params["pageToken"] = page_token

            payload = await self._request_google_json(
                "GET", "/users/me/calendarList", params=params
            )

            for entry in payload.get("items", []):
                if isinstance(entry, dict) and entry.get("summary") == name:
                    calendar_id = entry.get("id")
                    if isinstance(calendar_id, str) and calendar_id.strip():
                        logger.info("Discovered existing calendar %r (id=%s)", name, calendar_id)
                        return calendar_id

            next_token = payload.get("nextPageToken")
            if not next_token:
                break
            page_token = next_token

        # Not found — create a new calendar.
        created = await self._request_google_json("POST", "/calendars", json_body={"summary": name})
        calendar_id = created["id"]
        logger.info("Created new calendar %r (id=%s)", name, calendar_id)
        return calendar_id

    async def list_calendars(self) -> list[dict[str, Any]]:
        """Return all calendars from the authenticated user's calendar list."""
        calendars: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params: dict[str, Any] = {"maxResults": 250}
            if page_token is not None:
                params["pageToken"] = page_token
            payload = await self._request_google_json(
                "GET", "/users/me/calendarList", params=params
            )
            for entry in payload.get("items", []):
                if isinstance(entry, dict) and entry.get("id"):
                    calendars.append(entry)
            next_token = payload.get("nextPageToken")
            if not next_token:
                break
            page_token = next_token
        return calendars

    async def get_calendar_summary(self, calendar_id: str) -> str | None:
        """Fetch the human-readable summary for a Google Calendar by its ID."""
        normalized = quote(calendar_id, safe="")
        try:
            payload = await self._request_google_json("GET", f"/users/me/calendarList/{normalized}")
            summary = payload.get("summaryOverride") or payload.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
        except Exception:
            logger.debug("Failed to fetch calendar summary for '%s'", calendar_id)
        return None

    async def list_events(
        self,
        *,
        calendar_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 50,
    ) -> list[CalendarEvent]:
        if limit < 1:
            raise ValueError("limit must be at least 1")

        normalized_calendar_id = quote(calendar_id, safe="")
        params: dict[str, Any] = {
            "singleEvents": True,
            "showDeleted": False,
            "orderBy": "startTime",
            "maxResults": min(limit, 250),
        }
        if start_at is not None:
            params["timeMin"] = _google_rfc3339(start_at)
        if end_at is not None:
            params["timeMax"] = _google_rfc3339(end_at)

        payload = await self._request_google_json(
            "GET",
            f"/calendars/{normalized_calendar_id}/events",
            params=params,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise CalendarAuthError("Google Calendar list_events response missing items array")

        events: list[CalendarEvent] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            event = _google_event_to_calendar_event(item, fallback_timezone=self._config.timezone)
            if event is not None:
                events.append(event)
        return events

    async def get_event(self, *, calendar_id: str, event_id: str) -> CalendarEvent | None:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response = await self._request_with_bearer(
            method="GET",
            path=f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
        )

        if response.status_code == 404:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise CalendarAuthError(
                "Google Calendar API returned invalid JSON for get_event"
            ) from exc

        if not isinstance(payload, dict):
            raise CalendarAuthError("Google Calendar API returned an unexpected get_event payload")
        return _google_event_to_calendar_event(payload, fallback_timezone=self._config.timezone)

    async def get_response_calendar(self, *, calendar_id: str) -> dict[str, Any]:
        return await self._request_google_json(
            "GET", f"/users/me/calendarList/{quote(calendar_id, safe='')}"
        )

    async def get_response_event(self, *, calendar_id: str, event_id: str) -> dict[str, Any]:
        """Use an ordinary read without flattening participant provenance."""
        return await self._request_google_json(
            "GET", f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        )

    async def prepare_response_write(self) -> str:
        return await self._oauth.get_access_token()

    async def respond_to_invitation(
        self,
        *,
        calendar_id: str,
        before: SelfResponseSnapshot,
        response_status: str,
        send_updates: str,
        access_token: str,
    ) -> dict[str, Any]:
        """Perform the dedicated participant PATCH, never the generic retry path.

        The owning command checks and commits its one-attempt fence first.
        Token readiness has already completed. Provider errors are closed
        categories: request URLs, credentials and response bodies cannot enter
        the approval executor's exception text.
        """
        if response_status not in {"needsAction", "accepted", "declined", "tentative"}:
            raise CalendarResponseError("response_status_invalid")
        if send_updates not in {"none", "all", "externalOnly"}:
            raise CalendarResponseError("response_notifications_invalid")
        path = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(before.event_id, safe='')}"
        try:
            # httpx's ordinary transport has retries=0. In production this is
            # the module-owned client; injected transports are controlled tests.
            response = await self._http_client.request(
                "PATCH",
                f"{GOOGLE_CALENDAR_API_BASE_URL}{path}",
                params={"sendUpdates": send_updates},
                headers={"Authorization": f"Bearer {access_token}", "If-Match": before.etag},
                json={
                    "attendeesOmitted": True,
                    "attendees": [{"email": before.self_email, "responseStatus": response_status}],
                },
            )
        except httpx.HTTPError:
            raise CalendarResponseUncertainError("response_transport_uncertain") from None
        if 400 <= response.status_code < 500 and response.status_code != 408:
            raise CalendarRequestError(
                status_code=response.status_code, message="response_provider_rejected"
            )
        if response.status_code != 200:
            raise CalendarResponseUncertainError("response_provider_uncertain")
        try:
            payload = response.json()
        except ValueError:
            raise CalendarResponseUncertainError("response_result_unverified") from None
        verified_response_result(payload, before=before, response_status=response_status)
        return payload

    async def create_event(
        self,
        *,
        calendar_id: str,
        payload: CalendarEventCreate,
    ) -> CalendarEvent:
        normalized_calendar_id = quote(calendar_id, safe="")
        body = _build_google_event_body(payload)
        response_payload = await self._request_google_json(
            "POST",
            f"/calendars/{normalized_calendar_id}/events",
            json_body=body,
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=payload.timezone or self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after create",
            )
        return event

    async def update_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        patch: CalendarEventUpdate,
    ) -> CalendarEvent:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")

        # When only the timezone changes (no time boundaries supplied), we need
        # the existing start/end datetimes to re-emit them with the new timezone.
        # Fetch the current event to obtain existing time boundaries (and the etag
        # when none was supplied by the caller) for optimistic concurrency.
        existing_start_at: datetime | None = None
        existing_end_at: datetime | None = None
        existing_timezone: str | None = None
        existing_all_day: bool | None = None
        fetched_etag: str | None = None

        needs_existing_times = (
            patch.all_day is not None and (patch.start_at is None or patch.end_at is None)
        ) or (patch.timezone is not None and patch.start_at is None and patch.end_at is None)
        if needs_existing_times:
            # Fetch the current event to get both the existing boundaries and
            # (if the caller did not supply one) the etag for optimistic
            # concurrency.  Always raise 404 consistently if the event is gone.
            existing = await self.get_event(
                calendar_id=calendar_id,
                event_id=normalized_event_id,
            )
            if existing is None:
                raise CalendarRequestError(
                    status_code=404,
                    message=f"Event '{normalized_event_id}' not found",
                )
            existing_start_at = existing.start_at
            existing_end_at = existing.end_at
            existing_timezone = existing.timezone
            existing_all_day = existing.all_day
            fetched_etag = existing.etag

        body = _build_google_event_patch_body(
            patch,
            existing_start_at=existing_start_at,
            existing_end_at=existing_end_at,
            existing_timezone=existing_timezone,
            existing_all_day=existing_all_day,
        )

        # Build extra headers for optimistic concurrency using the etag.
        # Prefer the caller-supplied etag; fall back to the one fetched above.
        resolved_etag = patch.etag or fetched_etag
        extra_headers: dict[str, str] | None = None
        if resolved_etag is not None:
            extra_headers = {"If-Match": resolved_etag}

        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            json_body=body,
            extra_headers=extra_headers,
        )

        fallback_tz = patch.timezone or self._config.timezone
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=fallback_tz,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after update",
            )
        return event

    async def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        send_updates: str | None = None,
    ) -> None:
        """Delete a Google Calendar event.

        Sends a DELETE request to the Google Calendar API.  A 404 response is
        treated as success (the event was already deleted).  For events with
        attendees, pass ``send_updates="all"`` to send cancellation
        notifications; the default (``None`` / ``"none"``) suppresses emails.

        For recurring events, v1 always operates on the full series: the
        ``event_id`` must be the base recurring-event ID, not an instance ID.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")

        params: dict[str, Any] | None = None
        if send_updates is not None:
            normalized_send_updates = send_updates.strip()
            if normalized_send_updates:
                params = {"sendUpdates": normalized_send_updates}

        response = await self._request_with_bearer(
            method="DELETE",
            path=f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params=params,
        )

        # 404 means the event was already deleted — treat as success.
        if response.status_code == 404:
            logger.debug(
                "delete_event: event '%s' not found (already deleted); treating as success",
                normalized_event_id,
            )
            return

        if response.status_code < 200 or response.status_code >= 300:
            raise CalendarRequestError(
                status_code=response.status_code,
                message=_safe_google_error_message(response),
            )

    async def get_recurrence(self, *, calendar_id: str, event_id: str) -> list[str]:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        payload = await self._request_google_json(
            "GET",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
        )
        recurrence = payload.get("recurrence")
        if not isinstance(recurrence, list):
            return []
        return [entry for entry in recurrence if isinstance(entry, str) and entry.strip()]

    async def set_recurrence(
        self,
        *,
        calendar_id: str,
        event_id: str,
        recurrence: list[str],
        send_updates: str | None = None,
    ) -> CalendarEvent:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        params: dict[str, Any] | None = None
        if send_updates is not None and send_updates.strip():
            params = {"sendUpdates": send_updates.strip()}
        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params=params,
            json_body={"recurrence": list(recurrence)},
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after recurrence update",
            )
        return event

    async def add_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        optional: bool = False,
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Add attendees to an event, deduplicating by email (case-insensitive).

        Fetches the current event, merges the new attendees with existing ones
        (preserving all existing attendee fields), then PATCHes the event.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        emails_to_add = [e.strip() for e in attendees if e.strip()]
        if not emails_to_add:
            raise ValueError("attendees must contain at least one non-empty email address")

        existing = await self.get_event(calendar_id=calendar_id, event_id=normalized_event_id)
        if existing is None:
            raise CalendarRequestError(
                status_code=404,
                message=f"Event '{normalized_event_id}' not found",
            )

        # Deduplicate by email (case-insensitive), preserving existing attendees.
        existing_emails_lower = {a.email.lower() for a in existing.attendees}
        merged_attendees = list(existing.attendees)
        for email in emails_to_add:
            if email.lower() not in existing_emails_lower:
                merged_attendees.append(
                    AttendeeInfo(
                        email=email,
                        optional=optional,
                    )
                )
                existing_emails_lower.add(email.lower())

        google_attendees = _attendee_info_list_to_google(merged_attendees)
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params={"sendUpdates": send_updates},
            json_body={"attendees": google_attendees},
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after add_attendees",
            )
        return event

    async def remove_attendees(
        self,
        *,
        calendar_id: str,
        event_id: str,
        attendees: list[str],
        send_updates: str = "none",
    ) -> CalendarEvent:
        """Remove attendees from an event by email (case-insensitive).

        Fetches the current event, filters out the specified email addresses,
        then PATCHes the event.
        """
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")

        emails_to_remove_lower = {e.strip().lower() for e in attendees if e.strip()}
        if not emails_to_remove_lower:
            raise ValueError("attendees must contain at least one non-empty email address")

        existing = await self.get_event(calendar_id=calendar_id, event_id=normalized_event_id)
        if existing is None:
            raise CalendarRequestError(
                status_code=404,
                message=f"Event '{normalized_event_id}' not found",
            )

        remaining_attendees = [
            a for a in existing.attendees if a.email.lower() not in emails_to_remove_lower
        ]

        google_attendees = _attendee_info_list_to_google(remaining_attendees)
        normalized_calendar_id = quote(calendar_id, safe="")
        encoded_event_id = quote(normalized_event_id, safe="")
        response_payload = await self._request_google_json(
            "PATCH",
            f"/calendars/{normalized_calendar_id}/events/{encoded_event_id}",
            params={"sendUpdates": send_updates},
            json_body={"attendees": google_attendees},
        )
        event = _google_event_to_calendar_event(
            response_payload,
            fallback_timezone=self._config.timezone,
        )
        if event is None:
            raise CalendarRequestError(
                status_code=200,
                message="Google Calendar returned a cancelled event after remove_attendees",
            )
        return event

    async def get_free_busy(
        self,
        *,
        calendar_ids: list[str],
        start_at: datetime,
        end_at: datetime,
        timezone: str | None = None,
    ) -> list[BusyWindow]:
        if not calendar_ids:
            return []
        if end_at <= start_at:
            raise ValueError("end_at must be after start_at")

        timezone_str = timezone or self._config.timezone
        payload = await self._request_google_json(
            "POST",
            "/freeBusy",
            json_body={
                "timeMin": _google_rfc3339(start_at),
                "timeMax": _google_rfc3339(end_at),
                "timeZone": timezone_str,
                "items": [{"id": calendar_id} for calendar_id in calendar_ids],
            },
        )
        calendars_payload = payload.get("calendars")
        if not isinstance(calendars_payload, dict):
            raise CalendarAuthError("Google Calendar freeBusy response missing calendars object")

        raw_windows: list[tuple[datetime, datetime]] = []
        for calendar_id in calendar_ids:
            calendar_payload = calendars_payload.get(calendar_id)
            if not isinstance(calendar_payload, dict):
                # Single-calendar callers tolerate a differently-keyed sole entry
                # (preserves the pre-refactor find_conflicts fallback).
                if len(calendar_ids) == 1 and len(calendars_payload) == 1:
                    calendar_payload = next(iter(calendars_payload.values()))
                else:
                    raise CalendarAuthError(
                        "Google Calendar freeBusy response missing calendar entry for requested id"
                    )
            if not isinstance(calendar_payload, dict):
                raise CalendarAuthError(
                    "Google Calendar freeBusy response calendar entry is invalid"
                )

            busy_payload = calendar_payload.get("busy")
            if not isinstance(busy_payload, list):
                raise CalendarAuthError("Google Calendar freeBusy response missing busy array")

            for window in busy_payload:
                if not isinstance(window, dict):
                    continue
                start_raw = window.get("start")
                end_raw = window.get("end")
                if not isinstance(start_raw, str) or not isinstance(end_raw, str):
                    raise CalendarAuthError(
                        "Google Calendar freeBusy busy windows must include start/end"
                    )
                window_start = _parse_google_datetime(start_raw)
                window_end = _parse_google_datetime(end_raw)
                if window_end <= window_start:
                    continue
                raw_windows.append((window_start, window_end))

        return _merge_busy_windows(raw_windows)

    async def find_conflicts(
        self,
        *,
        calendar_id: str,
        candidate: CalendarEventCreate,
    ) -> list[CalendarEvent]:
        # Coerce date/datetime boundaries to datetime for freeBusy API.
        # Do this before the guard comparison to avoid TypeError when one boundary
        # is a date and the other is a datetime (Python 3 does not allow cross-type
        # comparisons between date and datetime).
        timezone_str = candidate.timezone or self._config.timezone
        tz = _coerce_zoneinfo(timezone_str)
        start_at = candidate.start_at
        end_at = candidate.end_at
        if isinstance(start_at, date) and not isinstance(start_at, datetime):
            start_at = datetime(start_at.year, start_at.month, start_at.day, tzinfo=tz)
        if isinstance(end_at, date) and not isinstance(end_at, datetime):
            end_at = datetime(end_at.year, end_at.month, end_at.day, tzinfo=tz)

        if end_at <= start_at:
            raise ValueError("candidate.end_at must be after candidate.start_at")

        # Free/busy lives in exactly one place now: delegate to get_free_busy for
        # the single-calendar candidate window and wrap the merged busy windows
        # back into the synthetic ``(busy)`` events this method has always returned.
        windows = await self.get_free_busy(
            calendar_ids=[calendar_id],
            start_at=start_at,
            end_at=end_at,
            timezone=timezone_str,
        )
        return [
            CalendarEvent(
                event_id=f"busy-{index + 1}",
                title="(busy)",
                start_at=window.start_at,
                end_at=window.end_at,
                timezone=timezone_str,
            )
            for index, window in enumerate(windows)
        ]

    async def sync_incremental(
        self,
        *,
        calendar_id: str,
        sync_token: str | None,
        full_sync_window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    ) -> tuple[list[CalendarEvent], list[str], str]:
        """Fetch incremental changes using Google's syncToken / nextSyncToken flow.

        Performs a full sync when ``sync_token`` is ``None`` (first run or
        after token invalidation).  Returns updated events, cancelled IDs, and
        the token to use on the next call.

        Raises:
            CalendarSyncTokenExpiredError: When Google returns 410 Gone,
                indicating the sync token is no longer valid.
        """
        normalized_calendar_id = quote(calendar_id, safe="")
        params: dict[str, Any] = {
            "showDeleted": True,
            "singleEvents": False,
        }

        if sync_token is not None:
            params["syncToken"] = sync_token
        else:
            # Full sync: restrict to a time window to avoid fetching all history.
            now = datetime.now(UTC)
            window_start = now - timedelta(days=full_sync_window_days)
            params["timeMin"] = _google_rfc3339(window_start)

        updated_events: list[CalendarEvent] = []
        cancelled_event_ids: list[str] = []
        next_page_token: str | None = None
        next_sync_token: str | None = None

        while True:
            if next_page_token is not None:
                params["pageToken"] = next_page_token
            elif "pageToken" in params:
                del params["pageToken"]

            response = await self._request_with_bearer(
                method="GET",
                path=f"/calendars/{normalized_calendar_id}/events",
                params=params,
            )

            # 410 Gone means the sync token is expired; caller must do full re-sync.
            if response.status_code == 410:
                raise CalendarSyncTokenExpiredError(
                    f"Sync token expired for calendar '{calendar_id}'; full re-sync required"
                )

            if response.status_code < 200 or response.status_code >= 300:
                raise CalendarRequestError(
                    status_code=response.status_code,
                    message=_safe_google_error_message(response),
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise CalendarAuthError(
                    "Google Calendar sync response returned invalid JSON"
                ) from exc

            if not isinstance(payload, dict):
                raise CalendarAuthError(
                    "Google Calendar sync response has unexpected payload shape"
                )

            items = payload.get("items")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_status = item.get("status")
                    event_id_raw = item.get("id")
                    if not isinstance(event_id_raw, str) or not event_id_raw.strip():
                        continue
                    if isinstance(item_status, str) and item_status.lower() == "cancelled":
                        cancelled_event_ids.append(event_id_raw.strip())
                    else:
                        event = _google_event_to_calendar_event(
                            item,
                            fallback_timezone=self._config.timezone,
                        )
                        if event is not None:
                            updated_events.append(event)

            next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
            candidate_sync_token = (
                payload.get("nextSyncToken") if isinstance(payload, dict) else None
            )
            if isinstance(candidate_sync_token, str) and candidate_sync_token.strip():
                next_sync_token = candidate_sync_token.strip()

            if next_page_token is None:
                break

        if next_sync_token is None:
            raise CalendarAuthError(
                f"Google Calendar sync response for '{calendar_id}' did not return nextSyncToken"
            )

        return updated_events, cancelled_event_ids, next_sync_token

    async def shutdown(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()
