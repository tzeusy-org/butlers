"""Calendar module public API and lifecycle.

Types/helpers below are direct imports of their single defining objects.
Behavior mixins share this module instance; tool registrars run only here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from butlers.calendar_action_result import reconstruct_action_result as reconstruct_action_result
from butlers.core.permissions import CALENDAR_WRITE_PERMISSION
from butlers.core.tool_catalog import RecipientParameters
from butlers.modules.base import Module, ToolMeta, group_enabled, tool_registrar
from butlers.modules.calendar_response import CalendarResponseCoordinator, CalendarResponseError
from butlers.modules.calendar_response_fence import RESPONSE_FENCE_OWNERS, CalendarResponseFence

from .butler_events import ButlerEventsMixin, register_butler_event_tools
from .conflicts import _PART_OF_DAY_BOUNDS as _PART_OF_DAY_BOUNDS
from .conflicts import _SLOT_WEEKDAY_CODES as _SLOT_WEEKDAY_CODES
from .conflicts import ConflictsMixin
from .conflicts import _compute_free_slots as _compute_free_slots
from .conflicts import _merge_busy_windows as _merge_busy_windows
from .conflicts import _parse_slot_constraints as _parse_slot_constraints
from .conflicts import _slot_matches_constraints as _slot_matches_constraints
from .conflicts import _subtract_busy as _subtract_busy
from .constants import _CALENDAR_DISCOVERY_NAME as _CALENDAR_DISCOVERY_NAME
from .constants import _CALENDAR_EVENT_DEFAULT_ACTION as _CALENDAR_EVENT_DEFAULT_ACTION
from .constants import _CREDENTIAL_KEY_CALENDAR_ID as _CREDENTIAL_KEY_CALENDAR_ID
from .constants import (
    _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID as _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID,
)
from .constants import _INVALID_PROBE_CALENDAR_IDS as _INVALID_PROBE_CALENDAR_IDS
from .constants import _OBSOLETE_INTERNAL_SOURCE_KEYS as _OBSOLETE_INTERNAL_SOURCE_KEYS
from .constants import _SUGGESTION_SEARCH_HORIZON as _SUGGESTION_SEARCH_HORIZON
from .constants import BUTLER_EVENT_SOURCE_REMINDER as BUTLER_EVENT_SOURCE_REMINDER
from .constants import BUTLER_EVENT_SOURCE_SCHEDULED as BUTLER_EVENT_SOURCE_SCHEDULED
from .constants import BUTLER_EVENT_TITLE_PREFIX as BUTLER_EVENT_TITLE_PREFIX
from .constants import BUTLER_GENERATED_PRIVATE_KEY as BUTLER_GENERATED_PRIVATE_KEY
from .constants import BUTLER_NAME_PRIVATE_KEY as BUTLER_NAME_PRIVATE_KEY
from .constants import CALENDAR_ROLE_BUTLERS as CALENDAR_ROLE_BUTLERS
from .constants import CALENDAR_ROLE_DEFAULT_TARGET as CALENDAR_ROLE_DEFAULT_TARGET
from .constants import DEFAULT_BUTLER_NAME as DEFAULT_BUTLER_NAME
from .constants import DEFAULT_CONFLICT_SUGGESTION_COUNT as DEFAULT_CONFLICT_SUGGESTION_COUNT
from .constants import (
    DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES as DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES,
)
from .constants import (
    DEFAULT_SCHEDULED_TASK_DURATION_MINUTES as DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
)
from .constants import DEFAULT_SYNC_INTERVAL_MINUTES as DEFAULT_SYNC_INTERVAL_MINUTES
from .constants import DEFAULT_SYNC_WINDOW_DAYS as DEFAULT_SYNC_WINDOW_DAYS
from .constants import FORCE_SYNC_ACTION_TYPE as FORCE_SYNC_ACTION_TYPE
from .constants import GOOGLE_CALENDAR_API_BASE_URL as GOOGLE_CALENDAR_API_BASE_URL
from .constants import GOOGLE_OAUTH_TOKEN_URL as GOOGLE_OAUTH_TOKEN_URL
from .constants import MUTATION_HIGH_IMPACT_ACTIONS as MUTATION_HIGH_IMPACT_ACTIONS
from .constants import MUTATION_STATUS_APPLIED as MUTATION_STATUS_APPLIED
from .constants import MUTATION_STATUS_FAILED as MUTATION_STATUS_FAILED
from .constants import MUTATION_STATUS_NOOP as MUTATION_STATUS_NOOP
from .constants import MUTATION_STATUS_PENDING as MUTATION_STATUS_PENDING
from .constants import MUTATION_STATUS_RUNNING as MUTATION_STATUS_RUNNING
from .constants import (
    NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY as NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
)
from .constants import PROJECTION_STALENESS_MULTIPLIER as PROJECTION_STALENESS_MULTIPLIER
from .constants import PROJECTION_STATUS_FAILED as PROJECTION_STATUS_FAILED
from .constants import PROJECTION_STATUS_FRESH as PROJECTION_STATUS_FRESH
from .constants import PROJECTION_STATUS_STALE as PROJECTION_STATUS_STALE
from .constants import RATE_LIMIT_BASE_BACKOFF_SECONDS as RATE_LIMIT_BASE_BACKOFF_SECONDS
from .constants import RATE_LIMIT_MAX_RETRIES as RATE_LIMIT_MAX_RETRIES
from .constants import RATE_LIMIT_RETRY_STATUS_CODES as RATE_LIMIT_RETRY_STATUS_CODES
from .constants import RECURRENCE_HIGH_IMPACT_THRESHOLD as RECURRENCE_HIGH_IMPACT_THRESHOLD
from .constants import RECURRENCE_PROJECTION_WINDOW_DAYS as RECURRENCE_PROJECTION_WINDOW_DAYS
from .constants import SOURCE_KIND_INTERNAL_REMINDERS as SOURCE_KIND_INTERNAL_REMINDERS
from .constants import SOURCE_KIND_INTERNAL_SCHEDULER as SOURCE_KIND_INTERNAL_SCHEDULER
from .constants import SOURCE_KIND_PROVIDER as SOURCE_KIND_PROVIDER
from .constants import SYNC_CURSOR_PROJECTION as SYNC_CURSOR_PROJECTION
from .constants import SYNC_CURSOR_PROVIDER as SYNC_CURSOR_PROVIDER
from .constants import SYNC_ERROR_KIND_AUTH as SYNC_ERROR_KIND_AUTH
from .constants import SYNC_ERROR_KIND_NONE as SYNC_ERROR_KIND_NONE
from .constants import SYNC_ERROR_KIND_NOT_FOUND as SYNC_ERROR_KIND_NOT_FOUND
from .constants import SYNC_ERROR_KIND_TOKEN_EXPIRED as SYNC_ERROR_KIND_TOKEN_EXPIRED
from .constants import SYNC_ERROR_KIND_TRANSIENT as SYNC_ERROR_KIND_TRANSIENT
from .constants import SYNC_STATE_KEY_PREFIX as SYNC_STATE_KEY_PREFIX
from .constants import VALID_CONFLICT_POLICIES as VALID_CONFLICT_POLICIES
from .constants import ApprovalEnqueuer as ApprovalEnqueuer
from .constants import ButlerEventSourceHint as ButlerEventSourceHint
from .constants import CalendarConflictPolicy as CalendarConflictPolicy
from .constants import logger as logger
from .errors import CalendarAuthError as CalendarAuthError
from .errors import CalendarCredentialError as CalendarCredentialError
from .errors import CalendarRequestError as CalendarRequestError
from .errors import CalendarSyncTokenExpiredError as CalendarSyncTokenExpiredError
from .errors import CalendarTokenRefreshError as CalendarTokenRefreshError
from .errors import _build_structured_error as _build_structured_error
from .errors import _google_error_code as _google_error_code
from .errors import _is_transient_connectivity_error as _is_transient_connectivity_error
from .errors import _redact_credential_values as _redact_credential_values
from .errors import _safe_google_error_message as _safe_google_error_message
from .errors import classify_sync_error_kind as classify_sync_error_kind
from .event_tools import register_event_tools
from .google import _GoogleProvider as _GoogleProvider
from .google_mapping import _attendee_info_list_to_google as _attendee_info_list_to_google
from .google_mapping import _build_google_event_body as _build_google_event_body
from .google_mapping import _build_google_event_patch_body as _build_google_event_patch_body
from .google_mapping import _coerce_zoneinfo as _coerce_zoneinfo
from .google_mapping import _extract_google_attendees as _extract_google_attendees
from .google_mapping import _extract_google_organizer as _extract_google_organizer
from .google_mapping import _extract_google_private_metadata as _extract_google_private_metadata
from .google_mapping import _extract_google_recurrence_rule as _extract_google_recurrence_rule
from .google_mapping import _google_event_to_calendar_event as _google_event_to_calendar_event
from .google_mapping import _google_rfc3339 as _google_rfc3339
from .google_mapping import _normalize_optional_text as _normalize_optional_text
from .google_mapping import _parse_google_datetime as _parse_google_datetime
from .google_mapping import _parse_google_event_boundary as _parse_google_event_boundary
from .google_mapping import _parse_google_event_status as _parse_google_event_status
from .google_mapping import _parse_google_event_transparency as _parse_google_event_transparency
from .google_mapping import _parse_google_event_visibility as _parse_google_event_visibility
from .google_mapping import _parse_google_rfc3339_optional as _parse_google_rfc3339_optional
from .internal_projection import InternalProjectionMixin
from .models import AttendeeInfo as AttendeeInfo
from .models import AttendeeResponseStatus as AttendeeResponseStatus
from .models import BusyWindow as BusyWindow
from .models import CalendarConfig as CalendarConfig
from .models import CalendarConflictDefaults as CalendarConflictDefaults
from .models import CalendarEvent as CalendarEvent
from .models import CalendarEventCreate as CalendarEventCreate
from .models import CalendarEventUpdate as CalendarEventUpdate
from .models import CalendarNormalizedNotification as CalendarNormalizedNotification
from .models import CalendarNotificationDefaults as CalendarNotificationDefaults
from .models import CalendarNotificationInput as CalendarNotificationInput
from .models import CalendarSyncConfig as CalendarSyncConfig
from .models import CalendarSyncState as CalendarSyncState
from .models import EventStatus as EventStatus
from .models import EventVisibility as EventVisibility
from .models import SendUpdatesPolicy as SendUpdatesPolicy
from .models import _ensure_valid_timezone as _ensure_valid_timezone
from .models import _is_date_only as _is_date_only
from .models import _is_naive_datetime as _is_naive_datetime
from .models import _normalize_datetime as _normalize_datetime
from .models import _normalize_notification as _normalize_notification
from .models import _normalize_optional_string as _normalize_optional_string
from .models import _normalize_recurrence as _normalize_recurrence
from .models import _normalize_recurrence_rule as _normalize_recurrence_rule
from .models import _resolve_all_day_flag as _resolve_all_day_flag
from .mutations import MutationsMixin
from .oauth import _extract_google_credential_value as _extract_google_credential_value
from .oauth import _GoogleOAuthClient as _GoogleOAuthClient
from .oauth import _GoogleOAuthCredentials as _GoogleOAuthCredentials
from .projection import ProjectionMixin
from .provider import CalendarProvider as CalendarProvider
from .recurrence import RecurrenceMixin
from .reminders import RemindersMixin, register_reminder_tools
from .response_tools import register_response_tools
from .sync import SyncMixin
from .sync_queue import SyncQueueMixin
from .time_helpers import _cron_next_occurrence as _cron_next_occurrence
from .time_helpers import _cron_occurrences_in_window as _cron_occurrences_in_window
from .time_helpers import _format_ical_utc as _format_ical_utc
from .time_helpers import _recurrence_lines_append_exdate as _recurrence_lines_append_exdate
from .time_helpers import _recurrence_lines_bound_until as _recurrence_lines_bound_until
from .time_helpers import _rrule_occurrences_in_window as _rrule_occurrences_in_window
from .tools import register_proposal_tools, register_read_tools, register_sync_tools


class CalendarModule(
    MutationsMixin,
    InternalProjectionMixin,
    RemindersMixin,
    SyncMixin,
    ProjectionMixin,
    SyncQueueMixin,
    RecurrenceMixin,
    ButlerEventsMixin,
    ConflictsMixin,
    Module,
):
    """Calendar module with provider selection and validated config."""

    _PROVIDER_CLASSES: dict[str, type[CalendarProvider]] = {
        "google": _GoogleProvider,
    }

    name = "calendar"

    def __init__(self) -> None:
        self._config: CalendarConfig | None = None
        self._provider: CalendarProvider | None = None
        self._response_ready = False
        self._response_credential_entity_id: uuid.UUID | None = None
        self._response_coordinator = CalendarResponseCoordinator(self)
        self._response_fence = CalendarResponseFence(self)
        self._butler_name: str = DEFAULT_BUTLER_NAME
        self._approval_enqueuer: ApprovalEnqueuer | None = None
        self._db: Any = None
        self._audit_pool: Any = None
        self._credential_store: Any = None
        self._sync_task: asyncio.Task[None] | None = None
        self._internal_projection_task: asyncio.Task[None] | None = None
        self._force_sync_queue_task: asyncio.Task[None] | None = None
        self._force_sync_queue_wake = asyncio.Event()
        self._force_sync_queue_lock = asyncio.Lock()
        # In-memory sync state cache (calendar_id → CalendarSyncState).
        self._sync_states: dict[str, CalendarSyncState] = {}
        # Event set to trigger immediate sync (for calendar_force_sync tool).
        self._force_sync_event: asyncio.Event = asyncio.Event()
        # Projection cache: avoids repeated table-presence checks per cycle.
        self._projection_tables_available_cache: bool | None = None
        # Calendar ID resolved at startup from credential store or auto-discovery.
        self._resolved_calendar_id: str | None = None
        # True when the calendar ID was explicitly configured (credential store),
        # False when it was auto-discovered (shared "Butlers" calendar).
        self._calendar_is_butler_specific: bool = False
        # All provider calendar IDs discovered at startup (for pull-all sync).
        self._all_provider_calendar_ids: list[str] = []
        self._provider_calendar_discovery_completed = False
        # User's primary Google Calendar ID (email address), discovered from the
        # provider's calendarList.  Used as the implicit default target for
        # user-facing MCP tool mutations when the user has not explicitly chosen
        # a default-target calendar.
        self._primary_calendar_id: str | None = None
        # User-chosen DEFAULT-TARGET calendar id (set via ``calendar_set_primary``
        # / the dashboard and persisted under GOOGLE_CALENDAR_DEFAULT_TARGET_ID).
        # This is deliberately SEPARATE from ``_resolved_calendar_id`` (the
        # immutable Butlers calendar): choosing a default target must never
        # clobber the Butlers calendar id.  When set, it takes precedence over
        # ``_primary_calendar_id`` for the default-target role.
        self._default_target_calendar_id: str | None = None

    @property
    def config_schema(self) -> type[BaseModel]:
        return CalendarConfig

    @property
    def credentials_env(self) -> list[str]:
        return []

    def canonical_approval_preparers(self) -> dict[str, Any]:
        if self._config is None or not self._config.response_enabled:
            return {}
        return {"calendar_respond": self._response_coordinator}

    def wire_runtime(
        self,
        spawner: Any,
        repo_root: Path,
        *,
        switchboard_client: Any = None,
        switchboard_client_getter: Callable[[], Any] | None = None,
    ) -> None:
        """Use only the daemon-supplied Switchboard transport for owning fence reads.

        The getter resolves the live client at claim time, so a heartbeat reconnect
        is picked up without re-wiring; ``switchboard_client`` is the static fallback.
        """
        self._response_fence.wire(
            repo_root, switchboard_client, client_getter=switchboard_client_getter
        )

    def set_response_approval_ready(self, ready: bool) -> None:
        self._response_ready = ready

    def tool_metadata(self) -> dict[str, ToolMeta]:
        # Conditional registrations declare their complete descriptive metadata
        # through the same module channel as argument sensitivities. The daemon
        # binds the actual owner and configured approval policy after registration.
        metadata: dict[str, ToolMeta] = {}
        if self._butler_name in RESPONSE_FENCE_OWNERS:
            metadata["calendar_response_fence_status"] = ToolMeta(
                arg_sensitivities={"request": True},
                canonical_name="calendar_response_fence_status",
                module_name="calendar",
                group_name="direct",
                namespace="calendar.response",
                llm_presentable=False,
                load_posture="deferred",
                effect_class="read",
                output_trust="untrusted",
                output_sensitivity="sensitive",
                resource_params=("request",),
                selection_bound={"request": False},
                safety_args=(),
                idempotency="none",
                owner_authority=False,
                recipient_params=RecipientParameters(),
            )
        if not self.canonical_approval_preparers():
            return metadata
        metadata["calendar_respond"] = ToolMeta(
            arg_sensitivities={
                "_write": True,
                "entry_id": True,
                "response_status": True,
                "request_id": True,
                "send_updates": True,
                "_command_id": True,
                "_command_digest": True,
            },
            canonical_name="calendar_respond",
            module_name="calendar",
            group_name="response",
            namespace="calendar.response",
            llm_presentable=True,
            load_posture="deferred",
            effect_class="communicate",
            output_trust="untrusted",
            output_sensitivity="sensitive",
            resource_params=("entry_id",),
            selection_bound={"entry_id": False},
            safety_args=("entry_id", "response_status", "request_id", "send_updates"),
            idempotency="key_required",
            owner_authority=False,
            recipient_params=RecipientParameters(
                argument_names=("entry_id",),
                undeterminable=True,
                undeterminable_condition=(
                    "Event attendees and inherited provider recipients require resolution."
                ),
            ),
            read_stores=("calendar_events", "calendar_event_instances", "external:calendar"),
            write_stores=("calendar_action_log", "calendar_events", "external:calendar"),
        )
        return metadata

    async def _verified_response_account(self) -> dict[str, str]:
        from butlers.google_account_registry import get_google_account

        if self._config is None or self._response_credential_entity_id is None:
            raise CalendarResponseError("response_credentials_unverified")
        account = await get_google_account(self._db.pool, self._config.account)
        if not isinstance(account.email, str) or not account.email.strip():
            raise CalendarResponseError("response_credentials_unverified")
        permission = await self._db.pool.fetchrow(
            "SELECT granted FROM public.permissions WHERE butler=$1 AND permission=$2",
            self._butler_name,
            CALENDAR_WRITE_PERMISSION,
        )
        if permission is not None and permission["granted"] is not True:
            raise CalendarResponseError("response_permission_denied")
        write_scopes = {
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        }
        if (
            account.entity_id != self._response_credential_entity_id
            or account.status != "active"
            or not write_scopes.intersection(account.granted_scopes)
            or (self._config.account is None and not account.is_primary)
        ):
            raise CalendarResponseError("response_credentials_changed")
        return {"entity_id": str(account.entity_id), "email": account.email}

    def set_approval_enqueuer(self, enqueuer: ApprovalEnqueuer) -> None:
        """Set the callback used to enqueue overlap override requests for approval.

        The enqueuer receives (tool_name, tool_args, agent_summary) and returns
        an action_id string.  When set, overlap overrides produce
        ``status=approval_required`` instead of writing through immediately.
        """
        self._approval_enqueuer = enqueuer

    @property
    def approvals_enabled(self) -> bool:
        """Whether the approvals integration is active for this module."""
        return self._approval_enqueuer is not None

    @staticmethod
    def _coerce_config(config: Any) -> CalendarConfig:
        return config if isinstance(config, CalendarConfig) else CalendarConfig(**(config or {}))

    def _resolve_effective_butler_name(self, candidate: str | None = None) -> str:
        """Resolve the current butler identity from explicit, module, or DB context.

        Precedence:
        1. explicit ``candidate`` (trimmed, non-empty)
        2. already-set ``self._butler_name`` if it is a concrete identity
           (i.e. not the ``DEFAULT_BUTLER_NAME`` sentinel)
        3. DB schema / db_name fallback (matches legacy ``register_tools`` logic)
        4. ``self._butler_name`` if it is the default sentinel
        5. ``DEFAULT_BUTLER_NAME``
        """
        candidate_normalized = _normalize_optional_string(candidate)
        if candidate_normalized is not None and candidate_normalized.lower() != "unknown":
            return candidate_normalized

        current_normalized = _normalize_optional_string(getattr(self, "_butler_name", None))
        if current_normalized is not None and current_normalized != DEFAULT_BUTLER_NAME:
            return current_normalized

        db = getattr(self, "_db", None)
        if db is not None:
            schema_normalized = _normalize_optional_string(getattr(db, "schema", None))
            if schema_normalized is not None:
                return schema_normalized

            db_name_normalized = _normalize_optional_string(getattr(db, "db_name", None))
            if db_name_normalized is not None:
                return db_name_normalized.removeprefix("butler_") or DEFAULT_BUTLER_NAME

        if current_normalized is not None:
            return current_normalized

        return DEFAULT_BUTLER_NAME

    async def _resolve_credentials(
        self,
        *,
        db: Any,
        credential_store: Any,
    ) -> _GoogleOAuthCredentials:
        """Resolve Google OAuth credentials using canonical lookup sources.

        When ``config.account`` is set, credentials are resolved for that
        specific Google account from ``public.google_accounts`` and its
        companion entity_info.  Otherwise, falls back to the primary account
        (via owner entity_info) for backward compatibility.

        Parameters
        ----------
        db:
            Butler database instance (used to extract ``db.pool``).
        credential_store:
            Optional CredentialStore for step 1.  When ``None``, step 1 is
            skipped.

        Returns
        -------
        _GoogleOAuthCredentials
            Resolved credentials.

        Raises
        ------
        RuntimeError
            If credentials cannot be resolved from DB-backed credential storage.
        """
        # Step 1: Try CredentialStore (butler_secrets) for client_id + client_secret.
        if credential_store is not None:
            client_id = await credential_store.resolve("GOOGLE_OAUTH_CLIENT_ID", env_fallback=False)
            client_secret = await credential_store.resolve(
                "GOOGLE_OAUTH_CLIENT_SECRET", env_fallback=False
            )

            pool = getattr(db, "pool", None)
            account_email = self._config.account if self._config is not None else None

            if pool is not None and account_email:
                # Account-aware: resolve refresh token from the companion entity
                # for the specific Google account row in public.google_accounts.
                from butlers.google_account_registry import (
                    GoogleAccountNotFoundError,
                    get_google_account,
                )
                from butlers.google_account_registry import (
                    MissingGoogleCredentialsError as _RegistryMissingError,
                )

                try:
                    google_account = await get_google_account(pool, account_email)
                except (GoogleAccountNotFoundError, _RegistryMissingError) as exc:
                    raise RuntimeError(
                        f"CalendarModule: Google account '{account_email}' is not connected. "
                        "Connect the account via the dashboard OAuth flow and re-start."
                    ) from exc

                # Scope validation: the account must have granted calendar access.
                granted = google_account.granted_scopes or []
                has_calendar_scope = any("calendar" in s.lower() for s in granted)
                if not has_calendar_scope:
                    raise RuntimeError(
                        f"CalendarModule: Google account '{account_email}' has not granted "
                        "Calendar scope. Re-authorize the account with Calendar access via "
                        "the dashboard OAuth flow."
                    )

                # Fetch the refresh token from the companion entity's entity_info.
                refresh_token: str | None = None
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT value FROM public.entity_info
                        WHERE entity_id = $1 AND type = 'google_oauth_refresh'
                        LIMIT 1
                        """,
                        google_account.entity_id,
                    )
                    if row is not None:
                        refresh_token = row["value"]

                if client_id and client_secret and refresh_token:
                    logger.debug(
                        "CalendarModule: resolved Google credentials for account %s",
                        account_email,
                    )
                    self._response_credential_entity_id = google_account.entity_id
                    return _GoogleOAuthCredentials(
                        client_id=client_id,
                        client_secret=client_secret,
                        refresh_token=refresh_token,
                    )
            elif pool is not None:
                # No explicit account configured — resolve via primary Google account.
                from butlers.google_credentials import (
                    _resolve_account_entity_id,
                    _resolve_entity_refresh_token,
                )

                primary_entity_id = await _resolve_account_entity_id(pool, None)
                if primary_entity_id is not None:
                    refresh_token = await _resolve_entity_refresh_token(pool, primary_entity_id)

                if client_id and client_secret and refresh_token:
                    logger.debug("CalendarModule: resolved Google credentials from CredentialStore")
                    self._response_credential_entity_id = primary_entity_id
                    return _GoogleOAuthCredentials(
                        client_id=client_id,
                        client_secret=client_secret,
                        refresh_token=refresh_token,
                    )

        raise RuntimeError(
            "CalendarModule: Google OAuth credentials are not available in database. "
            "Store them via the dashboard OAuth flow (shared credential store)."
        )

    async def _resolve_startup_calendar_id(self, credential_store: Any) -> str:
        """Resolve the primary calendar ID from the shared credential store or auto-discovery.

        Resolution order:
        1. ``GOOGLE_CALENDAR_ID`` in the **shared** credential store (single
           source of truth for all butlers).
        2. Auto-discover a calendar named "Butlers" via the Google Calendar API.
           If it does not exist, create it.
        3. Persist the discovered/created ID back to the **shared** store.

        Side-effect: sets ``_calendar_is_butler_specific`` so downstream code
        can distinguish butler-owned calendars from the shared project calendar.
        """
        # 1. Try shared credential store (all butlers share one Butlers calendar).
        if credential_store is not None:
            stored_id = await credential_store.load_shared(
                _CREDENTIAL_KEY_CALENDAR_ID,
            )
            if stored_id:
                logger.debug("CalendarModule: resolved calendar ID from shared credential store")
                self._calendar_is_butler_specific = True
                return stored_id

        # 2. Auto-discover or create.
        if self._provider is None or not hasattr(self._provider, "discover_or_create_calendar"):
            raise RuntimeError(
                "CalendarModule: calendar ID not found in credential store and provider "
                "does not support auto-discovery"
            )
        discovered_id = await self._provider.discover_or_create_calendar(_CALENDAR_DISCOVERY_NAME)
        self._calendar_is_butler_specific = False

        # 3. Persist to the shared store so subsequent butlers skip discovery.
        if credential_store is not None:
            await credential_store.store_shared(
                _CREDENTIAL_KEY_CALENDAR_ID,
                discovered_id,
                category="google",
                description="Auto-discovered Google Calendar ID for the Butlers calendar",
                is_sensitive=False,
            )
            logger.info(
                "CalendarModule: persisted discovered calendar ID to shared credential store"
            )

        return discovered_id

    async def _discover_and_register_all_calendars(self) -> list[str]:
        """Discover all Google calendars and register each as a provider source.

        Returns the list of calendar IDs that were registered.
        """
        provider = self._require_provider()
        if not hasattr(provider, "list_calendars"):
            resolved = self._resolved_calendar_id
            return [resolved] if resolved else []

        try:
            calendars = await provider.list_calendars()
        except Exception as exc:
            logger.warning("Failed to list calendars for discovery: %s", exc)
            resolved = self._resolved_calendar_id
            return [resolved] if resolved else []

        # The Google calendarList entry with "primary": true has the user's
        # email as its "id".  Store it on every source so the frontend can
        # show which account each calendar belongs to.
        account_email: str | None = None
        for cal in calendars:
            if cal.get("primary") is True:
                candidate = cal.get("id")
                if isinstance(candidate, str) and "@" in candidate:
                    account_email = candidate
                    self._primary_calendar_id = candidate
                break

        calendar_ids: list[str] = []
        for cal in calendars:
            cal_id = cal.get("id")
            if not cal_id or not isinstance(cal_id, str):
                continue
            display_name = cal.get("summaryOverride") or cal.get("summary") or cal_id
            source_key = f"provider:{provider.name}:{cal_id}"
            is_butlers_cal = cal_id == self._resolved_calendar_id
            writable = cal.get("accessRole") in ("owner", "writer")
            metadata: dict[str, Any] = {
                "projection": "provider_sync",
                "butler_specific": is_butlers_cal,
            }
            if account_email:
                metadata["account_email"] = account_email
            try:
                await self._ensure_calendar_source(
                    source_key=source_key,
                    source_kind=SOURCE_KIND_PROVIDER,
                    lane="user",
                    provider=provider.name,
                    calendar_id=cal_id,
                    butler_name=self._butler_name if is_butlers_cal else None,
                    display_name=display_name,
                    writable=writable,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.debug("Failed to register calendar source '%s': %s", source_key, exc)
                continue
            calendar_ids.append(cal_id)

        # If the resolved (Butlers) calendar wasn't among the discovered
        # calendars (e.g. freshly created and not yet in calendarList), register
        # it explicitly so sync and projection can target it.
        if self._resolved_calendar_id and self._resolved_calendar_id not in calendar_ids:
            butlers_source_key = f"provider:{provider.name}:{self._resolved_calendar_id}"
            butlers_display = _CALENDAR_DISCOVERY_NAME
            if hasattr(provider, "get_calendar_summary"):
                try:
                    summary = await provider.get_calendar_summary(self._resolved_calendar_id)
                    if summary:
                        butlers_display = summary
                except Exception:
                    pass
            butlers_meta: dict[str, Any] = {
                "projection": "provider_sync",
                "butler_specific": True,
            }
            if account_email:
                butlers_meta["account_email"] = account_email
            try:
                await self._ensure_calendar_source(
                    source_key=butlers_source_key,
                    source_kind=SOURCE_KIND_PROVIDER,
                    lane="butler",
                    provider=provider.name,
                    calendar_id=self._resolved_calendar_id,
                    butler_name=self._butler_name,
                    display_name=butlers_display,
                    writable=True,
                    metadata=butlers_meta,
                )
                calendar_ids.append(self._resolved_calendar_id)
                logger.info(
                    "Registered Butlers calendar '%s' as source (not in calendarList)",
                    self._resolved_calendar_id,
                )
            except Exception as exc:
                logger.warning("Failed to register Butlers calendar source: %s", exc)

        logger.info(
            "Discovered %d calendar(s) for butler '%s'",
            len(calendar_ids),
            self._butler_name,
        )
        return calendar_ids

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Initialize the calendar provider with resolved Google OAuth credentials.

        Credentials are resolved from CredentialStore (``butler_secrets``) only.

        Parameters
        ----------
        config:
            Module configuration (``CalendarConfig`` or raw dict).
        db:
            Butler database instance.
        credential_store:
            Optional :class:`~butlers.credential_store.CredentialStore`.
            When provided, individual Google credential keys are resolved from
            ``butler_secrets``.
        """
        self._response_ready = False
        self._response_credential_entity_id = None
        self._config = self._coerce_config(config)
        self._db = db
        self._butler_name = self._resolve_effective_butler_name()
        self._credential_store = credential_store

        provider_cls = self._PROVIDER_CLASSES.get(self._config.provider)
        if provider_cls is None:
            supported = ", ".join(sorted(self._PROVIDER_CLASSES))
            raise RuntimeError(
                f"Unsupported calendar provider '{self._config.provider}'. "
                f"Supported providers: {supported}"
            )

        credentials = await self._resolve_credentials(db=db, credential_store=credential_store)
        pool = getattr(db, "pool", None)
        self._provider = provider_cls(self._config, credentials, pool=pool)

        # Invitation responses resolve their exact projected source and account
        # at admission/execution. They need the credential-bound provider, but
        # must not discover calendars or start the core projection workers.
        if (
            group_enabled(self._config, "response")
            and not group_enabled(self._config, "core")
            and not group_enabled(self._config, "butler_events")
        ):
            return

        self._resolved_calendar_id = await self._resolve_startup_calendar_id(credential_store)

        # Discover all user calendars and register them as sources for pull-all.
        self._all_provider_calendar_ids = await self._discover_and_register_all_calendars()
        self._provider_calendar_discovery_completed = True

        # If the stored calendar ID is stale (e.g. created under a previous Google
        # account), it won't appear in the discovered list.  Re-discover so a new
        # "Butlers" calendar is created under the current account.
        if (
            self._resolved_calendar_id
            and self._resolved_calendar_id not in self._all_provider_calendar_ids
        ):
            logger.warning(
                "CalendarModule: stored calendar ID %s not found in current account's "
                "calendars — re-discovering",
                self._resolved_calendar_id,
            )
            self._resolved_calendar_id = await self._resolve_startup_calendar_id(None)
            # Re-run discovery to register the new calendar as a source.
            self._all_provider_calendar_ids = await self._discover_and_register_all_calendars()
            self._provider_calendar_discovery_completed = True
            # Persist the new ID to shared store.
            if credential_store is not None and self._resolved_calendar_id:
                await credential_store.store_shared(
                    _CREDENTIAL_KEY_CALENDAR_ID,
                    self._resolved_calendar_id,
                    category="google",
                    description="Auto-discovered Google Calendar ID for the Butlers calendar",
                    is_sensitive=False,
                )

        if not self._all_provider_calendar_ids and self._resolved_calendar_id:
            self._all_provider_calendar_ids = [self._resolved_calendar_id]

        # Restore the user's chosen DEFAULT-TARGET calendar (separate from the
        # immutable Butlers calendar id) so the selection persists across
        # restarts.  Only honour it if it is still a discovered calendar.
        if credential_store is not None:
            try:
                stored_default_target = await credential_store.load_shared(
                    _CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID,
                )
            except Exception as exc:
                stored_default_target = None
                logger.warning("CalendarModule: failed to load default-target calendar id: %s", exc)
            if stored_default_target:
                if (
                    not self._all_provider_calendar_ids
                    or stored_default_target in self._all_provider_calendar_ids
                ):
                    self._default_target_calendar_id = stored_default_target
                else:
                    logger.warning(
                        "CalendarModule: stored default-target calendar %s not found in "
                        "current account's calendars — ignoring",
                        stored_default_target,
                    )

        # One-time hygiene: drop any residual probe/sentinel calendar source
        # (e.g. ``provider:google:__invalid_check__``) left in the ledger by an
        # older validation path. Idempotent — a no-op once purged, and the
        # registration guard in ``_ensure_calendar_source`` prevents recreation.
        await self._purge_invalid_probe_sources()
        await self._purge_obsolete_internal_sources()

        if self._config.sync.enabled:
            self._sync_task = asyncio.create_task(
                self._run_sync_poller(), name="calendar-sync-poller"
            )
            logger.info(
                "Calendar sync poller started (interval=%dm, calendar_id=%s)",
                self._config.sync.interval_minutes,
                self._resolved_calendar_id,
            )

        # Dashboard sync requests are recorded in calendar_action_log and
        # deliberately drained by the module, independent of the optional
        # polling configuration. A force-sync click must work even when normal
        # periodic polling is disabled.
        if await self._projection_tables_available():
            recovered = await self._recover_force_sync_commands()
            self._force_sync_queue_task = asyncio.create_task(
                self._run_force_sync_queue_worker(), name="calendar-force-sync-queue"
            )
            if recovered:
                self._force_sync_queue_wake.set()
                logger.warning(
                    "Calendar force-sync queue recovered %d interrupted command(s)",
                    recovered,
                )

        self._internal_projection_task = asyncio.create_task(
            self._run_internal_projection_poller(),
            name="calendar-internal-projection-poller",
        )
        logger.info(
            "Calendar internal projection poller started (interval=%dm)",
            DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES,
        )

    async def on_shutdown(self) -> None:
        self._response_ready = False
        self._response_credential_entity_id = None
        await self._response_fence.close()
        if self._force_sync_queue_task is not None and not self._force_sync_queue_task.done():
            self._force_sync_queue_task.cancel()
            try:
                await self._force_sync_queue_task
            except asyncio.CancelledError:
                pass
        self._force_sync_queue_task = None

        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        self._sync_task = None

        if self._internal_projection_task is not None and not self._internal_projection_task.done():
            self._internal_projection_task.cancel()
            try:
                await self._internal_projection_task
            except asyncio.CancelledError:
                pass
        self._internal_projection_task = None

        if self._provider is not None:
            await self._provider.shutdown()
        self._provider = None

    def wire_audit_pool(self, audit_pool: Any) -> None:
        """Store the switchboard audit pool for google_calendar_write egress audit entries."""
        self._audit_pool = audit_pool

    async def extra_status_fields(self) -> dict[str, Any]:
        """Return OAuth/credential health fields for the calendar module status entry.

        Queries ``public.google_accounts`` for the primary account's OAuth
        status and maps it to the ``oauth_status`` and ``credential_health``
        fields consumed by the dashboard Config tab.

        Returns ``{}`` gracefully when the DB pool is unavailable or the
        google_accounts table does not exist (e.g. during tests or early
        bootstrap).

        Mapping from ``public.google_accounts.status``:

        - ``'active'``         → ``oauth_status='granted'``, ``credential_health='ok'``
        - ``'revoked'``/``'expired'`` → ``oauth_status='reauth_needed'``,
          ``credential_health='error'``
        - no primary account  → ``oauth_status='not_configured'``,
          ``credential_health='warning'``
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return {}
        try:
            row = await pool.fetchrow(
                "SELECT status FROM public.google_accounts WHERE is_primary = true LIMIT 1"
            )
        except Exception:
            logger.debug("extra_status_fields: google_accounts query failed", exc_info=True)
            return {}

        if row is None:
            return {
                "oauth_status": "not_configured",
                "credential_health": "warning",
            }

        account_status = row["status"]
        if account_status == "active":
            return {
                "oauth_status": "granted",
                "credential_health": "ok",
            }
        # 'revoked' or 'expired' → needs re-auth
        return {
            "oauth_status": "reauth_needed",
            "credential_health": "error",
        }

    def _require_provider(self) -> CalendarProvider:
        if self._provider is None:
            raise RuntimeError("Calendar provider is not initialized; call on_startup first")
        return self._provider

    def _require_config(self) -> CalendarConfig:
        if self._config is None:
            raise RuntimeError("Calendar config is not initialized")
        return self._config

    def _resolve_role_calendar_id(self, role: str) -> str | None:
        """Resolve the calendar id that serves a given calendar-id role.

        - ``CALENDAR_ROLE_BUTLERS`` → the immutable Butlers calendar id
          (``_resolved_calendar_id``, cred key ``GOOGLE_CALENDAR_ID``).  This is
          never affected by ``calendar_set_primary``.
        - ``CALENDAR_ROLE_DEFAULT_TARGET`` → the user's chosen default target for
          user-facing MCP mutations (``_default_target_calendar_id``), falling
          back to the discovered primary calendar, then the Butlers calendar.

        Returns ``None`` when no calendar id is available for the role (e.g.
        before ``on_startup`` has run).
        """
        if role == CALENDAR_ROLE_BUTLERS:
            return self._resolved_calendar_id
        if role == CALENDAR_ROLE_DEFAULT_TARGET:
            return (
                self._default_target_calendar_id
                or self._primary_calendar_id
                or self._resolved_calendar_id
            )
        raise ValueError(f"Unknown calendar role: {role!r}")

    def _resolve_calendar_id(
        self, override_calendar_id: str | None, *, for_create: bool = False
    ) -> str:
        if override_calendar_id is None:
            # Resolution of the no-override default depends on whether this is an
            # event CREATE or any other (read/update/delete/sync) operation:
            #
            # * ``for_create=True``: butler-authored creates default to the
            #   dedicated "Butlers" calendar (``_resolved_calendar_id``), NOT the
            #   user's default-target/primary.  The explicit ``calendar_id``
            #   override branch below is the documented opt-out for "put this on
            #   my primary calendar".  The default-target selection
            #   (``calendar_set_primary``) deliberately does not affect this
            #   create default.
            # * ``for_create=False`` (default): reads and other operations resolve
            #   to the user-facing DEFAULT-TARGET calendar (the user's chosen
            #   default target if set, else the discovered primary, else the
            #   Butlers calendar).  Routing creates to the Butlers calendar must
            #   never hide the user's primary-calendar events from reads.
            role = CALENDAR_ROLE_BUTLERS if for_create else CALENDAR_ROLE_DEFAULT_TARGET
            target = self._resolve_role_calendar_id(role)
            if target is None:
                raise RuntimeError("Calendar ID not resolved; call on_startup first")
            return target

        normalized = override_calendar_id.strip()
        if not normalized:
            raise ValueError("calendar_id must be a non-empty string when provided")
        if self._provider_calendar_discovery_completed or self._all_provider_calendar_ids:
            known_calendar_ids = set(self._all_provider_calendar_ids)
            if self._primary_calendar_id is not None:
                known_calendar_ids.add(self._primary_calendar_id)
            if self._resolved_calendar_id is not None:
                known_calendar_ids.add(self._resolved_calendar_id)
            known_calendar_ids.add("primary")
            if normalized not in known_calendar_ids:
                raise ValueError("calendar_id is not one of the discovered provider calendars")
        return normalized

    async def _resolve_home_calendar_id(
        self, *, event_id: str, override_calendar_id: str | None
    ) -> str:
        """Resolve the calendar an EXISTING event lives on, for update/delete by id.

        Unlike :meth:`_resolve_calendar_id` (which picks a single default *write
        target*), this finds the calendar the event referenced by ``event_id``
        actually lives on.  This matters now that butler-authored events default
        to the dedicated "Butlers" calendar while the user's own events live on
        their primary (or other discovered) calendars: a blind
        ``_resolve_calendar_id(None)`` would target one calendar and silently
        miss (404) events living on the other.

        Resolution order:

        1. **Explicit override** → validated against discovered calendars and
           used directly (the resolver is bypassed).
        2. **Projection lookup** → join ``calendar_events.origin_ref`` (the
           provider event id) to its ``calendar_sources.calendar_id``.
        3. **Bounded search** → probe ``provider.get_event`` across the
           discovered calendars (Butlers calendar and the user's primary at
           minimum), excluding the fallback calendar the caller already probes.
        4. **Fallback** → the user's default-target/primary calendar.  This is
           fail-open: the caller's own ``get_event`` then surfaces ``not_found``
           rather than this resolver raising.
        """
        # 1. Explicit override wins (validated against discovered calendars).
        if override_calendar_id is not None:
            return self._resolve_calendar_id(override_calendar_id)

        normalized_event_id = event_id.strip()
        fallback_calendar_id = self._resolve_calendar_id(None)
        if not normalized_event_id:
            return fallback_calendar_id

        # 2. Projection lookup: the event's home calendar, if it has been synced.
        projected = await self._lookup_home_calendar_from_projection(normalized_event_id)
        if projected is not None:
            return projected

        # 3. Bounded search across discovered calendars.  The fallback calendar
        #    is skipped because the update/delete flow probes it anyway when this
        #    resolver returns the fallback, so searching it here would just
        #    double the provider round-trip.
        located = await self._search_home_calendar(
            normalized_event_id, skip_calendar_id=fallback_calendar_id
        )
        if located is not None:
            return located

        # 4. Fail-open fallback to the default-target/primary calendar.
        return fallback_calendar_id

    async def _lookup_home_calendar_from_projection(self, origin_ref: str) -> str | None:
        """Return the provider calendar id an event lives on, from the projection.

        Joins ``calendar_events.origin_ref`` (the provider event id) to its
        ``calendar_sources.calendar_id``, restricted to provider-lane sources
        that carry a concrete calendar id.  Returns ``None`` when the projection
        has no record (or is unavailable), so the caller falls through to a
        bounded search.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        if not await self._projection_tables_available():
            return None
        try:
            row = await pool.fetchrow(
                """
                SELECT cs.calendar_id AS calendar_id
                FROM calendar_events ce
                JOIN calendar_sources cs ON cs.id = ce.source_id
                WHERE ce.origin_ref = $1
                  AND cs.source_kind = $2
                  AND cs.calendar_id IS NOT NULL
                ORDER BY ce.updated_at DESC
                LIMIT 1
                """,
                origin_ref,
                SOURCE_KIND_PROVIDER,
            )
        except Exception as exc:
            logger.debug(
                "home-calendar projection lookup failed for origin_ref=%s: %s",
                origin_ref,
                exc,
                exc_info=True,
            )
            return None
        if row is None:
            return None
        calendar_id = row["calendar_id"]
        if isinstance(calendar_id, str) and calendar_id.strip():
            return calendar_id
        return None

    async def _search_home_calendar(
        self, event_id: str, *, skip_calendar_id: str | None = None
    ) -> str | None:
        """Bounded search for an event's home calendar across discovered calendars.

        Probes ``provider.get_event`` on each discovered calendar (the Butlers
        calendar and the user's primary/default-target first), returning the
        first calendar the event is found on.  ``skip_calendar_id`` is excluded
        because the caller already probes it on fallback, so searching it here
        would double the provider round-trip.  Returns ``None`` when the event
        is not located on any discovered calendar.
        """
        provider = self._provider
        if provider is None:
            return None

        ordered: list[str] = []
        seen: set[str] = set()
        for candidate in (
            self._resolved_calendar_id,
            self._resolve_role_calendar_id(CALENDAR_ROLE_DEFAULT_TARGET),
            self._primary_calendar_id,
            *self._all_provider_calendar_ids,
        ):
            if not candidate or candidate == skip_calendar_id or candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)

        for calendar_id in ordered:
            try:
                event = await provider.get_event(calendar_id=calendar_id, event_id=event_id)
            except Exception as exc:
                logger.debug(
                    "home-calendar search get_event failed (calendar_id=%s, event_id=%s): %s",
                    calendar_id,
                    event_id,
                    exc,
                )
                continue
            if event is not None:
                return calendar_id
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self._config = self._coerce_config(config)
        self._db = db
        self._butler_name = self._resolve_effective_butler_name(butler_name)
        _tool = tool_registrar(mcp, self._config)
        register_response_tools(self, mcp, _tool, butler_name)
        register_read_tools(self, mcp, _tool, butler_name)
        register_event_tools(self, mcp, _tool, butler_name)
        register_butler_event_tools(self, mcp, _tool, butler_name)
        register_sync_tools(self, mcp, _tool, butler_name)
        register_reminder_tools(self, mcp, _tool, butler_name)
        register_proposal_tools(self, mcp, _tool, butler_name)
