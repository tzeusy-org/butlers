import type { components } from "./schema.gen";
import type { ApiResult, ApiQuery, ApiOptions } from "./request-types";

/** Generated native contract aliases and explicit frontend query/view types. */

// ---------------------------------------------------------------------------
// Base response wrappers
// ---------------------------------------------------------------------------

/** Extensible metadata bag attached to every API response. */
export type ApiMeta = components["schemas"]["ApiMeta"];

/** Generic API response wrapper: { data: T, meta: {...} } */
export type ApiResponse<T> = Omit<components["schemas"]["ApiResponse"], "data"> & { data: T };

/** Standard error response envelope. */
export type ErrorResponse = components["schemas"]["ErrorResponse"];

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

/** Pagination metadata for list endpoints. */
export type PaginationMeta = components["schemas"]["PaginationMeta"];

/** API response wrapper for paginated list endpoints. */
export type PaginatedResponse<T> = Omit<components["schemas"]["PaginatedResponse_ApprovalAction_"], "data"> & { data: T[] };

/**
 * The one cursor-paginated list envelope (`Page[T]` in `src/butlers/api/pagination.py`).
 * `next_cursor` is opaque and null on the last page: "is there more" is
 * `next_cursor !== null`. `sources_degraded` names fan-out sources that failed
 * for this page (absent when every source answered).
 */
export type CursorPage<T> = Omit<components["schemas"]["Page_EntityFactEntry_"], "items"> & { items: T[] };

/**
 * Keyset (cursor) pagination metadata for the cross-butler session list, the
 * one cursor list still off {@link CursorPage} (it retires at the RFC 0038 D19 switch).
 * Drops the expensive `count(*)` total in favour of an opaque forward cursor.
 * `next_cursor` is base64url-encoded and opaque to the client; pass it back as
 * the `cursor` query param to fetch the next (older) page.
 */
export type KeysetMeta = components["schemas"]["KeysetMeta"];

/** API response wrapper for keyset-paginated list endpoints. */
export type KeysetResponse<T> = Omit<components["schemas"]["KeysetResponse_SessionSummary_"], "data"> & { data: T[] };

// ---------------------------------------------------------------------------
// Domain summaries
// ---------------------------------------------------------------------------

/** Lightweight butler representation for list views. */
export type ButlerSummary = components["schemas"]["ButlerSummary"];

/**
 * One butler's row on the consolidated fleet status board (bu-86c4c.17).
 *
 * `activity`/`cell_tone` are the canonical liveness verdict computed
 * server-side and shared verbatim by every consumer (roster board, /system
 * topology graph, /system heartbeat list) -- do not re-derive them
 * client-side.
 */
export type BoardRow = components["schemas"]["BoardRow"];

/** Fleet-wide aggregates for GET /api/butlers/board. */
export type BoardAggregates = components["schemas"]["BoardAggregates"];

/** Response envelope for GET /api/butlers/board. */
export type BoardResponse = components["schemas"]["BoardResponse"];

/**
 * Container-boundary-safe process facts for the butler Overview tab.
 * `pid` is intentionally absent.
 */
export type ProcessFacts = components["schemas"]["ProcessFacts"];

/** Per-module health status returned by GET /api/butlers/:name/modules. */
export type ModuleStatus = components["schemas"]["ModuleStatus"];

/** Extended butler representation returned by GET /api/butlers/:name. */
export type ButlerDetail = components["schemas"]["ButlerDetail"];

/** Lightweight session representation for list views. */
export type SessionSummary = components["schemas"]["SessionSummary"];

/** Full session detail returned by the single-session endpoint. */
export type SessionDetail = components["schemas"]["SessionDetail"];

export type SessionPromptReceipt = components["schemas"]["SessionPromptReceipt"];

/**
 * Window-scoped, filter-aware rollup returned by GET /api/sessions/aggregate.
 * Counts span all butlers matching the active filters (window-true), NOT the
 * fetched page. `success_rate` is null when no terminal sessions match
 * (success_count + failed_count == 0). Cost is intentionally omitted.
 *
 * `by_trigger_source` is only populated when the request set
 * `include_trigger_breakdown=true` -- otherwise an empty array (bu-y0v0c;
 * powers the sessions verdict opener's failure-clustering clause).
 * `trigger_breakdown_degraded_sources` names pools that failed only that
 * optional fan-out; it is distinct from scalar `meta.sources_degraded`.
 */
export type SessionAggregate = components["schemas"]["SessionAggregate"];

/** Query parameters for session list endpoints. */
export interface SessionParams {
  offset?: number;
  limit?: number;
  /** Opaque keyset cursor for the cross-butler list. First page omits it. */
  cursor?: string;
  butler?: string;
  trigger_source?: string;
  request_id?: string;
  status?: ApiQuery<"/sessions">["status"];
  since?: string;
  until?: string;
  /** Aggregate-only: also compute by_trigger_source (see SessionAggregate). */
  include_trigger_breakdown?: boolean;
}

export type ButlerSessionParams = Omit<SessionParams, "status"> & Pick<ApiQuery<"/butlers/{name}/sessions">, "status">;

/** Lightweight notification representation for list views. */
export type NotificationSummary = components["schemas"]["NotificationSummary"];

export type HealthResponse = components["schemas"]["HealthResponse"];

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

/** Aggregate notification statistics. */
export type NotificationStats = components["schemas"]["NotificationStats"];

/** Query parameters for notification list endpoints. */
export interface NotificationParams {
  offset?: number;
  limit?: number;
  butler?: string;
  channel?: string;
  /** Stored status plus computed `retried` or terminal `terminal_failed`. */
  status?: string;
  since?: string;
  until?: string;
}

/**
 * Query parameters for GET /api/notifications/stats -- window scoping only
 * (bu-y0v0c, JARVIS pursuit move 9 slice 3). Omitted entirely, the endpoint
 * returns its original all-time rollup; passing `since`/`until` scopes every
 * count to that `created_at` window (powers the notifications verdict
 * opener's "N failed notifications ... in the last Xh" clause).
 */
export interface NotificationStatsParams {
  since?: string;
  until?: string;
}

/**
 * Result of a manual retry or escalate action on a failed notification
 * (POST /api/notifications/{id}/retry or .../escalate). The original
 * notification is flipped to `read`; this describes the new attempt, which
 * has its own real `sent`/`failed` outcome.
 */
export type NotificationActionResult = components["schemas"]["NotificationActionResult"];

// ---------------------------------------------------------------------------
// Attention ledger (bu-tdd4k.4) -- the ledger's first reader.
// Mirrors src/butlers/api/models/attention_ledger.py.
// ---------------------------------------------------------------------------

/**
 * Delivery-vs-suppression counts for one `origin_butler` over a window.
 * `suppressed_never_delivered` is the marquee signal: true when this source
 * has been suppressed at least once and never delivered in the window --
 * the exact live failure bu-tdd4k.2 fixed for secrets_lifecycle (120
 * suppressed / 0 delivered).
 */
export type AttentionSourceSummary = components["schemas"]["AttentionSourceSummary"];

/** Response for GET /api/attention/ledger/summary. */
export type AttentionLedgerSummaryResponse = components["schemas"]["AttentionLedgerSummaryResponse"];

/** Query parameters for GET /api/attention/ledger/summary. */
export interface AttentionLedgerSummaryParams {
  since?: string;
  until?: string;
  intent?: string;
  source?: string;
  origin_butler?: string;
}

// ---------------------------------------------------------------------------
// Issues
// ---------------------------------------------------------------------------

/** Active issue detected across butler infrastructure. */
export type Issue = components["schemas"]["Issue"];

/**
 * Server-computed resolution of ONE `public.audit_log` failure row to the
 * Issues group it belongs to (GET /api/issues/group-for-audit/{audit_id},
 * bu-6jv4m.3).
 *
 * The Audit Log used to link a failure to `/issues?q=<first line of the
 * error>`, reconstructing the backend's grouping key client-side
 * (approximately) and then substring-matching a feed already bounded by its
 * own default window. That hop could land on an empty page that read as an
 * all-clear. This is the exact answer instead, computed from the same
 * `normalized_errors` CTE the feed groups on.
 *
 * `found` is the ONLY thing that may be read as "no group": when it is false,
 * `reason` says why in so many words. A transport failure is an error state,
 * NOT `found: false` -- the UI must never render an unavailable lookup as a
 * confident absence.
 */
export type AuditIssueGroupRef = components["schemas"]["AuditIssueGroupRef"];

// ---------------------------------------------------------------------------
// Activity / Timeline
// ---------------------------------------------------------------------------

/** Bounded presentation classification supplied by GET /api/timeline. */
export type TimelineMachineClass = "owner" | "heartbeat" | "maintenance";

/** A unified timeline event from GET /api/timeline. */
/** Timeline view accepts the existing rolling-deploy omission handled by timelineMachineClass.
 * The generated current wire schema retains its required machine_class. */
export type TimelineEvent = Omit<components["schemas"]["TimelineEvent"], "machine_class">
  & Partial<Pick<components["schemas"]["TimelineEvent"], "machine_class">>;

/** Aggregate counts over the heartbeat events in the current page (bu-86c4c.9). */
export type TimelineHeartbeatRollup = components["schemas"]["TimelineHeartbeatRollup"];

/** Response shape from GET /api/timeline. */
export type TimelineResponse = components["schemas"]["TimelineResponse"];

/** Query parameters for the timeline endpoint. */
export interface TimelineParams {
  limit?: number;
  /** Resolve this persisted identifier independently of head-page pagination. */
  event?: string;
  butler?: string[];
  event_type?: string[];
  before?: string;
  /** Filter sessions and trace-attributed notifications by OpenTelemetry trace ID. */
  trace?: string;
  /** Inclusive UTC minute bound; must be paired with until. */
  since?: string;
  /** Exclusive UTC minute bound; must be paired with since. */
  until?: string;
}

export type TimelineHistogramResponse = components["schemas"]["TimelineHistogramResponse"];

export interface TimelineHistogramParams {
  since: string;
  until: string;
  butler?: string[];
  event_type?: string[];
  trace?: string;
}

/** A content-blind recent record currently marked failed. */
export type TimelineAttentionItem = components["schemas"]["TimelineAttentionItem"];

export type TimelineAttentionResponse = components["schemas"]["TimelineAttentionResponse"];

export interface TimelineAttentionParams {
  butler?: string[];
  trace?: string;
}

// ---------------------------------------------------------------------------
// Spend
// ---------------------------------------------------------------------------

/** Executed-model ledger usage excluded because the model has no price entry. */
export type UnpricedModelUsage = components["schemas"]["UnpricedModelUsage"];

/** Material token disagreement between sessions and the executed ledger. */
export type SpendDivergence = components["schemas"]["SpendDivergence"];

/** Spend data for a single day. */
export type DailySpend = components["schemas"]["DailySpend"];

/** A session ranked by cost. */
export type TopSession = components["schemas"]["TopSession"];

// ---------------------------------------------------------------------------
// Dispatch attempts (model-failover provenance) — GET /api/dispatch/attempts
// ---------------------------------------------------------------------------

/**
 * A single failover/quota-skip provenance row. `outcome` is one of
 * `quota_skip` (candidate skipped before invocation -- either a routine
 * same-tier token-quota failover, or a monthly spend-ceiling hard block;
 * distinguish via `failure_reason`), `runtime_failure`, `allowance_exhausted`
 * (provider plan usage-limit; the breaker ignores it), `suppressed`,
 * `exhausted`, or `success`.
 */
export type DispatchAttemptEntry = components["schemas"]["DispatchAttemptEntry"];

/** Identifies one logical dispatch cycle by either or both session selectors. */
type DispatchAttemptsSessionSelector =
  | {
      session_id: string;
      logical_session_id?: string;
    }
  | {
      session_id?: never;
      logical_session_id: string;
    };

/** Shared pagination control for both dispatch-attempt query modes. */
interface DispatchAttemptsPaginationParams {
  limit?: number;
}

/**
 * Params for GET /api/dispatch/attempts.
 *
 * Callers select one mutually exclusive mode, mirroring backend validation:
 *
 * - session mode accepts `session_id`, `logical_session_id`, or both, and
 *   returns rows in `attempt_index` order;
 * - fleet mode requires `outcome`, has no session selector, and may narrow by
 *   `reason_prefix`, `since`, or `order`.
 *
 * The `never` fields make a fleet/session mixture a TypeScript error. The
 * backend repeats this validation for untyped callers.
 */
export type DispatchAttemptsParams =
  | (DispatchAttemptsPaginationParams &
      DispatchAttemptsSessionSelector & {
        outcome?: never;
        reason_prefix?: never;
        since?: never;
        order?: never;
      })
  | (DispatchAttemptsPaginationParams & {
      session_id?: never;
      logical_session_id?: never;
      outcome: string;
      reason_prefix?: string;
      /** ISO datetime; restricts fleet rows to ts >= since. */
      since?: string;
      order?: "asc" | "desc";
    });

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

/** A scheduled task belonging to a butler. */
export type ScheduleDispatchMode = "prompt" | "job";

/** Shared job arguments payload shape for deterministic schedule mode. */
export type ScheduleJobArgs = Record<string, unknown>;

/** A scheduled task belonging to a butler. */
export type Schedule = components["schemas"]["Schedule"];

/** Payload for creating a schedule (prompt or deterministic job mode). */
export type ScheduleCreate = components["schemas"]["ScheduleCreate"];

/** Payload for updating an existing schedule (all fields optional). */
export type ScheduleUpdate = components["schemas"]["ScheduleUpdate"];

/** Explicit desired state sent to the idempotent schedule-toggle action. */
export type ScheduleToggleRequest = components["schemas"]["ScheduleToggleRequest"];

/** Server-observed receipt returned by a successful schedule toggle. */
export type ScheduleToggleResult = components["schemas"]["ScheduleToggleResult"];

// ---------------------------------------------------------------------------
// Calendar workspace
// ---------------------------------------------------------------------------

/** Workspace mode toggle for /butlers/calendar. */
export type CalendarWorkspaceView = "user" | "butler";

/**
 * Read-lane selector accepted by GET /api/calendar/workspace. Extends the
 * user/butler toggle with the read-only `proposals` and `overlays` lanes (the
 * latter projects precomputed cross-domain overlay contributions). Overlays are
 * fetched as an additive layer, not a primary view mode.
 */
export type CalendarWorkspaceQueryView =
  CalendarWorkspaceView | "proposals" | "overlays";

/** Unified source categories for calendar entries. */
export type UnifiedCalendarSourceType =
  | "provider_event"
  | "scheduled_task"
  | "butler_reminder"
  | "manual_butler_event"
  | "proposed_event"
  | "overlay_contribution";

/** Normalized event row returned by GET /api/calendar/workspace. */
/**
 * A person linked to a calendar event via `calendar_event_entities`, resolved
 * to `entity_id` + `display_label` (the person's `public.entities.canonical_name`)
 * so an existing event pill/detail panel can hydrate linked-people avatars —
 * the persistence counterpart to the creation-time `ContactPeoplePicker` chips.
 * Mirrors backend `CalendarLinkedPerson`.
 */
export type CalendarLinkedPerson = components["schemas"]["CalendarLinkedPerson"];

export type UnifiedCalendarEntry = components["schemas"]["UnifiedCalendarEntry"];

/**
 * Optional inline overrides applied when accepting a calendar proposal.
 * Any field set here overrides the corresponding stored proposal value before
 * the butler event is created on the Butlers subcalendar. An empty/omitted body
 * accepts the proposal exactly as stored.
 * Mirrors backend `CalendarProposalAcceptRequest`.
 */
export type CalendarProposalAcceptRequest = components["schemas"]["CalendarProposalAcceptRequest"];

/** One row from calendar_action_log, enriched with source provenance. */
export type CalendarAuditEntry = components["schemas"]["CalendarAuditEntry"];

/** Query parameters for GET /api/calendar/workspace/audit. */
export interface CalendarAuditParams {
  limit?: number;
  offset?: number;
  butler?: string;
}

/** Source-level freshness metadata for workspace rendering. */
export type CalendarWorkspaceSourceFreshness = components["schemas"]["CalendarWorkspaceSourceFreshness"];

