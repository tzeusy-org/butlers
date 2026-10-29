import type { ApiArguments, ApiMethod, ApiOptions, ApiPath, ApiResult, ApiQuery } from "./request-types";
import { ownerFetch } from "./owner-session";
import { standingRuleHeaders } from "./approval-step-up";
/**
 * Typed fetch wrapper for the Butlers dashboard API.
 *
 * Uses native `fetch` — no external HTTP libraries required.
 */

import type { ApprovalActionParams, ApprovalApproveRequest, ApprovalDeferRequest, ApprovalDenyRequest, ApprovalAbandonRequest, ApprovalRuleCreateRequest, ApprovalRuleFromActionRequest, ApprovalRuleParams, ApprovalsPolicy, AutonomySuggestionDismissRequest, AutonomySuggestionParams, AuditLogParams, CalendarAuditParams, ConflictScanParams, CalendarInvitationsParams, CalendarResponseRequest, CalendarSourceToggleRequest, CalendarProposalAcceptRequest, CalendarWorkspaceParams, CalendarWorkspaceSearchParams, CalendarWorkspaceButlerMutationRequest, CalendarWorkspaceButlerEventPreviewRequest, CalendarWorkspaceSyncRequest, CalendarWorkspaceUserMutationRequest, QuickAddParseRequest, SetPrimaryCalendarRequest, DispatchAttemptsParams, ErrorResponse, GroupParams, NotificationParams, NotificationStatsParams, ScheduleCreate, ScheduleToggleRequest, ScheduleUpdate, SessionParams, ButlerSessionParams, StateSetRequest, TimelineParams, TimelineHistogramParams, TimelineAttentionParams, ButlerMcpToolCallRequest, ConditionCreateRequest, ConditionUpdateRequest, DoseLogRequest, MealParams, MealCreateRequest, MealUpdateRequest, MeasurementParams, MeasurementCreateRequest, MeasurementUpdateRequest, MedicationParams, MedicationCreateRequest, MedicationUpdateRequest, ResearchCreateRequest, ResearchParams, ResearchUpdateRequest, SymptomParams, SymptomCreateRequest, SymptomUpdateRequest, RoutingLogParams, CreateEntityInfoRequest, EntityDetailParams, EntityParams, UpdateEntityRequest, EpisodeParams, FactParams, MemoryInspectParams, ReembedRunRequest, UpdateRetentionPoliciesRequest, RuleParams, IngestionEventsParams, IngestionHistogramParams, IngestionWindowRollupParams, RulePromotionDismissRequest, IngestionRuleCreate, IngestionRuleUpdate, IngestionRuleListParams, IngestionRuleTestRequest, ChannelDefaultUpdate, PriorityContactAddRequest, PriorityContactListParams, ModelCatalogCreate, ModelCatalogUpdate, ModelPriorityDelta, ButlerModelOverridesWrite, ButlerModelOverrideReason, TokenLimitsRequest, ResetUsageRequest, SpotifyConfigRequest, HomeAssistantConfigRequest, HomePersonMappingInput, ConversationListParams, CreateConversationRequest, SendMessageRequest, MessageSearchParams, TelegramSendCodeRequest, TelegramVerifyCodeRequest, SteamConnectRequest, QaCaseJournalParams, QaCasesParams, QaDismissRequest, QaPatrolsParams, QaInvestigationsParams, QaRepoConfigUpdate, QaGitAuthorUpdate, QaAllowedRepoCreate, QaAllowedRepoPatch, RuntimeConfigPatch, ChroniclerAggregateByCategoryParams, ChroniclerAggregateByDayParams, ChroniclerCreateRoutineRequest, ChroniclerDayCloseParams, ChroniclerDayCloseRefreshRequest, ChroniclerEpisodesParams, ChroniclerEventsParams, ChroniclerBalanceParams, ChroniclerTrendsParams, ChroniclerRollupsParams, ChroniclerWhoYouWereWithParams, ChroniclerCorrectionPromptsParams, ChroniclerUpdateRoutineRequest, SubmitCorrectionRequest, CreateEntityNoteRequest, CreateEntityInteractionRequest, CreateEntityGiftRequest, NeighboursParams, RelationshipEntityListParams, CompareEntitiesRequest, DismissEntityPairRequest, MergeRelationshipEntitiesRequest, PromoteRelationshipEntityRequest, CreateRelationshipEntityRequest, FinanceTransactionListParams, FinanceSubscriptionListParams, FinanceAccountListParams, FinanceSpendingSummaryParams, FinanceUpcomingBillsParams, FinanceBulkUpdateRequest, TravelTripsParams, HomeAtmosphereLocationUpdate, ButlerLogsParams, HourlyActivityParams, DailyActivityParams, SessionKindsParams, LatencyStatsParams, FrictionSummaryParams, ActivityFeedParams, PromptUpdateRequest, KillRequest, EntityFactsParams, AddEntityContactRequest, UpdateEntityContactRequest, SetPreferredChannelRequest, TimelineSavedViewCreateRequest, TimelineSavedViewUpdateRequest } from "./types.ts";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API_BASE_URL: string =
  import.meta.env.VITE_API_URL ?? "/api";

/**
 * Resolve an API-relative path (as carried in a backend `redirect_url`) into a
 * URL the browser can actually navigate to.
 *
 * The backend cannot know where its own API is mounted: the same app is served
 * at `/butlers` (API at `/butlers-api/api`) and `/butlers-dev` (API at
 * `/butlers-dev-api/api`) behind Tailscale path mounts, so a backend-built
 * `/api/...` path is a dead link on every deployment except a bare-root one.
 * Backend payloads therefore carry the path *below* the API root and the client
 * prepends `API_BASE_URL`. Absolute URLs pass through untouched.
 */
