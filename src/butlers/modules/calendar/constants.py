"""Calendar constants implementation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, Literal

logger = logging.getLogger("butlers.modules.calendar")

# Type alias for the approval enqueue callback.
# Receives (tool_name, tool_args, agent_summary) and returns the action_id string.
ApprovalEnqueuer = Callable[
    [str, dict[str, Any], str],
    Coroutine[Any, Any, str],
]

GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE_URL = "https://www.googleapis.com/calendar/v3"

# Credential store key for the IMMUTABLE Butlers calendar ID (auto-discovered or
# created as the shared "Butlers" calendar).  This is the home for
# butler-authored events and must never be overwritten by a user's
# default-target selection.
_CREDENTIAL_KEY_CALENDAR_ID = "GOOGLE_CALENDAR_ID"
# Credential store key for the user-chosen DEFAULT-TARGET calendar — the
# calendar used for user-facing MCP mutations when no explicit calendar_id is
# passed.  Distinct from GOOGLE_CALENDAR_ID so that picking a default target
# never clobbers the immutable Butlers calendar id.
_CREDENTIAL_KEY_DEFAULT_TARGET_CALENDAR_ID = "GOOGLE_CALENDAR_DEFAULT_TARGET_ID"
# Name used to discover or create the shared Butlers calendar.
_CALENDAR_DISCOVERY_NAME = "Butlers"

# Calendar-id "roles" — which distinct concern a resolved calendar id serves.
# These two roles were historically conflated onto a single field/cred key.
#   - BUTLERS: the immutable dedicated Butlers calendar (cred key
#     GOOGLE_CALENDAR_ID); home for butler-authored events.
#   - DEFAULT_TARGET: the user-chosen default for user-facing MCP mutations
#     (cred key GOOGLE_CALENDAR_DEFAULT_TARGET_ID).
CALENDAR_ROLE_BUTLERS = "butlers"
CALENDAR_ROLE_DEFAULT_TARGET = "default_target"

BUTLER_EVENT_TITLE_PREFIX = "BUTLER:"
BUTLER_GENERATED_PRIVATE_KEY = "butler_generated"
BUTLER_NAME_PRIVATE_KEY = "butler_name"
NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY = "provider_event_id"
DEFAULT_BUTLER_NAME = "butler"
VALID_CONFLICT_POLICIES = {"suggest", "fail", "allow_overlap"}
DEFAULT_CONFLICT_SUGGESTION_COUNT = 3
# Upper bound on how far ahead conflict-suggestion search walks when owner
# scheduling-availability preferences are in effect, so an unsatisfiable
# constraint set terminates instead of looping forever.
_SUGGESTION_SEARCH_HORIZON = timedelta(days=90)

# Rate-limit retry configuration (spec section 14.2, 15).
# Retry on 429 Too Many Requests and 503 Service Unavailable with exponential backoff.
RATE_LIMIT_RETRY_STATUS_CODES = {429, 503}
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_BASE_BACKOFF_SECONDS = 1.0

# Sync state store key prefix (spec section 10.2).
# Format: calendar::sync::{calendar_id}
SYNC_STATE_KEY_PREFIX = "calendar::sync::"
# Calendar projection sync cursor names.
SYNC_CURSOR_PROVIDER = "provider_sync"
SYNC_CURSOR_PROJECTION = "projection"
# Default full sync window in days when no sync token exists (spec section 12.5).
DEFAULT_SYNC_WINDOW_DAYS = 30
# Default sync interval in minutes (spec section 12.5).
DEFAULT_SYNC_INTERVAL_MINUTES = 5
# Projection staleness grace period multiplier over the configured sync interval.
PROJECTION_STALENESS_MULTIPLIER = 2
# Projection table source constants.
SOURCE_KIND_PROVIDER = "provider_event"
SOURCE_KIND_INTERNAL_SCHEDULER = "internal_scheduler"
SOURCE_KIND_INTERNAL_REMINDERS = "internal_reminders"
# Sentinel calendar ids used by credential/health probes, never a real calendar.
# A probe that reaches the sync/source-registration path would otherwise persist
# a bogus ``provider:<name>:__invalid_check__`` source that permanently pollutes
# the source-freshness ledger, so registration is refused for these ids and any
# residual rows are purged once on startup (bu-ssp91).
_INVALID_PROBE_CALENDAR_IDS: frozenset[str] = frozenset({"__invalid_check__"})
# Exact residual source keys from an older internal-projection naming scheme.
# This deliberately is not a pattern: valid future/internal sources must remain
# eligible for ordinary source registration and cascade semantics.
_OBSOLETE_INTERNAL_SOURCE_KEYS: tuple[str, ...] = (
    "internal_scheduler:butler",
    "internal_scheduler:butlers",
    "internal_reminders:butlers",
)
# Projection status constants surfaced to sync/status consumers.
PROJECTION_STATUS_FRESH = "fresh"
PROJECTION_STATUS_STALE = "stale"
PROJECTION_STATUS_FAILED = "failed"
# Per-source error classification surfaced alongside the raw ``last_error`` so the
# dashboard can show the right recovery CTA (Recover vs Reconnect). Healthy
# sources report ``none``.
SYNC_ERROR_KIND_NONE = "none"
SYNC_ERROR_KIND_TOKEN_EXPIRED = "token_expired"
SYNC_ERROR_KIND_AUTH = "auth"
SYNC_ERROR_KIND_NOT_FOUND = "not_found"
SYNC_ERROR_KIND_TRANSIENT = "transient"
# Default duration for scheduled tasks when start_at/end_at are not set.
DEFAULT_SCHEDULED_TASK_DURATION_MINUTES = 15
# Interval for periodic internal projection refresh (startup + periodic background task).
DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES = 15
# Rolling window for recurring event instance expansion (cron and RRULE).
RECURRENCE_PROJECTION_WINDOW_DAYS = 90
MUTATION_STATUS_PENDING = "pending"
MUTATION_STATUS_RUNNING = "running"
MUTATION_STATUS_APPLIED = "applied"
MUTATION_STATUS_FAILED = "failed"
MUTATION_STATUS_NOOP = "noop"
FORCE_SYNC_ACTION_TYPE = "calendar_force_sync"
BUTLER_EVENT_SOURCE_SCHEDULED = "scheduled_task"
BUTLER_EVENT_SOURCE_REMINDER = "butler_reminder"
ButlerEventSourceHint = Literal["scheduled_task", "butler_reminder"]
# Default action string used when no explicit action is provided for a butler calendar event.
# When the stored prompt equals this sentinel (or is empty), the scheduler fires with no
# meaningful context, causing the session to improvise.  calendar_create_butler_event detects
# this and replaces it with a descriptive prompt built from the event title and start time.
_CALENDAR_EVENT_DEFAULT_ACTION = "Run butler event"
MUTATION_HIGH_IMPACT_ACTIONS = {
    "workspace_butler_delete",
    "workspace_butler_toggle",
}
# A recurrence-scoped mutation (``series`` / ``following``) that touches at least
# this many occurrences is treated as high-impact and routed through the approval
# gate when an approval enqueuer is wired.  A single-occurrence ``this`` mutation
# is never gated on impact.
RECURRENCE_HIGH_IMPACT_THRESHOLD = 10

CalendarConflictPolicy = Literal["suggest", "fail", "allow_overlap"]