/** Response payload for GET /api/calendar/workspace. */
export type CalendarWorkspaceReadResponse = components["schemas"]["CalendarWorkspaceReadResponse"];

/** A single scheduling problem detected in the scanned window. */
export type ConflictIssue = components["schemas"]["ConflictIssue"];

/**
 * Response payload for GET /api/calendar/workspace/conflicts.
 *
 * `issues_available` is `false` only in degraded mode (the scan could not run);
 * the FE renders no banner and adds no amber edges in that silent mode. An empty
 * `issues` list with `issues_available=true` genuinely means a clean window.
 */
export type ConflictScanResponse = components["schemas"]["ConflictScanResponse"];

/** Query params for GET /api/calendar/workspace/conflicts. */
export interface ConflictScanParams {
  start: string;
  end: string;
  timezone?: string;
  butler_name?: string;
  back_to_back_gap_minutes?: number;
  overloaded_day_hours?: number;
}

export type CalendarInvitationEntry = components["schemas"]["CalendarInvitationEntry"];

export type CalendarResponseRequest = components["schemas"]["CalendarRespondRequest"];

export type CalendarResponseReceipt = components["schemas"]["CalendarRespondResponse"];

export interface CalendarInvitationsParams {
  start: string;
  end: string;
  timezone?: string;
  limit?: number;
  cursor?: string;
}

/** All of one specialist butler's overlay entries for the day, bucketed by kind. */
export type DayBriefingButlerGroup = components["schemas"]["DayBriefingButlerGroup"];

/** Kind of an active owner commitment surfaced on the meeting-prep rail. */
export type CalendarPrepCommitmentKind =
  | "promise"
  | "waiting_for"
  | "follow_up"
  | "obligation"
  | "decision";

/** Direction of the obligation represented by a prep-rail commitment. */
export type CalendarPrepCommitmentDirection = "owner_to_other" | "other_to_owner" | "self";

/** An active owner commitment contributed to a meeting-prep attendee. */
export type CalendarPrepCommitment = components["schemas"]["CalendarPrepCommitment"];

/**
 * Precomputed prep context for one resolved attendee of a selected event.
 *
 * All fields are drawn from contribution-sourced cached data (the relationship
 * butler's deterministic prep job). `dunbar_tier` is the relationship
 * letter-mark source (the FE maps the integer tier to its letter via
 * {@link tierLabel}); `notes` are durable CRM notes; `last_met` /
 * `last_met_event` come from the most recent prior co-attended event;
 * `message_context` is the email/message-owning butlers' per-attendee
 * contribution (empty until a message-context job — bu-tmtpb — has run).
 * `commitments` are active owner-condition commitments contributed by the
 * relationship butler (empty for legacy envelopes).
 */
export type CalendarPrepAttendee = components["schemas"]["CalendarPrepAttendee"];

/** Writable user-lane calendar descriptor. */
export type CalendarWorkspaceWritableCalendar = components["schemas"]["CalendarWorkspaceWritableCalendar"];

/** Response payload for GET /api/calendar/workspace/meta. */
export type CalendarWorkspaceMetaResponse = components["schemas"]["CalendarWorkspaceMetaResponse"];

/** One connected Google account with its calendar connector health. */
export type CalendarAccountEntry = components["schemas"]["CalendarAccountEntry"];

/** Request payload for POST /api/calendar/sources. */
export type CalendarSourceToggleRequest = components["schemas"]["CalendarSourceToggleRequest"];

/** Response payload for POST /api/calendar/sources. */
export type CalendarSourceToggleResponse = components["schemas"]["CalendarSourceToggleResponse"];

/** Request payload for PUT /api/calendar/workspace/primary. */
export type SetPrimaryCalendarRequest = components["schemas"]["SetPrimaryCalendarRequest"];

/** Computed-status facet values accepted by GET /api/calendar/workspace. */
export type CalendarWorkspaceStatusFacet =
  "active" | "paused" | "cancelled" | "error" | "completed";

/** Query parameters for GET /api/calendar/workspace. */
export interface CalendarWorkspaceParams {
  view: CalendarWorkspaceQueryView;
  start: string;
  end: string;
  timezone?: string;
  butlers?: string[];
  sources?: string[];
  /** Server-side computed-status facet. */
  status?: CalendarWorkspaceStatusFacet;
  /** Server-side computed source-type facet. */
  source_type?: UnifiedCalendarSourceType;
  /** Server-side editable (writable-source) facet. */
  editable?: boolean;
  /** Max entries per page (keyset pagination). */
  limit?: number;
  /** Opaque keyset cursor from a prior page's `next_cursor`. */
  cursor?: string;
}

/** Query parameters for GET /api/calendar/workspace/search. */
export interface CalendarWorkspaceSearchParams {
  q: string;
  view: CalendarWorkspaceView;
  timezone?: string;
  butlers?: string[];
  sources?: string[];
  limit?: number;
}

/** Request payload for POST /api/calendar/workspace/sync. */
export type CalendarWorkspaceSyncRequest = components["schemas"]["CalendarWorkspaceSyncRequest"];

/** The only user-view calendar mutation the dashboard issues (operator view). */
export type CalendarWorkspaceUserMutationAction = "create";

/**
 * Request payload for POST /api/calendar/workspace/user-events (create only).
 */
export type CalendarWorkspaceUserMutationRequest = components["schemas"]["CalendarWorkspaceUserMutationRequest"];

/** Request payload for POST /api/calendar/workspace/butler-events. */
export type CalendarWorkspaceButlerMutationRequest = components["schemas"]["CalendarWorkspaceButlerMutationRequest"];

/** Request payload for POST /api/calendar/workspace/parse-quick-add. */
export type QuickAddParseRequest = components["schemas"]["QuickAddParseRequest"];

/** A parsed draft event — advisory only, never auto-written. */
export type QuickAddDraft = components["schemas"]["QuickAddDraft"];

/**
 * Request payload for POST /api/calendar/workspace/butler-events/preview.
 *
 * Dry-runs a draft butler event's recurrence expansion. Exactly one of
 * ``rrule`` or ``cron`` must be supplied; nothing is persisted.
 */
export type CalendarWorkspaceButlerEventPreviewRequest = components["schemas"]["CalendarButlerEventPreviewRequest"];

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** A key-value state entry from a butler's state store.
 *
 * ``value`` can be any JSON-serialisable type (object, array, scalar, or null)
 * because the underlying JSONB column places no shape restrictions on stored
 * values.
 */
export type StateEntry = components["schemas"]["StateEntry"];

/** Request body for setting a state value.
 *
 * ``value`` accepts any JSON-serialisable type, matching the same contract as
 * ``StateEntry.value``.
 */
export type StateSetRequest = components["schemas"]["StateSetRequest"];

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

/**
 * A single entry from the ``public.audit_log`` primitive table (core_092).
 * Matches the backend ``AuditLogEntry`` Pydantic model exactly.
 */
export type AuditLogEntry = components["schemas"]["AuditLogEntry"];