export function resolveApiHref(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE_URL}${path}`;
}

// ---------------------------------------------------------------------------
// Error class
// ---------------------------------------------------------------------------

/** Error thrown when an API request fails. */
export class ApiError extends Error {
  /** Machine-readable error code from the backend (or a fallback). */
  readonly code: string;
  /** HTTP status code of the response. */
  readonly status: number;
  /**
   * Structured error detail body, when the backend returned a JSON object
   * `detail` (e.g. the 409 bulk-retry `{error, unsafe_events}` rejection).
   * `undefined` when the backend returned a plain string/array detail.
   */
  readonly detail?: unknown;

  constructor(code: string, message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }
}

// ---------------------------------------------------------------------------
// Base fetch wrapper
// ---------------------------------------------------------------------------

/**
 * Every request gives up after this long rather than hanging indefinitely
 * (JARVIS audit move 10). A stuck backend should surface as a fast, honest
 * timeout error, not a spinner that never resolves.
 */
export const API_REQUEST_TIMEOUT_MS = 15_000;

/**
 * Map a failed response body onto ApiError fields -- the one mapping every
 * client call shares (bu-mia49k).
 *
 * - ErrorResponse envelope `{ error: { code, message, details } }`.
 * - FastAPI string detail `{ detail: "..." }`.
 * - Pydantic validation list `{ detail: [{ msg }, ...] }`.
 * - Structured dict detail `{ detail: { code?, message?, error?, ... } }`
 *   (e.g. 409 temporal_mutator_unsupported, contact_fact_changed, the
 *   unsafe-channel rejection): message is `detail.message`, else
 *   `detail.error`, else the JSON of the dict; code is `detail.code`. The
 *   raw dict stays on `detail` for callers that need structured fields
 *   (e.g. bulk-retry's `unsafe_events`).
 *
 * A non-JSON body keeps the status-derived defaults.
 */
async function parseErrorBody(
  response: Response,
  fallbackMessage: string,
): Promise<{ code: string; message: string; detail: unknown }> {
  let code = "UNKNOWN_ERROR";
  let message = response.statusText || fallbackMessage;
  let detail: unknown;

  try {
    const body = await response.json();
    if (body.error) {
      code = (body as ErrorResponse).error.code;
      message = (body as ErrorResponse).error.message;
      detail = (body as ErrorResponse).error.details ?? undefined;
    } else if (typeof body.detail === "string") {
      message = body.detail;
    } else if (Array.isArray(body.detail) && body.detail.length > 0) {
      message = body.detail
        .map((d: Record<string, unknown>) => String(d.msg ?? d.message ?? JSON.stringify(d)))
        .join("; ");
    } else if (body.detail !== null && typeof body.detail === "object") {
      const det = body.detail as Record<string, unknown>;
      message =
        typeof det.message === "string"
          ? det.message
          : typeof det.error === "string"
            ? det.error
            : JSON.stringify(det);
      if (typeof det.code === "string") code = det.code;
      detail = det;
    }
  } catch {
    // Response body is not valid JSON -- keep the status-derived defaults.
  }

  return { code, message, detail };
}

/**
 * Typed fetch wrapper that prepends `API_BASE_URL`, sets JSON headers,
 * aborts after {@link API_REQUEST_TIMEOUT_MS}, and throws {@link ApiError}
 * on non-ok responses (or on timeout).
 *
 * A caller-supplied `options.signal` (e.g. TanStack Query's queryFn signal,
 * which fires on unmount/refetch-superseded) is still honored: aborting it
 * aborts the underlying fetch immediately, same as before this wrapper
 * introduced its own internal timeout controller.
 */
export async function apiFetch<
  P extends ApiPath,
  M extends ApiMethod<P> = Extract<"GET", ApiMethod<P>>,
>(route: P, ...args: ApiArguments<P, M>): Promise<ApiResult<P, M>> {
  const options = (args[0] ?? {}) as ApiOptions<P, M>;
  const { timeoutMs = API_REQUEST_TIMEOUT_MS, path, query, body, omitEmptyQuery, ...fetchOptions } = options;
  const pathname = route.replace(/\{([^}]+)\}/g, (_, key: string) => {
    const value = (path as Record<string, string | number> | undefined)?.[key];
    if (value === undefined) throw new Error(`Missing API path parameter: ${key}`);
    return encodeURIComponent(String(value));
  });
  const search = new URLSearchParams();
  const emptyOmissions = new Set(omitEmptyQuery?.map(String));
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === "" && emptyOmissions.has(key)) continue;
    if (value !== undefined && value !== null) for (const item of Array.isArray(value) ? value : [value]) search.append(key, String(item));
  }
  const suffix = search.toString();
  const url = `${API_BASE_URL}${pathname}${suffix ? `?${suffix}` : ""}`;
  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const callerSignal = fetchOptions.signal;
  const forwardAbort = () => controller.abort();
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort();
    else callerSignal.addEventListener("abort", forwardAbort);
  }

  let response: Response;
  try {
    response = await ownerFetch(url, {
      ...fetchOptions,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...fetchOptions.headers,
      },
    });
  } catch (err) {
    // Distinguish OUR timeout from a caller-initiated cancel (e.g. query
    // unmount) — the latter must keep surfacing as a plain AbortError so
    // TanStack Query's own cancellation handling still recognizes it.
    if (timedOut) {
      throw new ApiError(
        "TIMEOUT",
        `Request timed out after ${timeoutMs / 1000}s`,
        0,
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
    if (callerSignal) callerSignal.removeEventListener("abort", forwardAbort);
  }

  if (!response.ok) {
    const { code, message, detail } = await parseErrorBody(response, "Request failed");
    throw new ApiError(code, message, response.status, detail);
  }

  if (response.status === 204) {
    return undefined as ApiResult<P, M>;
  }

  return (await response.json()) as ApiResult<P, M>;
}

// ---------------------------------------------------------------------------
// Endpoint functions
// ---------------------------------------------------------------------------

/** Fetch the health-check endpoint. */
export function getHealth() {
  return apiFetch("/health");
}

/** Fetch all butlers. */
export function getButlers() {
  return apiFetch("/butlers");
}

/**
 * Fetch the consolidated fleet status board in one round trip (bu-86c4c.17).
 *
 * Replaces the former per-butler runtime-config + hourly-activity fan-out:
 * this single endpoint returns rows (with the canonical liveness verdict and
 * cron-expectation join already computed server-side) plus fleet-wide
 * aggregates. See useButlerStatusBoard for the camelCase mapping consumed by
 * the /butlers status board.
 */
export function getButlersBoard() {
  return apiFetch("/butlers/board");
}

/** Fetch a single butler by name. */
export function getButler(name: string) {
  return apiFetch("/butlers/{name}", { path: { name: name } });
}

/** Fetch configuration files for a specific butler. */
export function getButlerConfig(name: string) {
  return apiFetch("/butlers/{name}/config", { path: { name: name } });
}

/** Fetch per-module health status for a specific butler. */
export function getButlerModules(name: string) {
  return apiFetch("/butlers/{name}/modules", { path: { name: name } });
}

/**
 * Build the filter query params shared by every sessions route.
 *
 * Only the params EVERY session route declares live here; pagination and
 * route-specific params are added by each caller so no route is sent a param
 * its backend never declares (bu-4u5l6). The three routes have disjoint
 * pagination models — keyset `cursor` for `/sessions`, none for
 * `/sessions/aggregate`, offset for `/butlers/{name}/sessions` — so a single
 * shared builder could not honestly serve all three.
 */
function sessionCommonSearchParams(params?: Omit<SessionParams, "status">): Pick<ApiQuery<"/butlers/{name}/sessions">, "trigger_source" | "request_id" | "from_date" | "to_date"> {
  const sp: Pick<ApiQuery<"/butlers/{name}/sessions">, "trigger_source" | "request_id" | "from_date" | "to_date"> = {};
  if (params?.trigger_source != null && params.trigger_source !== "")
    sp["trigger_source"] = params.trigger_source;
  if (params?.request_id != null && params.request_id !== "")
    sp["request_id"] = params.request_id;
  // Backend uses from_date/to_date; SessionParams uses since/until as field names.
  if (params?.since != null && params.since !== "") sp["from_date"] = params.since;
  if (params?.until != null && params.until !== "") sp["to_date"] = params.until;
  return sp;
}

/**
 * Fetch a keyset-paginated list of sessions across all butlers.
 *
 * Returns a {@link KeysetResponse}: `meta.next_cursor` is an opaque forward
 * cursor (pass it back as `params.cursor` for the next/older page) and
 * `meta.has_more` indicates whether more rows exist. There is no `total` —
 * the cross-butler list dropped the expensive count for keyset performance.
 */
export function getSessions(
  params?: SessionParams,
) {
  const sp: ApiQuery<"/sessions"> = sessionCommonSearchParams(params);
  if (params?.status != null && params.status !== "all") sp.status = params.status;
  // /sessions: keyset (cursor) pagination, cross-butler (accepts a butler filter).
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.cursor != null && params.cursor !== "") sp["cursor"] = params.cursor;
  if (params?.butler != null && params.butler !== "") sp["butler"] = params.butler;
  return apiFetch("/sessions", { query: sp });
}

/**
 * Fetch a window-scoped, filter-aware session aggregate across all butlers.
 *
 * Reuses the SAME filter mapping as {@link getSessions} (since→from_date,
 * until→to_date) but is NOT paginated — the counts are window-true, not
 * page-scoped. Callers should key any cache on the filter params only, never
 * the cursor, so the rollup recomputes on filter change but not on paging.
 */
export function getSessionAggregate(
  params?: SessionParams,
) {
  const sp: ApiQuery<"/sessions/aggregate"> = sessionCommonSearchParams(params);
  if (params?.status != null && params.status !== "all") sp.status = params.status;
  // /sessions/aggregate: NOT paginated (window-true counts). Accepts the
  // cross-butler filter and the trigger-breakdown toggle.
  if (params?.butler != null && params.butler !== "") sp["butler"] = params.butler;
  if (params?.include_trigger_breakdown) sp["include_trigger_breakdown"] = true;
  return apiFetch("/sessions/aggregate", { query: sp });
}

/** Fetch a single session by ID (cross-butler). */
export function getSession(id: string) {
  return apiFetch("/sessions/{session_id}", { path: { session_id: id } });
}

/** Fetch the separately protected effective-system-prompt receipt on demand. */
export function getSessionPrompt(id: string) {
  return apiFetch("/sessions/{session_id}/prompt", { path: { session_id: id } });
}

/** Fetch sessions for a specific butler. */
export function getButlerSessions(
  name: string,
  params?: ButlerSessionParams,
) {
  const sp: ApiQuery<"/butlers/{name}/sessions"> = sessionCommonSearchParams(params);
  if (params?.status != null && params.status !== "all") sp.status = params.status;
  // /butlers/{name}/sessions: offset pagination; the butler is the path param
  // (never a redundant `?butler=`), and there is no keyset `cursor`.
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;

  return apiFetch("/butlers/{name}/sessions", { path: { name: name }, query: sp });
}

// ---------------------------------------------------------------------------
// Butler analytics (bu-iuol4.16)
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/analytics/hourly-activity */
export function getButlerHourlyActivity(
  name: string,
  params?: HourlyActivityParams,
) {
  const qs: ApiQuery<"/butlers/{name}/analytics/hourly-activity", "GET"> = {};
  if (params?.window_hours != null) qs["window_hours"] = params.window_hours;
  return apiFetch("/butlers/{name}/analytics/hourly-activity", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/analytics/daily-activity */
export function getButlerDailyActivity(
  name: string,
  params?: DailyActivityParams,
) {
  const qs: ApiQuery<"/butlers/{name}/analytics/daily-activity", "GET"> = {};
  if (params?.window_days != null) qs["window_days"] = params.window_days;
  return apiFetch("/butlers/{name}/analytics/daily-activity", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/analytics/session-kinds */
export function getButlerSessionKinds(
  name: string,
  params?: SessionKindsParams,
) {
  const qs: ApiQuery<"/butlers/{name}/analytics/session-kinds", "GET"> = {};
  if (params?.window_days != null) qs["window_days"] = params.window_days;
  return apiFetch("/butlers/{name}/analytics/session-kinds", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/analytics/latency-stats */
export function getButlerLatencyStats(
  name: string,
  params?: LatencyStatsParams,
) {
  const qs: ApiQuery<"/butlers/{name}/analytics/latency-stats", "GET"> = {};
  if (params?.window_days != null) qs["window_days"] = params.window_days;
  return apiFetch("/butlers/{name}/analytics/latency-stats", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/analytics/friction */
export function getButlerFrictionSummary(
  name: string,
  params?: FrictionSummaryParams,
) {
  const qs: ApiQuery<"/butlers/{name}/analytics/friction", "GET"> = {};
  if (params?.period != null) qs["period"] = params.period;
  return apiFetch("/butlers/{name}/analytics/friction", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/activity-feed */
export function getButlerActivityFeed(
  name: string,
  params?: ActivityFeedParams,
) {
  const qs: ApiQuery<"/butlers/{name}/activity-feed", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  return apiFetch("/butlers/{name}/activity-feed", { path: { name: name }, query: qs });
}

/** GET /api/butlers/{name}/memory/stats */
export function getButlerMemoryStats(
  name: string,
) {
  return apiFetch("/butlers/{name}/memory/stats", { path: { name: name } });
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

/** Build a URLSearchParams from notification query parameters.
 *
 * Empty strings and the sentinel value "all" are treated as "no filter" and
 * are intentionally omitted from the query string so the backend does not
 * add spurious WHERE clauses that would return zero rows.
 */
function notificationCommonSearchParams(params?: NotificationParams): ApiQuery<"/butlers/{name}/notifications", "GET"> {
  const sp: ApiQuery<"/butlers/{name}/notifications", "GET"> = {};
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.channel != null && params.channel !== "" && params.channel !== "all")
    sp["channel"] = params.channel;
  if (params?.status != null && params.status !== "" && params.status !== "all")
    sp["status"] = params.status;
  if (params?.since != null && params.since !== "") sp["since"] = params.since;
  if (params?.until != null && params.until !== "") sp["until"] = params.until;
  return sp;
}

/** Fetch a paginated list of notifications across all butlers. */
export function getNotifications(
  params?: NotificationParams,
) {
  const sp: ApiQuery<"/notifications"> = notificationCommonSearchParams(params);
  // Only the cross-butler /notifications route declares a `butler` filter.
  if (params?.butler != null && params.butler !== "") sp["butler"] = params.butler;
  return apiFetch("/notifications", { query: sp });
}

/**
 * Fetch aggregate notification statistics.
 *
 * `since`/`until` are optional window bounds (bu-y0v0c) -- omitted, this is
 * the all-time rollup; when set, every count is scoped to that `created_at`
 * window.
 */
export function getNotificationStats(
  params?: NotificationStatsParams,
) {
  const sp: ApiQuery<"/notifications/stats", "GET"> = {};
  if (params?.since != null && params.since !== "") sp["since"] = params.since;
  if (params?.until != null && params.until !== "") sp["until"] = params.until;
  return apiFetch("/notifications/stats", { query: sp });
}

/** Fetch notifications for a specific butler. */
export function getButlerNotifications(
  name: string,
  params?: NotificationParams,
) {
  // Butler-scoped route: the butler is the path param `{name}`, so no
  // redundant `?butler=` is sent (bu-4u5l6).
  const qs: ApiQuery<"/notifications"> = notificationCommonSearchParams(params);

  return apiFetch("/butlers/{name}/notifications", { path: { name: name }, query: qs });
}

/** Mark a single notification as read (flips failed → read). */
export function markNotificationRead(
  notificationId: string,
) {
  return apiFetch("/notifications/{notification_id}/read", { method: "PATCH", path: { notification_id: notificationId } });
}

/** Acknowledge all failed notifications in bulk (flips all failed → read). */
export function acknowledgeAllFailed() {
  return apiFetch("/notifications/ack-failed", { method: "POST" });
}

/**
 * Manually re-attempt delivery of a failed notification, right now, on the
 * same channel. Flips the original to `read` on the backend and returns the
 * new attempt's own outcome.
 */
export function retryNotification(
  notificationId: string,
) {
  return apiFetch("/notifications/{notification_id}/retry", { method: "POST", path: { notification_id: notificationId } });
}

/**
 * Re-attempt a failed notification on the owner's alternate channel
 * (telegram<->email). Same forward-link/outcome contract as retryNotification.
 */
export function escalateNotification(
  notificationId: string,
) {
  return apiFetch("/notifications/{notification_id}/escalate", { method: "POST", path: { notification_id: notificationId } });
}

// ---------------------------------------------------------------------------
// Attention ledger (bu-tdd4k.4) -- the ledger's first reader
// ---------------------------------------------------------------------------

import type { AttentionLedgerSummaryParams } from "./types.ts";

/**
 * Fetch the per-source (per-`origin_butler`) delivery-vs-suppression summary
 * -- the Trust Console panel's data source. Defaults to the last 7 days
 * server-side when `since` is omitted.
 */
export function getAttentionLedgerSummary(
  params?: AttentionLedgerSummaryParams,
) {
  const sp: ApiQuery<"/attention/ledger/summary", "GET"> = {};
  if (params?.since != null && params.since !== "") sp["since"] = params.since;
  if (params?.until != null && params.until !== "") sp["until"] = params.until;
  if (params?.intent != null && params.intent !== "") sp["intent"] = params.intent;
  if (params?.source != null && params.source !== "") sp["source"] = params.source;
  if (params?.origin_butler != null && params.origin_butler !== "")
    sp["origin_butler"] = params.origin_butler;
  return apiFetch("/attention/ledger/summary", { query: sp });
}

// ---------------------------------------------------------------------------
// Issues
// ---------------------------------------------------------------------------

/** Fetch grouped issues across all butlers.
 *
 * When `includeDismissed` is true, the server returns *only* the issues that
 * have been dismissed (acked) — each flagged `dismissed: true` — so the UI can
 * offer a restore affordance instead of the active feed.
 *
 * `window` bounds the audit-derived (grouped) issues server-side (bu-qvnce.13,
 * capped CTE) — e.g. "24h" | "7d" | "30d" | "all". Omitted entirely when
 * absent so the backend's own default (7d) applies.
 */
export function getIssues(
  params: { includeDismissed?: boolean; window?: string } = {},
) {
  const qs: ApiQuery<"/issues", "GET"> = {};
  if (params.includeDismissed) qs["include_dismissed"] = true;
  if (params.window) qs["window"] = params.window;

  return apiFetch("/issues", { query: qs });
}

/**
 * Acknowledge an issue group server-side so it persists across browsers.
 *
 * Acknowledge-until-recurrence (JARVIS audit move 6, bu-86c4c.15): pass the
 * issue's current `last_seen_at` as `lastSeenAt` so the server can detect a
 * later recurrence and automatically un-ack the group — this is NOT
 * dismiss-forever. Omitting it falls back to dismiss-forever for that row.
 */
export function dismissIssue(
  issueKey: string,
  lastSeenAt?: string | null,
) {
  return apiFetch("/issues/dismiss", { method: "POST",
    body: { issue_key: issueKey, last_seen_at: lastSeenAt ?? null } });
}

/** Undismiss (restore) a previously-dismissed issue group server-side.
 *
 * Mirrors {@link dismissIssue}; removes the persisted ack so the issue can
 * reappear in the active feed.
 */
export function undismissIssue(
  issueKey: string,
) {
  return apiFetch("/issues/dismiss/{issue_key}", { method: "DELETE", path: { issue_key: issueKey } });
}

// ---------------------------------------------------------------------------
// Costs
// ---------------------------------------------------------------------------

/** Fetch aggregate cost summary, optionally scoped to a time period or custom date range.
 *
 * When `from` and `to` are provided (YYYY-MM-DD strings) they take precedence
 * over `period` and the server computes the summary over [from, to] inclusive.
 * Callers are responsible for formatting dates in the intended timezone before
 * passing them here.
 *
 * When `butler` is provided, only that butler's data is included. Supported by
 * the backend since bu-iuol4.12.
 */
export function getCostSummary(
  period?: string,
  from?: string,
  to?: string,
  butler?: string,
) {
  const sp: ApiQuery<"/spend", "GET"> = {};
  if (from && to) {
    sp["from"] = from;
    sp["to"] = to;
  } else if (period) {
    sp["period"] = period;
  }
  if (butler) sp["butler"] = butler;
  return apiFetch("/spend", { query: sp });
}

/** Fetch daily spend breakdown, optionally scoped to a date range (YYYY-MM-DD) and/or a butler. */
export function getDailyCosts(
  from?: string,
  to?: string,
  butler?: string,
) {
  const params: ApiQuery<"/spend/daily", "GET"> = {};
  if (from) params["from"] = from;
  if (to) params["to"] = to;
  if (butler) params["butler"] = butler;

  // DailySpendResponse types `meta.unavailable_butlers` so the stacked chart can
  // footnote butlers dropped from the fan-out instead of letting them silently
  // vanish (bu-jad4j.3).
  return apiFetch("/spend/daily", { query: params });
}

/** Fetch most expensive sessions, optionally scoped to a date range (YYYY-MM-DD). */
export function getTopSessions(
  limit?: number,
  from?: string,
  to?: string,
) {
  const params: ApiQuery<"/spend/top-sessions", "GET"> = {};
  if (limit) params["limit"] = limit;
  if (from) params["from"] = from;
  if (to) params["to"] = to;
  // TopSessionsResponse types `meta.unavailable_butlers` so the evidence table
  // can name butlers dropped from the fan-out rather than reading as complete
  // (bu-jad4j.3).
  return apiFetch("/spend/top-sessions", { query: params });
}

/** Fetch per-schedule cost analysis (projected monthly USD per cron job), optionally scoped to a date range (YYYY-MM-DD). */
export function getCostsBySchedule(from?: string, to?: string) {
  const params: ApiQuery<"/spend/by-schedule", "GET"> = {};
  if (from) params["from"] = from;
  if (to) params["to"] = to;
  // ScheduleCostsResponse types `meta.unavailable_butlers` so the By Schedule
  // table can footnote butlers dropped from the fan-out instead of letting
  // their schedules silently vanish (bu-h3ej9).
  return apiFetch("/spend/by-schedule", { query: params });
}

/**
 * GET /api/dispatch/attempts — failover/quota-skip provenance rows
 *
 * `DispatchAttemptsParams` selects one query mode: session mode accepts
 * `session_id`, `logical_session_id`, or both and returns the matching
 * provenance sequence; fleet mode requires `outcome`, permits no session
 * selector, and powers the /spend fleet-halt state. Fleet filters
 * (`reason_prefix`, `since`, and `order`) are valid only with `outcome`.
 * The type prevents mode mixtures, and the backend repeats the validation for
 * untyped callers.
 */
export function getDispatchAttempts(
  params: DispatchAttemptsParams,
) {
  const query: ApiQuery<"/dispatch/attempts", "GET"> = {};
  if (params.session_id) query["session_id"] = params.session_id;
  if (params.logical_session_id) query["logical_session_id"] = params.logical_session_id;
  if (params.outcome) query["outcome"] = params.outcome;
  if (params.reason_prefix) query["reason_prefix"] = params.reason_prefix;
  if (params.since) query["since"] = params.since;
  if (params.order) query["order"] = params.order;
  if (params.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/dispatch/attempts", { query: query });
}

export function getFleetHaltAttention() {
  return apiFetch("/spend/runtime-attention");
}

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

/** Fetch all schedules for a specific butler. */
export function getButlerSchedules(name: string) {
  return apiFetch("/butlers/{name}/schedules", { path: { name: name } });
}

/** Create a new schedule for a specific butler. */
export function createButlerSchedule(
  name: string,
  body: ScheduleCreate,
) {
  return apiFetch("/butlers/{name}/schedules", { method: "POST",
      body: body, path: { name: name } });
}

/** Update an existing schedule for a specific butler. */
export function updateButlerSchedule(
  name: string,
  scheduleId: string,
  body: ScheduleUpdate,
) {
  return apiFetch("/butlers/{name}/schedules/{schedule_id}", { method: "PUT",
      body: body, path: { name: name, schedule_id: scheduleId } });
}

/** Delete a schedule for a specific butler. */
export function deleteButlerSchedule(
  name: string,
  scheduleId: string,
) {
  return apiFetch("/butlers/{name}/schedules/{schedule_id}", { method: "DELETE", path: { name: name, schedule_id: scheduleId } });
}

/** Trigger a schedule immediately (one-off dispatch). */
export function triggerButlerSchedule(
  name: string,
  scheduleId: string,
) {
  return apiFetch("/butlers/{name}/schedules/{schedule_id}/trigger", { method: "POST", path: { name: name, schedule_id: scheduleId } });
}

/** Toggle the enabled/disabled state of a schedule. */
export function toggleButlerSchedule(
  name: string,
  scheduleId: string,
  body: ScheduleToggleRequest,
) {
  return apiFetch("/butlers/{name}/schedules/{schedule_id}/toggle", { method: "PATCH", body: body, path: { name: name, schedule_id: scheduleId } });
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** Fetch skills available to a specific butler. */
export function getButlerSkills(name: string) {
  return apiFetch("/butlers/{name}/skills", { path: { name: name } });
}

// ---------------------------------------------------------------------------
// Logs (bu-iuol4.17)
// ---------------------------------------------------------------------------

/** Fetch recent log lines for a specific butler.
 *
 * @param name   Butler name.
 * @param params Optional filter/limit params.
 *               - level: minimum severity filter (DEBUG < INFO < WARN < ERROR)
 *               - since: ISO 8601 start timestamp
 *               - limit: maximum number of lines (default 100)
 */
export function getButlerLogs(
  name: string,
  params?: ButlerLogsParams,
) {
  const sp: ApiQuery<"/butlers/{name}/logs", "GET"> = {};
  if (params?.level) sp["level"] = params.level;
  if (params?.since) sp["since"] = params.since;
  if (params?.limit != null) sp["limit"] = params.limit;

  return apiFetch("/butlers/{name}/logs", { path: { name: name }, query: sp });
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** Fetch all state entries for a butler. */
export function getButlerState(name: string) {
  return apiFetch("/butlers/{name}/state", { path: { name: name } });
}

/** Set a state value for a butler (creates or updates). */
export function setButlerState(
  name: string,
  key: string,
  value: StateSetRequest["value"],
) {
  return apiFetch("/butlers/{name}/state/{key}", { method: "PUT",
      body: { value }, path: { name: name, key: key } });
}

/** Delete a state entry for a butler. */
export function deleteButlerState(
  name: string,
  key: string,
) {
  return apiFetch("/butlers/{name}/state/{key}", { method: "DELETE", path: { name: name, key: key } });
}

// ---------------------------------------------------------------------------
// Trigger
// ---------------------------------------------------------------------------

/**
 * Trigger a CC session for a specific butler. When `complexity` is omitted,
 * defaults to "workhorse" -- must be one of the backend's valid tiers
 * (reasoning/workhorse/cheap/specialty/local/legacy -- see
 * model_settings.py:_COMPLEXITY_TIERS), matching the backend TriggerRequest
 * default (bu-86c4c.18 / bu-jlhk5).
 */
export function triggerButler(
  name: string,
  prompt: string,
  complexity?: string,
) {
  return apiFetch("/butlers/{name}/trigger", { method: "POST",
      body: { prompt, complexity: complexity ?? "workhorse" }, path: { name: name } });
}

/**
 * Force an immediate scheduler tick on a specific butler (real backend: calls
 * the butler's MCP `tick` tool, which runs any due schedules right now).
 * Backs the "run schedule now" / "trigger tick" operator verbs (JARVIS audit
 * move 6, bu-86c4c.15) on the Issues and /system surfaces.
 */
export function forceButlerTick(name: string) {
  return apiFetch("/butlers/{name}/tick", { method: "POST", path: { name: name } });
}

/** Fetch MCP tools exposed by a specific butler. */
export function getButlerMcpTools(name: string) {
  return apiFetch("/butlers/{name}/mcp/tools", { path: { name: name } });
}

/** Call an MCP tool on a specific butler with optional arguments. */
export function callButlerMcpTool(
  name: string,
  request: ButlerMcpToolCallRequest,
) {
  return apiFetch("/butlers/{name}/mcp/call", { method: "POST",
      body: {
        tool_name: request.tool_name,
        arguments: request.arguments ?? {},
      }, path: { name: name } });
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

/** Fetch a paginated list of audit log entries from public.audit_log. */
export function getAuditLog(
  params?: AuditLogParams,
) {
  const sp: ApiQuery<"/audit-log", "GET"> = {};
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.actor) sp["actor"] = params.actor;
  if (params?.action) sp["action"] = params.action;
  if (params?.since) sp["since"] = params.since;
  if (params?.from_date) sp["from_date"] = params.from_date;
  if (params?.to_date) sp["to_date"] = params.to_date;
  if (params?.key) sp["key"] = params.key;
  if (params?.result) sp["result"] = params.result;
  if (params?.kind) sp["kind"] = params.kind;
  return apiFetch("/audit-log", { query: sp });
}

/**
 * Fetch the raw audit_log rows behind one "Seen Nx" issue group (JARVIS audit
 * move 6). `issueKey` is the group's stable `Issue.issue_key`.
 *
 * `window` (bu-hmdqz.4) MUST match the window the feed was viewed under --
 * the endpoint re-derives the group with the same time bound and row cap as
 * `GET /api/issues`, so the reported total never disagrees with what the
 * feed showed.
 */
export function getIssueOccurrences(
  issueKey: string,
  params?: { window?: string; offset?: number; limit?: number },
) {
  const sp: ApiQuery<"/issues/{issue_key}/occurrences", "GET"> = {};
  if (params?.window) sp["window"] = params.window;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;

  return apiFetch("/issues/{issue_key}/occurrences", { path: { issue_key: issueKey }, query: sp });
}

/**
 * Resolve ONE audit_log failure row to the exact Issues group it belongs to
 * (bu-6jv4m.3).
 *
 * Replaces the old `/issues?q=<first line of the error>` guess. `window` is
 * optional: omitted, the server picks the narrowest window that actually
 * CONTAINS the row (so a historical failure widens to "all" instead of
 * resolving to a confident-looking nothing).
 *
 * A rejected promise means the lookup was unavailable and the caller must say
 * so; it must never be collapsed into "no group exists".
 */
export function getAuditIssueGroup(
  auditId: number,
  params?: { window?: string },
) {
  const sp: ApiQuery<"/issues/group-for-audit/{audit_id}", "GET"> = {};
  if (params?.window) sp["window"] = params.window;

  return apiFetch("/issues/group-for-audit/{audit_id}", { path: { audit_id: auditId }, query: sp });
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/** Search across all butlers for sessions, state, and other entities. */
export function searchAll(query: string, limit?: number) {
  const sp: ApiQuery<"/search", "GET"> = { q: query };
  if (limit) sp["limit"] = limit;
  return apiFetch("/search", { query: sp });
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

/** Fetch the unified timeline with cursor-based pagination. */
export async function getTimeline(params?: TimelineParams) {
  const sp: ApiQuery<"/timeline", "GET"> = {};
  if (params?.limit) sp["limit"] = params.limit;
  if (params?.event) sp["event"] = params.event;
  if (params?.before) sp["before"] = params.before;
  if (params?.trace) sp["trace"] = params.trace;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  params?.butler?.forEach((b) => (sp["butler"] ??= []).push(b));
  params?.event_type?.forEach((t) => (sp["event_type"] ??= []).push(t));
  const response = await apiFetch("/timeline", { query: sp });
  // The backend field is additive. Normalize an older rolling-deploy response
  // so Timeline consumers can distinguish an intentionally empty name list
  // from an omitted field without weakening generic degraded_sources handling.
  return {
    ...response,
    meta: {
      ...response.meta,
      degraded_butlers: response.meta.degraded_butlers ?? [],
    },
  };
}

/** Fetch server-counted minute density for one bounded Timeline interval. */
export function getTimelineHistogram(
  params: TimelineHistogramParams,
) {
  const sp: ApiQuery<"/timeline/histogram", "GET"> = { since: params.since, until: params.until };
  if (params.trace) sp["trace"] = params.trace;
  params.butler?.forEach((butler) => (sp["butler"] ??= []).push(butler));
  params.event_type?.forEach((type) => (sp["event_type"] ??= []).push(type));
  return apiFetch("/timeline/histogram", { query: sp });
}

/** Fetch recent records created in the last 24 hours that are currently failed. */
export function getTimelineAttention(
  params?: TimelineAttentionParams,
) {
  const sp: ApiQuery<"/timeline/attention", "GET"> = {};
  if (params?.trace) sp["trace"] = params.trace;
  params?.butler?.forEach((butler) => (sp["butler"] ??= []).push(butler));
  return apiFetch("/timeline/attention", { query: sp });
}

// ---------------------------------------------------------------------------
// Calendar workspace
// ---------------------------------------------------------------------------

/** Build URLSearchParams from calendar workspace read query parameters. */
function calendarWorkspaceSearchParams(params: CalendarWorkspaceParams): ApiQuery<"/calendar/workspace", "GET"> {
  const sp: ApiQuery<"/calendar/workspace", "GET"> = { "view": params.view, "start": params.start, "end": params.end };



  if (params.timezone != null && params.timezone !== "") sp["timezone"] = params.timezone;
  params.butlers?.forEach((butler) => {
    if (butler) (sp["butlers"] ??= []).push(butler);
  });
  params.sources?.forEach((source) => {
    if (source) (sp["sources"] ??= []).push(source);
  });
  if (params.status != null) sp["status"] = params.status;
  if (params.source_type != null) sp["source_type"] = params.source_type;
  if (params.editable != null) sp["editable"] = params.editable;
  if (params.limit != null) sp["limit"] = params.limit;
  if (params.cursor != null && params.cursor !== "") sp["cursor"] = params.cursor;
  return sp;
}

/** Fetch normalized calendar workspace entries for a given range and view. */
export function getCalendarWorkspace(
  params: CalendarWorkspaceParams,
) {
  return apiFetch("/calendar/workspace", { query: calendarWorkspaceSearchParams(params) });
}

/** Fetch calendar workspace metadata: capabilities, sources, and lanes. */
export function getCalendarWorkspaceMeta() {
  return apiFetch("/calendar/workspace/meta");
}

/**
 * Scan the visible window for conflicts / overcommitment (the radar).
 *
 * Read-only and fail-open: a degraded response carries `issues_available=false`
 * with an empty `issues` list, which the FE must render as "silent" (no banner).
 */
export function getCalendarWorkspaceConflicts(
  params: ConflictScanParams,
) {
  const sp: ApiQuery<"/calendar/workspace/conflicts", "GET"> = { "start": params.start, "end": params.end };


  if (params.timezone != null && params.timezone !== "") sp["timezone"] = params.timezone;
  if (params.butler_name != null && params.butler_name !== "")
    sp["butler_name"] = params.butler_name;
  if (params.back_to_back_gap_minutes != null)
    sp["back_to_back_gap_minutes"] = params.back_to_back_gap_minutes;
  if (params.overloaded_day_hours != null)
    sp["overloaded_day_hours"] = params.overloaded_day_hours;
  return apiFetch("/calendar/workspace/conflicts", { query: sp });
}

/** Read-only projected invitations; no provider/RSVP action. */
export function getCalendarInvitations(
  params: CalendarInvitationsParams,
) {
  const sp: ApiQuery<"/calendar/workspace/invitations", "GET"> = { start: params.start, end: params.end };
  if (params.timezone) sp["timezone"] = params.timezone;
  if (params.limit != null) sp["limit"] = params.limit;
  if (params.cursor) sp["cursor"] = params.cursor;
  return apiFetch("/calendar/workspace/invitations", { query: sp });
}

/** Authenticated owner response through canonical preparation and approval. */
export function getCalendarResponseReceipt(
  commandId: string, sourceButler: string, inverse = false,
) {
  const query: ApiQuery<"/calendar/workspace/responses/{command_id}", "GET"> = { source_butler: sourceButler, inverse: inverse };
  return apiFetch("/calendar/workspace/responses/{command_id}", { path: { command_id: commandId }, query: query });
}

export function respondToCalendarInvitation(
  body: CalendarResponseRequest,
) {
  return apiFetch("/calendar/workspace/respond", { method: "POST", body: body });
}

/**
 * Fetch the structured "tomorrow at a glance" day-briefing card for a target
 * date. Reads the precomputed overlay view grouped by butler/kind — no per-open
 * LLM call. Fail-open server-side: a missing/unreadable view yields an honest
 * empty-state (`has_domain_context: false`), never an error.
 */
export function getCalendarDayBriefing(params: {
  date: string;
  timezone?: string;
  butlers?: string[];
}) {
  const sp: ApiQuery<"/calendar/workspace/day-briefing", "GET"> = { "date": params.date };

  if (params.timezone != null && params.timezone !== "") sp["timezone"] = params.timezone;
  params.butlers?.forEach((butler) => {
    if (butler) (sp["butlers"] ??= []).push(butler);
  });
  return apiFetch("/calendar/workspace/day-briefing", { query: sp });
}

/**
 * Fetch the meeting-prep rail context for a selected calendar event. Reads the
 * precomputed `calendar.v_prep_contributions` view (attendees + relationship
 * notes + last-met + per-attendee message context, merged across contributing
 * butlers) — no per-open LLM call. Fail-open server-side: an event with no prep
 * contribution yields the honest empty-state (`has_prep_context: false`), never
 * an error.
 */
export function getCalendarMeetingPrep(
  eventId: string,
) {
  return apiFetch("/calendar/workspace/prep/{event_id}", { path: { event_id: eventId } });
}

/** Full-text search calendar events by title/description/location, ranked by relevance. */
export function searchCalendarWorkspace(
  params: CalendarWorkspaceSearchParams,
) {
  const sp: ApiQuery<"/calendar/workspace/search", "GET"> = { "q": params.q, "view": params.view };


  if (params.timezone != null && params.timezone !== "") sp["timezone"] = params.timezone;
  if (params.limit != null) sp["limit"] = params.limit;
  params.butlers?.forEach((butler) => {
    if (butler) (sp["butlers"] ??= []).push(butler);
  });
  params.sources?.forEach((source) => {
    if (source) (sp["sources"] ??= []).push(source);
  });
  return apiFetch("/calendar/workspace/search", { query: sp });
}

/** Trigger calendar workspace sync globally or for a selected source. */
export function syncCalendarWorkspace(
  body: CalendarWorkspaceSyncRequest,
) {
  return apiFetch("/calendar/workspace/sync", { method: "POST",
    body: body });
}

/** List connected Google accounts joined with Google Calendar connector health. */
export function getCalendarAccounts() {
  return apiFetch("/calendar/accounts");
}

/** Enable or disable a single calendar as a sync source. */
export function toggleCalendarSource(
  body: CalendarSourceToggleRequest,
) {
  return apiFetch("/calendar/sources", { method: "POST",
    body: body });
}

/** Set the primary calendar for a butler. */
export function setPrimaryCalendar(
  body: SetPrimaryCalendarRequest,
) {
  return apiFetch("/calendar/workspace/primary", { method: "PUT",
      body: body });
}

/** Create, update, or delete a user-view provider event through workspace APIs. */
export function mutateCalendarWorkspaceUserEvent(
  body: CalendarWorkspaceUserMutationRequest,
) {
  return apiFetch("/calendar/workspace/user-events", { method: "POST",
      body: body });
}

/**
 * Parse a natural-language phrase into a draft event (parse-only, no write).
 *
 * Confirmation is NOT a new write path: the caller submits the (possibly
 * edited) draft to {@link mutateCalendarWorkspaceUserEvent} with
 * ``action="create"`` and a fresh ``request_id``.
 */
export function parseCalendarQuickAdd(
  body: QuickAddParseRequest,
) {
  return apiFetch("/calendar/workspace/parse-quick-add", { method: "POST",
      body: body });
}

/** Create/update/delete/toggle butler-lane schedule/reminder events. */
export function mutateCalendarWorkspaceButlerEvent(
  body: CalendarWorkspaceButlerMutationRequest,
) {
  return apiFetch("/calendar/workspace/butler-events", { method: "POST",
      body: body });
}

/**
 * Dry-run a draft butler event's recurrence expansion. Returns projected
 * occurrence dates + a "+N more" sentinel + lossy-conversion notes. Persists
 * nothing.
 */
export function previewCalendarWorkspaceButlerEvent(
  body: CalendarWorkspaceButlerEventPreviewRequest,
) {
  return apiFetch("/calendar/workspace/butler-events/preview", { method: "POST",
      body: body });
}

/**
 * Accept a calendar proposal — POST /calendar/workspace/proposals/{id}/accept.
 *
 * Reads the stored proposal payload (with optional inline `overrides`), creates
 * the butler event on the Butlers subcalendar, and flips the proposal to
 * `accepted`. Idempotent server-side (re-accept returns the existing
 * `accepted_event_id`). Throws {@link ApiError} with status 404 (unknown id) or
 * 409 (proposal already dismissed).
 */
export function acceptCalendarProposal(
  proposalId: string,
  overrides?: CalendarProposalAcceptRequest,
) {
  return apiFetch("/calendar/workspace/proposals/{proposal_id}/accept", { method: "POST",
      body: overrides ?? {}, path: { proposal_id: proposalId } });
}

/**
 * Dismiss a calendar proposal — POST /calendar/workspace/proposals/{id}/dismiss.
 *
 * Flips the proposal to `dismissed` with no provider write. Idempotent
 * server-side. Throws {@link ApiError} with status 404 (unknown id) or 409
 * (proposal already accepted).
 */
export function dismissCalendarProposal(
  proposalId: string,
) {
  return apiFetch("/calendar/workspace/proposals/{proposal_id}/dismiss", { method: "POST", path: { proposal_id: proposalId } });
}

/** Fetch a single calendar workspace entry by instance ID. */
export function getCalendarWorkspaceEntry(
  entryId: string,
  timezone?: string,
) {
  const sp: ApiQuery<"/calendar/workspace/entries/{entry_id}", "GET"> = {};
  if (timezone) sp["timezone"] = timezone;
  return apiFetch("/calendar/workspace/entries/{entry_id}", { path: { entry_id: entryId }, query: sp });
}
/** Fetch paginated calendar mutation audit log entries. */
export function getCalendarWorkspaceAudit(
  params?: CalendarAuditParams,
) {
  const sp: ApiQuery<"/calendar/workspace/audit", "GET"> = {};
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.butler) sp["butler"] = params.butler;
  return apiFetch("/calendar/workspace/audit", { query: sp });
}

// ---------------------------------------------------------------------------
// Relationship / CRM
// ---------------------------------------------------------------------------

/** Get owner identity setup status. */
export function getOwnerSetupStatus() {
  return apiFetch("/relationship/owner/setup-status");
}

/** Fetch contacts that are overdue on their Dunbar tier cadence (bu-iuol4.22). */
export function getOverdueContacts(days?: number) {
  const sp: ApiQuery<"/relationship/contacts/overdue", "GET"> = {};
  if (days != null) sp["days"] = days;
  return apiFetch("/relationship/contacts/overdue", { query: sp });
}

/** Fetch prompted, unanswered meeting debriefs (bu-q7vx1q.63). */
export function getPendingMeetingDebriefs() {
  return apiFetch("/relationship/meeting-debriefs/pending");
}

/** Build URLSearchParams from group query parameters. */
function groupSearchParams(params?: GroupParams): ApiQuery<"/relationship/groups", "GET"> {
  const sp: ApiQuery<"/relationship/groups", "GET"> = {};
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/** Fetch a paginated list of groups. */
export function getGroups(params?: GroupParams) {
  const qs = groupSearchParams(params);
  return apiFetch("/relationship/groups", { query: qs });
}

/** Fetch a single group by ID. */
export function getGroup(groupId: string) {
  return apiFetch("/relationship/groups/{group_id}", { path: { group_id: groupId } });
}

/** Fetch a group's member roster (bu-5umz4 — Circles lens deep-links). */
export function getGroupMembers(groupId: string) {
  return apiFetch("/relationship/groups/{group_id}/members", { path: { group_id: groupId } });
}

/** Fetch all labels. */
export function getLabels() {
  return apiFetch("/relationship/labels");
}

/** Create a new label. */
export function createLabel(body: { name: string; color?: string | null }) {
  return apiFetch("/relationship/labels", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body });
}

/** Assign a label to a group. */
export function assignGroupLabel(groupId: string, labelId: string) {
  return apiFetch("/relationship/groups/{group_id}/labels/{label_id}", { method: "POST", path: { group_id: groupId, label_id: labelId } });
}

/** Remove a label from a group. */
export function removeGroupLabel(groupId: string, labelId: string) {
  return apiFetch("/relationship/groups/{group_id}/labels/{label_id}", { method: "DELETE", path: { group_id: groupId, label_id: labelId } });
}

/** Fetch upcoming dates within a given number of days. */
export function getUpcomingDates(days?: number) {
  return apiFetch("/relationship/upcoming-dates", { query: { days: days ?? undefined } });
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** Fetch the observed active Health measurement vocabulary. */
export function getMeasurementTypes() {
  return apiFetch("/health/measurements/types");
}

/** Fetch a paginated list of health measurements. */
export function getMeasurements(params?: MeasurementParams) {
  const sp: ApiQuery<"/health/measurements", "GET"> = {};
  if (params?.type) sp["type"] = params.type;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/measurements", { query: sp });
}

/**
 * Log a measurement. Persists through the Health butler's own fact-store path
 * (POST /health/measurements -> measurement_log), so the new reading is read
 * back by getMeasurements immediately.
 */
export function createMeasurement(body: MeasurementCreateRequest) {
  return apiFetch("/health/measurements", { method: "POST",
    body: body });
}

/** Update a measurement. Only supplied fields are applied (PUT /health/measurements/{id}). */
export function updateMeasurement(
  measurementId: string,
  body: MeasurementUpdateRequest,
) {
  return apiFetch("/health/measurements/{measurement_id}", { method: "PUT",
    body: body, path: { measurement_id: measurementId } });
}

/** Soft-delete a measurement (DELETE /health/measurements/{id}). Returns 204. */
export function deleteMeasurement(measurementId: string) {
  return apiFetch("/health/measurements/{measurement_id}", { method: "DELETE", path: { measurement_id: measurementId } });
}

/** Fetch a paginated list of medications. */
export function getMedications(params?: MedicationParams) {
  const sp: ApiQuery<"/health/medications", "GET"> = {};
  if (params?.active != null) sp["active"] = params.active;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/medications", { query: sp });
}

/** Fetch dose log entries for a specific medication. */
export function getMedicationDoses(medicationId: string, params?: { since?: string; until?: string }) {
  const sp: ApiQuery<"/health/medications/{medication_id}/doses", "GET"> = {};
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  return apiFetch("/health/medications/{medication_id}/doses", { path: { medication_id: medicationId }, query: sp });
}

/**
 * Fetch the server-computed adherence summary for a medication
 * (GET /health/medications/{id}/adherence). `adherence_rate` is the
 * frequency-expected percentage — the authoritative figure to render, never a
 * naive client-side taken/total ratio.
 */
export function getMedicationAdherence(
  medicationId: string,
  params?: { start?: string; end?: string },
) {
  const sp: ApiQuery<"/health/medications/{medication_id}/adherence", "GET"> = {};
  if (params?.start) sp["start"] = params.start;
  if (params?.end) sp["end"] = params.end;
  return apiFetch("/health/medications/{medication_id}/adherence", { path: { medication_id: medicationId }, query: sp });
}

/**
 * Log (or skip) a dose for a medication. Persists through the Health butler's
 * own fact-store path (POST /health/medications/{id}/doses -> medication_log_dose,
 * a `took_dose` temporal fact), so the dose is read back by getMedicationDoses
 * and reflected in getMedicationAdherence immediately. Set `skipped` to record
 * a missed dose; `taken_at` defaults to now when omitted.
 */
export function logMedicationDose(
  medicationId: string,
  body: DoseLogRequest = {},
) {
  return apiFetch("/health/medications/{medication_id}/doses", { method: "POST",
      body: body, path: { medication_id: medicationId } });
}

/**
 * Create a medication. Persists through the Health butler's own fact-store path
 * (POST /health/medications -> medication_add), so the new record is read back
 * by getMedications immediately.
 */
export function createMedication(body: MedicationCreateRequest) {
  return apiFetch("/health/medications", { method: "POST",
    body: body });
}

/** Update a medication. Only supplied fields are merged (PUT /health/medications/{id}). */
export function updateMedication(
  medicationId: string,
  body: MedicationUpdateRequest,
) {
  return apiFetch("/health/medications/{medication_id}", { method: "PUT",
    body: body, path: { medication_id: medicationId } });
}

/** Soft-delete a medication (DELETE /health/medications/{id}). Returns 204. */
export function deleteMedication(medicationId: string) {
  return apiFetch("/health/medications/{medication_id}", { method: "DELETE", path: { medication_id: medicationId } });
}

/** Fetch a paginated list of health conditions. */
export function getConditions(params?: { offset?: number; limit?: number }) {
  const sp: ApiQuery<"/health/conditions", "GET"> = {};
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/conditions", { query: sp });
}

/**
 * Create a condition. Persists through the Health butler's own fact-store path
 * (POST /health/conditions -> condition_add), so the new record is read back by
 * getConditions immediately.
 */
export function createCondition(body: ConditionCreateRequest) {
  return apiFetch("/health/conditions", { method: "POST",
    body: body });
}

/** Update a condition. Only supplied fields are merged (PUT /health/conditions/{id}). */
export function updateCondition(
  conditionId: string,
  body: ConditionUpdateRequest,
) {
  return apiFetch("/health/conditions/{condition_id}", { method: "PUT",
    body: body, path: { condition_id: conditionId } });
}

/** Soft-delete a condition (DELETE /health/conditions/{id}). Returns 204. */
export function deleteCondition(conditionId: string) {
  return apiFetch("/health/conditions/{condition_id}", { method: "DELETE", path: { condition_id: conditionId } });
}

/** Fetch a paginated list of symptoms. */
export function getSymptoms(params?: SymptomParams) {
  const sp: ApiQuery<"/health/symptoms", "GET"> = {};
  if (params?.name) sp["name"] = params.name;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/symptoms", { query: sp });
}

/**
 * Log a symptom. Persists through the Health butler's own fact-store path
 * (POST /health/symptoms -> symptom_log), so the new record is read back by
 * getSymptoms immediately.
 */
export function createSymptom(body: SymptomCreateRequest) {
  return apiFetch("/health/symptoms", { method: "POST",
    body: body });
}

/** Update a symptom. Only supplied fields are applied (PUT /health/symptoms/{id}). */
export function updateSymptom(
  symptomId: string,
  body: SymptomUpdateRequest,
) {
  return apiFetch("/health/symptoms/{symptom_id}", { method: "PUT",
    body: body, path: { symptom_id: symptomId } });
}

/** Soft-delete a symptom (DELETE /health/symptoms/{id}). Returns 204. */
export function deleteSymptom(symptomId: string) {
  return apiFetch("/health/symptoms/{symptom_id}", { method: "DELETE", path: { symptom_id: symptomId } });
}

/** Fetch a paginated list of meals. */
export function getMeals(params?: MealParams) {
  const sp: ApiQuery<"/health/meals", "GET"> = {};
  if (params?.type) sp["type"] = params.type;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/meals", { query: sp });
}

/**
 * Log a meal. Persists through the Health butler's own fact-store path
 * (POST /health/meals -> meal_log), so the new record is read back by
 * getMeals immediately.
 */
export function createMeal(body: MealCreateRequest) {
  return apiFetch("/health/meals", { method: "POST",
    body: body });
}

/** Update a meal. Only supplied fields are applied (PUT /health/meals/{id}). */
export function updateMeal(mealId: string, body: MealUpdateRequest) {
  return apiFetch("/health/meals/{meal_id}", { method: "PUT",
    body: body, path: { meal_id: mealId } });
}

/** Soft-delete a meal (DELETE /health/meals/{id}). Returns 204. */
export function deleteMeal(mealId: string) {
  return apiFetch("/health/meals/{meal_id}", { method: "DELETE", path: { meal_id: mealId } });
}

/**
 * Fetch aggregate nutrition totals over a date range.
 *
 * GET /api/health/nutrition/summary?start=&end=
 * Aggregates meal_* facts with nutrition metadata (the same surface the
 * meal_log MCP tool writes). Meals without nutrition data are excluded.
 * Both `start` and `end` are required ISO-8601 date or datetime strings.
 */
export function getNutritionSummary(
  params: import("./types").NutritionSummaryParams,
) {
  const sp: ApiQuery<"/health/nutrition/summary", "GET"> = { "start": params.start, "end": params.end };


  return apiFetch("/health/nutrition/summary", { query: sp });
}

/** Fetch the latest measurement value for each requested type.
 *
 * GET /api/health/measurements/latest?types=glucose,hrv,steps
 * Returns { measurements: { "<type>": { measured_at, value, unit, metadata } | null } }
 */
export function getMeasurementsLatest(
  types: string[],
) {
  const query: ApiQuery<"/health/measurements/latest", "GET"> = { types: types.join(",") };
  // Preserve the original [] -> omitted required query -> server 422 edge.
  return apiFetch("/health/measurements/latest", { query, omitEmptyQuery: ["types"] });
}

/** Fetch bucketed mean/min/max trend aggregation for a single measurement type.
 *
 * GET /api/health/measurements/trend?type=weight&window_days=14&bucket=daily
 * Returns { type, window_days, bucket, buckets: [{ bucket_start, value_mean, ... }] }.
 */
export function getMeasurementsTrend(
  params: import("./types").MeasurementTrendParams,
) {
  const sp: ApiQuery<"/health/measurements/trend", "GET"> = { "type": params.type };

  if (params.window_days != null) sp["window_days"] = params.window_days;
  if (params.bucket) sp["bucket"] = params.bucket;
  return apiFetch("/health/measurements/trend", { query: sp });
}

/** Fetch the latest sleep session with stage breakdown.
 *
 * GET /api/health/measurements/sleep/latest
 */
export async function getSleepLatest(): Promise<import("./types").SleepLatestResponse> {
  const session = await apiFetch("/health/measurements/sleep/latest");
  return {
    session_date: session?.session_start ?? null,
    total_minutes: session?.total_duration_minutes ?? null,
    stages: session?.stages.map(({ kind, minutes }) => ({
      stage: kind, duration_minutes: minutes, start_time: null,
    })) ?? null,
    source: null,
  };
}

/** Fetch all active measurement sources with their last-sample timestamps.
 *
 * GET /api/health/measurements/sources
 */
export async function getMeasurementSources() {
  const res = await apiFetch("/health/measurements/sources");
  return res.sources ?? [];
}

/** Read stored Health usual ranges; a failed read remains explicitly unavailable. */
export function getHealthBaselines() {
  return apiFetch("/health/baselines");
}

/** Fetch liveness-qualified Health measurement expectations. */
export function getExpectedSignals() {
  return apiFetch("/health/measurements/expected-signals");
}

/**
 * Fetch the health Voice briefing.
 *
 * GET /api/health/briefing — mirrors GET /api/dashboard/briefing but scoped to
 * the health butler. Source is exactly "llm" or "fallback". Owner-only (403).
 * Backed by a 5-minute per-owner TTL cache.
 *
 * The returned promise resolves to the unwrapped Briefing data.
 */
export function getHealthBriefing() {
  return apiFetch("/health/briefing").then(
    (r) => r.data,
  );
}

/**
 * Fetch proactive insight candidates from the Switchboard.
 *
 * GET /api/switchboard/insights — read-only reader for public.insight_candidates.
 * Hosted on the Switchboard role (the only butler role with SELECT on this table).
 * Defaults to status=pending; filter by butler to scope to a specific origin.
 */
export function getInsightCandidates(
  params?: import("./types").InsightCandidatesParams,
) {
  const sp: ApiQuery<"/switchboard/insights", "GET"> = {};
  if (params?.butler) sp["butler"] = params.butler;
  if (params?.status) sp["status"] = params.status;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/switchboard/insights", { query: sp }).then((r) => r.data);
}

/** Record one of the broker's bounded, reversible owner-feedback verbs. */
export function submitInsightFeedback(
  ...[insightId, verdict, snoozeUntil]:
    | [insightId: string, verdict: "not_now", snoozeUntil: string]
    | [insightId: string, verdict: Exclude<import("./types").InsightFeedbackVerdict, "not_now">, snoozeUntil?: string]
) {
  const path = { insight_id: insightId };
  const action = verdict === "not_now" ? "snooze" : verdict === "never" ? "mute" : "useful";
  const options = verdict === "not_now"
    ? { method: "POST" as const, path, body: { snooze_until: snoozeUntil } }
    : { method: "POST" as const, path };
  return apiFetch(`/switchboard/insights/{insight_id}/${action}`, options)
    .then((response) => response.data);
}

/** Fetch a paginated list of health research notes. */
export function getResearch(params?: ResearchParams) {
  const sp: ApiQuery<"/health/research", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.tag) sp["tag"] = params.tag;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/health/research", { query: sp });
}

/**
 * Create a research note. Persists through the Health butler's own fact-store
 * path (POST /health/research -> research_save), so the new note is read back by
 * getResearch immediately.
 */
export function createResearch(body: ResearchCreateRequest) {
  return apiFetch("/health/research", { method: "POST",
    body: body });
}

/** Update a research note. Only supplied fields are merged (PUT /health/research/{id}). */
export function updateResearch(
  researchId: string,
  body: ResearchUpdateRequest,
) {
  return apiFetch("/health/research/{research_id}", { method: "PUT",
    body: body, path: { research_id: researchId } });
}

/** Soft-delete a research note (DELETE /health/research/{id}). Returns 204. */
export function deleteResearch(researchId: string) {
  return apiFetch("/health/research/{research_id}", { method: "DELETE", path: { research_id: researchId } });
}

// ---------------------------------------------------------------------------
// General / Switchboard
// ---------------------------------------------------------------------------

/** Fetch the switchboard routing log. */
export function getRoutingLog(
  params?: RoutingLogParams,
) {
  const sp: ApiQuery<"/switchboard/routing-log", "GET"> = {};
  if (params?.source_butler) sp["source_butler"] = params.source_butler;
  if (params?.target_butler) sp["target_butler"] = params.target_butler;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/switchboard/routing-log", { query: sp });
}

/** Fetch the switchboard butler registry. */
export function getRegistry() {
  return apiFetch("/switchboard/registry");
}

/** Set a butler's eligibility state in the switchboard registry. */
export function setButlerEligibility(
  name: string,
  eligibilityState: string,
) {
  return apiFetch("/switchboard/registry/{name}/eligibility", { method: "POST",
      body: { eligibility_state: eligibilityState }, path: { name: name } });
}


// ---------------------------------------------------------------------------
// General butler — collections API (bu-iuol4.30)
// ---------------------------------------------------------------------------

/** GET /api/general/stats — aggregated KPIs and collection size histogram. */
export function getGeneralStats() {
  return apiFetch("/general/stats");
}

export interface GeneralCollectionsParams {
  q?: string;
  offset?: number;
  limit?: number;
}

/** GET /api/general/collections — list collections with entity counts. */
export function getGeneralCollections(
  params?: GeneralCollectionsParams,
) {
  const sp: ApiQuery<"/general/collections", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/general/collections", { query: sp });
}

export interface GeneralEntitiesParams {
  q?: string;
  collection?: string;
  tag?: string;
  offset?: number;
  limit?: number;
}

/** GET /api/general/entities — search or list all entities. */
export function getGeneralEntities(
  params?: GeneralEntitiesParams,
) {
  const sp: ApiQuery<"/general/entities", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.collection) sp["collection"] = params.collection;
  if (params?.tag) sp["tag"] = params.tag;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/general/entities", { query: sp });
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

/** Build URLSearchParams from episode query parameters. */
function episodeSearchParams(params?: EpisodeParams): ApiQuery<"/memory/episodes", "GET"> {
  const sp: ApiQuery<"/memory/episodes", "GET"> = {};
  if (params?.butler) sp["butler"] = params.butler;
  if (params?.consolidated != null) sp["consolidated"] = params.consolidated;
  if (params?.status) sp["status"] = params.status;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/** Build URLSearchParams from fact query parameters. */
function factSearchParams(params?: FactParams): ApiQuery<"/memory/facts", "GET"> {
  const sp: ApiQuery<"/memory/facts", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.scope) sp["scope"] = params.scope;
  if (params?.validity) sp["validity"] = params.validity;
  if (params?.permanence) sp["permanence"] = params.permanence;
  if (params?.subject) sp["subject"] = params.subject;
  if (params?.importance_min != null)
    sp["importance_min"] = params.importance_min;
  if (params?.source_episode_id)
    sp["source_episode_id"] = params.source_episode_id;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/** Build URLSearchParams from rule query parameters. */
function ruleSearchParams(params?: RuleParams): ApiQuery<"/memory/rules", "GET"> {
  const sp: ApiQuery<"/memory/rules", "GET"> = {};
  if (params?.held != null) sp["held"] = params.held;
  if (params?.q) sp["q"] = params.q;
  if (params?.scope) sp["scope"] = params.scope;
  if (params?.maturity) sp["maturity"] = params.maturity;
  if (params?.forgotten != null) sp["forgotten"] = params.forgotten;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/**
 * Fetch aggregated memory statistics.
 *
 * Returns {@link MemoryStatsResponse} so `meta.pools_failed` — the backend's
 * degraded-envelope flag naming any memory pool dropped from the fan-out — is
 * typed and consumable (MemoryOverture gates its all-clear on it).
 */
export function getMemoryStats() {
  return apiFetch("/memory/stats");
}

/** Fetch a paginated list of episodes. */
export function getEpisodes(
  params?: EpisodeParams,
) {
  const qs = episodeSearchParams(params);
  return apiFetch("/memory/episodes", { query: qs });
}

/** Fetch a single episode by ID. */
export function getEpisode(episodeId: string) {
  return apiFetch("/memory/episodes/{episode_id}", { path: { episode_id: episodeId } });
}

/**
 * Requeue a dead-lettered episode: resets consolidation_status to 'pending'
 * (clearing attempts/lease/dead_letter_reason) so the next scheduled
 * consolidation sweep reconsolidates it. POST /api/memory/episodes/{id}/requeue
 * (bu-kqnum.14.3). Nothing is run by the request. Returns the refreshed
 * episode. 409 if the episode is not dead_letter.
 */
export function retryEpisodeConsolidation(
  episodeId: string,
) {
  return apiFetch("/memory/episodes/{episode_id}/requeue", { method: "POST", path: { episode_id: episodeId } });
}

/** Fetch a paginated list of facts. */
export function getFacts(
  params?: FactParams,
) {
  const qs = factSearchParams(params);
  return apiFetch("/memory/facts", { query: qs });
}

/** Fetch a single fact by ID. */
export function getFact(factId: string) {
  return apiFetch("/memory/facts/{fact_id}", { path: { fact_id: factId } });
}

/**
 * Re-ink a fact: reset its confidence-decay timer (last_confirmed_at = now).
 * POST /api/memory/facts/{id}/confirm (bu-awo8k.3). Returns the refreshed fact.
 */
export function confirmFact(factId: string) {
  return apiFetch("/memory/facts/{fact_id}/confirm", { method: "POST", path: { fact_id: factId } });
}

/**
 * Retract a fact: mark it invalid (validity = 'retracted'). The inverse of
 * confirm. POST /api/memory/facts/{id}/retract (bu-awo8k.4). Returns the
 * refreshed fact.
 */
export function retractFact(factId: string) {
  return apiFetch("/memory/facts/{fact_id}/retract", { method: "POST", path: { fact_id: factId } });
}

// ---------------------------------------------------------------------------
// Lifestyle taste ledger (bu-2jtfw.10)
// ---------------------------------------------------------------------------

import type { TasteVerdictsParams, TasteWorksParams } from "./types.ts";

function tasteWorksSearchParams(params?: TasteWorksParams): ApiQuery<"/lifestyle/taste/works", "GET"> {
  const sp: ApiQuery<"/lifestyle/taste/works", "GET"> = {};
  if (params?.kind) sp["kind"] = params.kind;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/** Fetch ledger-wide taste counts. GET /api/lifestyle/taste/summary. */
export function getLifestyleTasteSummary() {
  return apiFetch("/lifestyle/taste/summary");
}

/** Fetch a paginated list of taste-ledger works. */
export function getLifestyleTasteWorks(
  params?: TasteWorksParams,
) {
  const qs = tasteWorksSearchParams(params);
  return apiFetch("/lifestyle/taste/works", { query: qs });
}

/** Fetch a paginated list of owner-asserted taste verdicts. */
export function getLifestyleTasteVerdicts(
  params?: TasteVerdictsParams,
) {
  const sp: ApiQuery<"/lifestyle/taste/verdicts", "GET"> = {};
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/lifestyle/taste/verdicts", { query: sp });
}

/** Fetch a paginated list of rules. */
export function getRules(
  params?: RuleParams,
) {
  const qs = ruleSearchParams(params);
  return apiFetch("/memory/rules", { query: qs });
}

/** Fetch a single rule by ID. */
export function getRule(ruleId: string) {
  return apiFetch("/memory/rules/{rule_id}", { path: { rule_id: ruleId } });
}

/**
 * Retire a rule: it stops firing but stays on the books (retired_at set).
 * PATCH /api/memory/rules/{id}/retire (bu-6t8ix.3). Returns the refreshed rule.
 */
export function retireRule(ruleId: string) {
  return apiFetch("/memory/rules/{rule_id}/retire", { method: "PATCH", path: { rule_id: ruleId } });
}

/** Fetch recent memory activity. */
export function getMemoryActivity(
  limit?: number,
) {
  return apiFetch("/memory/activity", { query: { limit: limit ?? undefined } });
}

// ---------------------------------------------------------------------------
// Memory retention policies
// ---------------------------------------------------------------------------

/** Fetch all retention policies. */
export function getMemoryRetentionPolicies() {
  return apiFetch("/memory/retention-policies");
}

/** Bulk-update retention policies. */
export function updateMemoryRetentionPolicies(
  body: UpdateRetentionPoliciesRequest,
) {
  return apiFetch("/memory/retention-policies", { method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body });
}

/** Fetch recent compaction log entries. */
export function getMemoryCompactionLog(
  limit?: number,
) {
  return apiFetch("/memory/compaction-log", { query: { limit: limit ?? undefined } });
}

/** Count stale embeddings per tier — GET /api/memory/reembed/pending. */
export function getReembedPending(
  butler?: string,
  currentModel?: string,
) {
  const sp: ApiQuery<"/memory/reembed/pending", "GET"> = {};
  if (butler) sp["butler"] = butler;
  if (currentModel) sp["current_model"] = currentModel;
  return apiFetch("/memory/reembed/pending", { query: sp });
}

/** Trigger a synchronous re-embedding run — POST /api/memory/reembed. */
export function runReembed(
  body: ReembedRunRequest,
) {
  return apiFetch("/memory/reembed", { method: "POST",
    body: body });
}

/** Search memory (inspect). */
export function inspectMemory(
  params?: MemoryInspectParams,
) {
  const sp: ApiQuery<"/memory/inspect", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.kind) sp["kind"] = params.kind;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/memory/inspect", { query: sp });
}

// ---------------------------------------------------------------------------
// Entities (Knowledge Graph)
// ---------------------------------------------------------------------------

function entitySearchParams(params?: EntityParams): ApiQuery<"/memory/entities", "GET"> {
  const sp: ApiQuery<"/memory/entities", "GET"> = {};
  if (params?.q) sp["q"] = params.q;
  if (params?.entity_type) sp["entity_type"] = params.entity_type;
  if (params?.unidentified != null) sp["unidentified"] = params.unidentified;
  if (params?.archived != null) sp["archived"] = params.archived;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return sp;
}

/** Fetch a paginated list of entities. */
export function getEntities(
  params?: EntityParams,
) {
  const qs = entitySearchParams(params);
  return apiFetch("/memory/entities", { query: qs });
}

/** Fetch a single entity by ID. */
export function getEntity(
  entityId: string,
  params?: EntityDetailParams,
) {
  const qs: ApiQuery<"/memory/entities/{entity_id}", "GET"> = {};
  if (params?.facts_offset != null) qs["facts_offset"] = params.facts_offset;
  if (params?.facts_limit != null) qs["facts_limit"] = params.facts_limit;

  return apiFetch("/memory/entities/{entity_id}", { path: { entity_id: entityId }, query: qs });
}

/** Update entity core fields (name, aliases). */
export function updateEntity(
  entityId: string,
  request: UpdateEntityRequest,
) {
  return apiFetch("/memory/entities/{entity_id}", { method: "PATCH", body: request, path: { entity_id: entityId } });
}

/** Promote a transitory (unidentified) entity by clearing the unidentified flag. */
export function promoteEntity(
  entityId: string,
) {
  return apiFetch("/memory/entities/{entity_id}/promote", { method: "POST", path: { entity_id: entityId } });
}

/** Archive an entity (hide from default views, preserves all data). */
/** @public knip mis-traces this import (live consumer exists); remove tag when bu-9jvhm fixes the tracing gap. */
export function archiveEntity(entityId: string) {
  return apiFetch("/memory/entities/{entity_id}/archive", { method: "POST", path: { entity_id: entityId } });
}

/** Create an entity_info entry for an entity. */
export function createEntityInfo(
  entityId: string,
  request: CreateEntityInfoRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/info", { method: "POST", body: request, path: { entity_id: entityId } });
}

/** Reveal the actual value of a secured entity_info entry. */
export function revealEntitySecret(
  entityId: string,
  infoId: string,
) {
  return apiFetch("/relationship/entities/{entity_id}/secrets/{info_id}", { path: { entity_id: entityId, info_id: infoId } });
}

// ---------------------------------------------------------------------------
// Entity-contacts triple API (§9.4, bu-u1w78)
// Writes channel-fact triples in relationship.entity_facts (has-* predicates).
// Used by ContactChannelCard after the write-path cut-over (bu-k9ylx).
// ---------------------------------------------------------------------------

/**
 * Add (or upsert) a contact-fact triple for an entity.
 *
 * `predicate` must start with "has-" (e.g. "has-email", "has-phone",
 * "has-handle", "has-website"). Returns 201 on success, 202 when the
 * owner-entity carve-out parks the write as pending_approval.
 */
export function addEntityContact(
  entityId: string,
  request: AddEntityContactRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/contacts", { method: "POST", body: request, path: { entity_id: entityId } });
}

/**
 * Retract an active contact-fact triple.
 *
 * `predicate` must start with "has-". `valueHash` is SHA-256[:16] of the
 * object value (matches `ContactFact.value_hash`). Returns 200 on success,
 * 404 when no active fact matches (entity_id, predicate, value_hash).
 */
export function deleteEntityContact(
  entityId: string,
  predicate: string,
  valueHash: string,
) {
  return apiFetch("/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}", { method: "DELETE", path: { entity_id: entityId, predicate: predicate, value_hash: valueHash } });
}

/**
 * Mark an active contact-fact triple as owner-verified.
 *
 * `predicate` must start with "has-". `valueHash` is SHA-256[:16] of the
 * object value (matches `ContactInfoEntry.value_hash`). Returns 200 on
 * success, 403 when no owner entity is registered, 404 when no active fact
 * matches (entity_id, predicate, value_hash).
 */
export function markEntityContactVerified(
  entityId: string,
  predicate: string,
  valueHash: string,
) {
  return apiFetch("/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}/verify", { method: "POST", path: { entity_id: entityId, predicate: predicate, value_hash: valueHash } });
}

/**
 * Edit-in-place a contact-fact triple: retract old value, assert new value atomically.
 *
 * `predicate` must start with "has-". `valueHash` is SHA-256[:16] of the
 * current object value (matches `ContactFact.value_hash`). Returns 200 on
 * success, 202 on owner-entity pending_approval, 404 when no active fact
 * matches (entity_id, predicate, value_hash).
 */
export function updateEntityContact(
  entityId: string,
  predicate: string,
  valueHash: string,
  request: UpdateEntityContactRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/contacts/{predicate}/{value_hash}", { method: "PUT", body: request, path: { entity_id: entityId, predicate: predicate, value_hash: valueHash } });
}

/**
 * Set an entity's preferred outbound channel via the `prefers-channel` fact.
 *
 * Single-valued: supersedes any prior active preference. Returns 200 on
 * success; 400 when the entity has no contact fact for `channel` (reachability
 * validation), 403 when no owner entity is registered, 404 when the entity does
 * not exist.
 */
export function setEntityPreferredChannel(
  entityId: string,
  request: SetPreferredChannelRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/preferred-channel", { method: "PUT", body: request, path: { entity_id: entityId } });
}

/**
 * Clear an entity's preferred channel by retracting the active `prefers-channel`
 * fact. Idempotent (`cleared: 0` when no preference was set).
 */
export function clearEntityPreferredChannel(
  entityId: string,
) {
  return apiFetch("/relationship/entities/{entity_id}/preferred-channel", { method: "DELETE", path: { entity_id: entityId } });
}

// ---------------------------------------------------------------------------
// Relationship butler: entity-level fetch and tab endpoints
// ---------------------------------------------------------------------------

/** Fetch all contacts linked to a relationship entity. */
export function getEntityLinkedContacts(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/linked-contacts", { path: { entity_id: entityId } });
}

/** Fetch gifts tab data for a relationship entity. */
export function getEntityGifts(
  entityId: string,
  params?: { limit?: number; offset?: number },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/gifts", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.offset != null) qs["offset"] = params.offset;

  return apiFetch("/relationship/entities/{entity_id}/gifts", { path: { entity_id: entityId }, query: qs });
}

/** Fetch interaction facts for a relationship entity, newest first. */
export function getEntityInteractions(
  entityId: string,
  params?: { limit?: number; offset?: number },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/interactions", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.offset != null) qs["offset"] = params.offset;

  return apiFetch("/relationship/entities/{entity_id}/interactions", { path: { entity_id: entityId }, query: qs });
}

// ---------------------------------------------------------------------------
// Relationship butler: entity-level tab writes — the log-interaction and
// gift-idea operator verbs (bu-6t8ix.4). A third verb, draft-reach-out
// (getEntityReachOutDrafts/createEntityReachOutDraft), shipped alongside
// these and was retired in bu-2jtfw.11, replaced by the prepared-action
// mechanism surfaced on the insight digest.
//
// Each POST persists through the relationship butler's own fact-store tool, so
// a dashboard-authored record is indistinguishable from a butler-authored one.
// Both are owner-gated (403 `owner_required`) and answer 409 with an
// `existing_id` rather than writing a duplicate.
// ---------------------------------------------------------------------------

/** Record a note for a relationship entity. */
export function createEntityNote(
  entityId: string,
  request: CreateEntityNoteRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/notes", { method: "POST", body: request, path: { entity_id: entityId } });
}

/** Log an interaction with a relationship entity. */
export function createEntityInteraction(
  entityId: string,
  request: CreateEntityInteractionRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/interactions", { method: "POST", body: request, path: { entity_id: entityId } });
}

/** Capture a gift idea for a relationship entity. */
export function createEntityGift(
  entityId: string,
  request: CreateEntityGiftRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/gifts", { method: "POST", body: request, path: { entity_id: entityId } });
}

/** Fetch loans tab data for a relationship entity. */
export function getEntityLoans(
  entityId: string,
  params?: { limit?: number; offset?: number },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/loans", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.offset != null) qs["offset"] = params.offset;

  return apiFetch("/relationship/entities/{entity_id}/loans", { path: { entity_id: entityId }, query: qs });
}

/** Fetch unified timeline data for a relationship entity. */
export function getEntityTimeline(
  entityId: string,
  params?: { limit?: number; offset?: number },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/timeline", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.offset != null) qs["offset"] = params.offset;

  return apiFetch("/relationship/entities/{entity_id}/timeline", { path: { entity_id: entityId }, query: qs });
}

/** Fetch the canonical merged entity activity stream with cancellable pagination. */
export function getEntityActivity(
  entityId: string,
  params?: { limit?: number; offset?: number; signal?: AbortSignal },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/activity", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.offset != null) qs["offset"] = params.offset;

  return apiFetch("/relationship/entities/{entity_id}/activity", { signal: params?.signal, path: { entity_id: entityId }, query: qs }).then((response) => {
    if (!("items" in response)) throw new ApiError("INVALID_RESPONSE", "Activity stream is unavailable.", 200);
    return response;
  });
}

/** Fetch completeness-tagged interaction evidence for a rolling window. */
export function getEntityCadence(
  entityId: string,
  windowDays: number,
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/cadence", "GET"> = { window_days: windowDays };
  return apiFetch("/relationship/entities/{entity_id}/cadence", { path: { entity_id: entityId }, query: qs });
}

/** Fetch message thread summaries for a relationship entity. */
export function getEntityMessageThreads(
  entityId: string,
  params?: { limit?: number },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/message-threads", "GET"> = {};
  if (params?.limit != null) qs["limit"] = params.limit;

  return apiFetch("/relationship/entities/{entity_id}/message-threads", { path: { entity_id: entityId }, query: qs });
}

/**
 * Fetch the 90-day daily activity-count series for an entity's sparkline (bu-xzh76).
 *
 * Hits GET /api/butlers/relationship/entities/{id}/activity?bins=daily — returns
 * a dense, ascending-by-date series (one entry per day including zero-count
 * days) over ``window`` (default 90d). ``bins_only=true`` is always sent so the
 * merged stream is omitted. ``degraded=true`` means the Chronicler contribution
 * was unavailable, so zero-count bins are not a complete inactivity claim.
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function getEntityActivityBins(
  entityId: string,
  params?: { window?: string },
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/activity", "GET"> = { "bins": "daily", "bins_only": true };


  if (params?.window != null) qs["window"] = params.window;
  return apiFetch("/relationship/entities/{entity_id}/activity", { path: { entity_id: entityId }, query: qs }).then((response) => {
    if (!("bins" in response)) throw new ApiError("INVALID_RESPONSE", "Activity bins are unavailable.", 200);
    return response;
  });
}

/**
 * Fetch facts changed since the entity's view mark — delta-since-last-visit (bu-xzh76).
 *
 * Hits GET /api/butlers/relationship/entities/{id}/delta-facts — read-only; it
 * never moves the mark. The caller reads this on load, renders the banner, then
 * posts the view mark via {@link markEntityView} (spec: the delta is read
 * before the mark moves). ``marked_at`` is null on a first visit (no banner).
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function getEntityDeltaFacts(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/delta-facts", { path: { entity_id: entityId } });
}

/**
 * Upsert the owner's "last viewed" mark for an entity (bu-xzh76).
 *
 * Hits POST /api/butlers/relationship/entities/{id}/view-mark — persists
 * ``now()`` into ``relationship.entity_view_marks`` (one mark per entity). The
 * frontend posts this only *after* reading {@link getEntityDeltaFacts}, so the
 * next visit's delta is computed relative to this mark.
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function markEntityView(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/view-mark", { method: "POST", path: { entity_id: entityId } });
}

/**
 * Fetch the entity's date-kind facts with their next occurrence — core dates (bu-xzh76).
 *
 * Hits GET /api/butlers/relationship/entities/{id}/core-dates — server-side
 * extraction of date-kind predicates (``has-birthday``, anniversaries) with the
 * next calendar occurrence, ``days_until``, and provenance per row. Replaces the
 * former client-side string-matching on the generic facts list. Items are
 * ordered by ``days_until`` ascending (soonest first).
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function getEntityCoreDates(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/core-dates", { path: { entity_id: entityId } });
}

/** Forget (hard-delete with tombstone) a relationship entity.
 *
 * Maps to DELETE /api/butlers/relationship/entities/{entity_id}.
 * Retracts all active entity_facts and tombstones the entity row.
 * Irreversible. Owner-only (returns 403 if no owner entity).
 */
export function forgetRelationshipEntity(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}", { method: "DELETE", path: { entity_id: entityId } });
}

/** Pin or clear an entity's Dunbar tier. tier=null clears the pin. */
export function updateEntityDunbarTier(
  entityId: string,
  tier: number | null,
) {
  return apiFetch("/relationship/entities/{entity_id}/dunbar-tier", { method: "PATCH", body: { tier }, path: { entity_id: entityId } });
}

/** Search relationship entities using rule-based ranking (deterministic Finder, bu-xfjwk).
 *
 * Hits GET /api/butlers/relationship/entities/search — server scores results by
 * prefix > contact-fact > substring > predicate match. Results are already ordered
 * by score DESC. An empty or whitespace-only query returns an empty result set.
 */
export function searchRelationshipEntities(
  q: string,
  limit?: number,
) {
  const sp: ApiQuery<"/relationship/entities/search", "GET"> = { q };
  if (limit != null) sp["limit"] = limit;
  return apiFetch("/relationship/entities/search", { query: sp });
}

/**
 * List entities from the relationship butler with optional filter chips and pagination (§9.1).
 *
 * Hits GET /api/butlers/relationship/entities.  Distinct from the memory butler's
 * entity list — this surface joins relationship.entity_facts for tier, last_seen,
 * and contact_fact_count.
 */
export function listRelationshipEntities(
  params?: RelationshipEntityListParams,
) {
  const sp: ApiQuery<"/relationship/entities", "GET"> = {};
  if (params?.entity_type) {
    if (params.entity_type.length === 0) {
      (sp["entity_type"] ??= []).push("__none__");
    } else {
      params.entity_type.forEach((type) => (sp["entity_type"] ??= []).push(type));
    }
  }
  if (params?.state) sp["state"] = params.state;
  if (params?.has) sp["has"] = params.has;
  if (params?.ids) {
    // Always emit the param when ids is provided — an empty array must reach the
    // backend as a present-but-empty filter (→ empty result set), not absence.
    if (params.ids.length === 0) {
      (sp["ids"] ??= []).push("");
    } else {
      params.ids.forEach((id) => (sp["ids"] ??= []).push(id));
    }
  }
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.offset != null) sp["offset"] = params.offset;
  return apiFetch("/relationship/entities", { query: sp });
}

/**
 * Fetch the entity curation queue from the relationship butler (§9.5).
 *
 * Hits GET /api/butlers/relationship/entities/queue.  Returns three buckets in order:
 * unidentified → duplicate-candidate → stale.
 */
export function getRelationshipEntityQueue(params?: {
  limit?: number;
  offset?: number;
}) {
  const sp: ApiQuery<"/relationship/entities/queue", "GET"> = {};
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.offset != null) sp["offset"] = params.offset;
  return apiFetch("/relationship/entities/queue", { query: sp });
}

/** Promote an existing unidentified relationship entity in-place. */
export function promoteRelationshipEntity(
  request: PromoteRelationshipEntityRequest,
) {
  return apiFetch("/relationship/entities", { method: "POST",
    body: request });
}

/** Create a brand-new canonical entity through the relationship API (create path — no entity_id). */
export function createRelationshipEntity(
  request: CreateRelationshipEntityRequest,
) {
  return apiFetch("/relationship/entities", { method: "POST",
    body: request });
}

/** Archive a relationship entity, hiding it from default entity views. */
export function archiveRelationshipEntity(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/archive", { method: "POST", path: { entity_id: entityId } });
}

/** Dismiss a relationship entity from the curation queue. */
export function dismissRelationshipEntityQueueItem(
  entityId: string,
) {
  return apiFetch("/relationship/entities/queue/dismiss", { method: "POST", body: { entity_id: entityId } });
}

/** Merge two relationship entities, keeping the requested survivor. */
export function mergeRelationshipEntities(
  request: MergeRelationshipEntitiesRequest,
) {
  return apiFetch("/relationship/entities/{entity_id}/merge", { method: "POST", body: request, path: { entity_id: request.entityA } });
}

/**
 * Compute the structural diff of two entities — the merge-review compare view
 * (relationship-merge-review "Compare endpoint").
 *
 * Hits POST /api/relationship/entities/compare. Returns a server-computed,
 * deterministic diff: ``a`` / ``b`` per-entity blocks, ``shared`` (identical
 * identity-store rows = the duplicate evidence), and ``divergent`` (single-
 * cardinality predicate conflicts a merge must resolve). No scoring, ranking,
 * similarity score, or generated text.
 *
 * Returns owner-only gate 403 when no owner entity is registered; 404 when
 * either entity is unknown/tombstoned; 422 when ``entity_a == entity_b``.
 */
export function compareRelationshipEntities(
  request: CompareEntitiesRequest,
) {
  return apiFetch("/relationship/entities/compare", { method: "POST",
    body: request });
}

/**
 * Dismiss a compared duplicate-candidate pair — writes a ``merge_reviews`` row
 * with ``outcome = 'dismissed'`` (relationship-merge-review "Dismissal").
 *
 * Hits POST /api/relationship/entities/dismiss-pair. The dismissal suppresses
 * the pair from the duplicate-candidate queue bucket until new shared evidence
 * (a ``{predicate, shared_value}`` not in the snapshot) arises. The shared
 * snapshot is computed server-side at dismissal time.
 */
export function dismissRelationshipEntityPair(
  request: DismissEntityPairRequest,
) {
  return apiFetch("/relationship/entities/dismiss-pair", { method: "POST",
    body: request });
}

/** Fetch relational neighbours grouped by predicate for an entity (§9.2, bu-4wn79).
 *
 * Hits GET /api/butlers/relationship/entities/{id}/neighbours — returns only
 * kind='relational' predicates (excludes has-* contact predicates).
 *
 * Pass ``rank="weight"`` (and optional ``per_predicate``) to truncate each
 * predicate group to the top-N by weight; the per-predicate overflow count is
 * returned in the response ``remainders`` map (the "+N more" affordance).
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function getEntityNeighbours(
  entityId: string,
  params?: NeighboursParams,
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/neighbours", "GET"> = {};
  if (params?.rank != null) qs["rank"] = params.rank;
  if (params?.per_predicate != null) qs["per_predicate"] = params.per_predicate;

  return apiFetch("/relationship/entities/{entity_id}/neighbours", { path: { entity_id: entityId }, query: qs });
}

/**
 * Fetch the owner Plex's dimension halo: non-person entities grouped by type,
 * each type's top-N by last_seen, with person edges for the connection
 * spotlight.
 *
 * Hits GET /api/butlers/relationship/plex/halo. Owner-only gate 403 when no
 * owner entity is registered.
 */
export function getPlexHalo(perType?: number) {

  return apiFetch("/relationship/plex/halo", { query: { per_type: perType ?? undefined } });
}

/**
 * Fetch per-fact provenance data for an entity from relationship.entity_facts (bu-mg4dk).
 *
 * Hits GET /api/butlers/relationship/entities/{id}/facts — keyset (cursor)
 * paginated, ordered ``created_at DESC, id DESC``. Each row carries provenance
 * fields (weight, last_observed_at, object_kind, src) plus a ``store`` label and
 * ``staleness_band``.
 *
 * Filters: ``predicate`` (single predicate), ``validity`` (``active`` default /
 * ``superseded`` history), ``store`` (``identity`` default / ``all`` to append
 * labeled narrative rows). Pagination: ``limit`` + ``cursor`` (pass the prior
 * response's ``next_cursor``).
 *
 * Used by the Workbench ProvenanceGrid (§6b Amendment 7).
 * Returns owner-only gate 403 when no owner entity is registered.
 */
export function getEntityFacts(
  entityId: string,
  params?: EntityFactsParams,
) {
  const qs: ApiQuery<"/relationship/entities/{entity_id}/facts", "GET"> = {};
  if (params?.predicate != null) qs["predicate"] = params.predicate;
  if (params?.validity != null) qs["validity"] = params.validity;
  if (params?.store != null) qs["store"] = params.store;
  if (params?.limit != null) qs["limit"] = params.limit;
  if (params?.cursor != null) qs["cursor"] = params.cursor;

  return apiFetch("/relationship/entities/{entity_id}/facts", { path: { entity_id: entityId }, query: qs });
}

/**
 * Fetch concentration balance-sheet for a relational predicate (§9.3, bu-0vosj).
 *
 * Hits GET /api/relationship/entities/concentration?pred=<predicate>.
 * The response always includes ``predicate_tabs`` (full list of relational
 * predicates from the registry) so the frontend can render tabs without a
 * separate request.
 *
 * Returns owner-only gate 403 when no owner entity is registered.
 * Defaults to predicate ``'knows'`` when ``pred`` is omitted.
 */
export function getEntityConcentration(pred?: string) {
  return apiFetch("/relationship/entities/concentration", { query: { pred: pred || undefined } });
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

function approvalActionSearchParams(params?: ApprovalActionParams): ApiQuery<"/approvals/actions", "GET"> {
  const qs: ApiQuery<"/approvals/actions", "GET"> = {};
  if (params?.tool_name) qs["tool_name"] = params.tool_name;
  if (params?.status) qs["status"] = params.status;
  if (params?.butler) qs["butler"] = params.butler;
  if (params?.offset != null) qs["offset"] = params.offset;
  if (params?.limit != null) qs["limit"] = params.limit;
  return qs;
}

function approvalRuleSearchParams(params?: ApprovalRuleParams): ApiQuery<"/approvals/rules", "GET"> {
  const qs: ApiQuery<"/approvals/rules", "GET"> = {};
  if (params?.tool_name) qs["tool_name"] = params.tool_name;
  if (params?.active != null) qs["active"] = params.active;
  if (params?.butler) qs["butler"] = params.butler;
  if (params?.offset != null) qs["offset"] = params.offset;
  if (params?.limit != null) qs["limit"] = params.limit;
  return qs;
}

export function getApprovalActions(
  params?: ApprovalActionParams,
) {
  const qs = approvalActionSearchParams(params);
  return apiFetch("/approvals/actions", { query: qs });
}

export function getApprovalRules(
  params?: ApprovalRuleParams,
) {
  const qs = approvalRuleSearchParams(params);
  return apiFetch("/approvals/rules", { query: qs });
}

export function getApprovalGatedTools() {
  return apiFetch("/approvals/gated-tools");
}

export async function createApprovalRule(
  request: ApprovalRuleCreateRequest,
) {
  const proof = await standingRuleHeaders("create", request);
  return apiFetch("/approvals/rules", { method: "POST",
    headers: { "Content-Type": "application/json", ...proof },
    body: request });
}

export function getApprovalRuleSuggestions(
  actionId: string,
) {
  return apiFetch("/approvals/rules/suggestions/{action_id}", { path: { action_id: actionId } });
}

export async function createApprovalRuleFromAction(
  request: ApprovalRuleFromActionRequest,
) {
  const proof = await standingRuleHeaders("from_action", request);
  return apiFetch("/approvals/rules/from-action", { method: "POST",
    headers: { "Content-Type": "application/json", ...proof },
    body: request });
}

export function revokeApprovalRule(ruleId: string) {
  return apiFetch("/approvals/rules/{rule_id}/revoke", { method: "POST", path: { rule_id: ruleId } });
}

export function getApprovalMetrics() {
  return apiFetch("/approvals/metrics");
}

// ---------------------------------------------------------------------------
// New Dispatch-language approvals API (§8.3)
// ---------------------------------------------------------------------------

/**
 * Fetch the flat approvals queue.
 *
 * Returns {@link ApprovalsFlatListResponse} so the whole-population
 * `meta.stalled_count` and `meta.sources_degraded` fan-out health are typed
 * and consumable. A degraded source makes the aggregate partial, so the
 * verdict must not read it as an all-clear.
 */
export function getApprovalsFlat(
  state?: "waiting" | "decided" | "stalled" | "all",
  limit?: number,
) {
  const qs: ApiQuery<"/approvals", "GET"> = {};
  if (state) qs["state"] = state;
  if (limit != null) qs["limit"] = limit;
  return apiFetch("/approvals", { query: qs });
}

export function getUnroutableAttention() {
  return apiFetch("/approvals/unroutable");
}

export function retryUnroutableAttention(
  deadLetterId: string,
) {
  return apiFetch("/approvals/unroutable/{dead_letter_id}/retry", { method: "POST", path: { dead_letter_id: deadLetterId } });
}

export function getApprovalDetail(actionId: string, reviewSource?: string) {
  return apiFetch("/approvals/{action_id}", { path: { action_id: actionId }, query: { review_source: reviewSource } });
}

export function approveApproval(
  actionId: string,
  request: ApprovalApproveRequest,
) {
  return apiFetch("/approvals/{action_id}/approve", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: request ?? {}, path: { action_id: actionId } });
}

export function retryApproval(
  actionId: string,
) {
  return apiFetch("/approvals/{action_id}/retry", { method: "POST", path: { action_id: actionId } });
}

export function abandonApproval(
  actionId: string,
  request: ApprovalAbandonRequest,
) {
  return apiFetch("/approvals/{action_id}/abandon", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: request, path: { action_id: actionId } });
}

export function denyApproval(
  actionId: string,
  request?: ApprovalDenyRequest,
) {
  return apiFetch("/approvals/{action_id}/deny", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: request ?? {}, path: { action_id: actionId } });
}

export function deferApproval(
  actionId: string,
  request: ApprovalDeferRequest,
) {
  return apiFetch("/approvals/{action_id}/defer", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: request, path: { action_id: actionId } });
}

export function getApprovalsPolicy() {
  return apiFetch("/approvals/policy");
}

export function updateApprovalsPolicy(
  policy: ApprovalsPolicy,
) {
  return apiFetch("/approvals/policy", { method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: policy });
}

/**
 * Fetch decided approvals history.
 *
 * Returns {@link ApprovalsListResponse} so `meta.sources_degraded` — the
 * backend's degraded-envelope flag naming any butler pool dropped from the
 * fan-out — is typed and feeds the verdict opener's all-clear qualifier
 * (bu-jad4j.4).
 */
export function getApprovalsHistory(
  since?: string,
  limit?: number,
) {
  const qs: ApiQuery<"/approvals/history", "GET"> = {};
  if (since) qs["since"] = since;
  if (limit != null) qs["limit"] = limit;
  return apiFetch("/approvals/history", { query: qs });
}

function autonomySuggestionSearchParams(params?: AutonomySuggestionParams): ApiQuery<"/approvals/suggestions", "GET"> {
  const qs: ApiQuery<"/approvals/suggestions", "GET"> = {};
  if (params?.status) qs["status"] = params.status;
  if (params?.suggestion_type) qs["suggestion_type"] = params.suggestion_type;
  if (params?.limit !== undefined) qs["limit"] = params.limit;
  if (params?.offset !== undefined) qs["offset"] = params.offset;
  return qs;
}

export function getAutonomySuggestions(
  params?: AutonomySuggestionParams,
) {
  const qs = autonomySuggestionSearchParams(params);
  return apiFetch("/approvals/suggestions", { query: qs });
}

export async function confirmAutonomySuggestion(
  suggestionId: string,
) {
  const proof = await standingRuleHeaders("promotion", { suggestion_id: suggestionId });
  return apiFetch("/approvals/suggestions/{suggestion_id}/confirm", { method: "POST", headers: proof, path: { suggestion_id: suggestionId } });
}

export function dismissAutonomySuggestion(
  suggestionId: string,
  request?: AutonomySuggestionDismissRequest,
) {
  return apiFetch("/approvals/suggestions/{suggestion_id}/dismiss", { method: "POST",
      body: request ?? {}, path: { suggestion_id: suggestionId } });
}

// ---------------------------------------------------------------------------
// Rule-promotion approvals surface (bu-o62bc, bead 4)
// ---------------------------------------------------------------------------

export function getRulePromotionSuggestions() {
  return apiFetch("/switchboard/rule-promotion-suggestions");
}

/** Aggregate rule-promotion metrics for the approvals dashboard tile (bead 6). */
export function getRulePromotionStats() {
  return apiFetch("/switchboard/rule-promotion-stats");
}

export function confirmRulePromotionSuggestion(
  suggestionId: string,
) {
  return apiFetch("/switchboard/rule-promotion-suggestions/{suggestion_id}/confirm", { method: "POST", path: { suggestion_id: suggestionId } });
}

export function dismissRulePromotionSuggestion(
  suggestionId: string,
  request?: RulePromotionDismissRequest,
) {
  return apiFetch("/switchboard/rule-promotion-suggestions/{suggestion_id}/dismiss", { method: "POST", body: request ?? {}, path: { suggestion_id: suggestionId } });
}

export function setRulePromotionRuleEnabled(
  suggestionId: string,
  enabled: boolean,
) {
  return apiFetch("/switchboard/rule-promotion-suggestions/{suggestion_id}/rule-enabled", { method: "POST", body: { enabled }, path: { suggestion_id: suggestionId } });
}

// ---------------------------------------------------------------------------
// OAuth / Secrets management API functions
// ---------------------------------------------------------------------------

import type { UpsertAppCredentialsRequest } from "./types.ts";

/** Fetch the masked credential status (presence only, no secret values). */
export function getGoogleCredentialStatus() {
  return apiFetch("/oauth/google/credentials");
}

/** Store Google app credentials (client_id + client_secret). */
export function upsertGoogleCredentials(
  request: UpsertAppCredentialsRequest,
) {
  return apiFetch("/oauth/google/credentials", { method: "PUT",
    body: request });
}

/** Build the URL to start an OAuth flow for a new or existing Google account.
 *
 * ``scopeSet`` selects one or more named scope sets registered in
 * ``GOOGLE_SCOPE_SETS`` on the backend (e.g. ``"health"`` or
 * ``"calendar,drive"``). Omitting ``scopeSet`` reproduces the pre-existing
 * default scope composition — callers that only needed Calendar/Drive/
 * Gmail continue to work without modification.
 *
 * ``pageOfOrigin`` is threaded through the OAuth state token so the callback
 * can redirect back to the originating page. Supported values:
 *   - ``"secrets"``     → /secrets?focus=u:google&toast=connected
 *   - ``"ingestion"``   → /ingestion/connectors (handled by ingestion spec)
 *   - omitted / null    → defaults to /secrets (backend default)
 *
 * ``connectorDetailPath`` enables deep-link redirect back to a specific
 * connector after reauth. Format: ``"<connector_type>/<endpoint_identity>"``.
 * When set, the callback redirects to /ingestion/connectors/<path> instead of
 * the connectors roster. The backend validates the format and silently ignores
 * invalid values (safe fallback). Takes priority over ``pageOfOrigin``.
 *
 * ``selectAccount`` requests Google's account chooser. Use it for "connect
 * another account" flows where the active browser Google session may already
 * be authorized.
 */
export function getGoogleOAuthStartUrl(opts?: {
  accountHint?: string;
  forceConsent?: boolean;
  selectAccount?: boolean;
  scopeSet?: string;
  pageOfOrigin?: "secrets" | "ingestion";
  connectorDetailPath?: string;
}): string {
  const params = new URLSearchParams();
  if (opts?.accountHint) params.set("account_hint", opts.accountHint);
  if (opts?.forceConsent) params.set("force_consent", "true");
  if (opts?.selectAccount) params.set("select_account", "true");
  if (opts?.scopeSet) params.set("scope_set", opts.scopeSet);
  if (opts?.pageOfOrigin) params.set("page_of_origin", opts.pageOfOrigin);
  if (opts?.connectorDetailPath) params.set("connector_detail_path", opts.connectorDetailPath);
  const qs = params.toString();
  return `${API_BASE_URL}/oauth/google/start${qs ? `?${qs}` : ""}`;
}

/** Build the URL to start an OAuth flow for any registered provider.
 *
 * Uses the generalised ``/{provider}/start`` endpoint.  All options are
 * optional and forwarded as query parameters.
 *
 * ``connectorDetailPath`` enables deep-link redirect back to a specific
 * connector after reauth. Format: ``"<connector_type>/<endpoint_identity>"``.
 * When set, the callback redirects to /ingestion/connectors/<path>.
 * Takes priority over ``pageOfOrigin``.
 */
export function getProviderOAuthStartUrl(
  provider: string,
  opts?: {
    accountHint?: string;
    forceConsent?: boolean;
    scopeSet?: string;
    pageOfOrigin?: "secrets" | "ingestion";
    connectorDetailPath?: string;
  },
): string {
  const params = new URLSearchParams();
  if (opts?.accountHint) params.set("account_hint", opts.accountHint);
  if (opts?.forceConsent) params.set("force_consent", "true");
  if (opts?.scopeSet) params.set("scope_set", opts.scopeSet);
  if (opts?.pageOfOrigin) params.set("page_of_origin", opts.pageOfOrigin);
  if (opts?.connectorDetailPath) params.set("connector_detail_path", opts.connectorDetailPath);
  const qs = params.toString();
  return `${API_BASE_URL}/oauth/${encodeURIComponent(provider)}/start${qs ? `?${qs}` : ""}`;
}

/** Fetch all connected Google accounts. */
export function getGoogleAccounts() {
  return apiFetch("/oauth/google/accounts");
}

/** Set a Google account as the primary account. */
export function setPrimaryAccount(accountId: string) {
  return apiFetch("/oauth/google/accounts/{account_id}/primary", { method: "PUT", path: { account_id: accountId } });
}

/** Disconnect (or hard-delete) a Google account. */
export function disconnectAccount(
  accountId: string,
  hardDelete?: boolean,
) {

  return apiFetch("/oauth/google/accounts/{account_id}", { method: "DELETE", path: { account_id: accountId }, query: { hard_delete: hardDelete ? true : undefined } });
}

/** Fetch per-account credential status. */
export function getAccountStatus(accountId: string) {
  return apiFetch("/oauth/google/accounts/{account_id}/status", { path: { account_id: accountId } });
}

// ---------------------------------------------------------------------------
// Google Health connector API functions
// ---------------------------------------------------------------------------



/**
 * Google may report the broader non-`.readonly` variant of a googlehealth
 * scope when the account already holds it, so granted-scope checks must match
 * by FAMILY rather than exact URL (mirrors
 * `butlers.google_account_registry.google_health_scope_family`).
 */
const GOOGLE_HEALTH_SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth.";

export const GOOGLE_HEALTH_SCOPE_FAMILIES = [
  "sleep",
  "activity_and_fitness",
  "health_metrics_and_measurements",
] as const;

/** Return the Google Health family for a `googlehealth.*` scope URL, else null. */
export function googleHealthScopeFamily(scope: string): string | null {
  if (!scope.startsWith(GOOGLE_HEALTH_SCOPE_PREFIX)) return null;
  const family = scope.slice(GOOGLE_HEALTH_SCOPE_PREFIX.length).replace(/\.readonly$/, "");
  return (GOOGLE_HEALTH_SCOPE_FAMILIES as readonly string[]).includes(family) ? family : null;
}

/** Distinct Google Health families covered by `scopes` (readonly or broader variant). */
export function grantedGoogleHealthFamilies(scopes: readonly string[]): Set<string> {
  const families = new Set<string>();
  for (const scope of scopes) {
    const family = googleHealthScopeFamily(scope);
    if (family !== null) families.add(family);
  }
  return families;
}

/** Fetch the Google Health connector status (state, scopes, counts, flags). */
export function getGoogleHealthStatus() {
  return apiFetch("/connectors/google-health/status");
}

/**
 * Scope-selectively disconnect Google Health — preserves Calendar/Drive.
 *
 * When ``accountEmail`` is provided the operation targets that specific account
 * (which may be non-primary).  When omitted the primary account is targeted.
 */
export function disconnectGoogleHealth(opts?: {
  accountEmail?: string;
}) {
  const params: ApiQuery<"/connectors/google-health/disconnect", "DELETE"> = {};
  if (opts?.accountEmail != null) params["account_email"] = opts.accountEmail;
  return apiFetch("/connectors/google-health/disconnect", { method: "DELETE", query: params });
}

// ---------------------------------------------------------------------------
// CLI auth (device-code flow) API functions
// ---------------------------------------------------------------------------



/** List available CLI auth providers and their current auth status. */
export function listCLIAuthProviders() {
  return apiFetch("/cli-auth/providers");
}

/** Start a device-code auth flow for a CLI provider. */
export function startCLIAuth(provider: string) {
  return apiFetch("/cli-auth/{provider}/start", { method: "POST", path: { provider: provider } });
}

/** Poll the status of an in-flight CLI auth session. */
export function getCLIAuthSession(sessionId: string) {
  return apiFetch("/cli-auth/sessions/{session_id}", { path: { session_id: sessionId } });
}

/** Cancel a running CLI auth session. */
export function cancelCLIAuthSession(sessionId: string) {
  return apiFetch("/cli-auth/sessions/{session_id}", { method: "DELETE", path: { session_id: sessionId } });
}

/** Save an API key for an api_key-mode CLI auth provider. */
export function saveCLIAuthApiKey(
  provider: string,
  apiKey: string,
) {
  return apiFetch("/cli-auth/{provider}/api-key", { method: "PUT",
    body: { api_key: apiKey }, path: { provider: provider } });
}

/** Delete a stored API key for an api_key-mode CLI auth provider. */
export function deleteCLIAuthApiKey(provider: string) {
  return apiFetch("/cli-auth/{provider}/api-key", { method: "DELETE", path: { provider: provider } });
}

/** Test a stored API key by running the provider's test command.
 *
 * The backend runs the provider's real test subprocess (an LLM prompt for
 * api_key providers, a live auth probe for device_code providers) with a 30s
 * budget — the default 15s apiFetch timeout would abort legitimate runs.
 */
export function testCLIAuthApiKey(provider: string) {
  return apiFetch("/cli-auth/{provider}/test", { method: "POST",
    timeoutMs: 45_000, path: { provider: provider } });
}

// ---------------------------------------------------------------------------
// Generic secrets CRUD API functions
// ---------------------------------------------------------------------------


// ---------------------------------------------------------------------------
// Backfill job API
// ---------------------------------------------------------------------------



/** Fetch available connector profiles (independent of connector_registry).
 *
 * Returns the catalog of connector types the framework can deploy.
 * Safe to cache for at least 60s (per spec §3.5).
 */
export function listAvailableConnectors() {
  return apiFetch("/ingestion/connectors/available");
}

// ---------------------------------------------------------------------------
// Education
// ---------------------------------------------------------------------------

import type { CurriculumRequestBody, MindMapListParams, QuizResponseParams } from "./types.ts";

/** List mind maps with optional status filter and pagination. */
export function getEducationMindMaps(
  params?: MindMapListParams,
) {
  const sp: ApiQuery<"/education/mind-maps", "GET"> = {};
  if (params?.status) sp["status"] = params.status;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/education/mind-maps", { query: sp });
}

/** Get a single mind map with full node and edge DAG. */
export function getEducationMindMap(mindMapId: string) {
  return apiFetch("/education/mind-maps/{mind_map_id}", { path: { mind_map_id: mindMapId } });
}

/**
 * Resolve specific registered sources by ID.
 *
 * The node detail panel calls this with the `source_id`s named by one node's
 * `metadata.source_refs`, never the whole registry: a hit yields the source's
 * title, a miss means the source was removed and the reference must be shown
 * as unregistered rather than cited. An empty `sourceIds` array resolves to
 * `[]` without a request — there is nothing to look up.
 */
export function getEducationSources(
  sourceIds: string[],
) {
  if (sourceIds.length === 0) return Promise.resolve([]);
  const sp: ApiQuery<"/education/sources", "GET"> = { "source_ids": sourceIds.join(",") };

  return apiFetch("/education/sources", { query: sp });
}

/** Get frontier nodes for a mind map. */
export function getEducationMindMapFrontier(
  mindMapId: string,
) {
  return apiFetch("/education/mind-maps/{mind_map_id}/frontier", { path: { mind_map_id: mindMapId } });
}

/** Get analytics snapshot (with optional trend) for a mind map. */
export function getEducationMindMapAnalytics(
  mindMapId: string,
  trendDays?: number,
) {
  const sp: ApiQuery<"/education/mind-maps/{mind_map_id}/analytics", "GET"> = {};
  if (trendDays != null) sp["trend_days"] = trendDays;
  return apiFetch("/education/mind-maps/{mind_map_id}/analytics", { path: { mind_map_id: mindMapId }, query: sp });
}

/** Get nodes pending (and optionally upcoming) spaced-repetition review.
 *
 * Pass horizonDays to include reviews due within that many days from now,
 * enabling the timeline grouping UI (Overdue / Today / This Week / Later).
 * Omit to receive only overdue nodes (next_review_at <= now).
 */
export function getEducationPendingReviews(
  mindMapId: string,
  horizonDays?: number,
) {

  return apiFetch("/education/mind-maps/{mind_map_id}/pending-reviews", { path: { mind_map_id: mindMapId }, query: { horizon_days: horizonDays } });
}

/** Get aggregate mastery summary for a mind map. */
export function getEducationMasterySummary(
  mindMapId: string,
) {
  return apiFetch("/education/mind-maps/{mind_map_id}/mastery-summary", { path: { mind_map_id: mindMapId } });
}

/** List quiz responses with optional filters. */
export function getEducationQuizResponses(
  params?: QuizResponseParams,
) {
  const sp: ApiQuery<"/education/quiz-responses", "GET"> = {};
  if (params?.mind_map_id) sp["mind_map_id"] = params.mind_map_id;
  if (params?.node_id) sp["node_id"] = params.node_id;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/education/quiz-responses", { query: sp });
}

/** Get cross-topic comparative analytics. */
export function getEducationCrossTopicAnalytics() {
  return apiFetch("/education/analytics/cross-topic");
}

/** Update a mind map's status. */
export function updateEducationMindMapStatus(
  mindMapId: string,
  status: string,
) {
  return apiFetch("/education/mind-maps/{mind_map_id}/status", { method: "PUT", body: { status }, path: { mind_map_id: mindMapId } });
}

/** Submit a curriculum request for the butler to process.
 *
 * Resolves on 202 with the request's durable receipt ID. Acceptance only — read
 * the receipt via `getEducationCurriculumRequest` for the outcome.
 */
export function requestEducationCurriculum(
  body: CurriculumRequestBody,
) {
  return apiFetch("/education/curriculum-requests", { method: "POST",
    body: body });
}

/** Read one curriculum request receipt by its immutable request ID. */
export function getEducationCurriculumRequest(
  requestId: string,
) {
  return apiFetch("/education/curriculum-requests/{request_id}", { path: { request_id: requestId } });
}

/** Read the most recent curriculum request receipt, if any. */
export function getEducationLatestCurriculumRequest() {
  return apiFetch("/education/curriculum-requests/latest");
}

/** Get analytics trend time-series for a mind map (dedicated /analytics/trend endpoint).
 *
 * Wraps GET /api/education/mind-maps/{id}/analytics/trend?days={days}.
 * Snapshots are ordered oldest-first within the requested day window.
 */
export function getEducationMindMapAnalyticsTrend(
  mindMapId: string,
  days: number = 7,
) {
  return apiFetch("/education/mind-maps/{mind_map_id}/analytics/trend", { path: { mind_map_id: mindMapId }, query: { days } });
}

// ---------------------------------------------------------------------------
// Ingestion connectors API (docs/connectors/statistics.md §6)
// ---------------------------------------------------------------------------

import type {
  ConnectorArchiveResult,
  ConnectorAuthBlock,
  ConnectorCheckpoint,
  ConnectorCounters,
  ConnectorDaySummary,
  ConnectorDetail,
  ConnectorEventsResponse,
  ConnectorFanoutResponse,
  ConnectorFanoutRow,
  ConnectorIncidentsResponse,
  ConnectorRoutingRulesResponse,
  ConnectorScopeEntry,
  ConnectorStats,
  ConnectorStatsBucket,
  ConnectorStatsSummary,
  ConnectorSummariesResponse,
  ConnectorSummary,
  IngestionPeriod,
  PipelineStats,
} from "./types.ts";

// Re-export the types so they are accessible from this module too.
export type {
  ConnectorArchiveResult,
  ConnectorAuthBlock,
  ConnectorCheckpoint,
  ConnectorCounters,
  ConnectorDaySummary,
  ConnectorDetail,
  ConnectorScopeEntry,
  ConnectorEventsResponse,
  ConnectorFanoutResponse,
  ConnectorFanoutRow,
  ConnectorIncidentsResponse,
  ConnectorRoutingRulesResponse,
  ConnectorStats,
  ConnectorStatsBucket,
  ConnectorStatsSummary,
  ConnectorSummariesResponse,
  ConnectorSummary,
  IngestionPeriod,
  PipelineStats,
};

// ---------------------------------------------------------------------------
// Internal helpers — backend response shapes
// ---------------------------------------------------------------------------

/** Flat connector detail payload returned by the canonical detail routes. */
export type _ConnectorDetailPayload = import("./schema.gen").components["schemas"]["ConnectorDetailEntry"] &
  Pick<Partial<import("./schema.gen").components["schemas"]["ConnectorSummary"]>, "hourly_events">;

/** Actual generated hourly/daily payloads, projected by the existing view adapter. */
export type _BackendStatsRow = import("./schema.gen").components["schemas"]["ConnectorStatsHourly"] |
  import("./schema.gen").components["schemas"]["ConnectorStatsDaily"];

/**
 * Derive liveness string from last heartbeat timestamp.
 *
 * Mirrors butlers.core.liveness.derive_liveness (Python) exactly so
 * the same connector never disagrees between its detail card and any other
 * reader (bu-27dxl.6.6) -- this was previously a 30-minute
 * stale cutoff, a full 15 minutes later than the backend's, which could
 * show "stale" here for a connector every other surface already reports
 * "offline":
 * - online: heartbeat within the last 5 minutes
 * - stale: heartbeat between 5 and 15 minutes ago
 * - offline: no heartbeat, more than 15 minutes ago, or a heartbeat more
 *   than 5 minutes in the future (clock skew is never treated as online)
 */
function _deriveLiveness(lastHeartbeatAt: string | null): string {
  if (!lastHeartbeatAt) return "offline";
  const ageMs = Date.now() - new Date(lastHeartbeatAt).getTime();
  const ageMins = ageMs / 60_000;
  if (ageMins < -5) return "offline";
  if (ageMins <= 5) return "online";
  if (ageMins <= 15) return "stale";
  return "offline";
}

/** Map a canonical flat detail payload to the frontend ConnectorSummary shape. */
function _toConnectorSummary(entry: _ConnectorDetailPayload): ConnectorSummary {
  return {
    connector_type: entry.connector_type,
    endpoint_identity: entry.endpoint_identity,
    liveness: _deriveLiveness(entry.last_heartbeat_at),
    state: entry.state,
    error_message: entry.error_message,
    version: entry.version,
    uptime_s: entry.uptime_s,
    last_heartbeat_at: entry.last_heartbeat_at,
    first_seen_at: entry.first_seen_at,
    today: {
      messages_ingested: entry.today_messages_ingested,
      messages_failed: entry.today_messages_failed,
      uptime_pct: null,
    },
    hourly_events: entry.hourly_events ?? Array(24).fill(0),
  };
}

/** Map a canonical flat detail payload to the frontend ConnectorDetail shape. */
function _toConnectorDetail(entry: _ConnectorDetailPayload): ConnectorDetail {
  return {
    ..._toConnectorSummary(entry),
    instance_id: entry.instance_id,
    registered_via: entry.registered_via,
    checkpoint:
      entry.checkpoint_cursor != null || entry.checkpoint_updated_at != null
        ? {
            cursor: entry.checkpoint_cursor,
            updated_at: entry.checkpoint_updated_at,
          }
        : null,
    counters: {
      messages_ingested: entry.counter_messages_ingested,
      messages_failed: entry.counter_messages_failed,
      source_api_calls: entry.counter_source_api_calls,
      checkpoint_saves: entry.counter_checkpoint_saves,
      dedupe_accepted: entry.counter_dedupe_accepted,
    },
    settings: entry.settings,
    auth: entry.auth ?? null,
    scopes: entry.scopes ?? null,
  };
}

/**
 * Map a flat list of hourly/daily stats rows into the ConnectorStats shape
 * expected by the connector detail page's period-summary card.
 */
function _toConnectorStats(
  rows: _BackendStatsRow[],
  connectorType: string,
  endpointIdentity: string,
  period: IngestionPeriod,
  hourlyEventsAvailable: boolean,
): ConnectorStats {
  const timeseries: ConnectorStatsBucket[] = rows.map((r) => ({
    bucket: ("hour" in r ? r.hour : r.day),
    messages_ingested: r.messages_ingested,
    messages_failed: r.messages_failed,
    // ?? 0 guards older cached backend rows that predate the filtered series.
    messages_filtered: r.messages_filtered ?? 0,
    healthy_count: r.healthy_count,
    degraded_count: r.degraded_count,
    error_count: r.error_count,
  }));

  const totalIngested = timeseries.reduce((s, r) => s + r.messages_ingested, 0);
  const totalFailed = timeseries.reduce((s, r) => s + r.messages_failed, 0);
  const totalProcessed = totalIngested + totalFailed;
  const errorRatePct = totalProcessed > 0 ? (totalFailed / totalProcessed) * 100 : 0;
  // Approximate avg per hour: for 24h use hourly rows directly; for 7d/30d divide total by hours
  const periodHours = period === "24h" ? 24 : period === "7d" ? 168 : 720;
  const avgPerHour = periodHours > 0 ? totalIngested / periodHours : 0;

  const summary: ConnectorStatsSummary = {
    messages_ingested: totalIngested,
    messages_failed: totalFailed,
    error_rate_pct: errorRatePct,
    uptime_pct: null,
    avg_messages_per_hour: avgPerHour,
  };

  return {
    connector_type: connectorType,
    endpoint_identity: endpointIdentity,
    period,
    summary,
    timeseries,
    hourly_events_available: hourlyEventsAvailable,
  };
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/** Get full detail for a single connector. */
export async function getConnectorDetail(
  connectorType: string,
  endpointIdentity: string,
) {
  const resp = await apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}", { path: { connector_type: connectorType, endpoint_identity: endpointIdentity } });
  return {
    ...resp,
    data: _toConnectorDetail(resp.data),
  };
}

/** Get time-series statistics for a single connector. */
export async function getConnectorStats(
  connectorType: string,
  endpointIdentity: string,
  period: IngestionPeriod = "24h",
) {
  const resp = await apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/stats", { path: { connector_type: connectorType, endpoint_identity: endpointIdentity }, query: { period } });
  // meta.hourly_events_available is false only on a genuine backend DB-query
  // failure (bu-c48im). Absent (older cached response) must NOT read as false.
  const hourlyEventsAvailable = resp.meta?.hourly_events_available !== false;
  return {
    ...resp,
    data: _toConnectorStats(
      resp.data ?? [],
      connectorType,
      endpointIdentity,
      period,
      hourlyEventsAvailable,
    ),
  };
}

/**
 * GET /api/ingestion/connectors/summaries
 * Returns the connector list. Every field is DB-sourced — no Prometheus
 * dependency, so no `aggregates_available` flag on this response.
 */
export async function getConnectorSummaries() {
  const resp = await apiFetch("/ingestion/connectors/summaries");
  return resp;
}

/**
 * GET /api/ingestion/pipeline?window=24h
 * Returns pipeline funnel and independently available scrub estimates (60s TTL cache).
 * Always returns 200; aggregates_available=false when Prometheus is unreachable.
 */
export async function getPipelineStats(
  window: "1h" | "24h" | "7d" = "24h",
) {
  return apiFetch("/ingestion/pipeline", { query: { window } });
}

/**
 * GET /api/switchboard/ingestion/fanout?period=7d
 *
 * The route stays under Switchboard because it aggregates cross-butler
 * delivery. Its explicit availability flag distinguishes a measured empty
 * route set from an unreadable Prometheus source.
 */
export async function getConnectorFanout(
  period: IngestionPeriod = "7d",
) {
  return apiFetch("/switchboard/ingestion/fanout", { query: { period } });
}

/**
 * POST /api/ingestion/events/retry/bulk
 * Bulk-retry/replay up to 100 events from both ingestion and filtered tables.
 * Each event is attempted independently — partial failures do not abort the batch.
 */
export async function bulkRetryEvents(
  eventIds: string[],
) {
  return apiFetch("/ingestion/events/retry/bulk", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: { event_ids: eventIds } });
}

/**
 * GET /api/ingestion/connectors/{type}/{identity}/events?limit=N
 * Returns recent events for a single connector. Default limit=20, max=100.
 * [bu-5ywn2]
 */
export async function getConnectorEvents(
  connectorType: string,
  endpointIdentity: string,
  limit = 20,
) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/events", { path: { connector_type: connectorType, endpoint_identity: endpointIdentity }, query: { limit } });
}

/**
 * GET /api/ingestion/connectors/{type}/{identity}/incidents?limit=N
 * Returns incident events (failures, errors) for a single connector. Default limit=10, max=50.
 * [bu-5ywn2]
 */
export async function getConnectorIncidents(
  connectorType: string,
  endpointIdentity: string,
  limit = 10,
) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/incidents", { path: { connector_type: connectorType, endpoint_identity: endpointIdentity }, query: { limit } });
}

/**
 * GET /api/ingestion/connectors/{type}/{identity}/routing-rules
 * Returns ingestion rules scoped to this connector (scope='connector:type:identity').
 * [bu-5ywn2]
 */
export async function getConnectorRoutingRules(
  connectorType: string,
  endpointIdentity: string,
) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/routing-rules", { path: { connector_type: connectorType, endpoint_identity: endpointIdentity } });
}

/** Update connector settings (shallow merge). */
export async function updateConnectorSettings(
  connectorType: string,
  endpointIdentity: string,
  settings: Record<string, unknown>,
) {
  const resp = await apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/settings", { method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: { settings }, path: { connector_type: connectorType, endpoint_identity: endpointIdentity } });
  return {
    ...resp,
    data: _toConnectorDetail(resp.data),
  };
}

/**
 * POST /api/ingestion/connectors/{type}/{identity}/archive
 *
 * Soft-archive a superseded connector identity (audit-only, no Approvals gate).
 * Reuses the existing archive mechanism (bu-33dm2) — the archive review queue
 * (bu-u19yv) calls this for its one-click archive action. Idempotent.
 */
export async function archiveConnector(
  connectorType: string,
  endpointIdentity: string,
) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/archive", { method: "POST", path: { connector_type: connectorType, endpoint_identity: endpointIdentity } });
}

/**
 * POST /api/ingestion/connectors/{type}/{identity}/unarchive
 *
 * Restore an archived connector identity back to the active roster
 * (audit-only, bu-33dm2). The backend has carried this endpoint since the
 * archive mechanism shipped; the dashboard never wired a UI path back to it
 * until bu-ep4ks.11 (the archive review queue's one-click archive had no
 * undo affordance). Idempotent.
 */
export async function unarchiveConnector(
  connectorType: string,
  endpointIdentity: string,
) {
  return apiFetch("/ingestion/connectors/{connector_type}/{endpoint_identity}/unarchive", { method: "POST", path: { connector_type: connectorType, endpoint_identity: endpointIdentity } });
}

// ---------------------------------------------------------------------------
// Unified ingestion rules (design.md D8)
// ---------------------------------------------------------------------------

/** List active ingestion rules with optional filters. */
export function getIngestionRules(
  params?: IngestionRuleListParams,
) {

  return apiFetch("/switchboard/ingestion-rules", { query: params });
}

/** Create a new ingestion rule. */
export function createIngestionRule(
  body: IngestionRuleCreate,
) {
  return apiFetch("/switchboard/ingestion-rules", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body });
}

/** Partially update an ingestion rule. */
export function updateIngestionRule(
  ruleId: string,
  body: IngestionRuleUpdate,
) {
  return apiFetch("/switchboard/ingestion-rules/{rule_id}", { method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body, path: { rule_id: ruleId } });
}

/** Soft-delete an ingestion rule. */
export function deleteIngestionRule(ruleId: string) {
  return apiFetch("/switchboard/ingestion-rules/{rule_id}", { method: "DELETE", path: { rule_id: ruleId } });
}

/** Dry-run: evaluate a test envelope against active ingestion rules. */
export function testIngestionRule(
  body: IngestionRuleTestRequest,
) {
  return apiFetch("/switchboard/ingestion-rules/test", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body });
}

// ---------------------------------------------------------------------------
// Channel defaults (GET/PATCH /api/ingestion/channel-defaults/:channel)
//
// Per-channel fallback ingestion policy — public.channel_defaults. No DELETE
// surface; the backend always returns 405 for that verb.
// ---------------------------------------------------------------------------

/** Fetch a channel's default policy. Throws ApiError with status 404 if unset. */
export function getChannelDefault(channel: string) {
  return apiFetch("/ingestion/channel-defaults/{channel}", { path: { channel: channel } });
}

/** Upsert a channel's default policy. */
export function updateChannelDefault(
  channel: string,
  body: ChannelDefaultUpdate,
) {
  return apiFetch("/ingestion/channel-defaults/{channel}", { method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body, path: { channel: channel } });
}

// ---------------------------------------------------------------------------
// Priority contacts (GET/POST/DELETE /api/ingestion/priority-contacts)
//
// Runtime source of truth for priority senders — public.priority_contacts.
// ---------------------------------------------------------------------------

/** List priority contacts (global — butler-agnostic). */
export function getPriorityContacts(
  params?: PriorityContactListParams,
) {
  const sp: ApiQuery<"/ingestion/priority-contacts", "GET"> = {};
  if (params?.offset !== undefined) sp["offset"] = params.offset;
  if (params?.limit !== undefined) sp["limit"] = params.limit;
  return apiFetch("/ingestion/priority-contacts", { query: sp });
}

/** Add a priority contact (global — butler-agnostic). */
export function addPriorityContact(
  body: PriorityContactAddRequest,
) {
  return apiFetch("/ingestion/priority-contacts", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body });
}

/** Remove a priority contact (global — butler-agnostic). */
export function removePriorityContact(contactId: string) {
  return apiFetch("/ingestion/priority-contacts/{contact_id}", { method: "DELETE", path: { contact_id: contactId } });
}

// ---------------------------------------------------------------------------
// Contacts identity typeahead (GET /api/contacts/search)
//
// Read-only person-entity typeahead over the cross-butler identity layer.
// Powers the People picker that links contacts to calendar events. A blank or
// unmatched query returns HTTP 200 with an empty list — never an error.
// ---------------------------------------------------------------------------

/** Search known person entities for a contact typeahead. */
export function searchContacts(
  q: string,
  options?: { limit?: number; signal?: AbortSignal },
) {
  const sp: ApiQuery<"/contacts/search", "GET"> = { q };
  if (options?.limit !== undefined) sp["limit"] = options.limit;
  return apiFetch("/contacts/search", { signal: options?.signal, query: sp });
}

// ---------------------------------------------------------------------------
// Ingestion event lineage (GET /api/ingestion/events/*)
// ---------------------------------------------------------------------------

/** List ingestion events with cursor pagination (GET /api/ingestion/events). */
export async function listIngestionEvents(
  params?: IngestionEventsParams,
  signal?: AbortSignal,
) {
  const sp: ApiQuery<"/ingestion/events", "GET"> = {};
  if (params?.limit !== undefined) sp["limit"] = params.limit;
  if (params?.cursor) sp["cursor"] = params.cursor;
  if (params?.channels) sp["channels"] = params.channels;
  if (params?.status) sp["status"] = params.status;
  if (params?.statuses) sp["statuses"] = params.statuses;
  if (params?.q) sp["q"] = params.q;
  if (params?.from) sp["from"] = params.from;
  if (params?.to) sp["to"] = params.to;
  if (params?.sort) sp["sort"] = params.sort;
  if (params?.trace_id) sp["trace_id"] = params.trace_id;
  return apiFetch("/ingestion/events", { signal, query: sp });
}

/**
 * Aggregate event/session/cost counts for the active filter window.
 * GET /api/ingestion/rollup
 *
 * Accepts the same filter shape as GET /api/ingestion/events. ``cost`` is a
 * known-priced subtotal; ``unpriced_session_count`` covers sessions whose
 * cost cannot be derived.
 */
export async function getIngestionWindowRollup(
  params?: IngestionWindowRollupParams,
  signal?: AbortSignal,
) {
  const sp: ApiQuery<"/ingestion/rollup", "GET"> = {};
  if (params?.from) sp["from"] = params.from;
  if (params?.to) sp["to"] = params.to;
  if (params?.channels) sp["channels"] = params.channels;
  if (params?.statuses) sp["statuses"] = params.statuses;
  if (params?.q) sp["q"] = params.q;
  if (params?.trace_id) sp["trace_id"] = params.trace_id;
  return apiFetch("/ingestion/rollup", { signal, query: sp });
}

/**
 * Per-bucket ingestion event counts by status for a time window.
 * GET /api/ingestion/events/histogram
 *
 * Powers a status-aware timeline hour strip. `from` and `to` are required
 * UNLESS `trace_id` is set — a trace-scoped query auto-widens to the
 * trace's own event bounds server-side instead (bu-1f81d), so `from`/`to`
 * are omitted from the query string when absent rather than forcing an
 * unbounded scan the server has no default window for. Accepts the same
 * `channels`/`statuses`/`q` filters as GET /api/ingestion/events.
 *
 * The server enforces a bucket-count guardrail (422 when the range/bucket
 * combination is too wide — e.g. '1m' over a range >48h); retry with a
 * coarser `bucket` on 422. (Trace-scoped queries auto-escalate the bucket
 * server-side instead.)
 */
export async function getIngestionEventsHistogram(
  params: IngestionHistogramParams,
  signal?: AbortSignal,
) {
  const sp: ApiQuery<"/ingestion/events/histogram", "GET"> = {};
  if (params.from) sp["from"] = params.from;
  if (params.to) sp["to"] = params.to;
  if (params.bucket) sp["bucket"] = params.bucket;
  if (params.channels) sp["channels"] = params.channels;
  if (params.statuses) sp["statuses"] = params.statuses;
  if (params.q) sp["q"] = params.q;
  if (params.trace_id) sp["trace_id"] = params.trace_id;
  return apiFetch("/ingestion/events/histogram", { signal, query: sp });
}

/**
 * Unanswered drops of messages from known contacts.
 * GET /api/ingestion/events/dropped-known
 *
 * Degraded-honest: `available=false` means unknown, not zero.
 */
export async function getIngestionDroppedKnown(
  window: "1h" | "24h" | "7d" = "24h",
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/dropped-known", { signal, query: { window } });
}

/** Get a single ingestion event by request_id (GET /api/ingestion/events/{id}). */
export async function getIngestionEvent(
  requestId: string,
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/{request_id}", { signal, path: { request_id: requestId } });
}

/** Get sessions for an ingestion event (GET /api/ingestion/events/{id}/sessions). */
export async function getIngestionEventSessions(
  requestId: string,
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/{request_id}/sessions", { signal, path: { request_id: requestId } });
}

/**
 * Request replay of a filtered/error/replay_failed ingestion event.
 * POST /api/ingestion/events/{id}/replay
 *
 * Returns the updated event id + new status (replay_pending).
 * Throws ApiError on 404 (unknown id) or 409 (non-replayable status).
 */
export async function replayIngestionEvent(
  requestId: string,
) {
  return apiFetch("/ingestion/events/{event_id}/replay", { method: "POST", path: { event_id: requestId } });
}

/**
 * Get replay attempt history for an ingestion event.
 * GET /api/ingestion/events/{id}/replays
 */
export async function getIngestionEventReplays(
  requestId: string,
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/{event_id}/replays", { signal, path: { event_id: requestId } });
}

/**
 * Resolve sender_identity to a contact name for an ingestion event.
 * GET /api/ingestion/events/{id}/sender-contact
 */
export async function getIngestionEventSenderContact(
  requestId: string,
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/{event_id}/sender-contact", { signal, path: { event_id: requestId } });
}

/**
 * Get the raw inbound payload for an ingestion event.
 * GET /api/ingestion/events/{id}/payload
 *
 * Gated by audit log: the backend records an audit entry on every access.
 * Returns 403 when the caller lacks payload-access grant.
 * Callers MUST handle ApiError with status 403 and render a gated state.
 */
export async function getIngestionEventPayload(
  requestId: string,
  signal?: AbortSignal,
) {
  return apiFetch("/ingestion/events/{event_id}/payload", { signal, path: { event_id: requestId } });
}

// ---------------------------------------------------------------------------
// Model catalog
// ---------------------------------------------------------------------------

/** GET /api/settings/pricing — fetch per-model pricing map */
export function fetchPricingMap() {
  return apiFetch("/settings/pricing");
}

/** GET /api/settings/models — list all catalog entries */
export function listModelCatalog() {
  return apiFetch("/settings/models");
}

/** POST /api/settings/models — create a catalog entry */
export function createModelCatalogEntry(
  body: ModelCatalogCreate,
) {
  return apiFetch("/settings/models", { method: "POST",
    body: body });
}

/** PUT /api/settings/models/{id} — update a catalog entry */
export function updateModelCatalogEntry(
  id: string,
  body: ModelCatalogUpdate,
) {
  return apiFetch("/settings/models/{entry_id}", { method: "PUT",
      body: body, path: { entry_id: id } });
}

/** POST /api/settings/models/{id}/test — test a model config */
export function testModelCatalogEntry(
  id: string,
) {
  return apiFetch("/settings/models/{entry_id}/test", { method: "POST", path: { entry_id: id } });
}

export function getModelAttention() {
  return apiFetch("/settings/models/attention");
}

export function getModelAllowance() {
  return apiFetch("/settings/models/allowance");
}

export function reissueModelAttention(
  episodeId: string,
) {
  return apiFetch("/settings/models/attention/{episode_id}/reissue", { method: "POST", path: { episode_id: episodeId } });
}

/** DELETE /api/settings/models/{id} — delete a catalog entry */
export function deleteModelCatalogEntry(
  id: string,
) {
  return apiFetch("/settings/models/{entry_id}", { method: "DELETE", path: { entry_id: id } });
}

/** GET /api/settings/models/{id}/delete-impact — current cascade count */
export function getModelCatalogDeleteImpact(
  id: string,
) {
  return apiFetch("/settings/models/{entry_id}/delete-impact", { path: { entry_id: id } });
}

// ---------------------------------------------------------------------------
// Butler model overrides
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/model-overrides — list overrides for a butler */
export function listButlerModelOverrides(
  butlerName: string,
) {
  return apiFetch("/butlers/{name}/model-overrides", { path: { name: butlerName } });
}

/** PUT /api/butlers/{name}/model-overrides — batch upsert overrides */
export function upsertButlerModelOverrides(
  butlerName: string,
  body: ButlerModelOverridesWrite,
) {
  return apiFetch("/butlers/{name}/model-overrides", { method: "PUT",
      body: body, path: { name: butlerName } });
}

/** DELETE /api/butlers/{name}/model-overrides/{overrideId} — remove a single override */
export function deleteButlerModelOverride(
  butlerName: string,
  overrideId: string,
  body: ButlerModelOverrideReason,
) {
  return apiFetch("/butlers/{name}/model-overrides/{override_id}", { method: "DELETE", body: body, path: { name: butlerName, override_id: overrideId } });
}

/** GET /api/butlers/{name}/resolve-model?complexity=X — preview model resolution */
export function resolveButlerModel(
  butlerName: string,
  complexity: string,
) {
  return apiFetch("/butlers/{name}/resolve-model", { path: { name: butlerName }, query: { complexity } });
}

/** PUT /api/settings/models/{id}/limits — set or update token limits */
export function setModelTokenLimits(
  id: string,
  body: TokenLimitsRequest,
) {
  return apiFetch("/settings/models/{entry_id}/limits", { method: "PUT",
      body: body, path: { entry_id: id } });
}

/** POST /api/settings/models/{id}/reset-usage — reset usage window(s) */
export function resetModelUsage(
  id: string,
  body: ResetUsageRequest,
) {
  return apiFetch("/settings/models/{entry_id}/reset-usage", { method: "POST",
      body: body, path: { entry_id: id } });
}

/** GET /api/settings/models/{id}/usage — detailed usage for a single entry */
export function getModelUsageDetail(
  id: string,
) {
  return apiFetch("/settings/models/{entry_id}/usage", { path: { entry_id: id } });
}

/** PUT /api/settings/models/{id}/priority — adjust priority by delta */
export function updateModelPriority(
  id: string,
  body: ModelPriorityDelta,
) {
  return apiFetch("/settings/models/{entry_id}/priority", { method: "PUT",
      body: body, path: { entry_id: id } });
}

/** POST /api/settings/models/verify-all — re-verify every enabled model */
export function verifyAllModels() {
  return apiFetch("/settings/models/verify-all", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Provider configuration
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// WhatsApp connector API
// ---------------------------------------------------------------------------

/** GET /api/connectors/whatsapp/status — current connection state */
export function getWhatsAppStatus() {
  return apiFetch("/connectors/whatsapp/status");
}

/** POST /api/connectors/whatsapp/pair/start — initiate QR pairing */
export function startWhatsAppPairing() {
  return apiFetch("/connectors/whatsapp/pair/start", { method: "POST" });
}

/** GET /api/connectors/whatsapp/pair/poll — poll pairing progress */
export function pollWhatsAppPairing() {
  return apiFetch("/connectors/whatsapp/pair/poll");
}

/** POST /api/connectors/whatsapp/disconnect — gracefully disconnect */
export function disconnectWhatsApp() {
  return apiFetch("/connectors/whatsapp/disconnect", { method: "POST" });
}

/** GET /api/relationship/dunbar/ranking — Dunbar tier ranking for the Plex and contacts views. */
export function getDunbarRanking() {
  return apiFetch("/relationship/dunbar/ranking");
}

// ---------------------------------------------------------------------------
// Spotify connector API
// ---------------------------------------------------------------------------

/** GET /api/spotify/status — current Spotify connection state */
export function getSpotifyStatus() {
  return apiFetch("/connectors/spotify/status");
}

/** POST /api/spotify/oauth/start — initiate PKCE OAuth flow, returns authorization URL */
export function startSpotifyOAuth() {
  return apiFetch("/connectors/spotify/oauth/start", { method: "POST" });
}

/** POST /api/spotify/config — store Spotify client_id */
export function saveSpotifyConfig(data: SpotifyConfigRequest) {
  return apiFetch("/connectors/spotify/config", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data });
}

/** POST /api/connectors/spotify/disconnect — clear locally stored OAuth tokens and scopes */
export function disconnectSpotify() {
  return apiFetch("/connectors/spotify/disconnect", { method: "POST" });
}

// ---------------------------------------------------------------------------
// OwnTracks connector API
// ---------------------------------------------------------------------------

/** GET /api/connectors/owntracks/status — connection state, last event, event count */
export function getOwnTracksStatus() {
  return apiFetch("/connectors/owntracks/status");
}

/** GET /api/connectors/owntracks/config — webhook URL and setup metadata */
export function getOwnTracksConfig() {
  return apiFetch("/connectors/owntracks/config");
}

/** POST /api/connectors/owntracks/token/generate — generate/regenerate bearer token */
export function generateOwnTracksToken() {
  return apiFetch("/connectors/owntracks/token/generate", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Home Assistant settings API
// ---------------------------------------------------------------------------

/** GET /api/settings/home-assistant — current HA connection state */
export function getHomeAssistantStatus() {
  return apiFetch("/settings/home-assistant");
}

/** POST /api/settings/home-assistant — validate and save HA URL + token */
export function configureHomeAssistant(
  data: HomeAssistantConfigRequest,
) {
  return apiFetch("/settings/home-assistant", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data });
}

/** DELETE /api/settings/home-assistant — remove stored HA credentials */
export function deleteHomeAssistantConfig() {
  return apiFetch("/settings/home-assistant", { method: "DELETE" });
}

/** Submit exact private HA-person mappings as one atomic batch. */
export function submitHomePersonMappings(
  mappings: HomePersonMappingInput[],
  idempotencyKey: string,
) {
  return apiFetch("/home/person-mappings", { method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: { mappings } });
}

// ---------------------------------------------------------------------------
// Dashboard conversation API
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/conversations — paginated conversation list. */
export function listConversations(
  butlerName: string,
  params?: ConversationListParams,
) {
  return apiFetch("/butlers/{name}/conversations", { path: { name: butlerName }, query: params });
}

/**
 * GET /api/conversations/{id} — cross-butler conversation identity lookup by
 * id alone, regardless of owning butler_name (bu-0ynlk.11). Backs the
 * /chat/:conversationId deep-link route and cmdk recent-thread recall.
 * Raw (unwrapped) body — the response model carries no `data` envelope.
 * Throws `ApiError` with `status === 404` when the id is unknown.
 */
export function getConversationById(conversationId: string) {
  return apiFetch("/conversations/{conversation_id}", { path: { conversation_id: conversationId } });
}

/** GET /api/butlers/{name}/conversations/{id}/messages — message list for a conversation. */
export function getConversationMessages(
  butlerName: string,
  conversationId: string,
) {
  return apiFetch("/butlers/{name}/conversations/{conversation_id}/messages", { path: { name: butlerName, conversation_id: conversationId } });
}

/**
 * GET /api/butlers/{name}/conversations/search — full-text search across conversations.
 */
export function searchConversations(
  butlerName: string,
  query: string,
) {
  return apiFetch("/butlers/{name}/conversations/search", { path: { name: butlerName }, query: { q: query } });
}

/**
 * GET /api/conversations/messages/search — owner-scoped, cursor-paginated
 * full-text search across every butler's dashboard messages. One row per
 * matching message, ranked by relevance then recency, with highlight ranges.
 */
export function searchMessages(
  params: MessageSearchParams,
) {
  const sp: ApiQuery<"/conversations/messages/search", "GET"> = { "q": params.q };

  if (params.limit !== undefined) sp["limit"] = params.limit;
  if (params.cursor) sp["cursor"] = params.cursor;
  if (params.channel) sp["channel"] = params.channel;
  if (params.butler) sp["butler"] = params.butler;
  if (params.from) sp["from"] = params.from;
  if (params.to) sp["to"] = params.to;
  return apiFetch("/conversations/messages/search", { query: sp });
}

/**
 * POST /api/butlers/{name}/conversations — create a new conversation with SSE streaming.
 * Returns the raw Response so callers can consume the SSE body directly.
 */
export function createConversation(
  butlerName: string,
  body: CreateConversationRequest,
  signal?: AbortSignal,
): Promise<Response> {
  return ownerFetch(`${API_BASE_URL}/butlers/${encodeURIComponent(butlerName)}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
}

/**
 * POST /api/butlers/{name}/conversations/{id}/messages — send a follow-up with SSE streaming.
 * Returns the raw Response so callers can consume the SSE body directly.
 */
export function sendMessage(
  butlerName: string,
  conversationId: string,
  body: SendMessageRequest,
  signal?: AbortSignal,
): Promise<Response> {
  return ownerFetch(
    `${API_BASE_URL}/butlers/${encodeURIComponent(butlerName)}/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    },
  );
}

/**
 * POST /api/butlers/{name}/conversation-turns/{messageId}/cancel — cancel
 * one immutable dashboard user turn across classifier and routed runtimes.
 * Unlike the legacy conversation-scoped endpoint, this remains precise while
 * a Switchboard handoff is still in flight and even before SSE has delivered
 * a newly-created conversation id.
 */
export function cancelConversationMessageTurn(
  butlerName: string,
  messageId: string,
) {
  return apiFetch("/butlers/{name}/conversation-turns/{message_id}/cancel", { method: "POST", path: { name: butlerName, message_id: messageId } });
}

// ---------------------------------------------------------------------------
// Telegram Session Auth
// ---------------------------------------------------------------------------

/** POST /api/telegram/session/send-code — start Telegram login, send OTP */
export function telegramSendCode(
  request: TelegramSendCodeRequest,
) {
  return apiFetch("/telegram/session/send-code", { method: "POST",
    body: request });
}

/** POST /api/telegram/session/verify — verify OTP code and persist session */
export function telegramVerifyCode(
  request: TelegramVerifyCodeRequest,
) {
  return apiFetch("/telegram/session/verify", { method: "POST",
    body: request });
}

/** GET /api/telegram/session/status — check Telegram credentials and scope consent */
export function getTelegramSessionStatus() {
  return apiFetch("/telegram/session/status");
}

// ---------------------------------------------------------------------------
// General settings
// ---------------------------------------------------------------------------

/** GET /api/settings/general — fetch shared prompt defaults */
export function getGeneralSettings() {
  return apiFetch("/settings/general");
}

// ---------------------------------------------------------------------------
// Blob storage settings
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Steam connector API
// ---------------------------------------------------------------------------

/** GET /api/steam/accounts — list all connected Steam accounts */
export function listSteamAccounts() {
  return apiFetch("/steam/accounts");
}

/** POST /api/steam/accounts — connect a new Steam account */
export function connectSteamAccount(data: SteamConnectRequest) {
  return apiFetch("/steam/accounts", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: data });
}

/** DELETE /api/steam/accounts/{id} — disconnect (soft-revoke) a Steam account */
export function disconnectSteamAccount(accountId: string) {
  return apiFetch("/steam/accounts/{account_id}", { method: "DELETE", path: { account_id: accountId } });
}

// ---------------------------------------------------------------------------
// Healing attempts API (used by QA investigation detail page)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// QA Staffer API
// ---------------------------------------------------------------------------

/** GET /api/qa/summary — QA staffer status, last patrol, 24h/all-time stats */
export function getQaSummary() {
  return apiFetch("/qa/summary");
}

/** GET /api/qa/cases — paginated QA case summaries */
export function getQaCases(params?: QaCasesParams) {
  const query: ApiQuery<"/qa/cases", "GET"> = {};
  if (params?.sev) query["sev"] = params.sev;
  if (params?.state) query["state"] = params.state;
  if (params?.since) query["since"] = params.since;
  if (params?.butler != null) {
    const butlers = Array.isArray(params.butler) ? params.butler : [params.butler];
    butlers.forEach((name) => {
      const trimmed = name?.trim();
      if (trimmed != null && trimmed !== "") {
        (query["butler"] ??= []).push(trimmed);
      }
    });
  }
  if (params?.offset !== undefined) query["offset"] = params.offset;
  if (params?.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/qa/cases", { query: query });
}

/** GET /api/qa/cases/:caseId — full case dossier */
export function getQaCase(caseId: string) {
  return apiFetch("/qa/cases/{case_id}", { path: { case_id: caseId } });
}

/** GET /api/qa/cases/:caseId/journal — paginated journal events */
export function getQaCaseJournal(
  caseId: string,
  params?: QaCaseJournalParams,
) {
  const query: ApiQuery<"/qa/cases/{case_id}/journal", "GET"> = {};
  if (params?.cursor) query["cursor"] = params.cursor;
  if (params?.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/qa/cases/{case_id}/journal", { path: { case_id: caseId }, query: query });
}

/** GET /api/qa/patrols — paginated patrol list */
export function getQaPatrols(params?: QaPatrolsParams) {
  const query: ApiQuery<"/qa/patrols", "GET"> = {};
  if (params?.offset !== undefined) query["offset"] = params.offset;
  if (params?.limit !== undefined) query["limit"] = params.limit;
  if (params?.status) query["status"] = params.status;
  return apiFetch("/qa/patrols", { query: query });
}

/** GET /api/qa/patrols/:patrolId — full patrol with nested findings */
export function getQaPatrol(patrolId: string) {
  return apiFetch("/qa/patrols/{patrol_id}", { path: { patrol_id: patrolId } });
}

/** POST /api/qa/known-issues/:fingerprint/dismiss — dismiss a known issue */
export function dismissQaKnownIssue(
  fingerprint: string,
  body?: QaDismissRequest,
) {
  return apiFetch("/qa/known-issues/{fingerprint}/dismiss", { method: "POST",
      body: body ?? {}, path: { fingerprint: fingerprint } });
}

/** DELETE /api/qa/dismissals/:fingerprint — remove an active dismissal */
export function removeQaDismissal(
  fingerprint: string,
) {
  return apiFetch("/qa/dismissals/{fingerprint}", { method: "DELETE", path: { fingerprint: fingerprint } });
}

/** GET /api/qa/investigations — paginated investigation pipeline */
export function getQaInvestigations(
  params?: QaInvestigationsParams,
) {
  const query: ApiQuery<"/qa/investigations", "GET"> = {};
  if (params?.status) query["status"] = params.status;
  if (params?.offset !== undefined) query["offset"] = params.offset;
  if (params?.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/qa/investigations", { query: query });
}

/** POST /api/qa/force-patrol — request an immediate patrol cycle */
export function forceQaPatrol() {
  return apiFetch("/qa/force-patrol", { method: "POST" });
}

/** GET /api/qa/circuit-breaker — current circuit breaker state */
export function getQaCircuitBreaker() {
  return apiFetch("/qa/circuit-breaker");
}

/** POST /api/qa/circuit-breaker/reset — reset a tripped circuit breaker */
export function resetQaCircuitBreaker() {
  return apiFetch("/qa/circuit-breaker/reset", { method: "POST" });
}

/** GET /api/qa/settings/repo — current repo configuration */
export function getQaRepoConfig() {
  return apiFetch("/qa/settings/repo");
}

/** PUT /api/qa/settings/repo — update repo URL */
export function updateQaRepoConfig(
  body: QaRepoConfigUpdate,
) {
  return apiFetch("/qa/settings/repo", { method: "PUT",
    body: body });
}

/** POST /api/qa/settings/repo/sync — trigger immediate sync */
export function syncQaRepo() {
  return apiFetch("/qa/settings/repo/sync", { method: "POST" });
}

/** PUT /api/qa/settings/git-author — store git author identity (name + email) */
export function updateQaGitAuthor(
  body: QaGitAuthorUpdate,
) {
  return apiFetch("/qa/settings/git-author", { method: "PUT",
    body: body });
}

/** GET /api/qa/settings/allowed-repos — list allowed repositories */
export function getQaAllowedRepos() {
  return apiFetch("/qa/settings/allowed-repos");
}

/** POST /api/qa/settings/allowed-repos — add a repository */
export function addQaAllowedRepo(
  body: QaAllowedRepoCreate,
) {
  return apiFetch("/qa/settings/allowed-repos", { method: "POST",
    body: body });
}

/** PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle enabled */
export function patchQaAllowedRepo(
  owner: string,
  repo: string,
  body: QaAllowedRepoPatch,
) {
  return apiFetch("/qa/settings/allowed-repos/{owner}/{repo}", { method: "PATCH", body: body, path: { owner: owner, repo: repo } });
}

/** DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove */
export function deleteQaAllowedRepo(
  owner: string,
  repo: string,
) {
  return apiFetch("/qa/settings/allowed-repos/{owner}/{repo}", { method: "DELETE", path: { owner: owner, repo: repo } });
}

// ---------------------------------------------------------------------------
// Runtime Config
// ---------------------------------------------------------------------------

/** Fetch runtime config for a specific butler. */
export function getRuntimeConfig(
  name: string,
) {
  return apiFetch("/butlers/{name}/runtime-config", { path: { name: name } });
}

/** Partially update runtime config for a butler. */
export function patchRuntimeConfig(
  name: string,
  body: RuntimeConfigPatch,
) {
  return apiFetch("/butlers/{name}/runtime-config", { method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body, path: { name: name } });
}

// ---------------------------------------------------------------------------
// Chronicler
// ---------------------------------------------------------------------------

/** Fetch paginated chronicler episodes. Defaults: include_tombstoned=false. */
export function getChroniclerEpisodes(
  params?: ChroniclerEpisodesParams,
) {
  const sp: ApiQuery<"/chronicler/episodes", "GET"> = {};
  if (params?.source_name) sp["source_name"] = params.source_name;
  if (params?.episode_type) sp["episode_type"] = params.episode_type;
  if (params?.start_from) sp["start_from"] = params.start_from;
  if (params?.start_to) sp["start_to"] = params.start_to;
  if (params?.overlaps_start) sp["overlaps_start"] = params.overlaps_start;
  if (params?.overlaps_end) sp["overlaps_end"] = params.overlaps_end;
  if (params?.include_tombstoned != null)
    sp["include_tombstoned"] = params.include_tombstoned;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/chronicler/episodes", { query: sp });
}

/** Fetch category aggregates for a time window. Restricted excluded by default. */
export function getChroniclerAggregateByCategory(
  params: ChroniclerAggregateByCategoryParams,
) {
  const sp: ApiQuery<"/chronicler/aggregate/by-category", "GET"> = { start_at: params.start_at, end_at: params.end_at };
  if (params.tz) sp["tz"] = params.tz;
  if (params.privacy_tier) sp["privacy_tier"] = params.privacy_tier;
  if (params.include_tombstoned != null)
    sp["include_tombstoned"] = params.include_tombstoned;
  return apiFetch("/chronicler/aggregate/by-category", { query: sp });
}

/** Fetch time-bucketed episode durations grouped by (day, category). */
export function getChroniclerAggregateByDay(
  params: ChroniclerAggregateByDayParams,
) {
  const sp: ApiQuery<"/chronicler/aggregate/by-day", "GET"> = { start_at: params.start_at, end_at: params.end_at };
  if (params.tz) sp["tz"] = params.tz;
  if (params.category) sp["category"] = params.category;
  if (params.privacy_tier) sp["privacy_tier"] = params.privacy_tier;
  if (params.include_tombstoned != null)
    sp["include_tombstoned"] = params.include_tombstoned;
  return apiFetch("/chronicler/aggregate/by-day", { query: sp });
}

/** Fetch source adapter state joined with projection checkpoints (singleton, sorted by source_name). */
export function getChroniclerSourceState() {
  return apiFetch("/chronicler/source-state");
}

/**
 * Fetch the target day's per-lane balance annotated against the owner's
 * rolling "usual" baseline. IEA §9b — GET /api/chronicler/balance.
 */
export function getChroniclerBalance(
  params: ChroniclerBalanceParams,
) {
  const sp: ApiQuery<"/chronicler/balance", "GET"> = { date: params.date };
  if (params.lookback_days != null) sp["lookback_days"] = params.lookback_days;
  return apiFetch("/chronicler/balance", { query: sp });
}

/**
 * Fetch week/month-grained per-lane balance trends, streaks, and anomalies.
 * IEA §9b — GET /api/chronicler/trends.
 */
export function getChroniclerTrends(
  params?: ChroniclerTrendsParams,
) {
  const sp: ApiQuery<"/chronicler/trends", "GET"> = {};
  if (params?.window) sp["window"] = params.window;
  if (params?.end_date) sp["end_date"] = params.end_date;
  if (params?.lookback_days != null) sp["lookback_days"] = params.lookback_days;
  return apiFetch("/chronicler/trends", { query: sp });
}

/**
 * Fetch daily rollups + anomaly flags (with optional LLM narrative) for one
 * local day or an inclusive range. Provide either `date` alone, or
 * `start_date`+`end_date` together. GET /api/chronicler/rollups.
 */
export function getChroniclerRollups(
  params: ChroniclerRollupsParams,
) {
  const sp: ApiQuery<"/chronicler/rollups", "GET"> = {};
  if (params.date) sp["date"] = params.date;
  if (params.start_date) sp["start_date"] = params.start_date;
  if (params.end_date) sp["end_date"] = params.end_date;
  return apiFetch("/chronicler/rollups", { query: sp });
}

/**
 * Fetch the resolved people the owner spent time with in a window, with
 * co-present time and channel. IEA §9b — GET /api/chronicler/who-you-were-with.
 */
export function getChroniclerWhoYouWereWith(
  params: ChroniclerWhoYouWereWithParams,
) {
  const sp: ApiQuery<"/chronicler/who-you-were-with", "GET"> = { start_at: params.start_at, end_at: params.end_at };
  if (params.tz) sp["tz"] = params.tz;
  return apiFetch("/chronicler/who-you-were-with", { query: sp });
}

/**
 * Fetch the evidence chain backing an activity — "why is this counted?".
 * IEA §9a — GET /api/chronicler/episodes/{id}/evidence-chain.
 */
export function getChroniclerEvidenceChain(
  episodeId: string,
) {
  return apiFetch("/chronicler/episodes/{episode_id}/evidence-chain", { path: { episode_id: episodeId } });
}

/**
 * Fetch the window's low-confidence activities as correction prompts.
 * IEA §9a — GET /api/chronicler/correction-prompts.
 */
export function getChroniclerCorrectionPrompts(
  params: ChroniclerCorrectionPromptsParams,
) {
  const sp: ApiQuery<"/chronicler/correction-prompts", "GET"> = { start_at: params.start_at, end_at: params.end_at };
  if (params.tz) sp["tz"] = params.tz;
  if (params.limit != null) sp["limit"] = params.limit;
  return apiFetch("/chronicler/correction-prompts", { query: sp });
}

/**
 * Fetch the day-close cache entry for one exact local date/timezone tuple.
 * Returns fresh prose, a stale marker, or an invalid-without-prose marker.
 * 404 if no cache entry exists.
 */
export function getChroniclerDayClose(
  params: ChroniclerDayCloseParams,
) {
  const sp: ApiQuery<"/chronicler/aggregate/day-close", "GET"> = { date: params.date, tz: params.tz };
  return apiFetch("/chronicler/aggregate/day-close", { query: sp });
}

/**
 * Re-invoke the existing day-close schedule for one exact settled local-day tuple.
 * This is the only dashboard LLM-bearing action on the Chronicles surface.
 */
export function postChroniclerDayCloseRefresh(
  body: ChroniclerDayCloseRefreshRequest,
) {
  return apiFetch("/chronicler/aggregate/day-close/refresh", { method: "POST",
    body: body,
    // The endpoint awaits the existing reasoning-tier day-close session. Keep
    // the browser aligned with the dashboard's 120s butler-trigger budget
    // instead of aborting at the generic 15s read-request timeout.
    timeoutMs: 120_000 });
}

/** Fetch a single Chronicler episode by ID (corrected view). 404 if not found. */
export function getChroniclerEpisode(episodeId: string) {
  return apiFetch("/chronicler/episodes/{episode_id}", { path: { episode_id: episodeId } });
}

/**
 * Fetch point events linked to an episode.
 * Returns an empty list if the episode has no linked events.
 * 404 if the episode does not exist.
 */
export function getChroniclerEpisodeEvents(episodeId: string) {
  return apiFetch("/chronicler/episodes/{episode_id}/events", { path: { episode_id: episodeId } });
}

/**
 * Fetch the correction history for an episode, sorted by created_at DESC.
 * Returns an empty list if no corrections exist.
 * 404 if the episode does not exist.
 */
export function getChroniclerEpisodeCorrections(
  episodeId: string,
) {
  return apiFetch("/chronicler/episodes/{episode_id}/corrections", { path: { episode_id: episodeId } });
}

/**
 * Submit an episode correction (JARVIS audit move 6, bu-86c4c.15 —
 * "episode corrections on chronicles, a manifesto-binding promise").
 *
 * Real backend: `POST /api/chronicler/episodes/{id}/corrections`, which
 * inserts a row into the existing `overrides` table honored by
 * `v_episodes_corrected` — the same read path `getChroniclerEpisode` and
 * `getChroniclerEpisodeCorrections` already consume. At least one correction
 * field or a `note` is required (422 otherwise).
 */
export function submitChroniclerEpisodeCorrection(
  episodeId: string,
  body: SubmitCorrectionRequest,
) {
  return apiFetch("/chronicler/episodes/{episode_id}/corrections", { method: "POST",
    body: body, path: { episode_id: episodeId } });
}

/**
 * Trigger a per-episode Tier-2 LLM drilldown (rate-limited: once per 24 h per episode).
 *
 * Returns 403 when the episode is sensitive/restricted (excluded from LLM paths).
 * Returns 429 with code "episode_explain_rate_limited" when called too soon after
 * the last explain. The caller should check `error.status === 429` and disable
 * the Explain button accordingly.
 *
 * Returns 503 when the in-process spawner is not wired (standalone/test mode).
 */
export function postChroniclerEpisodeExplain(
  episodeId: string,
) {
  return apiFetch("/chronicler/episodes/{episode_id}/explain", { method: "POST", path: { episode_id: episodeId } });
}

/** Fetch paginated Chronicler point events. Defaults: include_tombstoned=false. */
export function getChroniclerEvents(
  params?: ChroniclerEventsParams,
) {
  const sp: ApiQuery<"/chronicler/events", "GET"> = {};
  if (params?.source_name) sp["source_name"] = params.source_name;
  if (params?.event_type) sp["event_type"] = params.event_type;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.include_tombstoned != null)
    sp["include_tombstoned"] = params.include_tombstoned;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/chronicler/events", { query: sp });
}

// ── Chronicler routines (bu-whhll.9 / bu-whhll.11) ─────────────────────────

/** List owner-reviewable weekly routines (mined + declared). */
export function getChroniclerRoutines(
  params?: { enabled_only?: boolean },
) {
  const sp: ApiQuery<"/chronicler/routines", "GET"> = {};
  if (params?.enabled_only) sp["enabled_only"] = true;
  return apiFetch("/chronicler/routines", { query: sp });
}

/** Declare an owner work schedule (origin='declared'). Returns the created row. */
export function createChroniclerRoutine(
  body: ChroniclerCreateRoutineRequest,
) {
  return apiFetch("/chronicler/routines", { method: "POST",
    body: body });
}

/**
 * Enable/disable, rename, or (declared only) re-schedule a routine.
 * Rejected with 400 when schedule fields are sent for a mined routine.
 */
export function updateChroniclerRoutine(
  routineId: string,
  body: ChroniclerUpdateRoutineRequest,
) {
  return apiFetch("/chronicler/routines/{routine_id}", { method: "PATCH",
    body: body, path: { routine_id: routineId } });
}

/** Delete a declared routine. Returns undefined on success (HTTP 204). */
export function deleteChroniclerRoutine(routineId: string) {
  return apiFetch("/chronicler/routines/{routine_id}", { method: "DELETE", path: { routine_id: routineId } });
}

// ---------------------------------------------------------------------------
// System endpoints — GET /api/system/*
// ---------------------------------------------------------------------------

/** Fetch software version, process uptime, and start timestamp. */
export function getInstanceFacts() {
  return apiFetch("/system/instance");
}

/** Fetch PostgreSQL catalog size facts: total size, per-schema breakdown, largest tables. */
export function getDatabaseFacts() {
  return apiFetch("/system/database");
}

/** Fetch backup recency and source reachability. Degrades gracefully (never 503). */
export function getBackupFacts() {
  return apiFetch("/system/backups");
}

/**
 * Fetch data-egress catalog (owner-only).
 *
 * Returns HTTP 403 for non-owner callers. Callers should handle `ApiError`
 * with `status === 403` gracefully rather than treating it as an unexpected error.
 */
export function getEgressCatalog() {
  return apiFetch("/system/egress");
}

/** Fetch per-butler liveness registry snapshots and session facts. */
export function getButlerHeartbeats() {
  return apiFetch("/system/butlers/heartbeat");
}

/**
 * Fetch the current state of the proactive insight delivery pipeline.
 *
 * Returns queued / delivered / failed counts and the last-delivery timestamp.
 * All counts reflect the last ~30 days (older non-pending rows are purged by
 * the delivery cycle).  An all-zero response with null last_delivery_at means
 * no delivery activity has occurred yet — that is an honest empty state, not
 * an error.
 */
export function getInsightDeliveryState() {
  return apiFetch("/system/insights/delivery-state");
}

/**
 * Fetch the migration-drift sentinel's current comparison (bu-9r3hd.1).
 *
 * Always returns HTTP 200. `drift_check_available: false` means the
 * comparison itself failed -- treat that as "unknown", not "clean".
 */
export function getDriftFacts() {
  return apiFetch("/system/drift");
}

/**
 * Fetch the stored-function drift comparison (bu-bi5an).
 *
 * Always returns HTTP 200. `stored_function_check_available: false` means the
 * comparison itself failed -- treat that as "unknown", not "clean".
 */
export function getStoredFunctionFacts() {
  return apiFetch("/system/stored-functions");
}

/** Params for getSystemConditions(). */
export interface SystemConditionsParams {
  /** "infra" (default) | "owner" -- bu-ep4ks.6 */
  ledger?: string;
  source?: string;
  /** "open" | "aging" | "resolved" */
  state?: string;
  offset?: number;
  limit?: number;
}

/**
 * Fetch standing conditions from GET /api/system/conditions (bu-27dxl.6.2 /
 * bu-ep4ks.3 / bu-ep4ks.6). Named distinctly from getConditions() (health
 * conditions, unrelated) to avoid a same-module symbol collision.
 *
 * `params.ledger` selects "infra" (default, infrastructure reliability) or
 * "owner" (owner-facing standing concerns) -- same envelope shape either way.
 *
 * Always returns HTTP 200 -- `data.conditions_available === false` means the
 * ledger query itself failed server-side; render "unknown", never "no active
 * conditions".
 */
export function getSystemConditions(
  params: SystemConditionsParams = {},
) {
  const query: ApiQuery<"/system/conditions", "GET"> = {};
  if (params.ledger) query["ledger"] = params.ledger;
  if (params.source) query["source"] = params.source;
  if (params.state) query["state"] = params.state;
  if (params.offset !== undefined) query["offset"] = params.offset;
  if (params.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/system/conditions", { query: query });
}

/** Params for listDelegationLedger(). */
export interface DelegationLedgerParams {
  /** "pending" | "routed" | "unroutable" | "failed" | "answered" */
  status?: string;
  asking_butler?: string;
  target_butler?: string;
  /** Only return rows stuck in callback_failed or task_conflict (bu-ep4ks.3). */
  wake_stuck?: boolean;
  offset?: number;
  limit?: number;
}

/**
 * List cross-butler delegated questions from GET /api/delegation/ledger
 * (bu-gxmfx), most-recent first.
 */
export function listDelegationLedger(
  params: DelegationLedgerParams = {},
) {
  const query: ApiQuery<"/delegation/ledger", "GET"> = {};
  if (params.status) query["status"] = params.status;
  if (params.asking_butler) query["asking_butler"] = params.asking_butler;
  if (params.target_butler) query["target_butler"] = params.target_butler;
  if (params.wake_stuck) query["wake_stuck"] = true;
  if (params.offset !== undefined) query["offset"] = params.offset;
  if (params.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/delegation/ledger", { query: query });
}

/** Params for listDomainEventSubscriptions(). */
export interface DomainEventSubscriptionsParams {
  subscriber_butler?: string;
  event_type?: string;
  active_only?: boolean;
}

/**
 * List standing (subscriber_butler, event_type) domain-event-bus
 * subscriptions from GET /api/domain-events/subscriptions (bu-317s5).
 */
export function listDomainEventSubscriptions(
  params: DomainEventSubscriptionsParams = {},
) {
  const query: ApiQuery<"/domain-events/subscriptions", "GET"> = {};
  if (params.subscriber_butler) query["subscriber_butler"] = params.subscriber_butler;
  if (params.event_type) query["event_type"] = params.event_type;
  if (params.active_only) query["active_only"] = true;
  return apiFetch("/domain-events/subscriptions", { query: query });
}

/** Params for listDomainEventDeliveries(). */
export interface DomainEventDeliveriesParams {
  subscriber_butler?: string;
  source_butler?: string;
  /** "pending" | "delivered" | "conflict" | "failed" | "failed_permanent" */
  status?: string;
  offset?: number;
  limit?: number;
}

/**
 * List domain-event-bus fan-out deliveries from GET /api/domain-events/deliveries
 * (bu-317s5), most-recent first.
 */
export function listDomainEventDeliveries(
  params: DomainEventDeliveriesParams = {},
) {
  const query: ApiQuery<"/domain-events/deliveries", "GET"> = {};
  if (params.subscriber_butler) query["subscriber_butler"] = params.subscriber_butler;
  if (params.source_butler) query["source_butler"] = params.source_butler;
  if (params.status) query["status"] = params.status;
  if (params.offset !== undefined) query["offset"] = params.offset;
  if (params.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/domain-events/deliveries", { query: query });
}

/** Atomically requeue one failed_permanent domain-event delivery. */
export function replayDomainEventDelivery(
  deliveryId: string,
) {
  return apiFetch("/domain-events/deliveries/{delivery_id}/replay", { method: "POST", path: { delivery_id: deliveryId } });
}

/**
 * Fetch the full reaction trace for one domain event from
 * GET /api/domain-events/events/{event_id}/reactions (bu-6jv4m.8), oldest
 * step first -- every step every subscriber recorded, not just the outcome.
 */
export function listDomainEventReactions(
  eventId: string,
) {
  return apiFetch("/domain-events/events/{event_id}/reactions", { path: { event_id: eventId } });
}

/** Params for listDomainEventContracts(). */
export interface DomainEventContractsParams {
  publisher?: string;
}

/**
 * List materialized publisher-owned event contracts from
 * GET /api/domain-events/contracts (bu-6jv4m.8).
 */
export function listDomainEventContracts(
  params: DomainEventContractsParams = {},
) {
  const query: ApiQuery<"/domain-events/contracts", "GET"> = {};
  if (params.publisher) query["publisher"] = params.publisher;
  return apiFetch("/domain-events/contracts", { query: query });
}

/** Params for getHealingDispatchEvents(). */
export interface HealingDispatchEventsParams {
  decision?: string;
  /** Same identity public.infra_conditions uses for source=infra_state (bu-ep4ks.3). */
  fingerprint?: string;
  offset?: number;
  limit?: number;
}

/**
 * List healing/QA-dispatch gate-decision events from
 * GET /api/healing/dispatch-events -- e.g. `decision=infra_condition_open`
 * rows record a QA finding suppressed by an active standing condition with
 * the same `fingerprint` (Gate 5.5, bu-27dxl.6.4).
 */
export function getHealingDispatchEvents(
  params: HealingDispatchEventsParams = {},
) {
  const query: ApiQuery<"/healing/dispatch-events", "GET"> = {};
  if (params.decision) query["decision"] = params.decision;
  if (params.fingerprint) query["fingerprint"] = params.fingerprint;
  if (params.offset !== undefined) query["offset"] = params.offset;
  if (params.limit !== undefined) query["limit"] = params.limit;
  return apiFetch("/healing/dispatch-events", { query: query });
}

/**
 * Fetch the current (most recent) deployment plus recent deployment history
 * (bu-9r3hd.3/bu-hmdqz.1).
 *
 * Always returns HTTP 200 for a legitimately empty ledger; HTTP 503 is
 * reserved for an actual query failure. `commits_behind_available: false`
 * means the GitHub compare comparison failed -- treat as "unknown", not
 * "up to date".
 */
export function getDeploymentFacts() {
  return apiFetch("/system/deployments");
}

// ---------------------------------------------------------------------------
// Dashboard briefing — GET /api/dashboard/briefing
//
// Server-composed briefing (greeting + classified headline + LLM elaboration).
// See: openspec/changes/dashboard-overview-briefing/specs/dashboard-briefing/spec.md
// ---------------------------------------------------------------------------

/**
 * Fetch the dashboard briefing for the editorial Overview surface.
 *
 * The endpoint never raises to the caller: LLM failures fall through to a
 * templated paragraph and `source` reflects which path produced the
 * elaboration. The response is per-owner cached for 5 minutes server-side.
 */
export function getDashboardBriefing() {
  return apiFetch("/dashboard/briefing").then((response) => response.data);
}

// ---------------------------------------------------------------------------
// Chronicles editorial briefing (bu-i29ix)
// GET /api/chronicler/briefing | /attention | /kpi
// ---------------------------------------------------------------------------

interface ChroniclesEditorialParams {
  date?: string;
  tz?: string;
}

function _chroniclesQs(params: ChroniclesEditorialParams | undefined): ApiQuery<"/chronicler/kpi", "GET"> {
  const sp: ApiQuery<"/chronicler/kpi", "GET"> = {};
  if (params?.date) sp["date"] = params.date;
  if (params?.tz) sp["tz"] = params.tz;
  return sp;
}

export function getChroniclesBriefing(
  params?: ChroniclesEditorialParams,
) {
  return apiFetch("/chronicler/briefing", { query: _chroniclesQs(params) });
}

export function getChroniclesKpi(
  params?: ChroniclesEditorialParams,
) {
  return apiFetch("/chronicler/kpi", { query: _chroniclesQs(params) });
}

// ---------------------------------------------------------------------------
// Finance butler (GET /api/finance/*)
// ---------------------------------------------------------------------------

/** List transactions with optional filters. */
export function getFinanceTransactions(
  params?: FinanceTransactionListParams,
) {
  const sp: ApiQuery<"/finance/transactions", "GET"> = {};
  if (params?.category) sp["category"] = params.category;
  if (params?.merchant) sp["merchant"] = params.merchant;
  if (params?.since) sp["since"] = params.since;
  if (params?.until) sp["until"] = params.until;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/finance/transactions", { query: sp });
}

/** List subscriptions with optional status filter. */
export function getFinanceSubscriptions(
  params?: FinanceSubscriptionListParams,
) {
  const sp: ApiQuery<"/finance/subscriptions", "GET"> = {};
  if (params?.status) sp["status"] = params.status;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/finance/subscriptions", { query: sp });
}

/** Read state-only Finance recurrence measurability. */
export function getFinanceExpectedSignals() {
  return apiFetch("/finance/expected-signals");
}

/** List the forward obligation ledger (bu-8cdl1.10): warn-by dates,
 * cancellation-door status, and pre-charge price-change flags per
 * subscription. Explicitly degraded (never a fabricated all-clear) when the
 * ledger read fails. */
export function getFinanceObligations() {
  return apiFetch("/finance/obligations");
}

/** List upcoming bills with urgency classification. */
export function getFinanceUpcomingBills(
  params?: FinanceUpcomingBillsParams,
) {
  const sp: ApiQuery<"/finance/upcoming-bills", "GET"> = {};
  if (params?.days_ahead != null) sp["days_ahead"] = params.days_ahead;
  if (params?.include_overdue != null) sp["include_overdue"] = params.include_overdue;
  return apiFetch("/finance/upcoming-bills", { query: sp });
}

/** Aggregate spending summary over a date range. */
export function getFinanceSpendingSummary(
  params?: FinanceSpendingSummaryParams,
) {
  const sp: ApiQuery<"/finance/spending-summary", "GET"> = {};
  if (params?.start_date) sp["start_date"] = params.start_date;
  if (params?.end_date) sp["end_date"] = params.end_date;
  if (params?.group_by) sp["group_by"] = params.group_by;
  return apiFetch("/finance/spending-summary", { query: sp });
}

/** List financial accounts with an optional type filter. */
export function getFinanceAccounts(
  params?: FinanceAccountListParams,
) {
  const sp: ApiQuery<"/finance/accounts", "GET"> = {};
  if (params?.type) sp["type"] = params.type;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/finance/accounts", { query: sp });
}

/**
 * Apply bulk metadata overlay (normalized_merchant / inferred_category) to
 * transaction facts matching each op's ILIKE merchant pattern.
 * PATCH /api/finance/transactions/bulk-metadata.
 */
export function patchFinanceBulkMetadata(
  request: FinanceBulkUpdateRequest,
) {
  return apiFetch("/finance/transactions/bulk-metadata", { method: "PATCH",
    body: request });
}

// ---------------------------------------------------------------------------
// Travel butler endpoints (bu-0eac9)
// GET /api/travel/trips | /trips/{id} | /upcoming
// ---------------------------------------------------------------------------

/** List trips with optional status and date range filters, paginated. */
export function getTravelTrips(
  params?: TravelTripsParams,
) {
  const sp: ApiQuery<"/travel/trips", "GET"> = {};
  if (params?.status) sp["status"] = params.status;
  if (params?.from_date) sp["from_date"] = params.from_date;
  if (params?.to_date) sp["to_date"] = params.to_date;
  if (params?.offset != null) sp["offset"] = params.offset;
  if (params?.limit != null) sp["limit"] = params.limit;
  return apiFetch("/travel/trips", { query: sp });
}

/** Fetch full trip summary (legs, accommodations, reservations, docs, timeline, alerts). */
export function getTravelTripSummary(tripId: string) {
  return apiFetch("/travel/trips/{trip_id}", { path: { trip_id: tripId } });
}

/** Fetch upcoming travel with urgency-ranked pre-trip action items. */
export function getTravelUpcoming(withinDays?: number) {
  return apiFetch("/travel/upcoming", { query: { within_days: withinDays ?? undefined } });
}

/** Fetch documents expiring within the given look-ahead window (default: 180 days). */
export function getTravelExpiringDocuments(
  days?: number,
) {
  return apiFetch("/travel/documents/expiring", { query: { days: days ?? undefined } });
}

// ---------------------------------------------------------------------------
// Home butler endpoints
// ---------------------------------------------------------------------------

export function getHomeSnapshotStatus() {
  return apiFetch("/home/snapshot-status");
}

/** Fetch the saved Home atmosphere location and the latest feed health. */
export function getHomeAtmosphereCurrent() {
  return apiFetch("/home/atmosphere/current");
}

/** Save Home atmosphere coordinates for the next scheduled feed refresh. */
export function updateHomeAtmosphereLocation(
  coordinates: HomeAtmosphereLocationUpdate,
) {
  return apiFetch("/home/atmosphere/location", { method: "PATCH",
    body: coordinates });
}

export function getHomeDevices(params?: {
  domain?: string;
  area?: string;
  health?: "healthy" | "offline";
  page?: number;
  page_size?: number;
}) {
  const sp: ApiQuery<"/home/devices", "GET"> = {};
  if (params?.domain) sp["domain"] = params.domain;
  if (params?.area) sp["area"] = params.area;
  if (params?.health) sp["health"] = params.health;
  if (params?.page != null) sp["page"] = params.page;
  if (params?.page_size != null) sp["page_size"] = params.page_size;
  return apiFetch("/home/devices", { query: sp });
}

export function getHomeMaintenance(params?: {
  category?: string;
  status?: "overdue" | "due" | "upcoming" | "ok";
}) {
  const sp: ApiQuery<"/home/maintenance", "GET"> = {};
  if (params?.category) sp["category"] = params.category;
  if (params?.status) sp["status"] = params.status;
  return apiFetch("/home/maintenance", { query: sp });
}

export function getHomeEnergy(params?: {
  period?: "day" | "hour";
  start?: string;
  end?: string;
}) {
  const sp: ApiQuery<"/home/energy", "GET"> = {};
  if (params?.period) sp["period"] = params.period;
  if (params?.start) sp["start"] = params.start;
  if (params?.end) sp["end"] = params.end;
  return apiFetch("/home/energy", { query: sp });
}

export function getHomeEnergyTopConsumers(params?: {
  start?: string;
  end?: string;
}) {
  const sp: ApiQuery<"/home/energy/top-consumers", "GET"> = {};
  if (params?.start) sp["start"] = params.start;
  if (params?.end) sp["end"] = params.end;
  return apiFetch("/home/energy/top-consumers", { query: sp });
}

export function getHomeCommandLog(params?: {
  limit?: number;
  domain?: string;
}) {
  const sp: ApiQuery<"/home/command-log", "GET"> = {};
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.domain) sp["domain"] = params.domain;
  return apiFetch("/home/command-log", { query: sp });
}

// ---------------------------------------------------------------------------
// Phase 7 — butler management (§9.2)
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/prompt — current versioned system prompt. */
export function getButlerPrompt(name: string) {
  return apiFetch("/butlers/{name}/prompt", { path: { name: name } });
}

/** GET /api/butlers/{name}/prompt/effective — protected composed prompt + drift truth. */
export function getButlerEffectivePrompt(
  name: string,
) {
  return apiFetch("/butlers/{name}/prompt/effective", { path: { name: name } });
}

/** PUT /api/butlers/{name}/prompt — update prompt, snapshots prior version. */
export function updateButlerPrompt(
  name: string,
  body: PromptUpdateRequest,
) {
  return apiFetch("/butlers/{name}/prompt", { method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: body, path: { name: name } });
}

/** GET /api/butlers/{name}/prompt/history — version history newest-first. */
export function getButlerPromptHistory(
  name: string,
  params?: { limit?: number; offset?: number },
) {
  const sp: ApiQuery<"/butlers/{name}/prompt/history", "GET"> = {};
  if (params?.limit != null) sp["limit"] = params.limit;
  if (params?.offset != null) sp["offset"] = params.offset;
  return apiFetch("/butlers/{name}/prompt/history", { path: { name: name }, query: sp });
}

/** GET /api/butlers/{name}/memory-access — memory tier access metadata. */
export function getButlerMemoryAccess(name: string) {
  return apiFetch("/butlers/{name}/memory-access", { path: { name: name } });
}

/** POST /api/butlers/{name}/kill — initiate graceful shutdown. */
export function killButler(name: string, body: KillRequest) {
  return apiFetch("/butlers/{name}/kill", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body, path: { name: name } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — breaks catalogue (bu-qo3sf)
// ---------------------------------------------------------------------------

import type { BreaksCatalogueParams } from "./types.ts";

/**
 * GET /api/secrets/breaks-catalogue
 *
 * Returns the list of butler features that depend on a given provider's
 * credential. When `?provider=` is omitted the full catalogue is returned.
 *
 * Response shape: BreaksCatalogueResponse (data: BreakEntry[], meta carries
 * the degraded-envelope `catalogue_available` flag).
 * When provider is omitted, meta.by_provider contains entries keyed by provider.
 */
export function getBreaksCatalogue(
  params?: BreaksCatalogueParams,
) {

  return apiFetch("/secrets/breaks-catalogue", { query: { provider: params?.provider || undefined } });
}

/**
 * POST /api/secrets/user/<provider>/reauthorize?identity=<uuid>
 *
 * Initiates an OAuth reauthorization dance for a user-scoped credential.
 * Returns a redirect_url that the caller should navigate to; the OAuth
 * callback will redirect back to /secrets?focus=u:<provider>&toast=connected
 * on success.
 *
 * Spec: redesign-secrets-passport §User credential mutations
 */
export function reauthorizeUserCredential(
  provider: string,
  identity: string,
) {
  return apiFetch("/secrets/user/{provider}/reauthorize", { method: "POST", path: { provider: provider }, query: { identity } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — inventory (bu-nrgk9)
// ---------------------------------------------------------------------------

import type {
  SecretsInventoryParams,
} from "./types.ts";

/**
 * GET /api/secrets/inventory?identity=<uuid>
 *
 * Returns the aggregated credential inventory for the /secrets passport page:
 * CLI runtime tokens, system secrets, and user (OAuth/token/key) credentials.
 *
 * When `identity` is provided, the `user` array is filtered to that entity.
 * When omitted, the owner entity is used (projection-lens semantics).
 *
 * Response shape: ApiResponse<InventoryData>
 */
export function getSecretsInventory(
  params?: SecretsInventoryParams,
) {

  return apiFetch("/secrets/inventory", { query: { identity: params?.identity || undefined } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — per-credential reads (bu-ayp6v.1)
// ---------------------------------------------------------------------------

import type { SecretsAuditParams } from "./types.ts";

/**
 * GET /api/secrets/user/<provider>?identity=<uuid>
 *
 * Returns the full evidence payload for a single user-scoped credential.
 * Raw values are NEVER returned — fingerprint + evidence only.
 *
 * Returns 404 when no matching credential exists.
 */
export function getUserCredential(
  provider: string,
  identity?: string,
) {
  return apiFetch("/secrets/user/{provider}", { path: { provider: provider }, query: { identity: identity || undefined } });
}

/**
 * GET /api/secrets/system/<key>
 *
 * Returns the content-blind evidence payload for a single system-scoped
 * credential. Raw values are NEVER returned — fingerprint + evidence only,
 * with probe and audit free text dropped server-side.
 *
 * Returns 404 when no matching credential exists.
 */
export function getSystemCredential(
  key: string,
) {
  return apiFetch("/secrets/system/{key}", { path: { key: key } });
}

/**
 * GET /api/secrets/cli/<id>
 *
 * Returns the content-blind evidence payload for a single CLI runtime token.
 * Raw values are NEVER returned — fingerprint + evidence only, with capability
 * categories in place of raw scopes.
 *
 * Returns 404 when no matching token exists.
 */
export function getCliCredential(id: string) {
  return apiFetch("/secrets/cli/{credential_id}", { path: { credential_id: id } });
}

/**
 * GET /api/secrets/audit/<scope>/<key>?limit=<n>
 *
 * Returns recent audit events for a single credential.
 * `scope` must be one of "user", "system", or "cli".
 * `key` is the provider/secret-key/cli-id for the credential.
 *
 * Timestamps in `ts` are pre-formatted server-side
 * (e.g. "14:21 today", "yesterday 09:08").
 *
 * meta.deep_link points to the full audit log page for this credential.
 */
export function getCredentialAudit(
  scope: "user" | "system" | "cli",
  key: string,
  params?: SecretsAuditParams,
) {
  return apiFetch("/secrets/audit/{scope}/{key}", { path: { scope: scope, key: key }, query: { limit: params?.limit ?? undefined } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — user credential mutations (bu-ayp6v.1)
// ---------------------------------------------------------------------------

import type { SecretsRotateUserRequest } from "./types.ts";

/**
 * POST /api/secrets/user/<provider>/rotate?identity=<uuid>
 *
 * Rotates (replaces) the stored value for a user-scoped credential.
 * Attempts to revoke the old OAuth token at the provider after the local
 * DB update (fire-and-forget; rotation still succeeds on revoke failure).
 * Writes a "rotated" audit row.
 *
 * Returns ApiResponse<SecretsUserDetail> (updated credential).
 * Returns 404 when no matching credential exists.
 */
export function rotateUserCredential(
  provider: string,
  body: SecretsRotateUserRequest,
  identity?: string,
) {
  return apiFetch("/secrets/user/{provider}/rotate", { method: "POST",
      body: body, path: { provider: provider }, query: { identity: identity || undefined } });
}

/**
 * POST /api/secrets/user/<provider>/disconnect?identity=<uuid>
 *
 * Disconnects (removes) a user-scoped credential.
 * Hard-deletes the matching entity_info row.
 * Writes a "disconnected" audit row.
 *
 * Returns ApiResponse<SecretsDisconnectStatus>.
 * Returns 404 when no matching credential exists.
 */
export function disconnectUserCredential(
  provider: string,
  identity?: string,
) {
  return apiFetch("/secrets/user/{provider}/disconnect", { method: "POST", path: { provider: provider }, query: { identity: identity || undefined } });
}

/**
 * POST /api/secrets/user/<provider>/probe?identity=<uuid>
 *
 * Probes a user-scoped credential and records the test result.
 * For supported providers (Google OAuth, GitHub PAT) makes a live verify call;
 * falls back to local-state check for others.
 * Writes to secret_probe_log + updates entity_info test-state columns
 * in one transaction.
 *
 * Returns ApiResponse<SecretsProbeResult> with the probe outcome.
 * Returns 404 when no matching credential exists.
 */
export function probeUserCredential(
  provider: string,
  identity?: string,
) {
  return apiFetch("/secrets/user/{provider}/probe", { method: "POST", path: { provider: provider }, query: { identity: identity || undefined } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — system credential mutations (bu-ayp6v.1)
// ---------------------------------------------------------------------------

// SecretsSystemCredentialDetail is imported by the read block above; the
// write route publishes the same payload.
import type { SecretsSystemSetRequest } from "./types.ts";

/**
 * POST /api/secrets/system/<key>
 *
 * Sets (first-time create), rotates (updates existing), or overrides
 * (per-butler) a system credential.
 *
 * body.target = "shared" → writes to the switchboard butler schema.
 * body.target = "<butler>" → creates a per-butler override row.
 *
 * Audit actions: "set" (first-time), "rotated" (existing), "overrode" (override).
 *
 * Returns ApiResponse<SecretsSystemCredentialDetail> (updated) — the same
 * content-blind payload the GET route publishes for the row, so no probe
 * message, audit note, or breaks entry rides back on the write.
 * Returns 404 when target is a butler name that is not registered.
 */
export function setSystemCredential(
  key: string,
  body: SecretsSystemSetRequest,
) {
  return apiFetch("/secrets/system/{key}", { method: "POST",
      body: body, path: { key: key } });
}

/**
 * POST /api/secrets/system/<key>/probe
 *
 * Probes a system credential and records the test result.
 * Derives the probe outcome from local state (no external provider calls).
 * Rate-limited to 1 call per 5 s per key (in-process guard).
 *
 * Returns ApiResponse<SecretsProbeResult>.
 * Returns 404 when no credential exists for the given key.
 * Returns 429 when the rate limit is exceeded.
 */
export function probeSystemCredential(key: string) {
  return apiFetch("/secrets/system/{key}/probe", { method: "POST", path: { key: key } });
}

/**
 * POST /api/secrets/probe-all
 *
 * Sweeps every probeable credential (system, user, cli-auth) and returns a
 * per-row outcome. Dispatches through the exact same probe_* functions as a
 * manual per-row click, serially with a per-provider circuit breaker — see
 * butlers.jobs.secrets_staleness for the engine.
 *
 * Returns ApiResponse<SecretsProbeAllResponse>.
 * Returns 429 when a sweep is already in progress.
 */
export function probeAllCredentials() {
  return apiFetch("/secrets/probe-all", { method: "POST" });
}

/**
 * DELETE /api/secrets/system/<key>?target=<butler|shared>
 *
 * Removes a system credential row.
 * target="shared" → deletes the shared (switchboard) row; audit "disconnected".
 * target="<butler>" → deletes the per-butler override row; audit "revoked".
 *
 * Returns ApiResponse<SecretsSystemDeleteStatus>.
 * Returns 404 when the key does not exist or the target butler is not registered.
 */
export function deleteSystemCredential(
  key: string,
  target: "shared" | string = "shared",
) {
  return apiFetch("/secrets/system/{key}", { method: "DELETE", path: { key: key }, query: { target } });
}

// ---------------------------------------------------------------------------
// Secrets v2 — CLI runtime mutations (bu-ayp6v.1)
// ---------------------------------------------------------------------------



/**
 * POST /api/secrets/cli/<id>/rotate
 *
 * Persists or rotates the secret value for a CLI runtime token.
 *
 * When `value` is supplied (non-empty), that exact owner-pasted value is
 * persisted verbatim — it is NOT replaced by a server-generated random one,
 * and it works even for a never_set provider (first-time save). When `value`
 * is omitted, the server generates a fresh random value (true rotate).
 *
 * The raw value is returned EXACTLY ONCE in this response.
 * No GET endpoint exposes raw values — this is the sole opportunity to copy
 * the value into local config.
 *
 * Returns ApiResponse<SecretsCliRotateResult> with {fingerprint, value}.
 */
export function rotateCliCredential(
  id: string,
  value?: string,
) {
  return apiFetch("/secrets/cli/{credential_id}/rotate", { method: "POST",
      ...(value !== undefined ? { body: { value } } : {}), path: { credential_id: id } });
}

/**
 * POST /api/secrets/cli/<id>/revoke
 *
 * Revokes (deletes) a CLI runtime token.
 * Hard-deletes the butler_secrets row (category='cli').
 * Writes a "disconnected" audit row.
 *
 * Returns ApiResponse<SecretsCliRevokeResult>.
 * Returns 404 when no matching CLI token exists.
 */
export function revokeCliCredential(id: string) {
  return apiFetch("/secrets/cli/{credential_id}/revoke", { method: "POST", path: { credential_id: id } });
}

/**
 * POST /api/secrets/cli/<id>/reauthorize
 *
 * Initiates (or resumes) re-authentication for a device-code or api-key CLI
 * runtime credential.  Writes an 'attempted' audit row.
 *
 * device_code response: { auth_mode: "device_code", session_id, auth_url,
 *   device_code, message } — poll GET /api/cli-auth/sessions/{session_id}.
 * api_key response: { auth_mode: "api_key", env_var, prompt } — render
 *   the key-entry form and submit via PUT /api/cli-auth/{provider}/api-key.
 *
 * Returns 404 when <id> is not a known CLI auth provider.
 *
 * Spec: bu-ayp6v.10 reauthorize bridge
 */
export function reauthorizeCliCredential(
  id: string,
) {
  return apiFetch("/secrets/cli/{credential_id}/reauthorize", { method: "POST", path: { credential_id: id } });
}

// ---------------------------------------------------------------------------
// Timeline saved views (bu-vgj88)
// ---------------------------------------------------------------------------

/**
 * GET /api/timeline/saved-views
 *
 * Returns all persisted custom saved views, newest first.
 * Returns an empty list when none exist.
 * Returns 503 when the shared database is unavailable.
 */
export function listTimelineSavedViews() {
  return apiFetch("/timeline/saved-views");
}

/**
 * POST /api/timeline/saved-views
 *
 * Creates a new saved view. Returns the created entry (HTTP 201).
 */
export function createTimelineSavedView(
  body: TimelineSavedViewCreateRequest,
) {
  return apiFetch("/timeline/saved-views", { method: "POST",
    body: body });
}

/**
 * PATCH /api/timeline/saved-views/{id}
 *
 * Updates name and/or filter_spec of an existing saved view.
 * Returns 404 when the view does not exist.
 */
export function updateTimelineSavedView(
  id: string,
  body: TimelineSavedViewUpdateRequest,
) {
  return apiFetch("/timeline/saved-views/{view_id}", { method: "PATCH",
    body: body, path: { view_id: id } });
}

/**
 * DELETE /api/timeline/saved-views/{id}
 *
 * Deletes a saved view. Returns undefined on success (HTTP 204).
 * Returns 404 when the view does not exist.
 */
export function deleteTimelineSavedView(id: string) {
  return apiFetch("/timeline/saved-views/{view_id}", { method: "DELETE", path: { view_id: id } });
}


export function getIdentityCandidates(entityId: string) {
  return apiFetch("/relationship/entities/{entity_id}/identity-candidates", { path: { entity_id: entityId } });
}

export function decideIdentityCandidate(entityId: string, factId: string, decision: "adopt" | "reject") {
  return apiFetch("/relationship/entities/{entity_id}/identity-facts/{fact_id}/{decision}", { method: "POST", path: { entity_id: entityId, fact_id: factId, decision: decision } });
}

export function getIdentityDecision(entityId: string, factId: string) {
  return apiFetch("/relationship/entities/{entity_id}/identity-facts/{fact_id}/decision", { path: { entity_id: entityId, fact_id: factId } });
}