/** Query parameters for the audit log endpoint (GET /api/audit-log). */
export interface AuditLogParams {
  offset?: number;
  limit?: number;
  /** Filter by actor (exact match). */
  actor?: string;
  /** Filter by action verb (exact match). */
  action?: string;
  /** ISO 8601 lower bound on ts. */
  since?: string;
  /** Owner-timezone calendar-day or ISO 8601 lower bound on ts. */
  from_date?: string;
  /** Owner-timezone calendar-day or ISO 8601 inclusive upper bound on ts. */
  to_date?: string;
  /** Filter by canonical credential key (e.g. "u:google"). Forwarded as ?key= to GET /api/audit-log. */
  key?: string;
  /** Filter by outcome (exact match), e.g. "success" | "error". */
  result?: string;
  /** Filter preset. "privileged" selects consequence-bearing actions and explicit errors. */
  kind?: string;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** A skill available to a butler. */
export type ButlerSkill = components["schemas"]["SkillInfo"];

// ---------------------------------------------------------------------------
// MCP debugging
// ---------------------------------------------------------------------------

/** A tool exposed by a butler's MCP server. */
export type ButlerMcpTool = components["schemas"]["MCPToolInfo"];

/** Request body for calling an MCP tool. */
export type ButlerMcpToolCallRequest = components["schemas"]["MCPToolCallRequest"];

/** Response from calling an MCP tool. */
export type ButlerMcpToolCallResponse = components["schemas"]["MCPToolCallResponse"];

// ---------------------------------------------------------------------------
// Relationship / CRM
// ---------------------------------------------------------------------------

/** A label that can be attached to contacts or groups. */
export type Label = components["schemas"]["Label"];

/** A single contact_info entry (phone, email, address, etc.).
 * When secured=true and value is null, the value is masked.
 * Use GET /relationship/entities/{entityId}/secrets/{infoId} to retrieve the real value.
 */
export type ContactInfoEntry = components["schemas"]["ContactInfoEntry"];

/** A contact group. */
export type Group = components["schemas"]["Group"];

/** One member entity of a group, for the Circles lens roster (bu-5umz4). */
export type GroupMember = components["schemas"]["GroupMember"];

/** Query parameters for the groups list endpoint. */
export interface GroupParams {
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** A health measurement record. */
export type Measurement = components["schemas"]["Measurement"];

/** A medication record. */
export type Medication = components["schemas"]["Medication"];

/**
 * Aggregated dose-adherence stats for a medication
 * (GET /health/medications/{id}/adherence).
 *
 * `adherence_rate` is the frequency-expected percentage of non-skipped doses
 * (server-computed), or `null` when no doses have been logged. This is the
 * authoritative adherence figure — never recompute it as a naive client-side
 * taken/total ratio.
 */
export type MedicationAdherence = components["schemas"]["MedicationAdherenceResponse"];

/**
 * Request body for logging a dose (POST /health/medications/{id}/doses).
 * `taken_at` defaults to now when omitted; set `skipped` to record a miss.
 */
export type DoseLogRequest = components["schemas"]["DoseLogRequest"];

/** A health condition record. */
export type HealthCondition = components["schemas"]["Condition"];

/** A symptom record. */
export type Symptom = components["schemas"]["Symptom"];

/** A meal record. */
export type Meal = components["schemas"]["Meal"];

/** A health research note. */
export type HealthResearch = components["schemas"]["Research"];

// ---------------------------------------------------------------------------
// General / Switchboard
// ---------------------------------------------------------------------------

/** A collection in the General butler entity store. */
export type GeneralCollection = components["schemas"]["Collection"];

/** An entity in the General butler entity store. */
export type GeneralEntity = components["schemas"]["Entity"];

// ---------------------------------------------------------------------------
// Health — new endpoints (bu-iuol4.24)
// ---------------------------------------------------------------------------

/**
 * A single latest-measurement entry as returned by
 * GET /api/health/measurements/latest?types=X,Y.
 * `null` means no measurement of that type has been recorded yet.
 */
export type LatestMeasurementEntry = components["schemas"]["LatestMeasurementEntry"];

/**
 * Response shape for GET /api/health/measurements/latest?types=X,Y,Z.
 * Keys are measurement type slugs; values are the latest entry or null.
 */
/** @public knip mis-traces this type's import (used by a live consumer); remove when bu-9jvhm fixes the tracing gap. */
export type MeasurementsLatestResponse = components["schemas"]["LatestMeasurementsResponse"];

/** A single stage within a sleep session. */
type SleepStageWire = components["schemas"]["SleepStage"];
/** Existing chart projection; the wire carries kind/minutes and no stage timestamp. */
export type SleepStage = { stage: SleepStageWire["kind"]; duration_minutes: SleepStageWire["minutes"]; start_time: null };

/**
 * Response shape for GET /api/health/measurements/sleep/latest.
 * `null` means no sleep session has been recorded yet.
 */
export interface SleepLatestResponse {
  session_date: string | null;
  total_minutes: number | null;
  stages: SleepStage[] | null;
  source: string | null;
}

/** A single data source as returned by GET /api/health/measurements/sources. */
export type MeasurementSource = components["schemas"]["MeasurementSource"];

/** A data-derived measurement predicate the Health API has observed. */
export type MeasurementTypeInfo = components["schemas"]["MeasurementTypeInfo"];

/** Query parameters for measurement endpoints. */
export interface MeasurementParams {
  type?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/** Allowed lookback windows for the measurement trend endpoint (days). */
export type MeasurementTrendWindowDays = 1 | 7 | 14 | 30 | 90;

/** Query parameters for GET /health/measurements/trend. */
export interface MeasurementTrendParams {
  /** Measurement type (e.g. "weight", "blood_pressure"). */
  type: string;
  /** Lookback window in days. One of 1, 7, 14, 30, 90. Defaults to 14. */
  window_days?: MeasurementTrendWindowDays;
  /** Bucket granularity. Defaults to "daily". */
  bucket?: "hourly" | "daily";
}

/** Response shape for GET /health/measurements/trend. */

/** @public knip mis-traces this type's inline import() usage (used by a live consumer); remove when bu-9jvhm fixes the tracing gap. */
export type MeasurementSourcesResponse = components["schemas"]["MeasurementSourcesResponse"];

export type ExpectedSignal = components["schemas"]["ExpectedSignalResponse"];

export type HealthBaseline = components["schemas"]["BaselineResponse"];

export type HealthBaselinesResponse = components["schemas"]["BaselinesResponse"];

export type ExpectedSignalsResponse = components["schemas"]["ExpectedSignalsResponse"];
/** @public knip mis-traces this type's import (used by a live consumer); remove when bu-9jvhm fixes the tracing gap. */
export type MeasurementTrendResponse = components["schemas"]["TrendResponse"];

/** The five measurement types the Health butler recognizes for direct CRUD. */
export type MeasurementType =
  "weight" | "blood_pressure" | "heart_rate" | "blood_sugar" | "temperature";

/**
 * Request body for logging a measurement (POST /health/measurements).
 *
 * Measurements are temporal facts: `measured_at` is the reading time and
 * multiple readings coexist by design (no supersession). `value` is JSONB and
 * may be a scalar wrapped as `{ value: 165 }` or a compound dict such as
 * `{ systolic: 120, diastolic: 80 }`.
 */
export type MeasurementCreateRequest = components["schemas"]["MeasurementCreateRequest"];

/**
 * Request body for updating a measurement (PUT /health/measurements/{id}).
 * All fields optional; only supplied fields are applied to the existing entry.
 * Changing `type` rewrites the underlying `measurement_{type}` predicate.
 */
export type MeasurementUpdateRequest = components["schemas"]["MeasurementUpdateRequest"];

/** Query parameters for medication endpoints. */
export interface MedicationParams {
  active?: boolean;
  offset?: number;
  limit?: number;
}

/** Request body for creating a medication (POST /health/medications). */
export type MedicationCreateRequest = components["schemas"]["MedicationCreateRequest"];

/**
 * Request body for updating a medication (PUT /health/medications/{id}).
 * All fields optional; only supplied fields are merged into the existing record.
 */
export type MedicationUpdateRequest = components["schemas"]["MedicationUpdateRequest"];

/** Request body for creating a condition (POST /health/conditions). */
export type ConditionCreateRequest = components["schemas"]["ConditionCreateRequest"];

/**
 * Request body for updating a condition (PUT /health/conditions/{id}).
 * All fields optional; only supplied fields are merged into the existing record.
 */
export type ConditionUpdateRequest = components["schemas"]["ConditionUpdateRequest"];

/** Query parameters for symptom endpoints. */
export interface SymptomParams {
  name?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/**
 * Request body for logging a symptom (POST /health/symptoms).
 *
 * Symptoms are temporal facts: `occurred_at` is the occurrence time and
 * multiple entries coexist by design (no supersession). `severity` is 1-10.
 */
export type SymptomCreateRequest = components["schemas"]["SymptomCreateRequest"];

/**
 * Request body for updating a symptom (PUT /health/symptoms/{id}).
 * All fields optional; only supplied fields are applied to the existing entry.
 */
export type SymptomUpdateRequest = components["schemas"]["SymptomUpdateRequest"];

/** Query parameters for meal endpoints. */
export interface MealParams {
  type?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/**
 * Request body for logging a meal (POST /health/meals).
 *
 * Meals are temporal facts: `eaten_at` is the eating time and multiple entries
 * coexist by design (no supersession). `type` is one of breakfast/lunch/
 * dinner/snack.
 */
export type MealCreateRequest = components["schemas"]["MealCreateRequest"];

/**
 * Request body for updating a meal (PUT /health/meals/{id}).
 * All fields optional; only supplied fields are applied to the existing entry.
 */
export type MealUpdateRequest = components["schemas"]["MealUpdateRequest"];

/** Query parameters for GET /health/nutrition/summary. */
export interface NutritionSummaryParams {
  /** Window start (ISO-8601 date or datetime, inclusive). */
  start: string;
  /** Window end (ISO-8601 date or datetime, inclusive). */
  end: string;
}

/**
 * Response for GET /api/health/nutrition/summary.
 *
 * Aggregates meal_* facts with nutrition metadata over the requested window.
 * Meals without nutrition data are excluded. days is the inclusive span used
 * to compute daily averages (minimum 1).
 */
/** @public knip mis-traces this type's import (used by a live consumer); remove when bu-9jvhm fixes the tracing gap. */
export type NutritionSummary = components["schemas"]["NutritionSummaryResponse"];

/** Query parameters for research endpoints. */
export interface ResearchParams {
  q?: string;
  tag?: string;
  offset?: number;
  limit?: number;
}

/**
 * Request body for creating a research note (POST /health/research).
 *
 * Research notes are property facts (like conditions, NOT temporal): a note with
 * the same title supersedes its predecessor. `condition_id`, when supplied, must
 * reference an existing condition.
 */
export type ResearchCreateRequest = components["schemas"]["ResearchCreateRequest"];

/**
 * Request body for updating a research note (PUT /health/research/{id}).
 * All fields optional; only supplied fields are merged into the existing record.
 */
export type ResearchUpdateRequest = components["schemas"]["ResearchUpdateRequest"];

/** A routing log entry from the Switchboard. */
export type RoutingEntry = components["schemas"]["RoutingEntry"];

/**
 * A butler registry entry from the Switchboard.
 *
 * Mirrors `RegistryEntry` in `roster/switchboard/api/models.py` -- keep in sync.
 *
 * `eligibility_state` is the raw stored `butler_registry` column: reconciled
 * lazily on routing calls, so it can sit stale forever for a butler nobody
 * has routed to recently. `derived_eligibility_state` is the current-liveness
 * read, recomputed server-side at request time from `last_seen_at` +
 * `liveness_ttl_seconds` (bu-p7dx8). Surfaces showing CURRENT liveness/health
 * should read `derived_eligibility_state`; only the immediate-write quarantine
 * case (e.g. pause/resume state) should read `eligibility_state`.
 */
export type RegistryEntry = components["schemas"]["RegistryEntry"];

/** Response from setting a butler's eligibility state. */
export type SetEligibilityResponse = components["schemas"]["SetEligibilityResponse"];

/** Query parameters for routing log. */
export interface RoutingLogParams {
  source_butler?: string;
  target_butler?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

/** An episode from the Eden memory tier. */
export type Episode = components["schemas"]["Episode"];

/** A consolidated fact from the mid-term memory tier. */
export type Fact = components["schemas"]["Fact"];

/** A behavioral rule from the long-term memory tier. */
export type MemoryRule = components["schemas"]["Rule"];

/** Aggregated statistics across all memory tiers. */
export type MemoryStats = components["schemas"]["MemoryStats"];

/** Fleet coverage state for the additive graph-health read model. */
export type GraphHealthCoverage = components["schemas"]["GraphHealthCoverage"];

/**
 * Pagination metadata for the memory list routes (episodes, facts, rules,
 * inspect). Each fans out across butler pools and, when one pool errors, drops
 * it and names it in `pools_failed` rather than failing the request, so a page
 * (including an empty one) may be incomplete. Absent or empty means every
 * queried pool answered.
 */
export interface MemoryPaginationMeta extends PaginationMeta {
  /** Names of memory pools whose query failed and were dropped from the page and total. */
  pools_failed?: string[];
}

/** Paginated memory list response carrying the degraded-pool meta. */
export interface MemoryPaginatedResponse<T> {
  data: T[];
  meta: MemoryPaginationMeta;
}

/**
 * Metadata for GET /api/memory/stats. Extends the base bag with the
 * degraded-envelope flag the backend emits when the per-pool fan-out drops one
 * or more memory pools (memory.py::get_stats -> `ApiMeta(pools_failed=...)`).
 * Mirrors the fleet-wide `meta.<flag>` degraded convention (see CLAUDE.md API
 * Conventions). Absent or empty means every queried pool answered; a non-empty
 * list means the aggregate totals undercount and must NOT read as an all-clear.
 */
export type MemoryStatsMeta = components["schemas"]["MemoryStatsMeta"];

/** A memory retention policy row. */
export type MemoryRetentionPolicy = components["schemas"]["MemoryRetentionPolicy"];

/** One entry in a bulk PUT retention policy request. */
export type UpdateRetentionPolicyEntry = components["schemas"]["UpdateRetentionPolicyEntry"];

/** Bulk update request body for PUT /api/memory/retention-policies. */
export type UpdateRetentionPoliciesRequest = components["schemas"]["UpdateRetentionPoliciesRequest"];

/** A compaction log entry. */
export type CompactionLogEntry = components["schemas"]["CompactionLogEntry"];

/** A single memory inspect search result. */
export type MemoryInspectResult = components["schemas"]["MemoryInspectResult"];

/** Query parameters for GET /api/memory/inspect. */
export interface MemoryInspectParams {
  q?: string;
  kind?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for episode list endpoints. */
export interface EpisodeParams {
  butler?: string;
  consolidated?: boolean;
  /**
   * Consolidation lifecycle filter (pending|consolidated|failed|dead_letter).
   * Maps to the GET /memory/episodes `status` enum filter; takes precedence
   * over the legacy `consolidated` bool. Drives the daybook filter pills.
   */
  status?: "pending" | "failed" | "consolidated" | "dead_letter";
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for fact list endpoints. */
export interface FactParams {
  q?: string;
  scope?: string;
  validity?: string;
  permanence?: string;
  subject?: string;
  /**
   * Minimum importance (inclusive) filter — GET /api/memory/facts supports
   * `importance_min` (bu-awo8k.7 / #2185). Used by the attention rail to count
   * high-importance fading facts (`validity=fading & importance_min=8`).
   */
  importance_min?: number;
  /**
   * Source-episode provenance filter — GET /api/memory/facts supports
   * `source_episode_id` (bu-awo8k.6 / #2181). Used by the episode detail page
   * to list the facts derived from that episode (the reverse provenance link).
   */
  source_episode_id?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for rule list endpoints. */
export interface RuleParams {
  /** Restrict the register to unendorsed held guidance. */
  held?: boolean;
  q?: string;
  scope?: string;
  maturity?: string;
  /**
   * Filter by forgotten (soft-deleted) status. Omit (default) to exclude
   * forgotten rules — the API's default is "live rules only". Pass `true`
   * to audit forgotten rules explicitly.
   */
  forgotten?: boolean;
  offset?: number;
  limit?: number;
}

/** A single entity_info row (credentials, identifiers, etc.). */
export type EntityInfoEntry = components["schemas"]["butlers__api__models__memory__EntityInfoEntry"];

/** Request body for creating an entity_info entry. */
export type CreateEntityInfoRequest = components["schemas"]["CreateEntityInfoRequest"];

/** Request body for updating entity core fields. */
export type UpdateEntityRequest = components["schemas"]["UpdateEntityRequest"];

/** Full entity detail including recent facts and linked contact info. */
export type EntityDetail = components["schemas"]["butlers__api__models__memory__EntityDetail"];

export type EntityRebindReceipt = components["schemas"]["EntityRebindReceipt"];

/** Query parameters for entity detail endpoints. */
export interface EntityDetailParams {
  facts_offset?: number;
  facts_limit?: number;
}

/** Query parameters for entity list endpoints. */
export interface EntityParams {
  q?: string;
  entity_type?: string;
  unidentified?: boolean;
  archived?: boolean;
  offset?: number;
  limit?: number;
}

export type ApprovalDeliveryTruth = components["schemas"]["ApprovalDeliveryTruth"];

export type ApprovalAction = components["schemas"]["ApprovalAction"];

/** Compact summary for GET /api/approvals flat-list endpoint. */
export type ApprovalSummary = components["schemas"]["ApprovalSummary"];

/** Full dossier for GET /api/approvals/{id}. */
export type DisplayedApprovalCall = components["schemas"]["DisplayedApprovalCall"];

export type ApprovalCallPreview = components["schemas"]["ApprovalCallPreview"];

export type ApprovalDetail = components["schemas"]["ApprovalDetail"];

export type ApprovalAbandonRequest = components["schemas"]["ApprovalAbandonRequest"];

/** Quiet-hours policy singleton. */
export type ApprovalsPolicy = components["schemas"]["ApprovalsPolicy"];

export type ApprovalApproveRequest = components["schemas"]["ApprovalApproveRequest"];

export type ApprovalDenyRequest = components["schemas"]["ApprovalDenyRequest"];

export type ApprovalDeferRequest = components["schemas"]["ApprovalDeferRequest"];

export type ApprovalRule = components["schemas"]["ApprovalRule"];

/** A configured approval gate, including the rules that narrow it. */
export type ApprovalGatedTool = components["schemas"]["ApprovalGatedTool"];

export type ApprovalMetrics = components["schemas"]["ApprovalMetrics"];

/** GET /api/approvals/metrics response with per-family availability. */
export type ApprovalMetricsResponse = components["schemas"]["ApiResponseWithMeta_ApprovalMetrics_ApprovalMetricsMeta_"];

export interface ApprovalActionParams {
  tool_name?: string;
  status?: string;
  butler?: string;
  offset?: number;
  limit?: number;
}

export interface ApprovalRuleParams {
  tool_name?: string;
  active?: boolean;
  butler?: string;
  offset?: number;
  limit?: number;
}

export type ApprovalRuleCreateRequest = components["schemas"]["ApprovalRuleCreateRequest"];

export type ApprovalRuleFromActionRequest = components["schemas"]["ApprovalRuleFromActionRequest"];

export type AutonomySuggestion = components["schemas"]["AutonomySuggestion"];

export interface AutonomySuggestionParams {
  status?: string;
  suggestion_type?: string;
  limit?: number;
  offset?: number;
}

export type AutonomySuggestionDismissRequest = components["schemas"]["AutonomySuggestionDismissRequest"];

export type GoogleAccount = components["schemas"]["GoogleAccountResponse"];

export type GoogleCredentialStatusResponse = components["schemas"]["GoogleCredentialStatusResponse"];

export type UpsertAppCredentialsRequest = components["schemas"]["UpsertAppCredentialsRequest"];

export type CLIAuthSessionResponse = components["schemas"]["CLIAuthSessionResponse"];

// ---------------------------------------------------------------------------
// Generic secrets management types
// ---------------------------------------------------------------------------

/** Metadata for a single secret. Values are never exposed in responses. */
export type SecretEntry = components["schemas"]["SecretEntry"];

/** Known secret categories for grouping. */
export type SecretCategory =
  "core" | "telegram" | "email" | "google" | "gemini" | "general";

/** Predefined secret key templates with descriptions and auto-detected categories. */
/** @public knip mis-traces this type's import (used by a live consumer); remove when bu-9jvhm fixes the tracing gap. */
export interface SecretTemplate {
  key: string;
  description: string;
  category: SecretCategory;
}

// ---------------------------------------------------------------------------
// Connector statistics and analytics types (docs/connectors/statistics.md)
// ---------------------------------------------------------------------------

export type IngestionPeriod = "24h" | "7d" | "30d";

/** Today's ingestion summary attached to a connector list entry. */
export type ConnectorDaySummary = components["schemas"]["ConnectorSummaryToday"] & { uptime_pct?: number | null };

/**
 * Per-device liveness entry for a multi-device connector_type (e.g. OwnTracks,
 * where several physical devices post through one shared connector_type and
 * connector_registry only tracks a single heartbeat identity for the whole
 * connector). Sourced from public.ingestion_events.source_sender_identity.
 */
export type ConnectorDeviceLiveness = components["schemas"]["ConnectorDeviceLiveness"];

/**
 * One persisted checkpoint cursor belonging to a parent connector (bu-6jv4m.11).
 *
 * connector_registry has two producers: the heartbeat tool, which registers an
 * executable connector process, and cursor_store, which persists a restart-safe
 * cursor per stream. A connector whose streams advance independently (Google
 * Health keeps one cursor per account AND per resource) therefore accumulates
 * registry rows that never heartbeat. These are STORAGE records: they belong
 * under their parent runtime instance and carry no liveness, state, or health
 * of their own — that authority is the parent's alone.
 */
export type ConnectorCheckpointRecord = components["schemas"]["ConnectorCheckpointRecord"];

/** A connector with current liveness and today's stats (GET /api/ingestion/connectors/summaries). */
type ConnectorSummaryWire = components["schemas"]["ConnectorSummary"];
/** Both the summary endpoint and the existing detail projection feed this view. */
export type ConnectorSummary = Pick<ConnectorSummaryWire,
  "connector_type" | "endpoint_identity" | "liveness" | "state" |
  "error_message" | "version" | "uptime_s" | "last_heartbeat_at" |
  "first_seen_at" | "hourly_events"> &
  Partial<Omit<ConnectorSummaryWire, "today">> & {
    /** Existing detail projection retains unavailable uptime separately. */
    today?: (NonNullable<ConnectorSummaryWire["today"]> & { uptime_pct?: number | null }) | null;
  };

/** One OAuth scope entry from connector-oauth-scope-surface backend. */
export type ConnectorScopeEntry = components["schemas"]["ConnectorScopeRow"];

/** Auth block from connector-oauth-scope-surface backend. */
export type ConnectorAuthBlock = components["schemas"]["ConnectorAuthBlock"];

/** Client view model projected from the canonical flat connector-detail response. */
type ConnectorDetailWire = components["schemas"]["ConnectorDetailEntry"];
export type ConnectorDetail = ConnectorSummary & Pick<ConnectorDetailWire,
  "instance_id" | "registered_via" | "settings" | "auth" | "scopes"> & {
  checkpoint: { cursor: ConnectorDetailWire["checkpoint_cursor"]; updated_at: ConnectorDetailWire["checkpoint_updated_at"] } | null;
  counters: {
    messages_ingested: ConnectorDetailWire["counter_messages_ingested"];
    messages_failed: ConnectorDetailWire["counter_messages_failed"];
    source_api_calls: ConnectorDetailWire["counter_source_api_calls"];
    checkpoint_saves: ConnectorDetailWire["counter_checkpoint_saves"];
    dedupe_accepted: ConnectorDetailWire["counter_dedupe_accepted"];
  } | null;
};

export interface ConnectorCheckpoint {
  cursor: string | null;
  updated_at: string | null;
}

export interface ConnectorCounters {
  messages_ingested: number;
  messages_failed: number;
  source_api_calls: number;
  checkpoint_saves: number;
  dedupe_accepted: number;
}

/** One time bucket in a stats timeseries. */
export interface ConnectorStatsBucket {
  bucket: string;
  messages_ingested: number;
  messages_failed: number;
  /**
   * Skip-routed volume for this bucket (bu-c48im), sourced from
   * connectors.filtered_events. A DISTINCT series — never summed into
   * messages_ingested — so a self-persisting connector's skip volume is visible
   * on the detail histogram. 0 when the connector self-persists no skips.
   */
  messages_filtered: number;
  healthy_count: number;
  degraded_count: number;
  error_count: number;
}

export interface ConnectorStatsSummary {
  messages_ingested: number;
  messages_failed: number;
  error_rate_pct: number;
  uptime_pct: number | null;
  avg_messages_per_hour: number;
}

/** Client view model projected from canonical connector stats rows. */
export interface ConnectorStats {
  connector_type: string;
  endpoint_identity: string;
  period: IngestionPeriod;
  summary: ConnectorStatsSummary;
  timeseries: ConnectorStatsBucket[];
  /**
   * DB-source health flag (bu-c48im), threaded from the response
   * `meta.hourly_events_available`. `false` only when the backend's combined
   * ingested+filtered query genuinely failed — in that case the histogram falls
   * back to all-zero and must surface a degraded note rather than render as an
   * honest quiet window. Absent/`true` means the series is trustworthy.
   */
  hourly_events_available: boolean;
}

/**
 * Pipeline funnel statistics (GET /api/ingestion/pipeline?window=24h).
 * Sourced from Prometheus via PromQL with 60s TTL cache.
 * aggregates_available=false means Prometheus is unreachable — all numeric
 * funnel fields are zero in that case; scrub and backlog flags are independent.
 */
export type PipelineStats = Pick<components["schemas"]["PipelineStatsResponse"], "window" | "aggregates_available" | "ingested" | "filtered" | "errored" | "routed_by_butler" | "spark24h" | "rate1h" | "routed_pct" | "filtered24h" | "backlog_available" | "failed_total" | "replay_pending_total" | "written_off_total"> & Partial<Pick<components["schemas"]["PipelineStatsResponse"], "bearer_scrub_counts" | "bearer_scrub_available">>;

/** One measured connector-to-butler route in the fanout distribution. */
export type ConnectorFanoutRow = components["schemas"]["FanoutRow"];

/** GET /api/switchboard/ingestion/fanout?period=7d. */
export type ConnectorFanoutResponse = components["schemas"]["ApiResponseWithMeta_list_FanoutRow__ConnectorFanoutMeta_"];

/**
 * Connector list (GET /api/ingestion/connectors/summaries).
 *
 * Every field on this response is DB-sourced — this endpoint has no Prometheus
 * dependency and therefore carries no `aggregates_available` flag. Its only
 * degraded-mode flags gate the DB queries that can independently fail. A
 * failed primary registry query returns an empty connector list with
 * `connector_registry_available=false`; the other flags cover the secondary
 * hourly, device-liveness, and OwnTracks-cadence queries.
 */
export type ConnectorSummariesResponse = components["schemas"]["ConnectorSummaryResponse"];

/**
 * Result of POST /api/ingestion/connectors/{type}/{identity}/archive
 * (and the unarchive variant). Audit-only soft-archive (bu-33dm2).
 */
export interface ConnectorArchiveResult {
  connector_type: string;
  endpoint_identity: string;
  archived: boolean;
  archived_at: string | null;
}

/** A connector profile from the available-discovery catalog.
 *
 * Returned by GET /api/ingestion/connectors/available.
 * Represents connectors the framework can deploy, regardless of whether
 * any instance is currently registered in connector_registry.
 */
export type ConnectorProfile = components["schemas"]["ConnectorProfile"];

// ---------------------------------------------------------------------------
// Ingestion event lineage types (GET /api/switchboard/ingestion/events/*)
// ---------------------------------------------------------------------------

/**
 * All possible lifecycle statuses for an ingestion event from the unified timeline.
 * - ingested: processed successfully
 * - skipped: stored but deliberately not dispatched (matched a `skip` triage rule)
 * - filtered: dropped by a rule
 * - error: processing failed
 * - failed: routing failed after the event was already ingested (see
 *   ``ingestion_event_mark_failed``); replayable straight back to `ingested`
 * - replay_pending: replay requested, awaiting processing
 * - replay_complete: replay succeeded
 * - replay_failed: replay attempt failed
 */
export type IngestionEventStatus =
  | "ingested"
  | "skipped"
  | "filtered"
  | "error"
  | "failed"
  | "replay_pending"
  | "replay_complete"
  | "replay_failed";

/** Compact per-session summary embedded in a list row (dispatch-ticks cell). */
export type IngestionEventListSessionSummary = components["schemas"]["IngestionEventListSessionSummary"];

/** One ingestion event from shared.ingestion_events (list view). */
export type IngestionEventSummary = components["schemas"]["IngestionEventSummary"];

/** One butler session spawned in response to an ingestion event. */
export type IngestionEventSession = components["schemas"]["IngestionEventSession"];

/** Query parameters for GET /api/ingestion/events (cursor-paginated). */
export interface IngestionEventsParams {
  limit?: number;
  /** Opaque cursor from the previous page's next_cursor. Omit for first page. */
  cursor?: string;
  /** Comma-separated channel values (e.g. "email,telegram"). */
  channels?: string;
  /** Filter by a single event status. Ignored when `statuses` is set. Omit to return all events. */
  status?: IngestionEventStatus;
  /** Comma-separated status values to include (e.g. "ingested,error"). Takes precedence over `status`. */
  statuses?: string;
  /**
   * Freetext search (ILIKE %q%) against source_channel, source_sender_identity,
   * and error_detail. Server-side; safe against injection.
   */
  q?: string;
  /** ISO-8601 inclusive lower bound on received_at. Omit for no lower bound. */
  from?: string;
  /** ISO-8601 exclusive upper bound on received_at. Omit for no upper bound. */
  to?: string;
  /**
   * Sort order. Omit or "recent" for newest-first (keyset cursor).
   * "cost" for highest-cost-first (offset cursor, NULLS LAST).
   * Do not mix cursor values across sort modes — start a fresh first page when switching.
   */
  sort?: "recent" | "cost";
  /**
   * Filter to events with at least one linked butler session carrying this
   * trace_id — the drill-down spine (server resolves trace_id -> matching
   * session request_ids via a cross-butler fan-out, then filters server-side).
   * A trace_id matching no session returns an empty page, not an error.
   */
  trace_id?: string;
}

/** Time window boundaries for GET /api/ingestion/rollup. */
export interface IngestionWindowRollupParams {
  /** ISO-8601 lower bound on received_at (inclusive). */
  from?: string;
  /** ISO-8601 upper bound on received_at (exclusive). */
  to?: string;
  /** Comma-separated source_channel values (e.g. "email,telegram"). */
  channels?: string;
  /** Comma-separated status values (e.g. "ingested,error"). */
  statuses?: string;
  /**
   * Freetext search (ILIKE %q%) against source_channel, source_sender_identity,
   * and error_detail.
   */
  q?: string;
  /**
   * Filter to events with at least one linked butler session carrying this
   * trace_id — same drill-down spine as IngestionEventsParams.trace_id. The
   * server resolves trace_id -> matching event ids before aggregating, so a
   * trace-scoped rollup band stays consistent with the trace-scoped ledger.
   * The server ignores any `from`/`to` passed alongside `trace_id` and
   * drops the window bound entirely (bu-1f81d) — omit them here too.
   */
  trace_id?: string;
}

/** Response from GET /api/ingestion/rollup. */
export type IngestionWindowRollup = components["schemas"]["IngestionWindowRollup"];

/** Bucket granularity accepted by GET /api/ingestion/events/histogram. */
export type IngestionHistogramBucketSize = "1m" | "5m" | "1h";

/** Query parameters for GET /api/ingestion/events/histogram. */
export interface IngestionHistogramParams {
  /**
   * ISO-8601 inclusive lower bound on received_at. Required unless
   * `trace_id` is set, in which case the server auto-widens to the trace's
   * own event bounds instead and ignores any explicit from/to (bu-1f81d).
   */
  from?: string;
  /**
   * ISO-8601 exclusive upper bound on received_at. Required unless
   * `trace_id` is set (see `from`).
   */
  to?: string;
  /**
   * Bucket granularity. Defaults to "1m" server-side. "1m" is capped at 48h
   * ranges by the server-side guardrail (max 2880 buckets); wider ranges must
   * use "5m" (up to 10 days) or "1h" (up to 120 days) or the request 422s.
   */
  bucket?: IngestionHistogramBucketSize;
  /** Comma-separated source_channel values (e.g. "email,telegram"). */
  channels?: string;
  /** Comma-separated status values to include (e.g. "ingested,error"). */
  statuses?: string;
  /** Freetext search (ILIKE %q%), same fields as GET /api/ingestion/events. */
  q?: string;
  /**
   * Filter to events with at least one linked butler session carrying this
   * trace_id — same drill-down spine as IngestionEventsParams.trace_id. The
   * server resolves trace_id -> matching event ids before bucketing, so a
   * trace-scoped hour strip stays consistent with the trace-scoped ledger.
   * Makes `from`/`to` optional (see above) — the server auto-widens the
   * window to the trace's own event bounds (bu-1f81d).
   */
  trace_id?: string;
}

/** Per-status event counts for one histogram bucket. Every status defaults to 0. */
export type IngestionHistogramCounts = components["schemas"]["IngestionHistogramCounts"];

/** One bucket of the ingestion events histogram. */
export type IngestionHistogramBucket = components["schemas"]["IngestionHistogramBucket"];

/**
 * Response from GET /api/ingestion/events/histogram.
 *
 * `buckets` omits zero-count buckets — a bucket only appears when at least
 * one event fell into it during the requested window.
 */
export type IngestionHistogramResponse = components["schemas"]["IngestionHistogramResponse"];

/**
 * Response from GET /api/ingestion/events/dropped-known.
 *
 * `available=false` means complete classification is unknown. Positive counts
 * remain a lower bound only with counts_available; failed-read zeros are placeholders.
 */
export type IngestionDroppedKnownSummary = components["schemas"]["IngestionDroppedKnownSummary"];

/** A concept node in a mind map. */
export type MindMapNode = components["schemas"]["MindMapNodeResponse"];

/** A mind map with optional nested nodes and edges. */
export type MindMap = components["schemas"]["MindMapResponse"];

/**
 * A registered source-material record.
 *
 * The registry stores metadata only — the education butler never fetches or
 * parses source contents. A `source_id` on a node's `metadata.source_refs`
 * that is absent from this list is a dangling reference, not a citation.
 */
export type EducationSourceMaterial = components["schemas"]["SourceMaterialResponse"];

/** Aggregate mastery statistics for a mind map. */
export type MasterySummary = components["schemas"]["MasterySummaryResponse"];

/** A node due for spaced-repetition review. */
export type PendingReviewNode = components["schemas"]["PendingReviewNodeResponse"];

/** One snapshot entry in an analytics trend time-series (from /analytics/trend). */
export type AnalyticsTrendEntry = components["schemas"]["AnalyticsTrendEntry"];

/** Request body for submitting a new curriculum request. */
export type CurriculumRequestBody = components["schemas"]["CurriculumRequestBody"];

export type CurriculumRequestReceipt = components["schemas"]["CurriculumRequestReceipt"];

/**
 * Read-only status envelope for curriculum request receipts.
 *
 * `receipts_available: false` means the receipt store could not be read — render
 * that as "status unavailable", never as "no request in flight".
 */
export type CurriculumRequestStatusResponse = components["schemas"]["CurriculumRequestStatusResponse"];

/** Query params for mind map list. */
export interface MindMapListParams {
  status?: string;
  offset?: number;
  limit?: number;
}

/** Query params for quiz response list. */
export interface QuizResponseParams {
  mind_map_id?: string;
  node_id?: string;
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Unified ingestion rules (design.md D8)
// ---------------------------------------------------------------------------

/** A persisted ingestion rule returned from the API. */
export type IngestionRule = components["schemas"]["IngestionRule"];

/** Request body for POST /api/switchboard/ingestion-rules. */
export type IngestionRuleCreate = components["schemas"]["IngestionRuleCreate"];

/** Request body for PATCH /api/switchboard/ingestion-rules/:id. All fields optional. */
export type IngestionRuleUpdate = components["schemas"]["IngestionRuleUpdate"];

/** Sample envelope for dry-run ingestion rule testing. */
export type IngestionRuleTestEnvelope = components["schemas"]["IngestionRuleTestEnvelope"];

/** Request body for POST /api/switchboard/ingestion-rules/test. */
export type IngestionRuleTestRequest = components["schemas"]["IngestionRuleTestRequest"];

/** Result of a dry-run ingestion rule test. */
export type IngestionRuleTestResult = components["schemas"]["IngestionRuleTestResult"];

/** Query params for GET /api/switchboard/ingestion-rules. */
export interface IngestionRuleListParams {
  scope?: string;
  rule_type?: string;
  action?: string;
  enabled?: boolean;
  /**
   * When true, return soft-deleted (archived) rules instead of the active set.
   * Powers the archived-rules view (and its restore affordance).
   */
  archived?: boolean;
}

// ---------------------------------------------------------------------------
// Channel defaults — per-channel fallback ingestion policy, public.channel_defaults.
//
// Distinct from the ingestion_rules-backed "channel_default" rows shown in
// the Filters pipeline list: this is the runtime policy document itself,
// read/written via GET/PATCH /api/ingestion/channel-defaults/:channel.
// There is no DELETE surface (the backend always returns 405).
// ---------------------------------------------------------------------------

/** Runtime priority-action vocabulary (src/butlers/ingestion_policy.py). */
export type ChannelDefaultPriorityAction =
  "pass_through" | "block" | "skip" | "metadata_only" | "low_priority_queue";

/** A channel's default policy document. */
export type ChannelDefaultPolicy = {
  priority_action: ChannelDefaultPriorityAction;
  /** Email-only: drop messages older than this many days. */
  max_age_days?: number;
};

/** GET/PATCH response for a single channel's defaults. */
export type ChannelDefaultEntry = components["schemas"]["ChannelDefaultEntry"];

/**
 * Request body for PATCH /api/ingestion/channel-defaults/:channel.
 *
 * No `updated_by`: the backend derives the row's attribution from the
 * authenticated principal and ignores any actor a client sends.
 */
export type ChannelDefaultUpdate = components["schemas"]["ChannelDefaultPatchRequest"];

// ---------------------------------------------------------------------------
// Priority contacts — runtime source of truth for priority senders.
//
// Unlike ingestion rules (a DSL proxy), these rows live in
// public.priority_contacts and are the table the Gmail policy evaluator
// actually reads at runtime (connectors/gmail_policy.py). The dashboard
// reads/writes them via GET/POST/DELETE /api/ingestion/priority-contacts.
// ---------------------------------------------------------------------------

/** One priority contact (global — butler-agnostic), joined to public.contacts. */
export type PriorityContactEntry = components["schemas"]["PriorityContactEntry"];

/** Request body for POST /api/ingestion/priority-contacts. */
export type PriorityContactAddRequest = components["schemas"]["PriorityContactAddRequest"];

/** Query parameters for GET /api/ingestion/priority-contacts. */
export interface PriorityContactListParams {
  offset?: number;
  limit?: number;
}

/**
 * A single person-entity match from GET /api/contacts/search. Mirrors backend
 * `ContactSearchResult`. `matched_identifier` is null when the entity matched
 * by name/alias only.
 */
export type ContactSearchResult = components["schemas"]["ContactSearchResult"];

/** Envelope for GET /api/contacts/search. Mirrors backend `ContactSearchResponse`. */
export type ContactSearchResponse = components["schemas"]["ContactSearchResponse"];

/** Response from GET /api/ingestion/connectors/{type}/{identity}/events. */
export type ConnectorEventsResponse = components["schemas"]["ConnectorEventsResponse"];

/** Response from GET /api/ingestion/connectors/{type}/{identity}/incidents. */
export type ConnectorIncidentsResponse = components["schemas"]["ConnectorIncidentsResponse"];

/** Response from GET /api/ingestion/connectors/{type}/{identity}/routing-rules. */
export type ConnectorRoutingRulesResponse = components["schemas"]["ConnectorRoutingRulesResponse"];

// ---------------------------------------------------------------------------
// Model catalog
// ---------------------------------------------------------------------------

/** Valid complexity tier values for the model catalog (canonical six). */
export type ComplexityTier =
  "reasoning" | "workhorse" | "cheap" | "specialty" | "local" | "legacy";

/** Per-model pricing (USD per 1M tokens). Keyed by model_id. */
export interface ModelPricingEntry {
  input_per_million: number;
  output_per_million: number;
}

/** Map of model_id → pricing. */
export type PricingMap = Record<string, ModelPricingEntry>;

/** A single entry in the shared model catalog. */
export type ModelCatalogEntry = components["schemas"]["ModelCatalogEntry"];

export type ModelAttentionEpisode = components["schemas"]["ModelAttentionEpisode"];

export type AllowanceAccount = components["schemas"]["AllowanceAccount"];

export type AllowanceObservation = components["schemas"]["AllowanceObservation"];

export type FleetHaltAttentionEpisode = components["schemas"]["FleetHaltAttentionEpisode"];

/** Request body for PUT /api/settings/models/{id}/priority. */
export type ModelPriorityDelta = components["schemas"]["ModelPriorityDelta"];

/** Request body for creating a catalog entry. */
export type ModelCatalogCreate = components["schemas"]["ModelCatalogCreate"];

/** Request body for updating a catalog entry (all fields optional). */
export type ModelCatalogUpdate = components["schemas"]["ModelCatalogUpdate"];

/** A single per-butler model override joined with catalog alias. */
export type ButlerModelOverride = components["schemas"]["ButlerModelOverride"];

/** Required request-level explanation for a consequential override mutation. */
export type ButlerModelOverrideReason = components["schemas"]["ButlerModelOverrideReason"];

export type ButlerModelOverridesWrite = components["schemas"]["ButlerModelOverridesWrite"];

/** Request body for PUT /api/settings/models/{entry_id}/limits. */
export type TokenLimitsRequest = components["schemas"]["TokenLimitsRequest"];

/** Window selector for POST /api/settings/models/{entry_id}/reset-usage. */
export type UsageWindow = "24h" | "30d" | "both";

/** Request body for POST /api/settings/models/{entry_id}/reset-usage. */
export type ResetUsageRequest = components["schemas"]["ResetUsageRequest"];

// ---------------------------------------------------------------------------
// Provider configuration
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WhatsApp connector types
// ---------------------------------------------------------------------------

/** Connection/session state for the WhatsApp account. */
export type WhatsAppState = components["schemas"]["WhatsAppState"];

// ---------------------------------------------------------------------------
// Spotify connector types
// ---------------------------------------------------------------------------

/** Connection state for the Spotify account. */
export type SpotifyState = components["schemas"]["SpotifyConnectionState"];

/** Request body for POST /api/spotify/config */
export type SpotifyConfigRequest = components["schemas"]["SpotifyConfigRequest"];

// ---------------------------------------------------------------------------
// OwnTracks connector types
// ---------------------------------------------------------------------------

/**
 * Connection state for the OwnTracks webhook connector.
 *
 * Mirrors backend `OwnTracksConnectionState`
 * (src/butlers/api/models/owntracks.py) exactly — keep these literals in
 * sync with that enum.
 */
export type OwnTracksState = components["schemas"]["OwnTracksConnectionState"];

/** Response from GET /api/connectors/owntracks/status */
export type OwnTracksStatusResponse = components["schemas"]["OwnTracksStatusResponse"];

/** Response from GET /api/connectors/owntracks/config */
export type OwnTracksConfigResponse = components["schemas"]["OwnTracksConfigResponse"];

/** Request body for POST /api/settings/home-assistant */
export type HomeAssistantConfigRequest = components["schemas"]["HAConfigRequest"];

export type HomePersonMappingInput = NonNullable<ApiOptions<"/home/person-mappings", "POST">["body"]>["mappings"][number];

export type HomePersonMappingReceipt = components["schemas"]["MappingReceipt"];

// ---------------------------------------------------------------------------
// Dunbar tier ranking
// ---------------------------------------------------------------------------

/** A single contact's Dunbar tier ranking entry. */
export type DunbarEntry = components["schemas"]["DunbarEntry"];

/** Response from GET /api/relationship/dunbar/ranking */
export type DunbarRankingResponse = components["schemas"]["DunbarRankingResponse"];

// ---------------------------------------------------------------------------
// Overdue contacts (bu-iuol4.22)
// ---------------------------------------------------------------------------

/** A single overdue contact entry (GET /contacts/overdue?days=N). */
export type OverdueContact = components["schemas"]["OverdueContact"];

export type PendingDebrief = components["schemas"]["PendingDebrief"];

// ---------------------------------------------------------------------------
// Dashboard conversations
// ---------------------------------------------------------------------------

/** A single tool call recorded on an assistant message. */
export interface MessageToolCall {
  id: string | null;
  name: string;
  arguments: unknown;
  result?: unknown;
}

export type Citation = components["schemas"]["ConversationCitation"];

/** A single message in a dashboard conversation. */
export type Message = Omit<components["schemas"]["ConversationMessage"], "captured_at" | "citations" | "model_name" | "page_context" | "routed_butler" | "sources"> & Partial<Pick<components["schemas"]["ConversationMessage"], "captured_at" | "citations" | "model_name" | "page_context" | "routed_butler" | "sources">> & { /** Existing optimistic/legacy UI alias; never sent as API wire. */ model?: components["schemas"]["ConversationMessage"]["model_name"] };

/** Summary of a dashboard conversation (list view). */
export type ConversationSummary = components["schemas"]["ConversationSummary"];

/** Query params for GET /api/butlers/{name}/conversations. */
export interface ConversationListParams {
  status?: "active" | "archived";
  limit?: number;
  offset?: number;
}

/**
 * Typed pointer to the specific resource a stateful page is showing (e.g.
 * the session id on SessionDetailPage, the active predicate filters on
 * ConcentrationPage). Set via `usePageSubject()` (see `@/lib/page-context.tsx`)
 * on top of the auto-captured `route`/`query_params`. `kind` must be one of
 * the values in `frontend/src/lib/page-context-registry.ts`'s vocabulary —
 * the backend's `PageContext` model enforces the same closed set.
 */
export interface PageContextVisibleResource {
  kind: string;
  id?: string | null;
  filters?: Record<string, string>;
  window?: string | null;
}

/**
 * One message-level full-text search hit
 * (`GET /api/conversations/messages/search`, bu-0ynlk.9).
 *
 * Owner-scoped across every butler — unlike `ConversationSearchResult`
 * (per-butler, one row per conversation), this is one row per matching
 * message, ranked by text relevance.
 */
export type MessageSearchResult = components["schemas"]["MessageSearchResult"];

/** Query params for GET /api/conversations/messages/search. */
export interface MessageSearchParams {
  q: string;
  limit?: number;
  /** Opaque cursor from the previous page's `next_cursor`. Omit for the first page. */
  cursor?: string;
  /** Filter to one conversation source_channel (e.g. "dashboard"). */
  channel?: string;
  /** Filter to one butler's conversations. */
  butler?: string;
  /** ISO-8601 inclusive lower bound on message created_at. */
  from?: string;
  /** ISO-8601 exclusive upper bound on message created_at. */
  to?: string;
}

/**
 * Dashboard route/query/entity/resource context captured at message send
 * time (bu-p6ey8.4, extended by bu-0ynlk.4). Mirrors the backend's
 * `PageContext` model. Built by `usePageContextCapture()`
 * (`@/lib/page-context.tsx`) and shown to the owner pre-send via
 * `ContextChip` before either surface (`ChatPanel.tsx`,
 * `FloatingChatWidget.tsx`) attaches it through their shared
 * `buildMessagePayload` choke point.
 */
export type PageContext = components["schemas"]["PageContext"];

/** Request body for POST /api/butlers/{name}/conversations. */
export interface CreateConversationRequest {
  message: string;
  /**
   * Immutable client-generated UUID. Dashboard UI reuses it for retries and
   * pre-SSE Stop; omission is legacy API compatibility only.
   */
  message_id?: string;
  title?: string;
  /** See `PageContext` — unpopulated seam for bu-p6ey8.4. */
  page_context?: PageContext;
}

/** Request body for POST /api/butlers/{name}/conversations/{id}/messages. */
export interface SendMessageRequest {
  message: string;
  /**
   * Immutable client-generated UUID. Dashboard UI reuses it for retries and
   * pre-SSE Stop; omission is legacy API compatibility only.
   */
  message_id?: string;
  /** See `PageContext` — unpopulated seam for bu-p6ey8.4. */
  page_context?: PageContext;
}

/** SSE event types emitted by the conversation streaming endpoints. */
export type ConversationSseEventType =
  | "conversation_created"
  | "dispatch_accepted"
  | "phase"
  | "token"
  | "message_complete"
  | "error"
  | "done";

/** A parsed SSE event from the conversation streaming endpoint. */
export interface ConversationSseEvent {
  event: ConversationSseEventType;
  data: unknown;
}

export interface ConversationSseMessageCompleteData {
  message_id: string;
  session_id: string | null;
  model_name: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  tool_calls: MessageToolCall[];
  sources: string[];
  citations: Citation[];
  routed_butler: string | null;
}

/**
 * Shape of the `data` payload on a `phase` SSE event (bu-0ynlk.7) — real-time
 * processing status, never fabricated: a phase is only emitted when the
 * backend can truthfully observe it (see
 * `src/butlers/api/routers/conversations.py` module docstring).
 */
export interface ConversationSsePhaseData {
  phase: "classifying" | "routed" | "starting_session" | "thinking" | "writing" | string;
  /** The butler handling this turn (`routed`/`starting_session`). */
  target?: string | null;
  /** The tool call in progress (`thinking` only). */
  tool?: string | null;
}

/**
 * Shape of the `data` payload on an `error` SSE event (see
 * `src/butlers/api/routers/conversations.py` module docstring for the
 * authoritative contract). `code` distinguishes a retryable connectivity
 * failure from a graceful reply timeout (which carries `session_id` for an
 * "inspect session" link), an in-progress durable handoff that needs Check
 * again rather than Retry, a terminal unknown outcome that cannot be retried,
 * or a deterministic rejection.
 */
export interface ConversationSseErrorData {
  code?:
    | "SWITCHBOARD_UNAVAILABLE"
    | "INGEST_REJECTED"
    | "SWITCHBOARD_ERROR"
    | "INGEST_IN_PROGRESS"
    | "SESSION_TIMEOUT"
    | "SESSION_CANCELLED"
    | "TURN_OUTCOME_UNKNOWN";
  message?: string;
  session_id?: string | null;
}

// ---------------------------------------------------------------------------
// Telegram Session Auth
// ---------------------------------------------------------------------------

/** Request body for POST /api/telegram/session/send-code */
export type TelegramSendCodeRequest = components["schemas"]["SendCodeRequest"];

/** Request body for POST /api/telegram/session/verify */
export type TelegramVerifyCodeRequest = components["schemas"]["VerifyCodeRequest"];

/** Response from GET /api/telegram/session/status */
export type TelegramSessionStatusResponse = components["schemas"]["SessionStatusResponse"];

/** Request body for POST /api/steam/accounts */
export type SteamConnectRequest = components["schemas"]["SteamConnectRequest"];

// ---------------------------------------------------------------------------
// Healing attempts (self-healing + QA-originated investigations)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// QA Staffer
// ---------------------------------------------------------------------------

/** Canonical persisted values accepted by GET /api/qa/patrols?status=. */
export type QaPatrolStatus =
  | "running"
  | "clean"
  | "findings_dispatched"
  | "error"
  | "skipped_overlap"
  | "suppressed";

/**
 * Patrol reads stay open to a malformed or future database value so the UI can
 * make that condition visible through its fail-closed presentation.
 */
export type QaPatrolReadStatus =
  QaPatrolStatus | (string & { readonly __qaPatrolStatus?: never });

/** Lightweight patrol record for list views — GET /api/qa/patrols */
export type QaPatrolSummary = components["schemas"]["QaPatrolSummary"];

/** A single finding record from a patrol — GET /api/qa/patrols/:id/findings */
export type QaFindingRecord = components["schemas"]["QaFindingRecord"];

/** Full patrol with nested findings — GET /api/qa/patrols/:id */
export type QaPatrolDetail = components["schemas"]["QaPatrolDetail"];

/** A dismissal record — GET /api/qa/dismissals */
export type QaDismissal = components["schemas"]["QaDismissal"];

/** Active dismissal embedded in a QA case dossier — GET /api/qa/cases/:id */
export type QaActiveDismissal = components["schemas"]["QaActiveDismissal"];

/** A known issue grouped by fingerprint — GET /api/qa/known-issues */
export interface QaKnownIssue {
  fingerprint: string;
  source_butler: string;
  source_type: string;
  severity: number;
  exception_type: string;
  event_summary: string;
  call_site: string;
  occurrence_count: number;
  first_seen: string;
  last_seen: string;
  patrol_count: number;
  healing_attempt_id: string | null;
  dismissal: QaDismissal | null;
}

/** KPI strip metrics for the QA dossier dashboard — GET /api/qa/summary */
export type QaKpiBlock = components["schemas"]["QaKpiBlock"];

/** Active-case status breakdown for the QA dossier dashboard — GET /api/qa/summary */
export type QaActiveBreakdown = components["schemas"]["QaActiveBreakdown"];

/** QA staffer summary — GET /api/qa/summary */
export type QaSummary = components["schemas"]["QaSummary"];

/** Summary row for the QA Cases API — GET /api/qa/cases */
export type QaCaseSummary = components["schemas"]["QaCaseSummary"];

/** Parsed QA investigation notes embedded in a case dossier. */
export type QaInvestigationNotes = components["schemas"]["InvestigationNotes"];

/** Pull request summary embedded in a QA case dossier. */
export type QaPrSummary = components["schemas"]["QaPrSummary"];

/** A single chronological event in the QA case journal. */
export type QaJournalEvent = components["schemas"]["QaJournalEvent"];

/** Full case payload for the QA dossier renderer — GET /api/qa/cases/:id */
export type QaCaseDossier = components["schemas"]["QaCaseDossier"];

/** Params for listing QA cases */
export interface QaCasesParams {
  sev?: "high" | "medium" | "low" | "all";
  state?: QaCaseSummary["state"] | "all";
  since?: "24h" | "7d" | "30d" | "all";
  butler?: string | string[];
  offset?: number;
  limit?: number;
}

/** Params for paginating one QA case journal */
export interface QaCaseJournalParams {
  cursor?: string;
  limit?: number;
}

/** Request body for dismissing a known issue */
export interface QaDismissRequest {
  dismissed_until?: string;
  dismissed_by?: string;
}

/** Params for listing patrols */
export interface QaPatrolsParams {
  offset?: number;
  limit?: number;
  status?: QaPatrolStatus;
}

/** A single investigation record — GET /api/qa/investigations */
export type QaInvestigation = components["schemas"]["QaInvestigation"];

/** Params for listing investigations */
export interface QaInvestigationsParams {
  status?: string;
  offset?: number;
  limit?: number;
}

/** A recent healing attempt relevant to circuit breaker state */
export type CircuitBreakerAttempt = components["schemas"]["CircuitBreakerAttempt"];

/** QA repository configuration — GET /api/qa/settings/repo */
export type QaRepoConfig = components["schemas"]["QaRepoConfig"];

/** Request body for PUT /api/qa/settings/repo */
export type QaRepoConfigUpdate = components["schemas"]["QaRepoConfigUpdate"];

/** Request body for PUT /api/qa/settings/git-author */
export type QaGitAuthorUpdate = components["schemas"]["QaGitAuthorUpdate"];

/** Request body for adding a repository to the whitelist. */
export type QaAllowedRepoCreate = components["schemas"]["AllowedRepoCreate"];

/** Request body for toggling the enabled flag on a whitelisted repository. */
export type QaAllowedRepoPatch = components["schemas"]["AllowedRepoPatch"];

/** Request body for PATCH /api/butlers/{name}/runtime-config. */
export type RuntimeConfigPatch = components["schemas"]["RuntimeConfigPatch"];

// ---------------------------------------------------------------------------
// Google Health connector status + scope-selective disconnect
// ---------------------------------------------------------------------------

/**
 * Operational state of the Google Health connector.
 *
 * ``not_configured`` is a dashboard-only state surfaced when no primary
 * Google account exists yet; the connector itself never reports this
 * value over its heartbeat.
 */
export type GoogleHealthConnectorState = components["schemas"]["GoogleHealthConnectorState"];

/** Per-account connector state — one entry per health-scoped Google account. */
export type GoogleHealthAccountStatus = components["schemas"]["AccountStatus"];

/** Response from GET /api/connectors/google-health/status. */
export type GoogleHealthStatusResponse = components["schemas"]["GoogleHealthStatusResponse"];

/** Query parameters for GET /api/chronicler/aggregate/by-category. */
export interface ChroniclerAggregateByCategoryParams {
  start_at: string;
  end_at: string;
  tz?: string;
  /** Comma-separated privacy tiers to include. Default: exclude restricted. */
  privacy_tier?: string;
  include_tombstoned?: boolean;
}

/** Query parameters for GET /api/chronicler/aggregate/by-day. */
export interface ChroniclerAggregateByDayParams {
  start_at: string;
  end_at: string;
  tz?: string;
  category?: string;
  privacy_tier?: string;
  include_tombstoned?: boolean;
}

/** Runtime state for a single source adapter, joined with projection checkpoints. */
export type ChroniclerSourceStateRow = components["schemas"]["SourceStateRow"];

/** Response envelope for GET /api/chronicler/balance. */
export type ChroniclerBalanceResponse = components["schemas"]["BalanceResponse"];

/** Query parameters for GET /api/chronicler/balance. */
export interface ChroniclerBalanceParams {
  /** Local calendar day (rollup tz) to compute balance for (YYYY-MM-DD). */
  date: string;
  /** Trailing local-day window used for the "usual" baseline. */
  lookback_days?: number;
}

/** One lane's day-by-day series across the requested trends window. */
export type ChroniclerTrendLaneSeries = components["schemas"]["TrendLaneSeries"];

/** Response envelope for GET /api/chronicler/trends. */
export type ChroniclerTrendsResponse = components["schemas"]["TrendsResponse"];

/** Query parameters for GET /api/chronicler/trends. */
export interface ChroniclerTrendsParams {
  window?: "week" | "month";
  /** Last local day of the window (inclusive, YYYY-MM-DD). */
  end_date?: string;
  lookback_days?: number;
}

/** One deterministic anomaly-flag row from `chronicler.daily_rollup_flags`. */
export type ChroniclerRollupFlagRow = components["schemas"]["RollupFlagRow"];

/** One local calendar day's rollup + flags, from GET /api/chronicler/rollups. */
export type ChroniclerRollupDay = components["schemas"]["RollupDay"];

/** Response envelope for GET /api/chronicler/rollups. */
export type ChroniclerRollupsResponse = components["schemas"]["RollupsResponse"];

/**
 * Query parameters for GET /api/chronicler/rollups. Provide either `date`
 * alone, or `start_date` + `end_date` together (both required if either is
 * given). Range capped server-side at 92 days.
 */
export interface ChroniclerRollupsParams {
  date?: string;
  start_date?: string;
  end_date?: string;
}

// ── Who-you-were-with (IEA, tasks.md §9b, bu-jc6htw.2) ──────────────────────

/** One resolved (or unattributed) companion for a who-you-were-with window. */
export type ChroniclerCompanionEntry = components["schemas"]["CompanionEntry"];

/** Response envelope for GET /api/chronicler/who-you-were-with. */
export type ChroniclerWhoYouWereWithResponse = components["schemas"]["WhoYouWereWithResponse"];

/** Query parameters for GET /api/chronicler/who-you-were-with. */
export interface ChroniclerWhoYouWereWithParams {
  start_at: string;
  end_at: string;
  tz?: string;
}

/** Response envelope for GET /api/chronicler/correction-prompts. */
export type ChroniclerCorrectionPrompts = components["schemas"]["CorrectionPrompts"];

/** Query parameters for GET /api/chronicler/correction-prompts. */
export interface ChroniclerCorrectionPromptsParams {
  start_at: string;
  end_at: string;
  tz?: string;
  limit?: number;
}

/** Query parameters for GET /api/chronicler/episodes. */
export interface ChroniclerEpisodesParams {
  source_name?: string;
  episode_type?: string;
  start_from?: string;
  start_to?: string;
  overlaps_start?: string;
  overlaps_end?: string;
  include_tombstoned?: boolean;
  offset?: number;
  limit?: number;
}

/** A single Chronicler episode (corrected view). */
export type ChroniclerEpisode = components["schemas"]["ChroniclerEpisode"];

/**
 * Fresh day-close cache response: prose + provenance refs.
 * Returned when cache_built_at >= all invalidating events for the requested date.
 */
export interface ChroniclerDayCloseFreshResponse {
  prose: string;
  provenance_refs: string[];
  cache_built_at: string;
}

/**
 * Stale day-close cache response: cache exists but has been invalidated.
 * Returned when any episode/point_event/override for the requested date changed after cache_built_at.
 */
export interface ChroniclerDayCloseStaleResponse {
  stale: true;
  cache_built_at: string;
  last_invalidating_event_at: string;
}

/** Invalid day-close cache response: no renderable prose is returned. */
export interface ChroniclerDayCloseInvalidResponse {
  invalid: true;
  invalid_reason: "inadmissible_prose" | "date_mismatch";
  cache_built_at: string;
}

/** Union of fresh, stale, and invalid day-close responses. */
export type ChroniclerDayCloseResponse =
  | ChroniclerDayCloseFreshResponse
  | ChroniclerDayCloseStaleResponse
  | ChroniclerDayCloseInvalidResponse;

/** POST /aggregate/day-close/refresh success shape. */
export type ChroniclerDayCloseRefreshResult = ApiResult<"/chronicler/aggregate/day-close/refresh", "POST">;

/** Required body for POST /api/chronicler/aggregate/day-close/refresh. */
export type ChroniclerDayCloseRefreshRequest = components["schemas"]["DayCloseRefreshRequest"];

/** Query parameters for GET /api/chronicler/aggregate/day-close. */
export interface ChroniclerDayCloseParams {
  /** Local calendar date in YYYY-MM-DD form. */
  date: string;
  /** Exact IANA timezone that defines this local day's cache identity. */
  tz: string;
}

/**
 * Request body for `POST /api/chronicler/episodes/{id}/corrections` — the
 * episode-correction write path (JARVIS audit move 6, bu-86c4c.15). At least
 * one correction field or a `note` is required (enforced server-side).
 * Carries no `submitted_by`: the server derives attribution from the
 * authenticated principal and ignores any value a client sends.
 */
export type SubmitCorrectionRequest = components["schemas"]["SubmitCorrectionRequest"];

/** Query parameters for GET /api/chronicler/events. */
export interface ChroniclerEventsParams {
  source_name?: string;
  event_type?: string;
  since?: string;
  until?: string;
  include_tombstoned?: boolean;
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Chronicler routines (bu-whhll.9 miner + bu-whhll.11 owner-declared schedule)
// ---------------------------------------------------------------------------

/**
 * One row from GET /api/chronicler/routines — a weekly work-pattern the
 * occupation-inference adapter consumes when enabled.
 *
 * `origin`:
 *  - `"mined"` — written by the deterministic weekly miner; its window/days
 *    are refreshed by the miner (not owner-editable), but enable/label are.
 *  - `"declared"` — owner bootstrap ("I work Mon-Fri 09:30-19:30 at Acme");
 *    fully owner-editable and deletable.
 *
 * Mined rows expose their evidence lifecycle for owner review. A `stale` row
 * has missed one or more evidence-backed weekly mining runs; a declared row
 * is never made stale by the miner.
 */
export type ChroniclerRoutine = components["schemas"]["RoutineRow"];

/** Request body for POST /api/chronicler/routines (declare a schedule). */
export type ChroniclerCreateRoutineRequest = components["schemas"]["CreateRoutineRequest"];

/**
 * Request body for PATCH /api/chronicler/routines/{id}.
 *
 * `enabled`/`label` apply to any routine. The schedule fields
 * (`dow_mask`/`window_*`/`timezone`) apply only to declared routines — the
 * server rejects them with 400 on a mined routine.
 */
export type ChroniclerUpdateRoutineRequest = components["schemas"]["UpdateRoutineRequest"];

/** An interaction fact for a relationship entity (predicate LIKE 'interaction_%'). */
export type EntityInteraction = components["schemas"]["EntityInteraction"];

/** Request body for POST /api/relationship/entities/{id}/notes. */
export type CreateEntityNoteRequest = components["schemas"]["CreateEntityNoteRequest"];

/**
 * Request body for POST /api/relationship/entities/{id}/interactions.
 *
 * `occurred_at` is an ISO timestamp. Omitting it defaults to now server-side;
 * sending it also opts the write into the backend's same-day idempotency
 * guard, which answers 409 rather than logging the interaction twice.
 */
export type CreateEntityInteractionRequest = components["schemas"]["CreateEntityInteractionRequest"];

/** Request body for POST /api/relationship/entities/{id}/gifts. */
export type CreateEntityGiftRequest = components["schemas"]["CreateEntityGiftRequest"];

/** A gift fact for a relationship entity (predicate='gift'). */
export type EntityGift = components["schemas"]["EntityGift"];

/** A loan fact for a relationship entity (predicate='loan'). */
export type EntityLoan = components["schemas"]["EntityLoan"];

/** A single entry in a relationship entity's unified timeline. */
export type EntityTimelineItem = components["schemas"]["EntityTimelineItem"];

/** A contact linked to an entity, for the entity detail page.
 *
 * Enriched with contact_info[], labels[], and preferred_channel so the
 * entity-card can render channel chips without N+1 getContact() calls.
 * contact_info only contains non-secured rows (secured=true rows are excluded).
 *
 * `preferred_channel` is sourced from the entity-keyed `prefers-channel` fact
 * (entity-keyed-preferred-channel), not the orphaned contacts CRM column.
 * `reachable_channels` is the deliverable channel set the entity has a contact
 * fact for (`email`/`telegram`); the channel-preference control offers only
 * these. Both are entity-level and attached to the first linked contact only.
 */
export type LinkedContactSummary = components["schemas"]["LinkedContactSummary"];

/** One row of message activity for an entity, grouped by channel + thread. */
export type MessageThreadSummary = components["schemas"]["MessageThreadSummary"];

/**
 * Compact entity row from GET /api/butlers/relationship/entities (list+filter API, §9.1).
 * Distinct from the memory-butler EntitySummary: this surface includes relationship-scoped
 * fields (tier, last_seen, first_seen, contact_fact_count) instead of memory-butler fact_count.
 */
export type RelationshipEntitySummary = components["schemas"]["relationship_api_models_internal__EntitySummary"];

/** Paginated response from GET /api/butlers/relationship/entities (§9.1). */
export type RelationshipEntityListResponse = components["schemas"]["EntityListResponse"];

/** Query parameters for the relationship entity list endpoint (§9.1). */
export interface RelationshipEntityListParams {
  entity_type?: string[];
  state?: "unidentified" | "duplicate-candidate" | "stale";
  has?: "contact";
  /** Restrict results to this explicit set of entity ids (e.g. to hydrate full
   *  summaries for the toolbar search's ranked id set). An empty array yields
   *  an empty result set. */
  ids?: string[];
  limit?: number;
  offset?: number;
}

/** One entry in the entity curation queue (§9.5). */
export type RelationshipQueueEntry = components["schemas"]["QueueEntry"];

/** Paginated curation queue response from GET /api/butlers/relationship/entities/queue (§9.5). */
export type RelationshipQueueResponse = components["schemas"]["QueueResponse"];

/** Request body for POST /api/butlers/relationship/entities. */
export type PromoteRelationshipEntityRequest = components["schemas"]["PromoteEntityRequest"];

/** Request body for POST /api/butlers/relationship/entities (create path — entity_id omitted). */
export type CreateRelationshipEntityRequest = components["schemas"]["PromoteEntityRequest"];

/** Request body for POST /api/butlers/relationship/entities/{id}/merge. */
export type MergeRelationshipEntitiesRequest = components["schemas"]["MergeEntitiesRequest"];

// ---------------------------------------------------------------------------
// Merge-review compare — POST /api/relationship/entities/compare (relationship-merge-review)
// ---------------------------------------------------------------------------

/** Request body for POST /api/relationship/entities/compare. */
export type CompareEntitiesRequest = components["schemas"]["CompareRequest"];

/**
 * One fact row in a compare block, carrying full provenance.
 *
 * Used for the per-entity ``identity_facts`` / ``narrative_facts`` lists and for
 * the ``shared`` / ``divergent`` lists. ``entity_id`` identifies which entity the
 * row belongs to so the two-column diff can place it. ``last_seen`` is null on
 * narrative-store rows (no ``last_seen`` column).
 */
export type CompareFact = components["schemas"]["CompareFact"];

/** Per-entity block (``a`` or ``b``) in a compare response. */
export type CompareEntityBlock = components["schemas"]["CompareEntityBlock"];

/**
 * Response for POST /api/relationship/entities/compare — a structural diff only.
 *
 * - ``a`` / ``b`` — per-entity blocks with identity + narrative facts.
 * - ``shared`` — identity-store rows present on BOTH entities with identical
 *   ``(predicate, object)`` (the duplicate evidence). One pair of rows per match.
 * - ``divergent`` — identity-store rows for single-cardinality predicates whose
 *   objects differ between the two entities (the conflicts a merge must resolve).
 *
 * No scoring, no ranking, no similarity percentage, no generated text.
 */
export type CompareEntitiesResponse = components["schemas"]["CompareResponse"];

/** Request body for POST /api/relationship/entities/dismiss-pair. */
export type DismissEntityPairRequest = components["schemas"]["DismissPairRequest"];

// ---------------------------------------------------------------------------
// System endpoints — GET /api/system/*
// ---------------------------------------------------------------------------

/** Software identity and process uptime facts. */
export type InstanceFacts = components["schemas"]["InstanceFacts"];

/** Disk footprint of a single butler schema. */
export type SchemaSize = components["schemas"]["SchemaSize"];

/** PostgreSQL catalog size facts for the running database. */
export type DatabaseFacts = components["schemas"]["DatabaseFacts"];

/** Result of the most recent weekly restore-drill attempt (bu-9r3hd.5).
 *
 * `result: "pending"` means the drill has never run yet -- a real "unknown"
 * state, never rendered as a passing drill.
 */
export type RestoreDrillFacts = components["schemas"]["RestoreDrillFacts"];

/** Outcome of the most recent backup *run*, successful or not (bu-xrqyu).
 *
 * A different question from `last_backup_at`/`backup_stale`, which describe
 * the newest surviving *artifact*. The backup script refuses to publish a bad
 * dump, so a failed run leaves yesterday's good file untouched and freshness
 * keeps reading as healthy -- this is the run's own signal.
 *
 * `result: "unknown"` means no readable receipt was found -- a real "we do not
 * know", never folded into a pass, and never rendered as a failure either.
 */
export type BackupRunFacts = components["schemas"]["BackupRunFacts"];

/** Backup recency, verified artifact health, run outcome, and drill facts.
 *
 * `last_backup_status`/`backup_stale`/`restore_drill`/`last_run` are optional
 * so fixtures written before bu-9r3hd.5 and bu-xrqyu keep typechecking;
 * components must treat their absence as "unknown", never as a fabricated
 * healthy state. For `last_run` that absence is the same state the backend
 * spells `result: "unknown"`, so both collapse onto one code path.
 */
export type BackupFacts = components["schemas"]["BackupFacts"];

/** Aggregated catalog of external-actor egress events. */
export type EgressCatalog = components["schemas"]["EgressCatalog"];

/** Aggregated state of the proactive insight delivery pipeline. */
export type InsightDeliveryState = components["schemas"]["InsightDeliveryState"];

/**
 * Migration-drift sentinel result.
 *
 * `drift_check_available: false` means the comparison itself failed (pool
 * unavailable, unreadable schema) -- per the fleet-wide degraded-envelope
 * convention, never render this as a truthful all-clear.
 */
export type DriftFacts = components["schemas"]["DriftFacts"];

/**
 * Deployed stored-function bodies vs the configured bootstrap source
 * (bu-bi5an, bu-uoctv).
 *
 * `not_deployed` is a legitimate state, not drift: a function only exists
 * once its migration chain has run the bootstrap installer. `is_drifted`
 * reflects `drifted` only -- never treat `not_deployed` as an alarm.
 * `stored_function_check_available: false` means the comparison itself
 * failed -- per the fleet-wide degraded-envelope convention, never render
 * this as a truthful all-clear.
 */
export type StoredFunctionFacts = components["schemas"]["StoredFunctionFacts"];

/**
 * One episode row from public.infra_conditions or public.owner_conditions
 * (bu-27dxl.6.2 / bu-ep4ks.3 / bu-ep4ks.6).
 *
 * An "open"/"aging" episode is an active outage/standing concern; a
 * "resolved" episode is retained history -- `resolved_at`/`recovered_after_s`
 * are only set once resolved, and are how the panel shows auto-resolve
 * provenance. `ledger` distinguishes "infra" (infrastructure reliability)
 * from "owner" (owner-facing standing concerns, e.g. an overdue bill) --
 * both share the same lifecycle and this same shape.
 */
export type ConditionEntry = components["schemas"]["ConditionEntry"];

/**
 * Linked runtime-attention delivery for a fleet or overdue-QA condition
 * (REQ-butler-control-plane-liveness-007). Only "sent" means delivered;
 * "sending" and "uncertain" never do, and nothing here can resend.
 */
export type ConditionAttention = components["schemas"]["ConditionAttention"];

/**
 * Standing infrastructure conditions, most-recently-detected first.
 *
 * `conditions_available: false` means the ledger query itself failed --
 * per the fleet-wide degraded-envelope convention, render "unknown", never
 * a fabricated "no active conditions" all-clear.
 */
export type ConditionsFacts = components["schemas"]["ConditionsFacts"];

/**
 * Current (most recent) deployment plus recent deployment history (bu-9r3hd.3/bu-hmdqz.1).
 *
 * `commits_behind_available: false` means the "N commits behind origin/main"
 * comparison itself failed (no current deployment, unknown git_sha, or the
 * GitHub compare call failed) -- per the fleet-wide degraded-envelope
 * convention, render that as "unknown", never as "up to date".
 */
export type DeploymentFacts = components["schemas"]["DeploymentFacts"];

/** Whether the elaboration paragraph came from the LLM or the templated fallback. */
export type BriefingSource = Briefing["source"];

/**
 * Server-composed briefing object the Overview page renders verbatim.
 *
 * `greet` and `headline` are deterministic templates; `elaboration` is one to
 * three sentences from the local catalog-backed runtime with a templated
 * fallback. `source` tells the status pill which path produced the elaboration.
 * Cached per-owner for 5 minutes (the hook below mirrors that TTL).
 */
export type Briefing = components["schemas"]["butlers__api__routers__dashboard_briefing__Briefing"];

/** State classes the chronicles briefing classifier produces. */
export type ChroniclesStateClass = components["schemas"]["ChroniclesStateClass"];

export type ChroniclesAttentionItem = components["schemas"]["ChroniclesAttentionItem"];

export type ChroniclesKpi = components["schemas"]["ChroniclesKpi"];

export type ChroniclesRecentDay = components["schemas"]["ChroniclesRecentDay"];

export type ChroniclesBriefing = components["schemas"]["ChroniclesBriefing"];

// ---------------------------------------------------------------------------
// Finance butler types (GET /api/finance/*)
// ---------------------------------------------------------------------------

export type FinanceTransaction = components["schemas"]["TransactionModel"];

export type FinanceSubscription = components["schemas"]["SubscriptionModel"];

/** One forward obligation ledger row (bu-8cdl1.10), denormalized with its
 * subscription's service/amount and cancellation-door status. */
export type FinanceObligation = components["schemas"]["ObligationModel"];

export type FinanceExpectedSignal = components["schemas"]["FinanceExpectedSignalModel"];

export type FinanceAccount = components["schemas"]["AccountModel"];

export type FinanceSpendingGroup = components["schemas"]["SpendingGroupModel"];

export type FinanceUpcomingBillItem = components["schemas"]["UpcomingBillItemModel"];

export interface FinanceTransactionListParams {
  category?: string;
  merchant?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

export interface FinanceSubscriptionListParams {
  status?: string;
  offset?: number;
  limit?: number;
}

export interface FinanceAccountListParams {
  type?: string;
  offset?: number;
  limit?: number;
}

export interface FinanceSpendingSummaryParams {
  start_date?: string;
  end_date?: string;
  group_by?: "category" | "merchant" | "week" | "month";
}

export interface FinanceUpcomingBillsParams {
  days_ahead?: number;
  include_overdue?: boolean;
}

/** A single op in a bulk-metadata request. */
export type FinanceBulkUpdateOp = components["schemas"]["BulkUpdateOpModel"];

/** Request body for PATCH /api/finance/transactions/bulk-metadata. */
export type FinanceBulkUpdateRequest = components["schemas"]["BulkUpdateRequestModel"];

// ---------------------------------------------------------------------------
// Travel butler types (bu-0eac9)
// ---------------------------------------------------------------------------

/** A travel trip container. */
export type TravelTrip = components["schemas"]["TripModel"];

/** A transport leg (flight, train, bus, ferry) within a trip. */
export type TravelLeg = components["schemas"]["LegModel"];

/** An accommodation (hotel, airbnb, hostel) within a trip. */
export type TravelAccommodation = components["schemas"]["AccommodationModel"];

/** A single entry in a trip's chronological timeline. */
export type TravelTimelineEntry = components["schemas"]["TimelineEntryModel"];

/** An upcoming trip with legs, accommodations, and days until departure. */
export type TravelUpcomingTrip = components["schemas"]["UpcomingTripModel"];

/** Params for listing trips. */
export interface TravelTripsParams {
  status?: string;
  from_date?: string;
  to_date?: string;
  offset?: number;
  limit?: number;
}

/** A document expiring within the requested look-ahead window. */
export type TravelExpiringDocument = components["schemas"]["ExpiringDocumentModel"];

// ---------------------------------------------------------------------------
// Home butler types
// ---------------------------------------------------------------------------

/** Aggregate statistics about the Home butler's entity snapshot cache. */
export type HomeSnapshotStatus = components["schemas"]["StatisticsResponse"];

/** Saved location and feed health used by the Home atmosphere refresh job. */
export type HomeAtmosphereCurrentResponse = components["schemas"]["AtmosphereCurrentResponse"];

/** Coordinates accepted by the Home atmosphere location endpoint. */
export type HomeAtmosphereLocationUpdate = components["schemas"]["AtmosphereLocationUpdateRequest"];

/** A single device entry in the home butler device inventory. */
export type HomeDeviceEntry = components["schemas"]["DeviceInventoryEntry"];

/** Paginated response for the device inventory endpoint. */
export type HomeDeviceInventoryResponse = components["schemas"]["DeviceInventoryResponse"];

/** A single time-series data point for energy consumption. */
export type HomeEnergyDataPoint = components["schemas"]["EnergyDataPoint"];

/** A top energy-consuming device entry. */
export type HomeTopConsumer = components["schemas"]["TopConsumerEntry"];

/** A maintenance item with computed status. */
export type HomeMaintenanceItem = components["schemas"]["MaintenanceItemResponse"];

/** A single entry in the Home Assistant command audit log. */
export type HomeCommandLogEntry = components["schemas"]["CommandLogEntry"];

// ---------------------------------------------------------------------------
// Butler logs (bu-iuol4.17)
// ---------------------------------------------------------------------------

/** Severity level for butler log lines. */
export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

/** Query parameters for the butler logs endpoint. */
export interface ButlerLogsParams {
  level?: LogLevel;
  since?: string;
  limit?: number;
}

/** Query params for GET /api/butlers/{name}/analytics/hourly-activity. */
export interface HourlyActivityParams {
  window_hours?: number;
}

/** Query params for GET /api/butlers/{name}/analytics/daily-activity. */
export interface DailyActivityParams {
  window_days?: 7 | 30;
}

/** Query params for GET /api/butlers/{name}/analytics/session-kinds. */
export interface SessionKindsParams {
  window_days?: number;
}

/** Query params for GET /api/butlers/{name}/analytics/latency-stats. */
export interface LatencyStatsParams {
  window_days?: number;
}

/**
 * Response from GET /api/butlers/{name}/analytics/friction (bu-8cdl1.9 S3).
 *
 * `by_kind` is zero-filled across every `sessions_friction.kind` value
 * (degenerate_tool_loop, guardrail_termination, classification_timeout,
 * recovered_error, dead_end) so a console panel can render a stable counter
 * set. `succeeded` / `failed` / `by_error_marker` mirror the outcome fields
 * `sessions_summary` computes for the same period and window.
 */
export type FrictionSummary = components["schemas"]["FrictionSummary"];

/** Query params for GET /api/butlers/{name}/analytics/friction. */
export interface FrictionSummaryParams {
  period?: "today" | "7d" | "30d";
}

// ---------------------------------------------------------------------------
// Activity feed (bu-y7lo7)
// ---------------------------------------------------------------------------

/** Discriminated event type for activity feed entries. */
export type ActivityEventType =
  "session_completed" | "approval_raised" | "memory_write";

/** A single event in the butler activity feed. */
export type ButlerActivityEvent = components["schemas"]["ActivityEvent"];

/** Response model for GET /api/butlers/{name}/activity-feed. */
export type ActivityFeed = components["schemas"]["ActivityFeed"];

/** Query params for GET /api/butlers/{name}/activity-feed. */
export interface ActivityFeedParams {
  limit?: number;
}

/**
 * Request body for PUT /api/butlers/{name}/prompt. Carries no `actor`: the
 * server derives attribution from the authenticated principal and ignores any
 * actor a client sends.
 */
export type PromptUpdateRequest = components["schemas"]["PromptUpdateRequest"];

/**
 * Request body for POST /api/butlers/{name}/kill. Carries no `actor` — see
 * `PromptUpdateRequest`.
 */
export type KillRequest = components["schemas"]["KillRequest"];

/** Request body for POST /api/memory/reembed. */
export type ReembedRunRequest = components["schemas"]["ReembedRunRequest"];

/** Response from POST /api/memory/reembed. */
export type ReembedRunResult = components["schemas"]["ReembedRunResult"];

// ---------------------------------------------------------------------------
// Relationship entity neighbours (GET /api/butlers/relationship/entities/{id}/neighbours)
// Used by PlexPage and the EntityFinder preview.
// ---------------------------------------------------------------------------

/**
 * A single neighbour reached via a relational triple.
 *
 * ``entity_id`` is the OTHER entity (not the queried anchor).
 * ``direction`` is ``"forward"`` when anchor is the subject (anchor → neighbour)
 * and ``"reverse"`` when anchor is the object (neighbour → anchor).
 */
export type NeighbourEntry = components["schemas"]["NeighbourEntry"];

/** Response envelope from GET /api/butlers/relationship/entities/{id}/neighbours. */
export type NeighboursResponse = components["schemas"]["NeighboursResponse"];

/** Query parameters for the entity neighbours endpoint. */
export interface NeighboursParams {
  /**
   * Ranking key for per-predicate truncation. Only ``"weight"`` in v1. When set,
   * each predicate group is truncated to the top ``per_predicate`` by weight and
   * the overflow count is reported in ``remainders``.
   */
  rank?: "weight";
  /**
   * Max neighbours returned per predicate group when ``rank`` is set
   * (top-N by weight). Defaults to 6 on the backend.
   */
  per_predicate?: number;
}

/** One non-person entity shown in a halo arc, with its person edges. */
export type HaloSatellite = components["schemas"]["HaloSatellite"];

/** Response envelope from GET /api/butlers/relationship/plex/halo. */
export type HaloResponse = components["schemas"]["HaloResponse"];

/** A single result from GET /api/butlers/relationship/entities/search. */
export type EntityFinderSearchResult = components["schemas"]["SearchResultEntry"];

// ---------------------------------------------------------------------------
// Relationship entity concentration (GET /api/relationship/entities/concentration)
// Used by ConcentrationPage §8.4 (bu-m4ya3).
// ---------------------------------------------------------------------------

/**
 * A predicate tab enumerated from ``relationship.entity_predicate_registry``.
 *
 * Only predicates with ``kind='relational'`` are surfaced as concentration
 * tabs (contact predicates like ``has-email`` do not produce meaningful
 * weight aggregations for the balance-sheet view).
 */
export type PredicateTab = components["schemas"]["PredicateTab"];

/**
 * One row in the concentration balance-sheet for a given predicate.
 *
 * ``weight_sum`` is the sum of edge weights (NULLs treated as 1 per triple).
 * ``share`` is the entity's fraction of total weight (0.0–1.0); null when total = 0.
 * ``targets`` lists where the predicate points (e.g. the organizations for a
 * `works-at` row); entity-kind targets carry an `entity_id` for hyperlinking.
 */
export type ConcentrationEntry = components["schemas"]["ConcentrationEntry"];

/** Response envelope for GET /api/relationship/entities/concentration?pred=<predicate>. */
export type ConcentrationResponse = components["schemas"]["ConcentrationResponse"];

/**
 * Read-time staleness band derived from the most-recent observation timestamp.
 * Separate axis from confidence (``conf``).
 */
export type EntityFactStalenessBand = EntityFact["staleness_band"];

/**
 * One fact row for the Workbench ProvenanceGrid.
 *
 * Provenance fields:
 * - ``weight`` — relational aggregation weight (null when not yet scored).
 * - ``last_observed_at`` — most-recent observation timestamp (null when never re-observed).
 * - ``object_kind`` — ``"literal"`` for plain values; ``"entity"`` for entity refs.
 * - ``src`` — butler slug that authored the fact.
 * - ``store`` — origin store of this row (``"identity"`` or ``"narrative"``).
 * - ``staleness_band`` — read-time freshness band (``"fresh"`` / ``"aging"`` / ``"stale"``).
 *
 * Note: ``source_event_id`` is not yet a column in relationship.entity_facts.
 * Use ``src`` for source attribution until that column is added.
 */
export type EntityFact = components["schemas"]["EntityFactEntry"];

/** Validity filter for the facts drill (active rows vs. superseded history). */
export type EntityFactsValidity = "active" | "superseded";

/** Query parameters for the entity facts drill endpoint (keyset paginated). */
export interface EntityFactsParams {
  /** Restrict to a single predicate. */
  predicate?: string;
  /** ``"active"`` (default) or ``"superseded"`` (the Workbench history view). */
  validity?: EntityFactsValidity;
  /**
   * ``"identity"`` (default; triple store only) or ``"all"`` (additionally
   * appends labeled narrative-store rows after the identity page).
   */
  store?: "identity" | "all";
  /** Page size (max 200). */
  limit?: number;
  /** Opaque keyset cursor from a prior response's ``next_cursor``. */
  cursor?: string;
}

// ---------------------------------------------------------------------------
// Entity v3 quick-refresh endpoints (sparkline / delta / core-dates)
// GET /api/butlers/relationship/entities/{id}/activity?bins=daily
// POST /api/butlers/relationship/entities/{id}/view-mark
// GET /api/butlers/relationship/entities/{id}/delta-facts
// GET /api/butlers/relationship/entities/{id}/core-dates
// bu-xzh76 (FE half of bu-bjvny / PR #2183)
// ---------------------------------------------------------------------------

/**
 * One day's activity count for the 90-day sparkline.
 *
 * ``date`` is an ISO calendar date (``YYYY-MM-DD``). ``count`` is the number of
 * merged activity entries (relationship facts + chronicler episodes) on that
 * day. Zero-activity days are present with ``count=0`` — the sparkline renders
 * quiet days honestly rather than collapsing them out.
 */
export type ActivityBin = components["schemas"]["ActivityBin"];

/** One canonical row in the merged entity activity stream. */
export type EntityActivityItem = components["schemas"]["ActivityEntry"];

/** Response for GET /api/relationship/entities/{id}/activity. */
export type EntityActivityResponse = components["schemas"]["ActivityResponse"];

/**
 * One fact that changed since the entity's view mark.
 *
 * Carries the same provenance shape as the facts-drill rows so the detail page
 * can highlight the delta in place. ``store`` discriminates identity vs
 * narrative origin; ``changed_at`` is the per-store change timestamp that beat
 * the view mark.
 */
export type DeltaFactEntry = components["schemas"]["DeltaFactEntry"];

/**
 * Response for GET /api/butlers/relationship/entities/{id}/delta-facts.
 *
 * ``marked_at`` is the view mark the delta was computed against (``null`` on a
 * first visit — no mark row exists yet, so ``items`` is empty and the frontend
 * renders no banner). The endpoint never moves the mark; the caller posts the
 * mark afterwards via the view-mark endpoint.
 */
export type DeltaFactsResponse = components["schemas"]["DeltaFactsResponse"];

/**
 * A date-kind fact with its owner-relevant next occurrence.
 *
 * Server-extracted from the facts API (not client-side string matching).
 * ``predicate`` is the date-kind predicate (e.g. ``has-birthday``). ``value`` is
 * the raw stored object (an ISO ``YYYY-MM-DD`` or ``--MM-DD`` partial date).
 * ``next_occurrence`` is the next calendar occurrence of (month, day) on or
 * after the request date; ``days_until`` is the integer day count to it.
 */
export type CoreDateEntry = components["schemas"]["CoreDateEntry"];

/**
 * Response for GET /api/butlers/relationship/entities/{id}/core-dates.
 *
 * ``items`` are date-kind facts ordered by ``days_until`` ascending (the
 * soonest upcoming date first), so the detail page surfaces the next occurrence
 * without client-side sorting.
 */
export type CoreDatesResponse = components["schemas"]["CoreDatesResponse"];

// ---------------------------------------------------------------------------
// Secrets v2 — breaks catalogue (GET /api/secrets/breaks-catalogue)
// bu-qo3sf
// ---------------------------------------------------------------------------

/**
 * One entry from the provider_feature_catalogue table.
 *
 * Returned by GET /api/secrets/breaks-catalogue?provider=<p>
 */
export type BreakEntry = components["schemas"]["BreakEntry"];

/** Query parameters for the breaks-catalogue endpoint. */
export interface BreaksCatalogueParams {
  /** Provider slug to filter by. When omitted, full catalogue is returned. */
  provider?: string;
}

/**
 * A CLI runtime token row as returned by GET /api/secrets/inventory.
 *
 * Maps to CliRuntimeSummary in the backend secrets_v2 router — the published
 * projection of CliRuntime. The inventory keeps the raw `key` but omits the
 * operator-authored `category` and `description` labels (bu-y5uq4). The
 * probe's free-text `message` and the cached `last_test_message` are not on
 * the wire (bu-iph56); do not add them back.
 */
export interface SecretsCliRaw {
  key: string;
  state: string;
  fingerprint: string | null;
  /** butler_secrets.created_at (real; bu-6v1hx). */
  issued?: string | null;
  /** butler_secrets.expires_at (real; bu-6v1hx). */
  expires?: string | null;
  last_verified: string | null;
  test: SecretsCredentialTestOutcome | null;
}

/**
 * A system credential row as returned by GET /api/secrets/inventory.
 *
 * Maps to SystemSecretSummary in the backend secrets_v2 router — the published
 * projection of SystemSecret. Probe messages and audit note free text are not
 * on the wire (bu-iph56); the raw `key` survives for operator identification,
 * while the operator-authored `category` and `description` labels are omitted
 * from this inventory projection (bu-y5uq4).
 */
export interface SecretsSystemRaw {
  key: string;
  state: string;
  fingerprint: string | null;
  last_verified: string | null;
  butler: string;
  test: SecretsCredentialTestOutcome | null;
  /**
   * Last few public.audit_log rows for this credential (target='s:<key>'),
   * newest first, without their free-text notes (bu-iph56). Real data
   * (bu-6v1hx); empty when nothing has ever been logged for this key. May be
   * absent on older backends (treat as []).
   */
  audit?: SecretsCredentialAuditOutcome[];
  /**
   * When true, the passport renders the row read-only (generic editor suppressed).
   * Shared-public rows (butler="shared-public") are NOT flagged read_only —
   * they are fully editable via target="shared-public". May be absent on older
   * backends (treat missing as false).
   */
  read_only?: boolean;
  /**
   * Statically known consumers of this key (bu-xzaxm) — e.g. the email
   * module for BUTLER_EMAIL_ADDRESS. Sourced from a hand-maintained
   * key->consumer map in secrets_v2.py, not runtime tracing. Empty/absent
   * means "no known consumer in the static map", NEVER "verified nobody
   * depends on this" — the frontend must render that as "usage not
   * tracked", not a confident all-clear. May be absent on older backends.
   */
  used_by?: string[];
}

/**
 * Content-blind probe outcome for a credential (bu-iph56).
 *
 * Maps to CredentialTestOutcome in the backend secrets_v2 router. The
 * distinction from `SecretsProbeResult` is deliberate and load-bearing: this
 * shape has no `message`, because a probe message can echo a provider response
 * or the credential's own content. Do not widen it to include one.
 *
 * The backend serialises `code` and `at` on every response, including when
 * their values are null; neither detail route excludes unset or null fields.
 */
export type SecretsCredentialTestOutcome = components["schemas"]["CredentialTestOutcome"];

/**
 * Latest content-blind probe outcome for one capability family of a user
 * credential (bu-iph56).
 *
 * Maps to CredentialCapabilityOutcome in the backend secrets_v2 router.
 * `capability` is always a member of the backend's fixed CAPABILITY_VOCABULARY
 * — 'calendar' | 'gmail' | 'drive' | 'health' | 'connectivity' | 'other' —
 * never a raw scope identifier.
 */
export type SecretsCredentialCapabilityOutcome = components["schemas"]["CredentialCapabilityOutcome"];

/**
 * One audit-log entry for a credential, without its free-text `note`
 * (bu-iph56).
 *
 * Maps to CredentialAuditOutcome in the backend secrets_v2 router. `note` is
 * the only operator-authored field on an audit row and the backend drops it on
 * read, for every writer, so there is nothing here to render.
 */
export type SecretsCredentialAuditOutcome = components["schemas"]["CredentialAuditOutcome"];

/**
 * A user credential row as returned by GET /api/secrets/inventory.
 *
 * Maps to UserSecretSummary in the backend secrets_v2 router — the
 * content-blind projection of that router's internal UserSecret record
 * (bu-iph56, owner decision 2026-08-13).
 *
 * Every field here is a database identifier, a timestamp, a derived hash, a
 * provider slug from the backend's fixed USER_PROVIDER_VOCABULARY, or a member
 * of its fixed CAPABILITY_VOCABULARY. The persisted credential type and label,
 * raw OAuth scope identifiers, probe messages, and audit note free text are
 * not on the wire and will not come back — the passport renders capability
 * categories instead. Do not re-add them here to make a component compile.
 */
export interface SecretsUserRaw {
  id: string;
  entity_id: string;
  /**
   * Provider slug clamped server-side to USER_PROVIDER_VOCABULARY (the
   * PROVIDER_CATALOG keys plus 'email' and 'other'). Never the raw
   * entity_info.type. Uncatalogued credentials arrive as 'other', so two of
   * them on one entity share a single passport row — a display grouping, not
   * a claim that they are the same credential.
   */
  provider: string;
  state: string;
  fingerprint: string | null;
  /** entity_info.created_at (real; bu-6v1hx). */
  issued?: string | null;
  /**
   * Real only for Google test-mode accounts, synthesized server-side from
   * google_accounts.last_token_refresh_at + the known 7-day test-mode
   * lifetime (bu-1lb5j). entity_info has no expires_at column of its own, so
   * every other provider stays null — no fabricated expiry.
   */
  expires?: string | null;
  last_verified: string | null;
  test: SecretsCredentialTestOutcome | null;
  /**
   * Capability categories this credential's provider needs, derived
   * server-side from public.provider_feature_catalogue.required_scopes.
   * Empty means "no capability is recorded", never "unknown".
   */
  capabilities_required?: string[];
  /**
   * Capability categories actually granted. A real source only exists for
   * Google today (public.google_accounts.granted_scopes); every other provider
   * stays empty — there is no per-credential granted-scope tracking for them.
   */
  capabilities_granted?: string[];
  /**
   * Last few public.audit_log rows for this credential (target='u:<provider>'),
   * newest first. Real data (bu-6v1hx); empty when nothing has ever been
   * logged for this credential.
   */
  audit?: SecretsCredentialAuditOutcome[];
  /**
   * Per-capability probe state (bu-4v5es) — e.g. calendar/gmail/drive/health
   * for Google, a single 'connectivity' entry for every other provider.
   * Empty/absent until the credential has been probed at least once.
   */
  capabilities?: SecretsCredentialCapabilityOutcome[];
}

/**
 * Identity metadata for one entity referenced by the inventory.
 *
 * Returned in the top-level ``identities`` array alongside credential
 * families so the identity switcher can show real names and roles without
 * N round-trips per entity_id.
 *
 * Maps to IdentityInfo in the backend secrets_v2 router.
 */
export type SecretsIdentityInfo = components["schemas"]["IdentityInfo"];

/**
 * Provider display metadata as returned by the backend catalog.
 *
 * Maps to ProviderMetadata in src/butlers/secrets_provider_catalog.py and
 * ProviderInfo in frontend/src/components/secrets/passport/types.ts.
 */
export interface SecretsProviderInfo {
  id: string;
  label: string;
  glyph: string;
  kind: "oauth" | "token" | "apikey" | "webhook";
  authority: string;
  brief: string;
  cadence: string;
}

/** Query parameters for GET /api/secrets/inventory. */
export interface SecretsInventoryParams {
  /**
   * Entity UUID to filter user credentials by.
   * When omitted, the owner identity is used (projection-lens semantics).
   */
  identity?: string;
}

/**
 * Content-blind evidence payload for a single system-scoped credential.
 *
 * Returned by GET /api/secrets/system/<key>
 * Maps to SystemCredentialDetail in secrets_v2.py.
 *
 * The probe's free-text `message` and every audit `note` are absent: audit /
 * probe / failure free text is off this wire regardless of credential family
 * (owner decision, 2026-08-13). `breaks` is absent too — nothing ever
 * populated it, so it was dropped rather than published as an empty array.
 */
export type SecretsSystemCredentialDetail = components["schemas"]["SystemCredentialDetail"];

/**
 * Content-blind evidence payload for a single CLI runtime token.
 *
 * Returned by GET /api/secrets/cli/<id>
 * Maps to CliCredentialDetail in secrets_v2.py.
 *
 * Capability evidence is published as fixed-vocabulary capability names, never
 * as raw OAuth scope identifiers. `last_used` is absent because nothing
 * persists it, and the probe's free-text `message` is off this wire.
 */
export type SecretsCliDetail = components["schemas"]["CliCredentialDetail"];

/**
 * Response payload for POST /api/secrets/user/<provider>/rotate.
 * Returns ApiResponse<SecretsUserDetail> (updated credential).
 */
export type SecretsRotateUserRequest = components["schemas"]["RotateRequest"];

/**
 * Request body for POST /api/secrets/system/<key>.
 * Maps to SystemSetRequest in secrets_v2.py.
 */
export type SecretsSystemSetRequest = components["schemas"]["SystemSetRequest"];

/** Query params for GET /api/secrets/audit/<scope>/<key>. */
export interface SecretsAuditParams {
  limit?: number;
}

/** Request body for POST /entities/{id}/contacts. */
export type AddEntityContactRequest = components["schemas"]["AddContactRequest"];

/** Request body for PUT /entities/{id}/preferred-channel. */
export type SetPreferredChannelRequest = components["schemas"]["SetPreferredChannelRequest"];

/** Request body for PUT /entities/{id}/contacts/{predicate}/{value_hash}. */
export type UpdateEntityContactRequest = components["schemas"]["UpdateContactRequest"];

// ---------------------------------------------------------------------------
// Timeline saved views (bu-vgj88)
// ---------------------------------------------------------------------------

/**
 * Filter state captured in a saved view's filter_spec.
 *
 * Keys are frontend-driven and may evolve without a schema migration.
 * Unknown keys are preserved on round-trip.
 */
export interface TimelineSavedViewFilterSpec {
  /** Active status filters (array of IngestionEventStatus). */
  statuses?: string[];
  /** Time-range selection: "1h" | "24h" | "7d". */
  range?: string;
  /** Search query string. */
  q?: string;
  /** Channel filter (comma-separated). */
  channels?: string;
  [key: string]: unknown;
}

/** A single persisted saved view returned from GET /api/timeline/saved-views. */
export type TimelineSavedViewEntry = components["schemas"]["SavedViewEntry"];

/** Request body for POST /api/timeline/saved-views. */
export type TimelineSavedViewCreateRequest = components["schemas"]["SavedViewCreateRequest"];

/** Request body for PATCH /api/timeline/saved-views/{id}. */
export type TimelineSavedViewUpdateRequest = components["schemas"]["SavedViewUpdateRequest"];

// ---------------------------------------------------------------------------
// Proactive insight candidates (bu-sqjc7.3 / bu-w7b18.1)
// Read from GET /api/switchboard/insights?butler=health&status=pending
// ---------------------------------------------------------------------------

/**
 * A single proactive-insight candidate from ``public.insight_candidates``.
 *
 * Mirrors the Switchboard InsightCandidate model (roster/switchboard/api/models.py).
 * The Switchboard role is the only butler role with SELECT on this table.
 */
export type InsightCandidate = components["schemas"]["InsightCandidate"];

/** Query parameters for GET /api/switchboard/insights. */
export interface InsightCandidatesParams {
  butler?: string;
  status?: string;
  limit?: number;
}

export type InsightFeedbackVerdict = components["schemas"]["InsightFeedbackResponse"]["verdict"];

// ---------------------------------------------------------------------------
// Rule-promotion approvals surface (bu-o62bc, bead 4)
// ---------------------------------------------------------------------------

/** A pending rule-promotion suggestion needing owner action (a confirm card). */
export type RulePromotionSuggestion = components["schemas"]["RulePromotionSuggestion"];

/** An auto-applied (auto-minted) rule-promotion, shown informationally. */
export type RulePromotionAutoApplied = components["schemas"]["RulePromotionAutoApplied"];

/** Aggregate rule-promotion metrics for the approvals dashboard tile (bead 6). */
export type RulePromotionStats = components["schemas"]["RulePromotionStats"];

/** Body for dismissing a pending rule-promotion suggestion. */
export type RulePromotionDismissRequest = components["schemas"]["RulePromotionDismissRequest"];

/**
 * One row of public.delegation_ledger -- a cross-butler question/answer
 * (bu-gxmfx). `wake_*`/`answer_digest` (bu-ep4ks.3) widen this entry with the
 * return-callback/task lifecycle migration core_181 added: `wake_state`
 * defaults to "not_applicable" (no v1 answer yet); "callback_failed" and
 * "task_conflict" are the two failure states the wake protocol introduces --
 * before this widening they rendered as an ordinary answered row.
 */
export type DelegationLedgerEntry = components["schemas"]["DelegationLedgerEntry"];

/**
 * One row of public.butler_subscriptions -- a standing (subscriber_butler,
 * event_type) registration on the domain-event bus (bu-317s5).
 */
export type SubscriptionEntry = components["schemas"]["SubscriptionEntry"];

/**
 * The latest step of a subscriber's reaction lifecycle for one event
 * (bu-6jv4m.8). Distinct from `DeliveryEntry.status`: that says the wake was
 * handed over, this says what the subscriber did about it.
 */
export type ReactionSummary = components["schemas"]["ReactionSummary"];

/**
 * One public.domain_event_reactions row -- an append-only step in one
 * subscriber's reaction to one event (bu-6jv4m.8).
 */
export type ReactionEntry = components["schemas"]["ReactionEntry"];

/**
 * One materialized publisher-owned event contract from
 * public.domain_event_contracts (bu-6jv4m.8) -- each butler's published copy
 * of its own `roster/<butler>/domain_events.toml` declaration, refreshed at
 * startup. Read surface only: the TOML is the source of truth.
 */
export type ContractEntry = components["schemas"]["ContractEntry"];

/**
 * One public.domain_event_deliveries row joined with its event -- a fan-out
 * delivery attempt to (or from) a butler on the domain-event bus (bu-317s5).
 */
export type DeliveryEntry = components["schemas"]["DeliveryEntry"];

// ---------------------------------------------------------------------------
// Lifestyle taste ledger (bu-2jtfw.10)
// ---------------------------------------------------------------------------

/** Ledger-wide taste counts. GET /api/lifestyle/taste/summary. */
export type TasteSummaryQueryName = components["schemas"]["TasteSummaryQueryName"];

export type TasteSummary = components["schemas"]["TasteSummary"];

export interface TasteWorksParams {
  kind?: string;
  offset?: number;
  limit?: number;
}

export interface TasteVerdictsParams {
  offset?: number;
  limit?: number;
}

export interface FactAttribution {
  verified?: boolean;
  confirmation_status?: "owner_asserted" | "owner_confirmed" | "legacy_verified" | "unconfirmed";
  content_authority?: "owner" | "owner_device" | "third_party" | "system" | "mixed" | null;
  reported_by?: {
    entity_id: string | null;
    name: string | null;
    availability: "available" | "unavailable" | "deleted" | "forgotten" | "merged" | "unresolved" | "legacy_unknown" | "system";
  } | null;
  confirmed_at?: string | null;
  confirmed_by_entity_id?: string | null;
  confirmation_source?: string | null;
}
