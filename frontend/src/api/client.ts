/**
 * Typed fetch wrapper for the Butlers dashboard API.
 *
 * Uses native `fetch` — no external HTTP libraries required.
 */

import type {
  ApprovalAction,
  ApprovalActionApproveRequest,
  ApprovalActionParams,
  ApprovalActionRejectRequest,
  ApprovalMetrics,
  ApprovalRule,
  ApprovalRuleCreateRequest,
  ApprovalRuleFromActionRequest,
  ApprovalRuleParams,
  AutonomySuggestion,
  AutonomySuggestionDismissRequest,
  AutonomySuggestionParams,
  ExpireStaleActionsResponse,
  RuleConstraintSuggestion,
  ApiResponse,
  AuditEntry,
  AuditLogParams,
  ButlerConfigResponse,
  ButlerDetail,
  ButlerSkill,
  ButlerSummary,
  CalendarWorkspaceMetaResponse,
  CalendarWorkspaceMutationResponse,
  CalendarWorkspaceParams,
  CalendarWorkspaceReadResponse,
  CalendarWorkspaceButlerMutationRequest,
  CalendarWorkspaceSyncRequest,
  CalendarWorkspaceSyncResponse,
  CalendarWorkspaceUserMutationRequest,
  SetPrimaryCalendarRequest,
  SetPrimaryCalendarResponse,
  ContactDetail,
  ContactListResponse,
  ContactParams,
  ContactsSyncTriggerResponse,
  CostSummary,
  DailyCost,
  ErrorResponse,
  Group,
  GroupListResponse,
  GroupParams,
  HealthResponse,
  Issue,
  Label,
  NotificationParams,
  NotificationStats,
  NotificationSummary,
  PaginatedResponse,
  Schedule,
  ScheduleCreate,
  ScheduleUpdate,
  SearchResults,
  SessionDetail,
  SessionParams,
  SessionSummary,
  StateEntry,
  StateSetRequest,
  TimelineParams,
  TimelineResponse,
  TopSession,
  TriggerResponse,
  ButlerMcpTool,
  ButlerMcpToolCallRequest,
  ButlerMcpToolCallResponse,
  Dose,
  HealthCondition,
  HealthResearch,
  Meal,
  MealParams,
  Measurement,
  MeasurementParams,
  Medication,
  MedicationParams,
  ResearchParams,
  Symptom,
  SymptomParams,
  RegistryEntry,
  RoutingEntry,
  SetEligibilityResponse,
  EligibilityHistoryResponse,
  RoutingLogParams,
  UpcomingDate,
  Episode,
  CreateEntityInfoRequest,
  CreateEntityInfoResponse,
  EntityDetail,
  EntityDetailParams,
  EntityInfoEntry,
  EntityParams,
  EntitySummary,
  UpdateEntityInfoRequest,
  UpdateEntityRequest,
  EpisodeParams,
  Fact,
  FactParams,
  MemoryActivity,
  MemoryRule,
  MemoryStats,
  RuleParams,
  ThreadAffinitySettings,
  ThreadAffinitySettingsUpdate,
  ThreadOverrideEntry,
  ThreadOverrideUpsert,
  ContactInfoEntry,
  ContactMergeRequest,
  ContactMergeResponse,
  ContactPatchRequest,
  CreateContactInfoRequest,
  CreateContactInfoResponse,
  PatchContactInfoRequest,
  OwnerSetupStatus,
  OwnerEntityInfoResponse,
  UnlinkedContactsResponse,
  EntityLinkSuggestion,
  LinkEntityRequest,
  LinkEntityResponse,
  CreateAndLinkEntityRequest,
  CreateAndLinkEntityResponse,
  MergeEntityResponse,
  IngestionEventSummary,
  IngestionEventSession,
  IngestionEventRollup,
  IngestionEventReplayResponse,
  IngestionEventsParams,
  IngestionRule,
  IngestionRuleCreate,
  IngestionRuleUpdate,
  IngestionRuleListParams,
  IngestionRuleTestRequest,
  IngestionRuleTestResponse,
  ModelCatalogEntry,
  PricingMap,
  ModelCatalogCreate,
  ModelCatalogUpdate,
  ModelTestResult,
  ButlerModelOverride,
  ButlerModelOverrideUpsert,
  ResolveModelResponse,
  TokenLimitsRequest,
  TokenLimitsResponse,
  ResetUsageRequest,
  TokenUsageDetail,
  ProviderConfig,
  ProviderConfigCreate,
  ProviderConfigUpdate,
  ProviderConnectivityResult,
  WhatsAppDisconnectResponse,
  WhatsAppHealthResponse,
  WhatsAppPairPollResponse,
  WhatsAppPairStartResponse,
  WhatsAppStatusResponse,
  SpotifyConfigRequest,
  SpotifyConfigResponse,
  SpotifyDisconnectResponse,
  SpotifyOAuthStartResponse,
  SpotifyStatusResponse,
  OwnTracksConfigResponse,
  OwnTracksStatusResponse,
  OwnTracksTokenResponse,
  HomeAssistantConfigRequest,
  HomeAssistantConfigResponse,
  HomeAssistantDeleteResponse,
  HomeAssistantStatusResponse,
  DunbarRankingResponse,
  ConversationSummary,
  ConversationListParams,
  Message,
  CreateConversationRequest,
  SendMessageRequest,
  TelegramSendCodeRequest,
  TelegramSendCodeResponse,
  TelegramVerifyCodeRequest,
  TelegramVerifyCodeResponse,
  TelegramSessionStatusResponse,
  GeneralSettings,
  GeneralSettingsUpdate,
  BlobStorageStatus,
  BlobStorageTestResult,
  SteamAccountListResponse,
  SteamConnectRequest,
  SteamConnectResponse,
  SteamDisconnectResponse,
  SteamPlaytimeAnalytics,
  QaPatrolSummary,
  QaPatrolDetail,
  QaFindingRecord,
  QaKnownIssue,
  QaSummary,
  QaDismissal,
  QaDismissRequest,
  QaPatrolsParams,
  QaKnownIssuesParams,
  QaInvestigation,
  QaInvestigationsParams,
  QaTrends,
  ForcePatrolResponse,
  CircuitBreakerStatus,
  CircuitBreakerResetResponse,
  QaRepoConfig,
  QaRepoConfigUpdate,
  QaRepoSyncResponse,
  QaAllowedRepo,
  QaAllowedRepoCreate,
  QaAllowedRepoPatch,
  RuntimeConfigResponse,
  RuntimeConfigPatch,
  RuntimeConfigPatchResponse,
  HealingAttempt,
  HealingAttemptsParams,
  ChroniclerAggregateByCategoryParams,
  ChroniclerAggregateByDayParams,
  ChroniclerAggregateByDayRow,
  ChroniclerCategoryBuckets,
  ChroniclerDayCloseParams,
  ChroniclerDayCloseRefreshRequest,
  ChroniclerDayCloseRefreshResponse,
  ChroniclerDayCloseResponse,
  ChroniclerEpisode,
  ChroniclerEpisodeExplainResponse,
  ChroniclerEpisodesParams,
  ChroniclerEventsParams,
  ChroniclerOverride,
  ChroniclerPointEvent,
  ChroniclerSourceStateRow,
  EntityNote,
  EntityInteraction,
  EntityGift,
  EntityLoan,
  EntityImportantDate,
  EntityTimelineItem,
  DunbarTierOverrideResponse,
  LinkedContactSummary,
  MessageThreadSummary,
  RelationshipEntityDetail,
  InstanceFacts,
  DatabaseFacts,
  BackupFacts,
  EgressCatalog,
  HeartbeatFacts,
  ModuleStatus,
  Briefing,
  ChroniclesBriefing,
  ChroniclesAttentionItem,
  ChroniclesKpi,
  FinanceTransaction,
  FinanceSubscription,
  FinanceBill,
  FinanceSpendingSummary,
  FinanceUpcomingBillsResponse,
  FinanceBillListParams,
  FinanceTransactionListParams,
  FinanceSubscriptionListParams,
  FinanceSpendingSummaryParams,
  FinanceUpcomingBillsParams,
  TravelTrip,
  TravelTripSummary,
  TravelUpcomingModel,
  TravelTripsParams,
  TravelExpiringDocumentsResponse,
  HomeSnapshotStatus,
  HomeDeviceInventoryResponse,
  HomeMaintenanceItem,
  HomeEnergyDataPoint,
  HomeTopConsumer,
  HomeCommandLogEntry,
  ContactInteractionsResponse,
  OverdueContactsResponse,
  ButlerLogsParams,
  ButlerLogsResponse,
} from "./types.ts";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API_BASE_URL: string =
  import.meta.env.VITE_API_URL ?? "/api";

// ---------------------------------------------------------------------------
// Error class
// ---------------------------------------------------------------------------

/** Error thrown when an API request fails. */
export class ApiError extends Error {
  /** Machine-readable error code from the backend (or a fallback). */
  readonly code: string;
  /** HTTP status code of the response. */
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

// ---------------------------------------------------------------------------
// Base fetch wrapper
// ---------------------------------------------------------------------------

/**
 * Typed fetch wrapper that prepends `API_BASE_URL`, sets JSON headers,
 * and throws {@link ApiError} on non-ok responses.
 */
export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${API_BASE_URL}${path}`;

  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    let code = "UNKNOWN_ERROR";
    let message = response.statusText || "Request failed";

    try {
      const body = await response.json();
      if (body.error) {
        code = (body as ErrorResponse).error.code;
        message = (body as ErrorResponse).error.message;
      } else if (typeof body.detail === "string") {
        // FastAPI HTTPException format: { "detail": "..." }
        message = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        // Pydantic ValidationError format: { "detail": [{ "msg": "..." }, ...] }
        message = body.detail
          .map((d: Record<string, unknown>) => String(d.msg ?? d.message ?? JSON.stringify(d)))
          .join("; ");
      }
    } catch {
      // Response body is not valid JSON — fall through to defaults.
    }

    throw new ApiError(code, message, response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Endpoint functions
// ---------------------------------------------------------------------------

/** Fetch the health-check endpoint. */
export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

/** Fetch all butlers. */
export function getButlers(): Promise<ApiResponse<ButlerSummary[]>> {
  return apiFetch<ApiResponse<ButlerSummary[]>>("/butlers");
}

/** Fetch a single butler by name. */
export function getButler(name: string): Promise<ApiResponse<ButlerDetail>> {
  return apiFetch<ApiResponse<ButlerDetail>>(`/butlers/${encodeURIComponent(name)}`);
}

/** Fetch configuration files for a specific butler. */
export function getButlerConfig(name: string): Promise<ApiResponse<ButlerConfigResponse>> {
  return apiFetch<ApiResponse<ButlerConfigResponse>>(
    `/butlers/${encodeURIComponent(name)}/config`,
  );
}

/** Fetch per-module health status for a specific butler. */
export function getButlerModules(name: string): Promise<ApiResponse<ModuleStatus[]>> {
  return apiFetch<ApiResponse<ModuleStatus[]>>(`/butlers/${encodeURIComponent(name)}/modules`);
}

/** Build a URLSearchParams from session query parameters. */
function sessionSearchParams(params?: SessionParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.butler != null && params.butler !== "") sp.set("butler", params.butler);
  if (params?.trigger_source != null && params.trigger_source !== "")
    sp.set("trigger_source", params.trigger_source);
  if (params?.request_id != null && params.request_id !== "")
    sp.set("request_id", params.request_id);
  if (params?.status != null && params.status !== "all") sp.set("status", params.status);
  // Backend uses from_date/to_date; SessionParams uses since/until as field names.
  if (params?.since != null && params.since !== "") sp.set("from_date", params.since);
  if (params?.until != null && params.until !== "") sp.set("to_date", params.until);
  return sp;
}

/** Fetch a paginated list of sessions across all butlers. */
export function getSessions(
  params?: SessionParams,
): Promise<PaginatedResponse<SessionSummary>> {
  const qs = sessionSearchParams(params).toString();
  const path = qs ? `/sessions?${qs}` : "/sessions";
  return apiFetch<PaginatedResponse<SessionSummary>>(path);
}

/** Fetch a single session by ID (cross-butler). */
export function getSession(id: string): Promise<ApiResponse<SessionDetail>> {
  return apiFetch<ApiResponse<SessionDetail>>(`/sessions/${encodeURIComponent(id)}`);
}

/** Fetch sessions for a specific butler. */
export function getButlerSessions(
  name: string,
  params?: SessionParams,
): Promise<PaginatedResponse<SessionSummary>> {
  const qs = sessionSearchParams(params).toString();
  const base = `/butlers/${encodeURIComponent(name)}/sessions`;
  const path = qs ? `${base}?${qs}` : base;
  return apiFetch<PaginatedResponse<SessionSummary>>(path);
}

/** Fetch a single session by ID for a specific butler. */
export function getButlerSession(
  name: string,
  id: string,
): Promise<ApiResponse<SessionDetail>> {
  return apiFetch<ApiResponse<SessionDetail>>(
    `/butlers/${encodeURIComponent(name)}/sessions/${encodeURIComponent(id)}`,
  );
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
function notificationSearchParams(params?: NotificationParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.butler != null && params.butler !== "") sp.set("butler", params.butler);
  if (params?.channel != null && params.channel !== "" && params.channel !== "all")
    sp.set("channel", params.channel);
  if (params?.status != null && params.status !== "" && params.status !== "all")
    sp.set("status", params.status);
  if (params?.since != null && params.since !== "") sp.set("since", params.since);
  if (params?.until != null && params.until !== "") sp.set("until", params.until);
  return sp;
}

/** Fetch a paginated list of notifications across all butlers. */
export function getNotifications(
  params?: NotificationParams,
): Promise<PaginatedResponse<NotificationSummary>> {
  const qs = notificationSearchParams(params).toString();
  const path = qs ? `/notifications?${qs}` : "/notifications";
  return apiFetch<PaginatedResponse<NotificationSummary>>(path);
}

/** Fetch aggregate notification statistics. */
export function getNotificationStats(): Promise<ApiResponse<NotificationStats>> {
  return apiFetch<ApiResponse<NotificationStats>>("/notifications/stats");
}

/** Fetch notifications for a specific butler. */
export function getButlerNotifications(
  name: string,
  params?: NotificationParams,
): Promise<PaginatedResponse<NotificationSummary>> {
  const qs = notificationSearchParams(params).toString();
  const base = `/butlers/${encodeURIComponent(name)}/notifications`;
  const path = qs ? `${base}?${qs}` : base;
  return apiFetch<PaginatedResponse<NotificationSummary>>(path);
}

// ---------------------------------------------------------------------------
// Issues
// ---------------------------------------------------------------------------

/** Fetch grouped issues across all butlers. */
export function getIssues(): Promise<ApiResponse<Issue[]>> {
  return apiFetch<ApiResponse<Issue[]>>("/issues");
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
 */
export function getCostSummary(
  period?: string,
  from?: string,
  to?: string,
): Promise<ApiResponse<CostSummary>> {
  const sp = new URLSearchParams();
  if (from && to) {
    sp.set("from", from);
    sp.set("to", to);
  } else if (period) {
    sp.set("period", period);
  }
  const qs = sp.toString() ? `?${sp.toString()}` : "";
  return apiFetch<ApiResponse<CostSummary>>(`/costs/summary${qs}`);
}

/** Fetch daily cost breakdown, optionally scoped to a date range (YYYY-MM-DD). */
export function getDailyCosts(from?: string, to?: string): Promise<ApiResponse<DailyCost[]>> {
  const params = new URLSearchParams();
  if (from) params.set("from", from);
  if (to) params.set("to", to);
  const query = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<ApiResponse<DailyCost[]>>(`/costs/daily${query}`);
}

/** Fetch most expensive sessions. */
export function getTopSessions(limit?: number): Promise<ApiResponse<TopSession[]>> {
  const params = limit ? `?limit=${limit}` : "";
  return apiFetch<ApiResponse<TopSession[]>>(`/costs/top-sessions${params}`);
}

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

/** Fetch all schedules for a specific butler. */
export function getButlerSchedules(name: string): Promise<ApiResponse<Schedule[]>> {
  return apiFetch<ApiResponse<Schedule[]>>(
    `/butlers/${encodeURIComponent(name)}/schedules`,
  );
}

/** Create a new schedule for a specific butler. */
export function createButlerSchedule(
  name: string,
  body: ScheduleCreate,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/butlers/${encodeURIComponent(name)}/schedules`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/** Update an existing schedule for a specific butler. */
export function updateButlerSchedule(
  name: string,
  scheduleId: string,
  body: ScheduleUpdate,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/butlers/${encodeURIComponent(name)}/schedules/${encodeURIComponent(scheduleId)}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** Delete a schedule for a specific butler. */
export function deleteButlerSchedule(
  name: string,
  scheduleId: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/butlers/${encodeURIComponent(name)}/schedules/${encodeURIComponent(scheduleId)}`,
    {
      method: "DELETE",
    },
  );
}

/** Trigger a schedule immediately (one-off dispatch). */
export function triggerButlerSchedule(
  name: string,
  scheduleId: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/butlers/${encodeURIComponent(name)}/schedules/${encodeURIComponent(scheduleId)}/trigger`,
    {
      method: "POST",
    },
  );
}

/** Toggle the enabled/disabled state of a schedule. */
export function toggleButlerSchedule(
  name: string,
  scheduleId: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/butlers/${encodeURIComponent(name)}/schedules/${encodeURIComponent(scheduleId)}/toggle`,
    {
      method: "PATCH",
    },
  );
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** Fetch skills available to a specific butler. */
export function getButlerSkills(name: string): Promise<ApiResponse<ButlerSkill[]>> {
  return apiFetch<ApiResponse<ButlerSkill[]>>(
    `/butlers/${encodeURIComponent(name)}/skills`,
  );
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
): Promise<ButlerLogsResponse> {
  const sp = new URLSearchParams();
  if (params?.level) sp.set("level", params.level);
  if (params?.since) sp.set("since", params.since);
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const path = `/butlers/${encodeURIComponent(name)}/logs${qs ? `?${qs}` : ""}`;
  return apiFetch<ButlerLogsResponse>(path);
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** Fetch all state entries for a butler. */
export function getButlerState(name: string): Promise<ApiResponse<StateEntry[]>> {
  return apiFetch<ApiResponse<StateEntry[]>>(
    `/butlers/${encodeURIComponent(name)}/state`,
  );
}

/** Set a state value for a butler (creates or updates). */
export function setButlerState(
  name: string,
  key: string,
  value: StateSetRequest["value"],
): Promise<ApiResponse<Record<string, string>>> {
  return apiFetch<ApiResponse<Record<string, string>>>(
    `/butlers/${encodeURIComponent(name)}/state/${encodeURIComponent(key)}`,
    {
      method: "PUT",
      body: JSON.stringify({ value }),
    },
  );
}

/** Delete a state entry for a butler. */
export function deleteButlerState(
  name: string,
  key: string,
): Promise<ApiResponse<Record<string, string>>> {
  return apiFetch<ApiResponse<Record<string, string>>>(
    `/butlers/${encodeURIComponent(name)}/state/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Trigger
// ---------------------------------------------------------------------------

/** Trigger a CC session for a specific butler. */
export function triggerButler(
  name: string,
  prompt: string,
  complexity?: string,
): Promise<TriggerResponse> {
  return apiFetch<TriggerResponse>(
    `/butlers/${encodeURIComponent(name)}/trigger`,
    {
      method: "POST",
      body: JSON.stringify({ prompt, complexity: complexity ?? "medium" }),
    },
  );
}

/** Fetch MCP tools exposed by a specific butler. */
export function getButlerMcpTools(name: string): Promise<ApiResponse<ButlerMcpTool[]>> {
  return apiFetch<ApiResponse<ButlerMcpTool[]>>(
    `/butlers/${encodeURIComponent(name)}/mcp/tools`,
  );
}

/** Call an MCP tool on a specific butler with optional arguments. */
export function callButlerMcpTool(
  name: string,
  request: ButlerMcpToolCallRequest,
): Promise<ApiResponse<ButlerMcpToolCallResponse>> {
  return apiFetch<ApiResponse<ButlerMcpToolCallResponse>>(
    `/butlers/${encodeURIComponent(name)}/mcp/call`,
    {
      method: "POST",
      body: JSON.stringify({
        tool_name: request.tool_name,
        arguments: request.arguments ?? {},
      }),
    },
  );
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

/** Fetch a paginated list of audit log entries. */
export function getAuditLog(
  params?: AuditLogParams,
): Promise<PaginatedResponse<AuditEntry>> {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.butler) sp.set("butler", params.butler);
  if (params?.operation) sp.set("operation", params.operation);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<AuditEntry>>(qs ? `/audit-log?${qs}` : "/audit-log");
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/** Search across all butlers for sessions, state, and other entities. */
export function searchAll(query: string, limit?: number): Promise<ApiResponse<SearchResults>> {
  const sp = new URLSearchParams({ q: query });
  if (limit) sp.set("limit", String(limit));
  return apiFetch<ApiResponse<SearchResults>>(`/search?${sp.toString()}`);
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

/** Fetch the unified timeline with cursor-based pagination. */
export function getTimeline(params?: TimelineParams): Promise<TimelineResponse> {
  const sp = new URLSearchParams();
  if (params?.limit) sp.set("limit", String(params.limit));
  if (params?.before) sp.set("before", params.before);
  params?.butler?.forEach((b) => sp.append("butler", b));
  params?.event_type?.forEach((t) => sp.append("event_type", t));
  const qs = sp.toString();
  return apiFetch<TimelineResponse>(qs ? `/timeline?${qs}` : "/timeline");
}

// ---------------------------------------------------------------------------
// Calendar workspace
// ---------------------------------------------------------------------------

/** Build URLSearchParams from calendar workspace read query parameters. */
function calendarWorkspaceSearchParams(params: CalendarWorkspaceParams): URLSearchParams {
  const sp = new URLSearchParams();
  sp.set("view", params.view);
  sp.set("start", params.start);
  sp.set("end", params.end);
  if (params.timezone != null && params.timezone !== "") sp.set("timezone", params.timezone);
  params.butlers?.forEach((butler) => {
    if (butler) sp.append("butlers", butler);
  });
  params.sources?.forEach((source) => {
    if (source) sp.append("sources", source);
  });
  return sp;
}

/** Fetch normalized calendar workspace entries for a given range and view. */
export function getCalendarWorkspace(
  params: CalendarWorkspaceParams,
): Promise<ApiResponse<CalendarWorkspaceReadResponse>> {
  return apiFetch<ApiResponse<CalendarWorkspaceReadResponse>>(
    `/calendar/workspace?${calendarWorkspaceSearchParams(params).toString()}`,
  );
}

/** Fetch calendar workspace metadata: capabilities, sources, and lanes. */
export function getCalendarWorkspaceMeta(): Promise<ApiResponse<CalendarWorkspaceMetaResponse>> {
  return apiFetch<ApiResponse<CalendarWorkspaceMetaResponse>>("/calendar/workspace/meta");
}

/** Trigger calendar workspace sync globally or for a selected source. */
export function syncCalendarWorkspace(
  body: CalendarWorkspaceSyncRequest,
): Promise<ApiResponse<CalendarWorkspaceSyncResponse>> {
  return apiFetch<ApiResponse<CalendarWorkspaceSyncResponse>>("/calendar/workspace/sync", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Set the primary calendar for a butler. */
export function setPrimaryCalendar(
  body: SetPrimaryCalendarRequest,
): Promise<ApiResponse<SetPrimaryCalendarResponse>> {
  return apiFetch<ApiResponse<SetPrimaryCalendarResponse>>(
    "/calendar/workspace/primary",
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** Create, update, or delete a user-view provider event through workspace APIs. */
export function mutateCalendarWorkspaceUserEvent(
  body: CalendarWorkspaceUserMutationRequest,
): Promise<ApiResponse<CalendarWorkspaceMutationResponse>> {
  return apiFetch<ApiResponse<CalendarWorkspaceMutationResponse>>(
    "/calendar/workspace/user-events",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/** Create/update/delete/toggle butler-lane schedule/reminder events. */
export function mutateCalendarWorkspaceButlerEvent(
  body: CalendarWorkspaceButlerMutationRequest,
): Promise<ApiResponse<CalendarWorkspaceMutationResponse>> {
  return apiFetch<ApiResponse<CalendarWorkspaceMutationResponse>>(
    "/calendar/workspace/butler-events",
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}
// ---------------------------------------------------------------------------
// Relationship / CRM
// ---------------------------------------------------------------------------

/** Build URLSearchParams from contact query parameters. */
function contactSearchParams(params?: ContactParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.q != null && params.q !== "") sp.set("q", params.q);
  if (params?.label != null && params.label !== "") sp.set("label", params.label);
  if (params?.archived) sp.set("archived", "true");
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Fetch a paginated list of contacts. */
export function getContacts(params?: ContactParams): Promise<ContactListResponse> {
  const qs = contactSearchParams(params).toString();
  const path = qs ? `/relationship/contacts?${qs}` : "/relationship/contacts";
  return apiFetch<ContactListResponse>(path);
}

/** Trigger a manual contacts sync for a specific provider. */
export function triggerContactsSync(
  mode: "incremental" | "full" = "incremental",
  provider: "google" | "telegram" = "google",
): Promise<ContactsSyncTriggerResponse> {
  const sp = new URLSearchParams({ mode, provider });
  return apiFetch<ContactsSyncTriggerResponse>(
    `/relationship/contacts/sync?${sp.toString()}`,
    { method: "POST" },
  );
}

/** Fetch a single contact by ID. */
export function getContact(contactId: string): Promise<ContactDetail> {
  return apiFetch<ContactDetail>(
    `/relationship/contacts/${encodeURIComponent(contactId)}`,
  );
}

/** Fetch pending (temp) contacts awaiting identity resolution. */
export function getPendingContacts(): Promise<ContactDetail[]> {
  return apiFetch<ContactDetail[]>("/relationship/contacts/pending");
}

/** Reveal a secured contact_info entry value. */
export function revealContactSecret(
  contactId: string,
  infoId: string,
): Promise<ContactInfoEntry> {
  return apiFetch<ContactInfoEntry>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/secrets/${encodeURIComponent(infoId)}`,
  );
}

/** Update a contact's fields (full_name, nickname, company, job_title, roles). */
export function patchContact(
  contactId: string,
  request: ContactPatchRequest,
): Promise<ContactDetail> {
  return apiFetch<ContactDetail>(
    `/relationship/contacts/${encodeURIComponent(contactId)}`,
    { method: "PATCH", body: JSON.stringify(request) },
  );
}

/** Merge a temp/pending contact into an existing contact. */
export function mergeContact(
  contactId: string,
  request: ContactMergeRequest,
): Promise<ContactMergeResponse> {
  return apiFetch<ContactMergeResponse>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/merge`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

/** Confirm a pending disambiguation contact as a new known contact. */
export function confirmContact(contactId: string): Promise<ContactDetail> {
  return apiFetch<ContactDetail>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/confirm`,
    { method: "POST" },
  );
}

/** Get owner identity setup status. */
export function getOwnerSetupStatus(): Promise<OwnerSetupStatus> {
  return apiFetch<OwnerSetupStatus>("/relationship/owner/setup-status");
}

/** Fetch paginated unlinked contacts with entity suggestions. */
export function getUnlinkedContacts(params?: {
  offset?: number;
  limit?: number;
  q?: string;
}): Promise<UnlinkedContactsResponse> {
  const qs = new URLSearchParams();
  if (params?.offset != null) qs.set("offset", String(params.offset));
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.q) qs.set("q", params.q);
  const suffix = qs.toString() ? `?${qs}` : "";
  return apiFetch<UnlinkedContactsResponse>(
    `/relationship/contacts/unlinked${suffix}`,
  );
}

/** Fetch on-demand entity suggestions for a specific contact. */
export function getEntitySuggestions(
  contactId: string,
  q?: string,
): Promise<EntityLinkSuggestion[]> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return apiFetch<EntityLinkSuggestion[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/entity-suggestions${qs}`,
  );
}

/** Link an existing memory entity to a contact. */
export function linkEntity(
  contactId: string,
  request: LinkEntityRequest,
): Promise<LinkEntityResponse> {
  return apiFetch<LinkEntityResponse>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/link-entity`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

/** Create a new memory entity from contact data and link it. */
export function createAndLinkEntity(
  contactId: string,
  request: CreateAndLinkEntityRequest,
): Promise<CreateAndLinkEntityResponse> {
  return apiFetch<CreateAndLinkEntityResponse>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/create-entity`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

/** Add a contact_info entry (email, telegram, etc.) to a contact. */
export function createContactInfo(
  contactId: string,
  request: CreateContactInfoRequest,
): Promise<CreateContactInfoResponse> {
  return apiFetch<CreateContactInfoResponse>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/contact-info`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

/** Delete a contact (hard-delete). */
export function deleteContact(contactId: string): Promise<void> {
  return apiFetch<void>(
    `/relationship/contacts/${encodeURIComponent(contactId)}`,
    { method: "DELETE" },
  );
}

/** Archive a contact (soft-delete, preserves source links so sync won't re-create). */
export function archiveContact(contactId: string): Promise<void> {
  return apiFetch<void>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/archive`,
    { method: "POST" },
  );
}

/** Unarchive a previously archived contact. */
export function unarchiveContact(contactId: string): Promise<void> {
  return apiFetch<void>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/unarchive`,
    { method: "POST" },
  );
}

/** Delete a contact_info entry. */
export function deleteContactInfo(
  contactId: string,
  infoId: string,
): Promise<void> {
  return apiFetch<void>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/contact-info/${encodeURIComponent(infoId)}`,
    { method: "DELETE" },
  );
}

/** Update a contact_info entry (type, value, is_primary). */
export function patchContactInfo(
  contactId: string,
  infoId: string,
  request: PatchContactInfoRequest,
): Promise<ContactInfoEntry> {
  return apiFetch<ContactInfoEntry>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/contact-info/${encodeURIComponent(infoId)}`,
    { method: "PATCH", body: JSON.stringify(request) },
  );
}

/** Fetch chronological interaction thread for a contact (bu-iuol4.22). */
export function getContactInteractions(
  contactId: string,
  limit?: number,
): Promise<ContactInteractionsResponse> {
  const sp = new URLSearchParams();
  if (limit != null) sp.set("limit", String(limit));
  const qs = sp.toString();
  return apiFetch<ContactInteractionsResponse>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/interactions${qs ? `?${qs}` : ""}`,
  );
}

/** Fetch contacts that are overdue on their Dunbar tier cadence (bu-iuol4.22). */
export function getOverdueContacts(days?: number): Promise<OverdueContactsResponse> {
  const sp = new URLSearchParams();
  if (days != null) sp.set("days", String(days));
  const qs = sp.toString();
  return apiFetch<OverdueContactsResponse>(
    `/relationship/contacts/overdue${qs ? `?${qs}` : ""}`,
  );
}

/** Build URLSearchParams from group query parameters. */
function groupSearchParams(params?: GroupParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Fetch a paginated list of groups. */
export function getGroups(params?: GroupParams): Promise<GroupListResponse> {
  const qs = groupSearchParams(params).toString();
  const path = qs ? `/relationship/groups?${qs}` : "/relationship/groups";
  return apiFetch<GroupListResponse>(path);
}

/** Fetch a single group by ID. */
export function getGroup(groupId: string): Promise<Group> {
  return apiFetch<Group>(
    `/relationship/groups/${encodeURIComponent(groupId)}`,
  );
}

/** Fetch all labels. */
export function getLabels(): Promise<Label[]> {
  return apiFetch<Label[]>("/relationship/labels");
}

/** Fetch upcoming dates within a given number of days. */
export function getUpcomingDates(days?: number): Promise<UpcomingDate[]> {
  const params = days != null ? `?days=${days}` : "";
  return apiFetch<UpcomingDate[]>(`/relationship/upcoming-dates${params}`);
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** Fetch a paginated list of health measurements. */
export function getMeasurements(params?: MeasurementParams): Promise<PaginatedResponse<Measurement>> {
  const sp = new URLSearchParams();
  if (params?.type) sp.set("type", params.type);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<Measurement>>(qs ? `/health/measurements?${qs}` : "/health/measurements");
}

/** Fetch a paginated list of medications. */
export function getMedications(params?: MedicationParams): Promise<PaginatedResponse<Medication>> {
  const sp = new URLSearchParams();
  if (params?.active != null) sp.set("active", String(params.active));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<Medication>>(qs ? `/health/medications?${qs}` : "/health/medications");
}

/** Fetch dose log entries for a specific medication. */
export function getMedicationDoses(medicationId: string, params?: { since?: string; until?: string }): Promise<Dose[]> {
  const sp = new URLSearchParams();
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  const qs = sp.toString();
  const base = `/health/medications/${encodeURIComponent(medicationId)}/doses`;
  return apiFetch<Dose[]>(qs ? `${base}?${qs}` : base);
}

/** Fetch a paginated list of health conditions. */
export function getConditions(params?: { offset?: number; limit?: number }): Promise<PaginatedResponse<HealthCondition>> {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<HealthCondition>>(qs ? `/health/conditions?${qs}` : "/health/conditions");
}

/** Fetch a paginated list of symptoms. */
export function getSymptoms(params?: SymptomParams): Promise<PaginatedResponse<Symptom>> {
  const sp = new URLSearchParams();
  if (params?.name) sp.set("name", params.name);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<Symptom>>(qs ? `/health/symptoms?${qs}` : "/health/symptoms");
}

/** Fetch a paginated list of meals. */
export function getMeals(params?: MealParams): Promise<PaginatedResponse<Meal>> {
  const sp = new URLSearchParams();
  if (params?.type) sp.set("type", params.type);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<Meal>>(qs ? `/health/meals?${qs}` : "/health/meals");
}

/** Fetch the latest measurement value for each requested type.
 *
 * GET /api/health/measurements/latest?types=glucose,hrv,steps
 * Returns { measurements: { "<type>": { measured_at, value, unit, metadata } | null } }
 */
export function getMeasurementsLatest(
  types: string[],
): Promise<import("./types").MeasurementsLatestResponse> {
  const sp = new URLSearchParams();
  if (types.length > 0) sp.set("types", types.join(","));
  const qs = sp.toString();
  return apiFetch<import("./types").MeasurementsLatestResponse>(
    qs ? `/health/measurements/latest?${qs}` : "/health/measurements/latest",
  );
}

/** Fetch the latest sleep session with stage breakdown.
 *
 * GET /api/health/measurements/sleep/latest
 */
export function getSleepLatest(): Promise<import("./types").SleepLatestResponse> {
  return apiFetch<import("./types").SleepLatestResponse>("/health/measurements/sleep/latest");
}

/** Fetch all active measurement sources with their last-sample timestamps.
 *
 * GET /api/health/measurements/sources
 */
export function getMeasurementSources(): Promise<import("./types").MeasurementSourcesResponse> {
  return apiFetch<import("./types").MeasurementSourcesResponse>("/health/measurements/sources");
}

/** Fetch a paginated list of health research notes. */
export function getResearch(params?: ResearchParams): Promise<PaginatedResponse<HealthResearch>> {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.tag) sp.set("tag", params.tag);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<HealthResearch>>(qs ? `/health/research?${qs}` : "/health/research");
}

// ---------------------------------------------------------------------------
// General / Switchboard
// ---------------------------------------------------------------------------

/** Fetch the switchboard routing log. */
export function getRoutingLog(
  params?: RoutingLogParams,
): Promise<PaginatedResponse<RoutingEntry>> {
  const sp = new URLSearchParams();
  if (params?.source_butler) sp.set("source_butler", params.source_butler);
  if (params?.target_butler) sp.set("target_butler", params.target_butler);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<RoutingEntry>>(
    qs ? `/switchboard/routing-log?${qs}` : "/switchboard/routing-log",
  );
}

/** Fetch the switchboard butler registry. */
export function getRegistry(): Promise<ApiResponse<RegistryEntry[]>> {
  return apiFetch<ApiResponse<RegistryEntry[]>>("/switchboard/registry");
}

/** Set a butler's eligibility state in the switchboard registry. */
export function setButlerEligibility(
  name: string,
  eligibilityState: string,
): Promise<ApiResponse<SetEligibilityResponse>> {
  return apiFetch<ApiResponse<SetEligibilityResponse>>(
    `/switchboard/registry/${encodeURIComponent(name)}/eligibility`,
    {
      method: "POST",
      body: JSON.stringify({ eligibility_state: eligibilityState }),
    },
  );
}


/** Fetch eligibility history for a butler over a given window. */
export function getEligibilityHistory(
  name: string,
  hours = 24,
): Promise<ApiResponse<EligibilityHistoryResponse>> {
  return apiFetch<ApiResponse<EligibilityHistoryResponse>>(
    `/switchboard/registry/${encodeURIComponent(name)}/eligibility-history?hours=${hours}`,
  );
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

/** Build URLSearchParams from episode query parameters. */
function episodeSearchParams(params?: EpisodeParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.butler) sp.set("butler", params.butler);
  if (params?.consolidated != null) sp.set("consolidated", String(params.consolidated));
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Build URLSearchParams from fact query parameters. */
function factSearchParams(params?: FactParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.scope) sp.set("scope", params.scope);
  if (params?.validity) sp.set("validity", params.validity);
  if (params?.permanence) sp.set("permanence", params.permanence);
  if (params?.subject) sp.set("subject", params.subject);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Build URLSearchParams from rule query parameters. */
function ruleSearchParams(params?: RuleParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.scope) sp.set("scope", params.scope);
  if (params?.maturity) sp.set("maturity", params.maturity);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Fetch aggregated memory statistics. */
export function getMemoryStats(): Promise<ApiResponse<MemoryStats>> {
  return apiFetch<ApiResponse<MemoryStats>>("/memory/stats");
}

/** Fetch a paginated list of episodes. */
export function getEpisodes(
  params?: EpisodeParams,
): Promise<PaginatedResponse<Episode>> {
  const qs = episodeSearchParams(params).toString();
  return apiFetch<PaginatedResponse<Episode>>(
    qs ? `/memory/episodes?${qs}` : "/memory/episodes",
  );
}

/** Fetch a single episode by ID. */
export function getEpisode(episodeId: string): Promise<ApiResponse<Episode>> {
  return apiFetch<ApiResponse<Episode>>(
    `/memory/episodes/${encodeURIComponent(episodeId)}`,
  );
}

/** Fetch a paginated list of facts. */
export function getFacts(
  params?: FactParams,
): Promise<PaginatedResponse<Fact>> {
  const qs = factSearchParams(params).toString();
  return apiFetch<PaginatedResponse<Fact>>(
    qs ? `/memory/facts?${qs}` : "/memory/facts",
  );
}

/** Fetch a single fact by ID. */
export function getFact(factId: string): Promise<ApiResponse<Fact>> {
  return apiFetch<ApiResponse<Fact>>(
    `/memory/facts/${encodeURIComponent(factId)}`,
  );
}

/** Fetch a paginated list of rules. */
export function getRules(
  params?: RuleParams,
): Promise<PaginatedResponse<MemoryRule>> {
  const qs = ruleSearchParams(params).toString();
  return apiFetch<PaginatedResponse<MemoryRule>>(
    qs ? `/memory/rules?${qs}` : "/memory/rules",
  );
}

/** Fetch a single rule by ID. */
export function getRule(ruleId: string): Promise<ApiResponse<MemoryRule>> {
  return apiFetch<ApiResponse<MemoryRule>>(
    `/memory/rules/${encodeURIComponent(ruleId)}`,
  );
}

/** Fetch recent memory activity. */
export function getMemoryActivity(
  limit?: number,
): Promise<ApiResponse<MemoryActivity[]>> {
  const params = limit != null ? `?limit=${limit}` : "";
  return apiFetch<ApiResponse<MemoryActivity[]>>(`/memory/activity${params}`);
}

// ---------------------------------------------------------------------------
// Entities (Knowledge Graph)
// ---------------------------------------------------------------------------

function entitySearchParams(params?: EntityParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.entity_type) sp.set("entity_type", params.entity_type);
  if (params?.unidentified != null) sp.set("unidentified", String(params.unidentified));
  if (params?.archived != null) sp.set("archived", String(params.archived));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  return sp;
}

/** Fetch a paginated list of entities. */
export function getEntities(
  params?: EntityParams,
): Promise<PaginatedResponse<EntitySummary>> {
  const qs = entitySearchParams(params).toString();
  return apiFetch<PaginatedResponse<EntitySummary>>(
    qs ? `/memory/entities?${qs}` : "/memory/entities",
  );
}

/** Fetch a single entity by ID. */
export function getEntity(
  entityId: string,
  params?: EntityDetailParams,
): Promise<ApiResponse<EntityDetail>> {
  const qs = new URLSearchParams();
  if (params?.facts_offset != null) qs.set("facts_offset", String(params.facts_offset));
  if (params?.facts_limit != null) qs.set("facts_limit", String(params.facts_limit));
  const path = qs.size
    ? `/memory/entities/${encodeURIComponent(entityId)}?${qs.toString()}`
    : `/memory/entities/${encodeURIComponent(entityId)}`;
  return apiFetch<ApiResponse<EntityDetail>>(
    path,
  );
}

/** Update entity core fields (name, aliases). */
export function updateEntity(
  entityId: string,
  request: UpdateEntityRequest,
): Promise<ApiResponse<EntitySummary>> {
  return apiFetch<ApiResponse<EntitySummary>>(
    `/memory/entities/${encodeURIComponent(entityId)}`,
    { method: "PATCH", body: JSON.stringify(request) },
  );
}

/** Merge source entity into target entity. */
export function mergeEntity(
  targetEntityId: string,
  sourceEntityId: string,
): Promise<MergeEntityResponse> {
  return apiFetch<MergeEntityResponse>(
    `/memory/entities/${encodeURIComponent(targetEntityId)}/merge`,
    { method: "POST", body: JSON.stringify({ source_entity_id: sourceEntityId }) },
  );
}

/** Delete (soft-delete) an entity. Pass retireFacts to auto-retire active facts. */
export function deleteEntity(
  entityId: string,
  opts?: { retireFacts?: boolean },
): Promise<void> {
  const qs = opts?.retireFacts ? "?retire_facts=true" : "";
  return apiFetch<void>(
    `/memory/entities/${encodeURIComponent(entityId)}${qs}`,
    { method: "DELETE" },
  );
}

/** Promote a transitory (unidentified) entity by clearing the unidentified flag. */
export function promoteEntity(
  entityId: string,
): Promise<ApiResponse<EntitySummary>> {
  return apiFetch<ApiResponse<EntitySummary>>(
    `/memory/entities/${encodeURIComponent(entityId)}/promote`,
    { method: "POST" },
  );
}

/** Archive an entity (hide from default views, preserves all data). */
export function archiveEntity(entityId: string): Promise<void> {
  return apiFetch<void>(
    `/memory/entities/${encodeURIComponent(entityId)}/archive`,
    { method: "POST" },
  );
}

/** Unarchive a previously archived entity. */
export function unarchiveEntity(entityId: string): Promise<void> {
  return apiFetch<void>(
    `/memory/entities/${encodeURIComponent(entityId)}/unarchive`,
    { method: "POST" },
  );
}

/** Get all entity_info entries for the owner entity. */
export function getOwnerEntityInfo(): Promise<OwnerEntityInfoResponse> {
  return apiFetch<OwnerEntityInfoResponse>("/relationship/owner/entity-info");
}

/** Create an entity_info entry for an entity. */
export function createEntityInfo(
  entityId: string,
  request: CreateEntityInfoRequest,
): Promise<CreateEntityInfoResponse> {
  return apiFetch<CreateEntityInfoResponse>(
    `/relationship/entities/${encodeURIComponent(entityId)}/info`,
    { method: "POST", body: JSON.stringify(request) },
  );
}

/** Update an entity_info entry. */
export function updateEntityInfo(
  entityId: string,
  infoId: string,
  request: UpdateEntityInfoRequest,
): Promise<EntityInfoEntry> {
  return apiFetch<EntityInfoEntry>(
    `/relationship/entities/${encodeURIComponent(entityId)}/info/${encodeURIComponent(infoId)}`,
    { method: "PATCH", body: JSON.stringify(request) },
  );
}

/** Delete an entity_info entry. */
export function deleteEntityInfo(
  entityId: string,
  infoId: string,
): Promise<void> {
  return apiFetch<void>(
    `/relationship/entities/${encodeURIComponent(entityId)}/info/${encodeURIComponent(infoId)}`,
    { method: "DELETE" },
  );
}

/** Reveal the actual value of a secured entity_info entry. */
export function revealEntitySecret(
  entityId: string,
  infoId: string,
): Promise<EntityInfoEntry> {
  return apiFetch<EntityInfoEntry>(
    `/relationship/entities/${encodeURIComponent(entityId)}/secrets/${encodeURIComponent(infoId)}`,
  );
}

// ---------------------------------------------------------------------------
// Relationship butler: entity-level fetch and tab endpoints
// ---------------------------------------------------------------------------

/** Fetch a relationship entity by ID (relationship-scoped; includes aliases, roles, metadata). */
export function getRelationshipEntity(entityId: string): Promise<RelationshipEntityDetail> {
  return apiFetch<RelationshipEntityDetail>(
    `/relationship/entities/${encodeURIComponent(entityId)}`,
  );
}

/** Fetch all contacts linked to a relationship entity. */
export function getEntityLinkedContacts(entityId: string): Promise<LinkedContactSummary[]> {
  return apiFetch<LinkedContactSummary[]>(
    `/relationship/entities/${encodeURIComponent(entityId)}/linked-contacts`,
  );
}

/** Fetch notes tab data for a relationship entity. */
export function getEntityNotes(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<EntityNote[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/notes?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/notes`;
  return apiFetch<EntityNote[]>(path);
}

/** Fetch interactions tab data for a relationship entity. */
export function getEntityInteractions(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<EntityInteraction[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/interactions?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/interactions`;
  return apiFetch<EntityInteraction[]>(path);
}

/** Fetch gifts tab data for a relationship entity. */
export function getEntityGifts(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<EntityGift[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/gifts?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/gifts`;
  return apiFetch<EntityGift[]>(path);
}

/** Fetch loans tab data for a relationship entity. */
export function getEntityLoans(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<EntityLoan[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/loans?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/loans`;
  return apiFetch<EntityLoan[]>(path);
}

/** Fetch unified timeline data for a relationship entity. */
export function getEntityTimeline(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<EntityTimelineItem[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  if (params?.offset != null) qs.set("offset", String(params.offset));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/timeline?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/timeline`;
  return apiFetch<EntityTimelineItem[]>(path);
}

/** Fetch message thread summaries for a relationship entity. */
export function getEntityMessageThreads(
  entityId: string,
  params?: { limit?: number },
): Promise<MessageThreadSummary[]> {
  const qs = new URLSearchParams();
  if (params?.limit != null) qs.set("limit", String(params.limit));
  const path = qs.size
    ? `/relationship/entities/${encodeURIComponent(entityId)}/message-threads?${qs}`
    : `/relationship/entities/${encodeURIComponent(entityId)}/message-threads`;
  return apiFetch<MessageThreadSummary[]>(path);
}

/** Fetch important dates (birthdays, anniversaries, etc.) for an entity. */
export function getEntityDates(entityId: string): Promise<EntityImportantDate[]> {
  return apiFetch<EntityImportantDate[]>(
    `/relationship/entities/${encodeURIComponent(entityId)}/dates`,
  );
}

/** Pin or clear an entity's Dunbar tier. tier=null clears the pin. */
export function updateEntityDunbarTier(
  entityId: string,
  tier: number | null,
): Promise<DunbarTierOverrideResponse> {
  return apiFetch<DunbarTierOverrideResponse>(
    `/relationship/entities/${encodeURIComponent(entityId)}/dunbar-tier`,
    { method: "PATCH", body: JSON.stringify({ tier }) },
  );
}

/** Link a contact to an entity. */
export function setEntityLinkedContact(
  entityId: string,
  contactId: string,
): Promise<{ entity_id: string; contact_id: string }> {
  return apiFetch<{ entity_id: string; contact_id: string }>(
    `/memory/entities/${encodeURIComponent(entityId)}/linked-contact`,
    { method: "PUT", body: JSON.stringify({ contact_id: contactId }) },
  );
}

/** Unlink the contact from an entity. */
export function unlinkEntityContact(
  entityId: string,
): Promise<void> {
  return apiFetch<void>(
    `/memory/entities/${encodeURIComponent(entityId)}/linked-contact`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

function approvalActionSearchParams(params?: ApprovalActionParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (params?.tool_name) qs.set("tool_name", params.tool_name);
  if (params?.status) qs.set("status", params.status);
  if (params?.butler) qs.set("butler", params.butler);
  if (params?.offset != null) qs.set("offset", params.offset.toString());
  if (params?.limit != null) qs.set("limit", params.limit.toString());
  return qs;
}

function approvalRuleSearchParams(params?: ApprovalRuleParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (params?.tool_name) qs.set("tool_name", params.tool_name);
  if (params?.active != null) qs.set("active", params.active.toString());
  if (params?.butler) qs.set("butler", params.butler);
  if (params?.offset != null) qs.set("offset", params.offset.toString());
  if (params?.limit != null) qs.set("limit", params.limit.toString());
  return qs;
}

export function getApprovalActions(
  params?: ApprovalActionParams,
): Promise<PaginatedResponse<ApprovalAction>> {
  const qs = approvalActionSearchParams(params).toString();
  return apiFetch<PaginatedResponse<ApprovalAction>>(
    qs ? `/approvals/actions?${qs}` : "/approvals/actions",
  );
}

export function getApprovalAction(actionId: string): Promise<ApiResponse<ApprovalAction>> {
  return apiFetch<ApiResponse<ApprovalAction>>(
    `/approvals/actions/${encodeURIComponent(actionId)}`,
  );
}

export function getExecutedActions(
  params?: ApprovalActionParams,
): Promise<PaginatedResponse<ApprovalAction>> {
  const qs = approvalActionSearchParams(params).toString();
  return apiFetch<PaginatedResponse<ApprovalAction>>(
    qs ? `/approvals/actions/executed?${qs}` : "/approvals/actions/executed",
  );
}

export function approveAction(
  actionId: string,
  request: ApprovalActionApproveRequest,
): Promise<ApiResponse<ApprovalAction>> {
  return apiFetch<ApiResponse<ApprovalAction>>(
    `/approvals/actions/${encodeURIComponent(actionId)}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
}

export function rejectAction(
  actionId: string,
  request: ApprovalActionRejectRequest,
): Promise<ApiResponse<ApprovalAction>> {
  return apiFetch<ApiResponse<ApprovalAction>>(
    `/approvals/actions/${encodeURIComponent(actionId)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
}

export function retryAction(
  actionId: string,
): Promise<ApiResponse<ApprovalAction>> {
  return apiFetch<ApiResponse<ApprovalAction>>(
    `/approvals/actions/${encodeURIComponent(actionId)}/retry`,
    { method: "POST" },
  );
}

export function expireStaleActions(
  butler?: string,
  hours?: number,
): Promise<ApiResponse<ExpireStaleActionsResponse>> {
  const params = new URLSearchParams();
  if (butler) params.set("butler", butler);
  if (hours != null) params.set("hours", hours.toString());
  const qs = params.toString();
  return apiFetch<ApiResponse<ExpireStaleActionsResponse>>(
    qs ? `/approvals/actions/expire-stale?${qs}` : "/approvals/actions/expire-stale",
    { method: "POST" },
  );
}

export function getApprovalRules(
  params?: ApprovalRuleParams,
): Promise<PaginatedResponse<ApprovalRule>> {
  const qs = approvalRuleSearchParams(params).toString();
  return apiFetch<PaginatedResponse<ApprovalRule>>(
    qs ? `/approvals/rules?${qs}` : "/approvals/rules",
  );
}

export function getApprovalRule(ruleId: string): Promise<ApiResponse<ApprovalRule>> {
  return apiFetch<ApiResponse<ApprovalRule>>(
    `/approvals/rules/${encodeURIComponent(ruleId)}`,
  );
}

export function createApprovalRule(
  request: ApprovalRuleCreateRequest,
): Promise<ApiResponse<ApprovalRule>> {
  return apiFetch<ApiResponse<ApprovalRule>>("/approvals/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function createRuleFromAction(
  request: ApprovalRuleFromActionRequest,
): Promise<ApiResponse<ApprovalRule>> {
  return apiFetch<ApiResponse<ApprovalRule>>("/approvals/rules/from-action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export function revokeApprovalRule(ruleId: string): Promise<ApiResponse<ApprovalRule>> {
  return apiFetch<ApiResponse<ApprovalRule>>(
    `/approvals/rules/${encodeURIComponent(ruleId)}/revoke`,
    { method: "POST" },
  );
}

export function getRuleSuggestions(
  actionId: string,
): Promise<ApiResponse<RuleConstraintSuggestion>> {
  return apiFetch<ApiResponse<RuleConstraintSuggestion>>(
    `/approvals/rules/suggestions/${encodeURIComponent(actionId)}`,
  );
}

export function getApprovalMetrics(): Promise<ApiResponse<ApprovalMetrics>> {
  return apiFetch<ApiResponse<ApprovalMetrics>>("/approvals/metrics");
}

function autonomySuggestionSearchParams(params?: AutonomySuggestionParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.suggestion_type) qs.set("suggestion_type", params.suggestion_type);
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  if (params?.offset !== undefined) qs.set("offset", String(params.offset));
  return qs;
}

export function getAutonomySuggestions(
  params?: AutonomySuggestionParams,
): Promise<PaginatedResponse<AutonomySuggestion>> {
  const qs = autonomySuggestionSearchParams(params).toString();
  return apiFetch<PaginatedResponse<AutonomySuggestion>>(
    qs ? `/approvals/suggestions?${qs}` : "/approvals/suggestions",
  );
}

export function confirmAutonomySuggestion(
  suggestionId: string,
): Promise<ApiResponse<AutonomySuggestion>> {
  return apiFetch<ApiResponse<AutonomySuggestion>>(
    `/approvals/suggestions/${encodeURIComponent(suggestionId)}/confirm`,
    { method: "POST" },
  );
}

export function dismissAutonomySuggestion(
  suggestionId: string,
  request?: AutonomySuggestionDismissRequest,
): Promise<ApiResponse<AutonomySuggestion>> {
  return apiFetch<ApiResponse<AutonomySuggestion>>(
    `/approvals/suggestions/${encodeURIComponent(suggestionId)}/dismiss`,
    {
      method: "POST",
      body: JSON.stringify(request ?? {}),
    },
  );
}

// ---------------------------------------------------------------------------
// OAuth / Secrets management API functions
// ---------------------------------------------------------------------------

import type {
  DeleteCredentialsResponse,
  DisconnectAccountResponse,
  GoogleAccount,
  GoogleAccountStatus,
  GoogleCredentialStatusResponse,
  OAuthStatusResponse,
  SetPrimaryAccountResponse,
  UpsertAppCredentialsRequest,
  UpsertAppCredentialsResponse,
} from "./types.ts";

/** Fetch the current OAuth status (probes Google token validity). */
export function getOAuthStatus(): Promise<OAuthStatusResponse> {
  return apiFetch<OAuthStatusResponse>("/oauth/status");
}

/** Fetch the masked credential status (presence only, no secret values). */
export function getGoogleCredentialStatus(): Promise<GoogleCredentialStatusResponse> {
  return apiFetch<GoogleCredentialStatusResponse>("/oauth/google/credentials");
}

/** Store Google app credentials (client_id + client_secret). */
export function upsertGoogleCredentials(
  request: UpsertAppCredentialsRequest,
): Promise<UpsertAppCredentialsResponse> {
  return apiFetch<UpsertAppCredentialsResponse>("/oauth/google/credentials", {
    method: "PUT",
    body: JSON.stringify(request),
  });
}

/** Delete all stored Google OAuth credentials. */
export function deleteGoogleCredentials(): Promise<DeleteCredentialsResponse> {
  return apiFetch<DeleteCredentialsResponse>("/oauth/google/credentials", {
    method: "DELETE",
  });
}

/** Trigger the Google OAuth flow (returns the authorization URL). */
export function getOAuthStartUrl(): string {
  return `${API_BASE_URL}/oauth/google/start`;
}

/** Build the URL to start an OAuth flow for a new or existing Google account.
 *
 * ``scopeSet`` selects one or more named scope sets registered in
 * ``GOOGLE_SCOPE_SETS`` on the backend (e.g. ``"health"`` or
 * ``"calendar,drive"``). Omitting ``scopeSet`` reproduces the pre-existing
 * default scope composition — callers that only needed Calendar/Drive/
 * Gmail continue to work without modification.
 */
export function getGoogleOAuthStartUrl(opts?: {
  accountHint?: string;
  forceConsent?: boolean;
  scopeSet?: string;
}): string {
  const params = new URLSearchParams();
  if (opts?.accountHint) params.set("account_hint", opts.accountHint);
  if (opts?.forceConsent) params.set("force_consent", "true");
  if (opts?.scopeSet) params.set("scope_set", opts.scopeSet);
  const qs = params.toString();
  return `${API_BASE_URL}/oauth/google/start${qs ? `?${qs}` : ""}`;
}

/** Fetch all connected Google accounts. */
export function getGoogleAccounts(): Promise<GoogleAccount[]> {
  return apiFetch<GoogleAccount[]>("/oauth/google/accounts");
}

/** Set a Google account as the primary account. */
export function setPrimaryAccount(accountId: string): Promise<SetPrimaryAccountResponse> {
  return apiFetch<SetPrimaryAccountResponse>(`/oauth/google/accounts/${accountId}/primary`, {
    method: "PUT",
  });
}

/** Disconnect (or hard-delete) a Google account. */
export function disconnectAccount(
  accountId: string,
  hardDelete?: boolean,
): Promise<DisconnectAccountResponse> {
  const url = hardDelete
    ? `/oauth/google/accounts/${accountId}?hard_delete=true`
    : `/oauth/google/accounts/${accountId}`;
  return apiFetch<DisconnectAccountResponse>(url, { method: "DELETE" });
}

/** Fetch per-account credential status. */
export function getAccountStatus(accountId: string): Promise<GoogleAccountStatus> {
  return apiFetch<GoogleAccountStatus>(`/oauth/google/accounts/${accountId}/status`);
}

// ---------------------------------------------------------------------------
// Google Health connector API functions
// ---------------------------------------------------------------------------

import type {
  GoogleHealthDisconnectResponse,
  GoogleHealthStatusResponse,
} from "./types.ts";

/**
 * Google Health scope URLs. Full URLs (not short names) are stored on
 * ``public.google_accounts.granted_scopes`` exactly as Google returns
 * them in the token response, so scope-presence checks compare against
 * these exact strings. Kept in sync with:
 *   src/butlers/api/routers/oauth.py ::GOOGLE_SCOPE_SETS["health"]
 *   src/butlers/api/routers/google_health.py ::GOOGLE_HEALTH_SCOPE_URLS
 */
export const GOOGLE_HEALTH_SCOPES = [
  "https://www.googleapis.com/auth/googlehealth.sleep",
  "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
  "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
] as const;

/** Fetch the Google Health connector status (state, scopes, counts, flags). */
export function getGoogleHealthStatus(): Promise<GoogleHealthStatusResponse> {
  return apiFetch<GoogleHealthStatusResponse>("/connectors/google-health/status");
}

/** Scope-selectively disconnect Google Health — preserves Calendar/Drive. */
export function disconnectGoogleHealth(): Promise<GoogleHealthDisconnectResponse> {
  return apiFetch<GoogleHealthDisconnectResponse>("/connectors/google-health/disconnect", {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// CLI auth (device-code flow) API functions
// ---------------------------------------------------------------------------

import type {
  CLIAuthApiKeyResponse,
  CLIAuthProvider,
  CLIAuthSessionResponse,
  CLIAuthStartResponse,
  CLIAuthTestResponse,
} from "./types.ts";

/** List available CLI auth providers and their current auth status. */
export function listCLIAuthProviders(): Promise<CLIAuthProvider[]> {
  return apiFetch<CLIAuthProvider[]>("/cli-auth/providers");
}

/** Start a device-code auth flow for a CLI provider. */
export function startCLIAuth(provider: string): Promise<CLIAuthStartResponse> {
  return apiFetch<CLIAuthStartResponse>(`/cli-auth/${provider}/start`, {
    method: "POST",
  });
}

/** Poll the status of an in-flight CLI auth session. */
export function getCLIAuthSession(sessionId: string): Promise<CLIAuthSessionResponse> {
  return apiFetch<CLIAuthSessionResponse>(`/cli-auth/sessions/${sessionId}`);
}

/** Cancel a running CLI auth session. */
export function cancelCLIAuthSession(sessionId: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/cli-auth/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** Save an API key for an api_key-mode CLI auth provider. */
export function saveCLIAuthApiKey(
  provider: string,
  apiKey: string,
): Promise<CLIAuthApiKeyResponse> {
  return apiFetch<CLIAuthApiKeyResponse>(`/cli-auth/${provider}/api-key`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

/** Delete a stored API key for an api_key-mode CLI auth provider. */
export function deleteCLIAuthApiKey(provider: string): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(`/cli-auth/${provider}/api-key`, {
    method: "DELETE",
  });
}

/** Test a stored API key by running the provider's test command. */
export function testCLIAuthApiKey(provider: string): Promise<CLIAuthTestResponse> {
  return apiFetch<CLIAuthTestResponse>(`/cli-auth/${provider}/test`, {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Generic secrets CRUD API functions
// ---------------------------------------------------------------------------

import type {
  SecretEntry,
  SecretUpsertRequest,
} from "./types.ts";

/** List all secrets for a butler (metadata only — values never returned). */
export function listSecrets(
  butlerName: string,
  category?: string,
): Promise<ApiResponse<SecretEntry[]>> {
  const qs = category ? `?category=${encodeURIComponent(category)}` : "";
  return apiFetch<ApiResponse<SecretEntry[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/secrets${qs}`,
  );
}

/** Fetch a single secret's metadata. */
export function getSecretMeta(
  butlerName: string,
  key: string,
): Promise<ApiResponse<SecretEntry>> {
  return apiFetch<ApiResponse<SecretEntry>>(
    `/butlers/${encodeURIComponent(butlerName)}/secrets/${encodeURIComponent(key)}`,
  );
}

/** Reveal the raw value of a secret. */
export function revealSecret(
  butlerName: string,
  key: string,
): Promise<ApiResponse<{ key: string; value: string }>> {
  return apiFetch<ApiResponse<{ key: string; value: string }>>(
    `/butlers/${encodeURIComponent(butlerName)}/secrets/${encodeURIComponent(key)}/reveal`,
  );
}

/** Create or update a secret. Value is write-only and never echoed back. */
export function upsertSecret(
  butlerName: string,
  key: string,
  request: SecretUpsertRequest,
): Promise<ApiResponse<SecretEntry>> {
  return apiFetch<ApiResponse<SecretEntry>>(
    `/butlers/${encodeURIComponent(butlerName)}/secrets/${encodeURIComponent(key)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
}

/** Delete a secret from a butler's secret store. */
export function deleteSecret(
  butlerName: string,
  key: string,
): Promise<ApiResponse<{ key: string; status: string }>> {
  return apiFetch<ApiResponse<{ key: string; status: string }>>(
    `/butlers/${encodeURIComponent(butlerName)}/secrets/${encodeURIComponent(key)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Backfill job API
// ---------------------------------------------------------------------------

import type {
  BackfillJobEntry,
  BackfillJobParams,
  BackfillJobSummary,
  BackfillLifecycleResponse,
  ConnectorEntry,
  CreateBackfillJobRequest,
} from "./types.ts";

/** List backfill jobs with optional filters. */
export function listBackfillJobs(
  params?: BackfillJobParams,
): Promise<PaginatedResponse<BackfillJobSummary>> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.connector_type) sp.set("connector_type", params.connector_type);
  if (params?.endpoint_identity) sp.set("endpoint_identity", params.endpoint_identity);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<BackfillJobSummary>>(
    qs ? `/switchboard/backfill?${qs}` : "/switchboard/backfill",
  );
}

/** Create a new backfill job. */
export function createBackfillJob(
  body: CreateBackfillJobRequest,
): Promise<ApiResponse<BackfillJobEntry>> {
  return apiFetch<ApiResponse<BackfillJobEntry>>("/switchboard/backfill", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Fetch a single backfill job by id. */
export function getBackfillJob(jobId: string): Promise<ApiResponse<BackfillJobEntry>> {
  return apiFetch<ApiResponse<BackfillJobEntry>>(
    `/switchboard/backfill/${encodeURIComponent(jobId)}`,
  );
}

/** Poll backfill job progress (alias for getBackfillJob). */
export function getBackfillJobProgress(jobId: string): Promise<ApiResponse<BackfillJobEntry>> {
  return apiFetch<ApiResponse<BackfillJobEntry>>(
    `/switchboard/backfill/${encodeURIComponent(jobId)}/progress`,
  );
}

/** Pause a backfill job. */
export function pauseBackfillJob(
  jobId: string,
): Promise<ApiResponse<BackfillLifecycleResponse>> {
  return apiFetch<ApiResponse<BackfillLifecycleResponse>>(
    `/switchboard/backfill/${encodeURIComponent(jobId)}/pause`,
    { method: "PATCH" },
  );
}

/** Cancel a backfill job. */
export function cancelBackfillJob(
  jobId: string,
): Promise<ApiResponse<BackfillLifecycleResponse>> {
  return apiFetch<ApiResponse<BackfillLifecycleResponse>>(
    `/switchboard/backfill/${encodeURIComponent(jobId)}/cancel`,
    { method: "PATCH" },
  );
}

/** Resume a paused backfill job. */
export function resumeBackfillJob(
  jobId: string,
): Promise<ApiResponse<BackfillLifecycleResponse>> {
  return apiFetch<ApiResponse<BackfillLifecycleResponse>>(
    `/switchboard/backfill/${encodeURIComponent(jobId)}/resume`,
    { method: "PATCH" },
  );
}

/** List registered connectors. */
export function listConnectors(): Promise<ApiResponse<ConnectorEntry[]>> {
  return apiFetch<ApiResponse<ConnectorEntry[]>>("/switchboard/connectors");
}

// ---------------------------------------------------------------------------
// Thread affinity API
// ---------------------------------------------------------------------------

/** Get global thread-affinity settings. */
export function getThreadAffinitySettings(): Promise<ThreadAffinitySettings> {
  return apiFetch<ThreadAffinitySettings>("/switchboard/thread-affinity/settings");
}

/** Update global thread-affinity settings. */
export function updateThreadAffinitySettings(
  body: ThreadAffinitySettingsUpdate,
): Promise<ThreadAffinitySettings> {
  return apiFetch<ThreadAffinitySettings>("/switchboard/thread-affinity/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** List per-thread affinity overrides. */
export function listThreadAffinityOverrides(): Promise<ThreadOverrideEntry[]> {
  return apiFetch<ThreadOverrideEntry[]>("/switchboard/thread-affinity/overrides");
}

/** Upsert a per-thread affinity override. */
export function upsertThreadAffinityOverride(
  threadId: string,
  body: ThreadOverrideUpsert,
): Promise<ThreadAffinitySettings> {
  return apiFetch<ThreadAffinitySettings>(
    `/switchboard/thread-affinity/overrides/${encodeURIComponent(threadId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/** Delete a per-thread affinity override. */
export function deleteThreadAffinityOverride(threadId: string): Promise<void> {
  return apiFetch<void>(
    `/switchboard/thread-affinity/overrides/${encodeURIComponent(threadId)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Education
// ---------------------------------------------------------------------------

import type {
  AnalyticsSnapshot,
  CrossTopicAnalytics,
  CurriculumRequestBody,
  CurriculumRequestResponse,
  MasterySummary,
  MindMap,
  MindMapListParams,
  MindMapNode,
  PendingReviewNode,
  QuizResponse,
  QuizResponseParams,
  TeachingFlow,
} from "./types.ts";

/** List mind maps with optional status filter and pagination. */
export function getEducationMindMaps(
  params?: MindMapListParams,
): Promise<PaginatedResponse<MindMap>> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<MindMap>>(
    qs ? `/education/mind-maps?${qs}` : "/education/mind-maps",
  );
}

/** Get a single mind map with full node and edge DAG. */
export function getEducationMindMap(mindMapId: string): Promise<MindMap> {
  return apiFetch<MindMap>(
    `/education/mind-maps/${encodeURIComponent(mindMapId)}`,
  );
}

/** Get frontier nodes for a mind map. */
export function getEducationMindMapFrontier(
  mindMapId: string,
): Promise<MindMapNode[]> {
  return apiFetch<MindMapNode[]>(
    `/education/mind-maps/${encodeURIComponent(mindMapId)}/frontier`,
  );
}

/** Get analytics snapshot (with optional trend) for a mind map. */
export function getEducationMindMapAnalytics(
  mindMapId: string,
  trendDays?: number,
): Promise<AnalyticsSnapshot> {
  const sp = new URLSearchParams();
  if (trendDays != null) sp.set("trend_days", String(trendDays));
  const qs = sp.toString();
  return apiFetch<AnalyticsSnapshot>(
    `/education/mind-maps/${encodeURIComponent(mindMapId)}/analytics${qs ? `?${qs}` : ""}`,
  );
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
): Promise<PendingReviewNode[]> {
  const url =
    horizonDays !== undefined
      ? `/education/mind-maps/${encodeURIComponent(mindMapId)}/pending-reviews?horizon_days=${horizonDays}`
      : `/education/mind-maps/${encodeURIComponent(mindMapId)}/pending-reviews`;
  return apiFetch<PendingReviewNode[]>(url);
}

/** Get aggregate mastery summary for a mind map. */
export function getEducationMasterySummary(
  mindMapId: string,
): Promise<MasterySummary> {
  return apiFetch<MasterySummary>(
    `/education/mind-maps/${encodeURIComponent(mindMapId)}/mastery-summary`,
  );
}

/** List quiz responses with optional filters. */
export function getEducationQuizResponses(
  params?: QuizResponseParams,
): Promise<PaginatedResponse<QuizResponse>> {
  const sp = new URLSearchParams();
  if (params?.mind_map_id) sp.set("mind_map_id", params.mind_map_id);
  if (params?.node_id) sp.set("node_id", params.node_id);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<QuizResponse>>(
    qs ? `/education/quiz-responses?${qs}` : "/education/quiz-responses",
  );
}

/** List teaching flows with optional status filter. */
export function getEducationFlows(
  status?: string,
): Promise<TeachingFlow[]> {
  const sp = new URLSearchParams();
  if (status) sp.set("status", status);
  const qs = sp.toString();
  return apiFetch<TeachingFlow[]>(
    qs ? `/education/flows?${qs}` : "/education/flows",
  );
}

/** Get cross-topic comparative analytics. */
export function getEducationCrossTopicAnalytics(): Promise<CrossTopicAnalytics> {
  return apiFetch<CrossTopicAnalytics>("/education/analytics/cross-topic");
}

/** Update a mind map's status. */
export function updateEducationMindMapStatus(
  mindMapId: string,
  status: string,
): Promise<MindMap> {
  return apiFetch<MindMap>(
    `/education/mind-maps/${encodeURIComponent(mindMapId)}/status`,
    { method: "PUT", body: JSON.stringify({ status }) },
  );
}

/** Submit a curriculum request for the butler to process. */
export function requestEducationCurriculum(
  body: CurriculumRequestBody,
): Promise<CurriculumRequestResponse> {
  return apiFetch<CurriculumRequestResponse>("/education/curriculum-requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Connector statistics API (docs/connectors/statistics.md §6)
// ---------------------------------------------------------------------------

import type {
  ConnectorCheckpoint,
  ConnectorCounters,
  ConnectorDaySummary,
  ConnectorDetail,
  ConnectorFanout,
  ConnectorFanoutEntry,
  ConnectorStats,
  ConnectorStatsBucket,
  ConnectorStatsSummary,
  ConnectorSummary,
  ConnectorSummaryEntry,
  CrossConnectorSummary,
  IngestionOverviewStats,
  IngestionPeriod,
} from "./types.ts";

// Re-export the types so they are accessible from this module too.
export type {
  ConnectorCheckpoint,
  ConnectorCounters,
  ConnectorDaySummary,
  ConnectorDetail,
  ConnectorFanout,
  ConnectorFanoutEntry,
  ConnectorStats,
  ConnectorStatsBucket,
  ConnectorStatsSummary,
  ConnectorSummary,
  ConnectorSummaryEntry,
  CrossConnectorSummary,
  IngestionOverviewStats,
  IngestionPeriod,
};

// ---------------------------------------------------------------------------
// Internal helpers — backend response shapes
// ---------------------------------------------------------------------------

/** Raw connector entry from GET /api/switchboard/connectors. */
interface _BackendConnectorEntry {
  connector_type: string;
  endpoint_identity: string;
  instance_id: string | null;
  version: string | null;
  state: string;
  error_message: string | null;
  uptime_s: number | null;
  last_heartbeat_at: string | null;
  first_seen_at: string;
  registered_via: string;
  counter_messages_ingested: number;
  counter_messages_failed: number;
  counter_source_api_calls: number;
  counter_checkpoint_saves: number;
  counter_dedupe_accepted: number;
  today_messages_ingested: number;
  today_messages_failed: number;
  checkpoint_cursor: string | null;
  checkpoint_updated_at: string | null;
  settings: Record<string, unknown> | null;
}

/** Raw aggregate summary from GET /api/switchboard/connectors/summary. */
interface _BackendConnectorSummary {
  total_connectors: number;
  online_count: number;
  stale_count: number;
  offline_count: number;
  unknown_count: number;
  total_messages_ingested: number;
  total_messages_failed: number;
  error_rate_pct: number;
}

/** Raw row from GET /api/switchboard/ingestion/fanout. */
interface _BackendFanoutRow {
  connector_type: string;
  endpoint_identity: string;
  target_butler: string;
  message_count: number;
}

/** Raw timeseries row from GET /api/switchboard/connectors/:type/:id/stats. */
interface _BackendStatsRow {
  connector_type: string;
  endpoint_identity: string;
  /** ISO string for hourly rollup (period=24h). */
  hour?: string;
  /** ISO date string for daily rollup (period=7d|30d). */
  day?: string;
  messages_ingested: number;
  messages_failed: number;
  source_api_calls: number;
  dedupe_accepted: number;
  heartbeat_count: number;
  healthy_count: number;
  degraded_count: number;
  error_count: number;
  uptime_pct?: number | null;
}

/**
 * Derive liveness string from last heartbeat timestamp.
 * - online: heartbeat within the last 5 minutes
 * - stale: heartbeat between 5 and 30 minutes ago
 * - offline: no heartbeat, or more than 30 minutes ago
 */
function _deriveLiveness(lastHeartbeatAt: string | null): string {
  if (!lastHeartbeatAt) return "offline";
  const ageMs = Date.now() - new Date(lastHeartbeatAt).getTime();
  const ageMins = ageMs / 60_000;
  if (ageMins < 5) return "online";
  if (ageMins < 30) return "stale";
  return "offline";
}

/** Map a backend ConnectorEntry to the frontend ConnectorSummary shape. */
function _toConnectorSummary(entry: _BackendConnectorEntry): ConnectorSummary {
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
  };
}

/** Map a backend ConnectorEntry to the frontend ConnectorDetail shape. */
function _toConnectorDetail(entry: _BackendConnectorEntry): ConnectorDetail {
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
  };
}

/**
 * Map a backend aggregate summary to the frontend CrossConnectorSummary shape.
 * The `/summary` endpoint does not include per-connector breakdown or period,
 * so those are synthesised as empty/default values.
 */
function _toCrossConnectorSummary(
  raw: _BackendConnectorSummary,
  period: IngestionPeriod,
): CrossConnectorSummary {
  return {
    period,
    total_connectors: raw.total_connectors,
    connectors_online: raw.online_count,
    connectors_stale: raw.stale_count,
    connectors_offline: raw.offline_count,
    total_messages_ingested: raw.total_messages_ingested,
    total_messages_failed: raw.total_messages_failed,
    overall_error_rate_pct: raw.error_rate_pct,
    by_connector: [],
  };
}

/**
 * Map a flat list of FanoutRow records into the matrix-shaped ConnectorFanout
 * expected by FanoutMatrix. Rows are grouped by (connector_type, endpoint_identity)
 * and each unique target_butler becomes a key in the `targets` dict.
 */
function _toConnectorFanout(
  rows: _BackendFanoutRow[],
  period: IngestionPeriod,
): ConnectorFanout {
  const index = new Map<string, ConnectorFanoutEntry>();
  for (const row of rows) {
    const key = `${row.connector_type}::${row.endpoint_identity}`;
    if (!index.has(key)) {
      index.set(key, {
        connector_type: row.connector_type,
        endpoint_identity: row.endpoint_identity,
        targets: Object.create(null) as Record<string, number>,
      });
    }
    index.get(key)!.targets[row.target_butler] = row.message_count;
  }
  return { period, matrix: Array.from(index.values()) };
}

/**
 * Map a flat list of hourly/daily stats rows into the ConnectorStats shape
 * expected by VolumeTrendChart and the period-summary card.
 */
function _toConnectorStats(
  rows: _BackendStatsRow[],
  connectorType: string,
  endpointIdentity: string,
  period: IngestionPeriod,
): ConnectorStats {
  const timeseries: ConnectorStatsBucket[] = rows.map((r) => ({
    bucket: (r.hour ?? r.day ?? ""),
    messages_ingested: r.messages_ingested,
    messages_failed: r.messages_failed,
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

  return { connector_type: connectorType, endpoint_identity: endpointIdentity, period, summary, timeseries };
}

// ---------------------------------------------------------------------------
// Public API functions
// ---------------------------------------------------------------------------

/** List all connectors with liveness and today's stats. */
export async function listConnectorSummaries(): Promise<ApiResponse<ConnectorSummary[]>> {
  const resp = await apiFetch<ApiResponse<_BackendConnectorEntry[]>>("/switchboard/connectors");
  return {
    ...resp,
    data: (resp.data ?? []).map(_toConnectorSummary),
  };
}

/** Get full detail for a single connector. */
export async function getConnectorDetail(
  connectorType: string,
  endpointIdentity: string,
): Promise<ApiResponse<ConnectorDetail>> {
  const resp = await apiFetch<ApiResponse<_BackendConnectorEntry>>(
    `/switchboard/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(endpointIdentity)}`,
  );
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
): Promise<ApiResponse<ConnectorStats>> {
  const resp = await apiFetch<ApiResponse<_BackendStatsRow[]>>(
    `/switchboard/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(endpointIdentity)}/stats?period=${period}`,
  );
  return {
    ...resp,
    data: _toConnectorStats(resp.data ?? [], connectorType, endpointIdentity, period),
  };
}

/** Get aggregate cross-connector summary. */
export async function getCrossConnectorSummary(
  period: IngestionPeriod = "24h",
): Promise<ApiResponse<CrossConnectorSummary>> {
  const resp = await apiFetch<ApiResponse<_BackendConnectorSummary>>(
    `/switchboard/connectors/summary`,
  );
  return {
    ...resp,
    data: _toCrossConnectorSummary(resp.data, period),
  };
}

/** Get period-scoped ingestion overview statistics (message_inbox-based). */
export async function getIngestionOverview(
  period: IngestionPeriod = "24h",
): Promise<ApiResponse<IngestionOverviewStats>> {
  return apiFetch<ApiResponse<IngestionOverviewStats>>(
    `/switchboard/ingestion/overview?period=${period}`,
  );
}

/** Get aggregate ingestion volume time-series (across all connectors). */
export async function getIngestionVolume(
  period: IngestionPeriod = "24h",
): Promise<ApiResponse<ConnectorStats>> {
  const resp = await apiFetch<ApiResponse<_BackendStatsRow[]>>(
    `/switchboard/ingestion/volume?period=${period}`,
  );
  return {
    ...resp,
    data: _toConnectorStats(resp.data ?? [], "all", "all", period),
  };
}

/** Get fanout distribution matrix. */
export async function getConnectorFanout(
  period: IngestionPeriod = "7d",
): Promise<ApiResponse<ConnectorFanout>> {
  const resp = await apiFetch<ApiResponse<_BackendFanoutRow[]>>(
    `/switchboard/ingestion/fanout?period=${period}`,
  );
  return {
    ...resp,
    data: _toConnectorFanout(resp.data ?? [], period),
  };
}

/** Update a connector's checkpoint cursor (PATCH /connectors/{type}/{identity}/cursor). */
export async function updateConnectorCursor(
  connectorType: string,
  endpointIdentity: string,
  cursor: string,
): Promise<ApiResponse<ConnectorDetail>> {
  const resp = await apiFetch<ApiResponse<_BackendConnectorEntry>>(
    `/switchboard/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(endpointIdentity)}/cursor`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cursor }),
    },
  );
  return {
    ...resp,
    data: _toConnectorDetail(resp.data),
  };
}

/** Update connector settings (shallow merge). */
export async function updateConnectorSettings(
  connectorType: string,
  endpointIdentity: string,
  settings: Record<string, unknown>,
): Promise<ApiResponse<ConnectorDetail>> {
  const resp = await apiFetch<ApiResponse<_BackendConnectorEntry>>(
    `/switchboard/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(endpointIdentity)}/settings`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings }),
    },
  );
  return {
    ...resp,
    data: _toConnectorDetail(resp.data),
  };
}

/** Delete (deregister) a connector and its heartbeat log. */
export async function deleteConnector(
  connectorType: string,
  endpointIdentity: string,
): Promise<ApiResponse<{ deleted: string }>> {
  return apiFetch<ApiResponse<{ deleted: string }>>(
    `/switchboard/connectors/${encodeURIComponent(connectorType)}/${encodeURIComponent(endpointIdentity)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Unified ingestion rules (design.md D8)
// ---------------------------------------------------------------------------

/** List active ingestion rules with optional filters. */
export function getIngestionRules(
  params?: IngestionRuleListParams,
): Promise<ApiResponse<IngestionRule[]>> {
  const qs = params
    ? Object.entries(params)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join("&")
    : "";
  return apiFetch<ApiResponse<IngestionRule[]>>(
    qs ? `/switchboard/ingestion-rules?${qs}` : "/switchboard/ingestion-rules",
  );
}

/** Create a new ingestion rule. */
export function createIngestionRule(
  body: IngestionRuleCreate,
): Promise<ApiResponse<IngestionRule>> {
  return apiFetch<ApiResponse<IngestionRule>>("/switchboard/ingestion-rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Partially update an ingestion rule. */
export function updateIngestionRule(
  ruleId: string,
  body: IngestionRuleUpdate,
): Promise<ApiResponse<IngestionRule>> {
  return apiFetch<ApiResponse<IngestionRule>>(
    `/switchboard/ingestion-rules/${encodeURIComponent(ruleId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

/** Soft-delete an ingestion rule. */
export function deleteIngestionRule(ruleId: string): Promise<void> {
  return apiFetch<void>(
    `/switchboard/ingestion-rules/${encodeURIComponent(ruleId)}`,
    { method: "DELETE" },
  );
}

/** Dry-run: evaluate a test envelope against active ingestion rules. */
export function testIngestionRule(
  body: IngestionRuleTestRequest,
): Promise<IngestionRuleTestResponse> {
  return apiFetch<IngestionRuleTestResponse>("/switchboard/ingestion-rules/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Ingestion event lineage (GET /api/ingestion/events/*)
// ---------------------------------------------------------------------------

/** List ingestion events with optional filtering (GET /api/ingestion/events). */
export async function listIngestionEvents(
  params?: IngestionEventsParams,
): Promise<PaginatedResponse<IngestionEventSummary>> {
  const sp = new URLSearchParams();
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  if (params?.offset !== undefined) sp.set("offset", String(params.offset));
  if (params?.source_channel) sp.set("source_channel", params.source_channel);
  if (params?.status) sp.set("status", params.status);
  const qs = sp.toString() ? `?${sp.toString()}` : "";
  return apiFetch<PaginatedResponse<IngestionEventSummary>>(
    `/ingestion/events${qs}`,
  );
}

/** Get a single ingestion event by request_id (GET /api/ingestion/events/{id}). */
export async function getIngestionEvent(
  requestId: string,
): Promise<ApiResponse<IngestionEventSummary>> {
  return apiFetch<ApiResponse<IngestionEventSummary>>(
    `/ingestion/events/${encodeURIComponent(requestId)}`,
  );
}

/** Get sessions for an ingestion event (GET /api/ingestion/events/{id}/sessions). */
export async function getIngestionEventSessions(
  requestId: string,
): Promise<ApiResponse<IngestionEventSession[]>> {
  return apiFetch<ApiResponse<IngestionEventSession[]>>(
    `/ingestion/events/${encodeURIComponent(requestId)}/sessions`,
  );
}

/** Get cost/token rollup for an ingestion event (GET /api/ingestion/events/{id}/rollup). */
export async function getIngestionEventRollup(
  requestId: string,
): Promise<ApiResponse<IngestionEventRollup>> {
  return apiFetch<ApiResponse<IngestionEventRollup>>(
    `/ingestion/events/${encodeURIComponent(requestId)}/rollup`,
  );
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
): Promise<IngestionEventReplayResponse> {
  return apiFetch<IngestionEventReplayResponse>(
    `/ingestion/events/${encodeURIComponent(requestId)}/replay`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// Model catalog
// ---------------------------------------------------------------------------

/** GET /api/settings/pricing — fetch per-model pricing map */
export function fetchPricingMap(): Promise<ApiResponse<PricingMap>> {
  return apiFetch<ApiResponse<PricingMap>>("/settings/pricing");
}

/** GET /api/settings/models — list all catalog entries */
export function listModelCatalog(): Promise<ApiResponse<ModelCatalogEntry[]>> {
  return apiFetch<ApiResponse<ModelCatalogEntry[]>>("/settings/models");
}

/** POST /api/settings/models — create a catalog entry */
export function createModelCatalogEntry(
  body: ModelCatalogCreate,
): Promise<ApiResponse<ModelCatalogEntry>> {
  return apiFetch<ApiResponse<ModelCatalogEntry>>("/settings/models", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** PUT /api/settings/models/{id} — update a catalog entry */
export function updateModelCatalogEntry(
  id: string,
  body: ModelCatalogUpdate,
): Promise<ApiResponse<ModelCatalogEntry>> {
  return apiFetch<ApiResponse<ModelCatalogEntry>>(
    `/settings/models/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** POST /api/settings/models/{id}/test — test a model config */
export function testModelCatalogEntry(
  id: string,
): Promise<ApiResponse<ModelTestResult>> {
  return apiFetch<ApiResponse<ModelTestResult>>(
    `/settings/models/${encodeURIComponent(id)}/test`,
    { method: "POST" },
  );
}

/** DELETE /api/settings/models/{id} — delete a catalog entry */
export function deleteModelCatalogEntry(
  id: string,
): Promise<ApiResponse<{ deleted: boolean; id: string }>> {
  return apiFetch<ApiResponse<{ deleted: boolean; id: string }>>(
    `/settings/models/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Butler model overrides
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/model-overrides — list overrides for a butler */
export function listButlerModelOverrides(
  butlerName: string,
): Promise<ApiResponse<ButlerModelOverride[]>> {
  return apiFetch<ApiResponse<ButlerModelOverride[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/model-overrides`,
  );
}

/** PUT /api/butlers/{name}/model-overrides — batch upsert overrides */
export function upsertButlerModelOverrides(
  butlerName: string,
  body: ButlerModelOverrideUpsert[],
): Promise<ApiResponse<ButlerModelOverride[]>> {
  return apiFetch<ApiResponse<ButlerModelOverride[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/model-overrides`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** DELETE /api/butlers/{name}/model-overrides/{overrideId} — remove a single override */
export function deleteButlerModelOverride(
  butlerName: string,
  overrideId: string,
): Promise<ApiResponse<{ deleted: boolean; id: string }>> {
  return apiFetch<ApiResponse<{ deleted: boolean; id: string }>>(
    `/butlers/${encodeURIComponent(butlerName)}/model-overrides/${encodeURIComponent(overrideId)}`,
    { method: "DELETE" },
  );
}

/** GET /api/butlers/{name}/resolve-model?complexity=X — preview model resolution */
export function resolveButlerModel(
  butlerName: string,
  complexity: string,
): Promise<ApiResponse<ResolveModelResponse>> {
  return apiFetch<ApiResponse<ResolveModelResponse>>(
    `/butlers/${encodeURIComponent(butlerName)}/resolve-model?complexity=${encodeURIComponent(complexity)}`,
  );
}

/** PUT /api/settings/models/{id}/limits — set or update token limits */
export function setModelTokenLimits(
  id: string,
  body: TokenLimitsRequest,
): Promise<ApiResponse<TokenLimitsResponse>> {
  return apiFetch<ApiResponse<TokenLimitsResponse>>(
    `/settings/models/${encodeURIComponent(id)}/limits`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** POST /api/settings/models/{id}/reset-usage — reset usage window(s) */
export function resetModelUsage(
  id: string,
  body: ResetUsageRequest,
): Promise<ApiResponse<{ catalog_entry_id: string; window: string; reset: boolean }>> {
  return apiFetch<ApiResponse<{ catalog_entry_id: string; window: string; reset: boolean }>>(
    `/settings/models/${encodeURIComponent(id)}/reset-usage`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/** GET /api/settings/models/{id}/usage — detailed usage for a single entry */
export function getModelUsageDetail(
  id: string,
): Promise<ApiResponse<TokenUsageDetail>> {
  return apiFetch<ApiResponse<TokenUsageDetail>>(
    `/settings/models/${encodeURIComponent(id)}/usage`,
  );
}

// ---------------------------------------------------------------------------
// Provider configuration
// ---------------------------------------------------------------------------

/** GET /api/settings/providers — list all configured providers */
export function listProviders(): Promise<ApiResponse<ProviderConfig[]>> {
  return apiFetch<ApiResponse<ProviderConfig[]>>("/settings/providers");
}

/** POST /api/settings/providers — register a new provider */
export function createProvider(
  body: ProviderConfigCreate,
): Promise<ApiResponse<ProviderConfig>> {
  return apiFetch<ApiResponse<ProviderConfig>>("/settings/providers", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** PUT /api/settings/providers/{providerType} — update provider config */
export function updateProvider(
  providerType: string,
  body: ProviderConfigUpdate,
): Promise<ApiResponse<ProviderConfig>> {
  return apiFetch<ApiResponse<ProviderConfig>>(
    `/settings/providers/${encodeURIComponent(providerType)}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    },
  );
}

/** DELETE /api/settings/providers/{providerType} — remove provider */
export function deleteProvider(
  providerType: string,
): Promise<ApiResponse<{ deleted: boolean; provider_type: string }>> {
  return apiFetch<ApiResponse<{ deleted: boolean; provider_type: string }>>(
    `/settings/providers/${encodeURIComponent(providerType)}`,
    { method: "DELETE" },
  );
}

/** POST /api/settings/providers/{providerType}/test-connectivity — probe base URL */
export function testProviderConnectivity(
  providerType: string,
): Promise<ApiResponse<ProviderConnectivityResult>> {
  return apiFetch<ApiResponse<ProviderConnectivityResult>>(
    `/settings/providers/${encodeURIComponent(providerType)}/test-connectivity`,
    { method: "POST" },
  );
}

// ---------------------------------------------------------------------------
// WhatsApp connector API
// ---------------------------------------------------------------------------

/** GET /api/connectors/whatsapp/status — current connection state */
export function getWhatsAppStatus(): Promise<WhatsAppStatusResponse> {
  return apiFetch<WhatsAppStatusResponse>("/connectors/whatsapp/status");
}

/** POST /api/connectors/whatsapp/pair/start — initiate QR pairing */
export function startWhatsAppPairing(): Promise<WhatsAppPairStartResponse> {
  return apiFetch<WhatsAppPairStartResponse>("/connectors/whatsapp/pair/start", {
    method: "POST",
  });
}

/** GET /api/connectors/whatsapp/pair/poll — poll pairing progress */
export function pollWhatsAppPairing(): Promise<WhatsAppPairPollResponse> {
  return apiFetch<WhatsAppPairPollResponse>("/connectors/whatsapp/pair/poll");
}

/** POST /api/connectors/whatsapp/disconnect — gracefully disconnect */
export function disconnectWhatsApp(): Promise<WhatsAppDisconnectResponse> {
  return apiFetch<WhatsAppDisconnectResponse>("/connectors/whatsapp/disconnect", {
    method: "POST",
  });
}

/** GET /api/connectors/whatsapp/health — session health for badge */
export function getWhatsAppHealth(): Promise<WhatsAppHealthResponse> {
  return apiFetch<WhatsAppHealthResponse>("/connectors/whatsapp/health");
}

/** GET /api/relationship/dunbar/ranking — Dunbar tier ranking for social map visualization. */
export function getDunbarRanking(): Promise<DunbarRankingResponse> {
  return apiFetch<DunbarRankingResponse>("/relationship/dunbar/ranking");
}

// ---------------------------------------------------------------------------
// Spotify connector API
// ---------------------------------------------------------------------------

/** GET /api/spotify/status — current Spotify connection state */
export function getSpotifyStatus(): Promise<SpotifyStatusResponse> {
  return apiFetch<SpotifyStatusResponse>("/connectors/spotify/status");
}

/** POST /api/spotify/oauth/start — initiate PKCE OAuth flow, returns authorization URL */
export function startSpotifyOAuth(): Promise<SpotifyOAuthStartResponse> {
  return apiFetch<SpotifyOAuthStartResponse>("/connectors/spotify/oauth/start", {
    method: "POST",
  });
}

/** POST /api/spotify/config — store Spotify client_id */
export function saveSpotifyConfig(data: SpotifyConfigRequest): Promise<SpotifyConfigResponse> {
  return apiFetch<SpotifyConfigResponse>("/connectors/spotify/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** POST /api/spotify/disconnect — remove all Spotify credentials */
export function disconnectSpotify(): Promise<SpotifyDisconnectResponse> {
  return apiFetch<SpotifyDisconnectResponse>("/connectors/spotify/disconnect", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// OwnTracks connector API
// ---------------------------------------------------------------------------

/** GET /api/connectors/owntracks/status — connection state, last event, event count */
export function getOwnTracksStatus(): Promise<OwnTracksStatusResponse> {
  return apiFetch<OwnTracksStatusResponse>("/connectors/owntracks/status");
}

/** GET /api/connectors/owntracks/config — webhook URL and setup metadata */
export function getOwnTracksConfig(): Promise<OwnTracksConfigResponse> {
  return apiFetch<OwnTracksConfigResponse>("/connectors/owntracks/config");
}

/** POST /api/connectors/owntracks/token/generate — generate/regenerate bearer token */
export function generateOwnTracksToken(): Promise<OwnTracksTokenResponse> {
  return apiFetch<OwnTracksTokenResponse>("/connectors/owntracks/token/generate", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Home Assistant settings API
// ---------------------------------------------------------------------------

/** GET /api/settings/home-assistant — current HA connection state */
export function getHomeAssistantStatus(): Promise<HomeAssistantStatusResponse> {
  return apiFetch<HomeAssistantStatusResponse>("/settings/home-assistant");
}

/** POST /api/settings/home-assistant — validate and save HA URL + token */
export function configureHomeAssistant(
  data: HomeAssistantConfigRequest,
): Promise<HomeAssistantConfigResponse> {
  return apiFetch<HomeAssistantConfigResponse>("/settings/home-assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** DELETE /api/settings/home-assistant — remove stored HA credentials */
export function deleteHomeAssistantConfig(): Promise<HomeAssistantDeleteResponse> {
  return apiFetch<HomeAssistantDeleteResponse>("/settings/home-assistant", {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Dashboard conversation API
// ---------------------------------------------------------------------------

/** GET /api/butlers/{name}/conversations — paginated conversation list. */
export function listConversations(
  butlerName: string,
  params?: ConversationListParams,
): Promise<ApiResponse<ConversationSummary[]>> {
  const qs = params ? `?${new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])).toString()}` : "";
  return apiFetch<ApiResponse<ConversationSummary[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/conversations${qs}`,
  );
}

/** GET /api/butlers/{name}/conversations/{id} — single conversation summary. */
export function getConversation(
  butlerName: string,
  conversationId: string,
): Promise<ApiResponse<ConversationSummary>> {
  return apiFetch<ApiResponse<ConversationSummary>>(
    `/butlers/${encodeURIComponent(butlerName)}/conversations/${encodeURIComponent(conversationId)}`,
  );
}

/** GET /api/butlers/{name}/conversations/{id}/messages — message list for a conversation. */
export function getConversationMessages(
  butlerName: string,
  conversationId: string,
): Promise<ApiResponse<Message[]>> {
  return apiFetch<ApiResponse<Message[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/conversations/${encodeURIComponent(conversationId)}/messages`,
  );
}

/**
 * GET /api/butlers/{name}/conversations/search — full-text search across conversations.
 */
export function searchConversations(
  butlerName: string,
  query: string,
): Promise<ApiResponse<ConversationSummary[]>> {
  return apiFetch<ApiResponse<ConversationSummary[]>>(
    `/butlers/${encodeURIComponent(butlerName)}/conversations/search?q=${encodeURIComponent(query)}`,
  );
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
  return fetch(`${API_BASE_URL}/butlers/${encodeURIComponent(butlerName)}/conversations`, {
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
  return fetch(
    `${API_BASE_URL}/butlers/${encodeURIComponent(butlerName)}/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    },
  );
}

// ---------------------------------------------------------------------------
// Telegram Session Auth
// ---------------------------------------------------------------------------

/** POST /api/telegram/session/send-code — start Telegram login, send OTP */
export function telegramSendCode(
  request: TelegramSendCodeRequest,
): Promise<TelegramSendCodeResponse> {
  return apiFetch<TelegramSendCodeResponse>("/telegram/session/send-code", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

/** POST /api/telegram/session/verify — verify OTP code and persist session */
export function telegramVerifyCode(
  request: TelegramVerifyCodeRequest,
): Promise<TelegramVerifyCodeResponse> {
  return apiFetch<TelegramVerifyCodeResponse>("/telegram/session/verify", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

/** GET /api/telegram/session/status — check if Telegram credentials are configured */
export function getTelegramSessionStatus(): Promise<TelegramSessionStatusResponse> {
  return apiFetch<TelegramSessionStatusResponse>("/telegram/session/status");
}

// ---------------------------------------------------------------------------
// General settings
// ---------------------------------------------------------------------------

/** GET /api/settings/general — fetch shared prompt defaults */
export function getGeneralSettings(): Promise<ApiResponse<GeneralSettings>> {
  return apiFetch<ApiResponse<GeneralSettings>>("/settings/general");
}

/** PUT /api/settings/general — update shared prompt defaults */
export function updateGeneralSettings(
  body: GeneralSettingsUpdate,
): Promise<ApiResponse<GeneralSettings>> {
  return apiFetch<ApiResponse<GeneralSettings>>("/settings/general", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Blob storage settings
// ---------------------------------------------------------------------------

/** GET /api/settings/blob-storage — current configuration status */
export function getBlobStorageStatus(): Promise<ApiResponse<BlobStorageStatus>> {
  return apiFetch<ApiResponse<BlobStorageStatus>>("/settings/blob-storage");
}

/** POST /api/settings/blob-storage/test — test S3 connectivity */
export function testBlobStorage(): Promise<ApiResponse<BlobStorageTestResult>> {
  return apiFetch<ApiResponse<BlobStorageTestResult>>("/settings/blob-storage/test", {
    method: "POST",
  });
}

// ---------------------------------------------------------------------------
// Steam connector API
// ---------------------------------------------------------------------------

/** GET /api/steam/accounts — list all connected Steam accounts */
export function listSteamAccounts(): Promise<SteamAccountListResponse> {
  return apiFetch<SteamAccountListResponse>("/steam/accounts");
}

/** POST /api/steam/accounts — connect a new Steam account */
export function connectSteamAccount(data: SteamConnectRequest): Promise<SteamConnectResponse> {
  return apiFetch<SteamConnectResponse>("/steam/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/** DELETE /api/steam/accounts/{id} — disconnect (soft-revoke) a Steam account */
export function disconnectSteamAccount(accountId: string): Promise<SteamDisconnectResponse> {
  return apiFetch<SteamDisconnectResponse>(`/steam/accounts/${encodeURIComponent(accountId)}`, {
    method: "DELETE",
  });
}

/** GET /api/steam/playtime — playtime analytics for a Steam account */
export function getSteamPlaytime(params?: {
  account_id?: string;
  top_n?: number;
}): Promise<SteamPlaytimeAnalytics> {
  const query = new URLSearchParams();
  if (params?.account_id) query.set("account_id", params.account_id);
  if (params?.top_n !== undefined) query.set("top_n", String(params.top_n));
  const qs = query.toString();
  return apiFetch<SteamPlaytimeAnalytics>(`/steam/playtime${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
// Healing attempts API (used by QA investigation detail page)
// ---------------------------------------------------------------------------

/** GET /api/healing/attempts — paginated list */
export function listHealingAttempts(
  params?: HealingAttemptsParams,
): Promise<PaginatedResponse<HealingAttempt>> {
  const query = new URLSearchParams();
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.status) query.set("status", params.status);
  const qs = query.toString();
  return apiFetch<PaginatedResponse<HealingAttempt>>(`/healing/attempts${qs ? `?${qs}` : ""}`);
}

/** GET /api/healing/attempts/:id — single attempt detail */
export function getHealingAttempt(attemptId: string): Promise<HealingAttempt> {
  return apiFetch<HealingAttempt>(`/healing/attempts/${encodeURIComponent(attemptId)}`);
}

// ---------------------------------------------------------------------------
// QA Staffer API
// ---------------------------------------------------------------------------

/** GET /api/qa/summary — QA staffer status, last patrol, 24h/all-time stats */
export function getQaSummary(): Promise<ApiResponse<QaSummary>> {
  return apiFetch<ApiResponse<QaSummary>>("/qa/summary");
}

/** GET /api/qa/patrols — paginated patrol list */
export function getQaPatrols(params?: QaPatrolsParams): Promise<PaginatedResponse<QaPatrolSummary>> {
  const query = new URLSearchParams();
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.status) query.set("status", params.status);
  const qs = query.toString();
  return apiFetch<PaginatedResponse<QaPatrolSummary>>(`/qa/patrols${qs ? `?${qs}` : ""}`);
}

/** GET /api/qa/patrols/:patrolId — full patrol with nested findings */
export function getQaPatrol(patrolId: string): Promise<ApiResponse<QaPatrolDetail>> {
  return apiFetch<ApiResponse<QaPatrolDetail>>(`/qa/patrols/${encodeURIComponent(patrolId)}`);
}

/** GET /api/qa/patrols/:patrolId/findings — paginated findings for a patrol */
export function getQaPatrolFindings(
  patrolId: string,
  params?: { source_type?: string; novel_only?: boolean; offset?: number; limit?: number },
): Promise<PaginatedResponse<QaFindingRecord>> {
  const query = new URLSearchParams();
  if (params?.source_type) query.set("source_type", params.source_type);
  if (params?.novel_only !== undefined) query.set("novel_only", String(params.novel_only));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  const qs = query.toString();
  return apiFetch<PaginatedResponse<QaFindingRecord>>(
    `/qa/patrols/${encodeURIComponent(patrolId)}/findings${qs ? `?${qs}` : ""}`,
  );
}

/** GET /api/qa/findings/by-attempt/:attemptId — finding that dispatched an attempt */
export function getQaFindingByAttempt(
  attemptId: string,
): Promise<ApiResponse<QaFindingRecord>> {
  return apiFetch<ApiResponse<QaFindingRecord>>(
    `/qa/findings/by-attempt/${encodeURIComponent(attemptId)}`,
  );
}

/** GET /api/qa/known-issues — known issues grouped by fingerprint */
export function getQaKnownIssues(
  params?: QaKnownIssuesParams,
): Promise<PaginatedResponse<QaKnownIssue>> {
  const query = new URLSearchParams();
  if (params?.source_butler) query.set("source_butler", params.source_butler);
  if (params?.severity !== undefined) query.set("severity", String(params.severity));
  if (params?.dismissed !== undefined) query.set("dismissed", String(params.dismissed));
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  const qs = query.toString();
  return apiFetch<PaginatedResponse<QaKnownIssue>>(`/qa/known-issues${qs ? `?${qs}` : ""}`);
}

/** POST /api/qa/known-issues/:fingerprint/dismiss — dismiss a known issue */
export function dismissQaKnownIssue(
  fingerprint: string,
  body?: QaDismissRequest,
): Promise<ApiResponse<QaDismissal>> {
  return apiFetch<ApiResponse<QaDismissal>>(
    `/qa/known-issues/${encodeURIComponent(fingerprint)}/dismiss`,
    {
      method: "POST",
      body: body ? JSON.stringify(body) : "{}",
    },
  );
}

/** DELETE /api/qa/known-issues/:fingerprint/dismiss — un-dismiss a known issue */
export function undismissQaKnownIssue(
  fingerprint: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/qa/known-issues/${encodeURIComponent(fingerprint)}/dismiss`,
    { method: "DELETE" },
  );
}

/** GET /api/qa/investigations — paginated investigation pipeline */
export function getQaInvestigations(
  params?: QaInvestigationsParams,
): Promise<PaginatedResponse<QaInvestigation>> {
  const query = new URLSearchParams();
  if (params?.status) query.set("status", params.status);
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  const qs = query.toString();
  return apiFetch<PaginatedResponse<QaInvestigation>>(`/qa/investigations${qs ? `?${qs}` : ""}`);
}

/** GET /api/qa/trends — 7-day daily patrol success rate + source breakdown */
export function getQaTrends(days = 7): Promise<ApiResponse<QaTrends>> {
  return apiFetch<ApiResponse<QaTrends>>(`/qa/trends?days=${days}`);
}

/** POST /api/qa/force-patrol — request an immediate patrol cycle */
export function forceQaPatrol(): Promise<ApiResponse<ForcePatrolResponse>> {
  return apiFetch<ApiResponse<ForcePatrolResponse>>("/qa/force-patrol", { method: "POST" });
}

/** GET /api/qa/circuit-breaker — current circuit breaker state */
export function getQaCircuitBreaker(): Promise<ApiResponse<CircuitBreakerStatus>> {
  return apiFetch<ApiResponse<CircuitBreakerStatus>>("/qa/circuit-breaker");
}

/** POST /api/qa/circuit-breaker/reset — reset a tripped circuit breaker */
export function resetQaCircuitBreaker(): Promise<ApiResponse<CircuitBreakerResetResponse>> {
  return apiFetch<ApiResponse<CircuitBreakerResetResponse>>("/qa/circuit-breaker/reset", {
    method: "POST",
  });
}

/** GET /api/qa/settings/repo — current repo configuration */
export function getQaRepoConfig(): Promise<ApiResponse<QaRepoConfig>> {
  return apiFetch<ApiResponse<QaRepoConfig>>("/qa/settings/repo");
}

/** PUT /api/qa/settings/repo — update repo URL */
export function updateQaRepoConfig(
  body: QaRepoConfigUpdate,
): Promise<ApiResponse<QaRepoConfig>> {
  return apiFetch<ApiResponse<QaRepoConfig>>("/qa/settings/repo", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** POST /api/qa/settings/repo/sync — trigger immediate sync */
export function syncQaRepo(): Promise<ApiResponse<QaRepoSyncResponse>> {
  return apiFetch<ApiResponse<QaRepoSyncResponse>>("/qa/settings/repo/sync", {
    method: "POST",
  });
}

/** GET /api/qa/settings/allowed-repos — list allowed repositories */
export function getQaAllowedRepos(): Promise<PaginatedResponse<QaAllowedRepo>> {
  return apiFetch<PaginatedResponse<QaAllowedRepo>>("/qa/settings/allowed-repos?limit=200");
}

/** POST /api/qa/settings/allowed-repos — add a repository */
export function addQaAllowedRepo(
  body: QaAllowedRepoCreate,
): Promise<ApiResponse<QaAllowedRepo>> {
  return apiFetch<ApiResponse<QaAllowedRepo>>("/qa/settings/allowed-repos", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** PATCH /api/qa/settings/allowed-repos/{owner}/{repo} — toggle enabled */
export function patchQaAllowedRepo(
  owner: string,
  repo: string,
  body: QaAllowedRepoPatch,
): Promise<ApiResponse<QaAllowedRepo>> {
  return apiFetch<ApiResponse<QaAllowedRepo>>(
    `/qa/settings/allowed-repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  );
}

/** DELETE /api/qa/settings/allowed-repos/{owner}/{repo} — remove */
export function deleteQaAllowedRepo(
  owner: string,
  repo: string,
): Promise<ApiResponse<Record<string, unknown>>> {
  return apiFetch<ApiResponse<Record<string, unknown>>>(
    `/qa/settings/allowed-repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// Runtime Config
// ---------------------------------------------------------------------------

/** Fetch runtime config for a specific butler. */
export function getRuntimeConfig(
  name: string,
): Promise<RuntimeConfigResponse> {
  return apiFetch<RuntimeConfigResponse>(
    `/butlers/${encodeURIComponent(name)}/runtime-config`,
  );
}

/** Partially update runtime config for a butler. */
export function patchRuntimeConfig(
  name: string,
  body: RuntimeConfigPatch,
): Promise<RuntimeConfigPatchResponse> {
  return apiFetch<RuntimeConfigPatchResponse>(
    `/butlers/${encodeURIComponent(name)}/runtime-config`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

// ---------------------------------------------------------------------------
// Chronicler
// ---------------------------------------------------------------------------

/** Fetch paginated chronicler episodes. Defaults: include_tombstoned=false. */
export function getChroniclerEpisodes(
  params?: ChroniclerEpisodesParams,
): Promise<{ data: ChroniclerEpisode[]; meta: { total: number; offset: number; limit: number; has_more: boolean } }> {
  const sp = new URLSearchParams();
  if (params?.source_name) sp.set("source_name", params.source_name);
  if (params?.episode_type) sp.set("episode_type", params.episode_type);
  if (params?.start_from) sp.set("start_from", params.start_from);
  if (params?.start_to) sp.set("start_to", params.start_to);
  if (params?.overlaps_start) sp.set("overlaps_start", params.overlaps_start);
  if (params?.overlaps_end) sp.set("overlaps_end", params.overlaps_end);
  if (params?.include_tombstoned != null)
    sp.set("include_tombstoned", String(params.include_tombstoned));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch(qs ? `/chronicler/episodes?${qs}` : "/chronicler/episodes");
}

/** Fetch category aggregates for a time window. Restricted excluded by default. */
export function getChroniclerAggregateByCategory(
  params: ChroniclerAggregateByCategoryParams,
): Promise<{ data: ChroniclerCategoryBuckets; meta: Record<string, unknown> }> {
  const sp = new URLSearchParams({ start_at: params.start_at, end_at: params.end_at });
  if (params.tz) sp.set("tz", params.tz);
  if (params.privacy_tier) sp.set("privacy_tier", params.privacy_tier);
  if (params.include_tombstoned != null)
    sp.set("include_tombstoned", String(params.include_tombstoned));
  return apiFetch(`/chronicler/aggregate/by-category?${sp.toString()}`);
}

/** Fetch time-bucketed episode durations grouped by (day, category). */
export function getChroniclerAggregateByDay(
  params: ChroniclerAggregateByDayParams,
): Promise<ChroniclerAggregateByDayRow[]> {
  const sp = new URLSearchParams({ start_at: params.start_at, end_at: params.end_at });
  if (params.tz) sp.set("tz", params.tz);
  if (params.category) sp.set("category", params.category);
  if (params.privacy_tier) sp.set("privacy_tier", params.privacy_tier);
  if (params.include_tombstoned != null)
    sp.set("include_tombstoned", String(params.include_tombstoned));
  return apiFetch(`/chronicler/aggregate/by-day?${sp.toString()}`);
}

/** Fetch source adapter state joined with projection checkpoints (singleton, sorted by source_name). */
export function getChroniclerSourceState(): Promise<{ data: ChroniclerSourceStateRow[]; meta: Record<string, unknown> }> {
  return apiFetch("/chronicler/source-state");
}

/**
 * Fetch the day-close cache entry for a window.
 * Returns fresh prose or a stale marker. 404 if no cache entry exists.
 */
export function getChroniclerDayClose(
  params: ChroniclerDayCloseParams,
): Promise<ChroniclerDayCloseResponse> {
  const sp = new URLSearchParams({
    window_start: params.window_start,
    window_end: params.window_end,
  });
  return apiFetch(`/chronicler/aggregate/day-close?${sp.toString()}`);
}

/** Fetch a single Chronicler episode by ID (corrected view). 404 if not found. */
export function getChroniclerEpisode(episodeId: string): Promise<ChroniclerEpisode> {
  return apiFetch(`/chronicler/episodes/${encodeURIComponent(episodeId)}`);
}

/**
 * Fetch point events linked to an episode.
 * Returns an empty list if the episode has no linked events.
 * 404 if the episode does not exist.
 */
export function getChroniclerEpisodeEvents(episodeId: string): Promise<ChroniclerPointEvent[]> {
  return apiFetch(`/chronicler/episodes/${encodeURIComponent(episodeId)}/events`);
}

/**
 * Fetch the correction history for an episode, sorted by created_at DESC.
 * Returns an empty list if no corrections exist.
 * 404 if the episode does not exist.
 */
export function getChroniclerEpisodeCorrections(
  episodeId: string,
): Promise<ChroniclerOverride[]> {
  return apiFetch(`/chronicler/episodes/${encodeURIComponent(episodeId)}/corrections`);
}

/**
 * Trigger a day-close Tier-2 refresh (rate-limited: once per 24 h per date).
 *
 * Returns 429 with code "day_close_rate_limited" when called too soon after
 * the last refresh. The caller should check `error.status === 429` and disable
 * the Explain button accordingly.
 *
 * Returns 503 when the in-process spawner is not wired (standalone/test mode).
 */
export function postChroniclerDayCloseRefresh(
  body: ChroniclerDayCloseRefreshRequest,
): Promise<ChroniclerDayCloseRefreshResponse> {
  return apiFetch("/chronicler/aggregate/day-close/refresh", {
    method: "POST",
    body: JSON.stringify(body),
  });
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
): Promise<ChroniclerEpisodeExplainResponse> {
  return apiFetch(`/chronicler/episodes/${encodeURIComponent(episodeId)}/explain`, {
    method: "POST",
  });
}

/** Fetch paginated Chronicler point events. Defaults: include_tombstoned=false. */
export function getChroniclerEvents(
  params?: ChroniclerEventsParams,
): Promise<{ data: ChroniclerPointEvent[]; meta: { total: number; offset: number; limit: number; has_more: boolean } }> {
  const sp = new URLSearchParams();
  if (params?.source_name) sp.set("source_name", params.source_name);
  if (params?.event_type) sp.set("event_type", params.event_type);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.include_tombstoned != null)
    sp.set("include_tombstoned", String(params.include_tombstoned));
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch(qs ? `/chronicler/events?${qs}` : "/chronicler/events");
}

// ---------------------------------------------------------------------------
// System endpoints — GET /api/system/*
// ---------------------------------------------------------------------------

/** Fetch software version, process uptime, and start timestamp. */
export function getInstanceFacts(): Promise<ApiResponse<InstanceFacts>> {
  return apiFetch<ApiResponse<InstanceFacts>>("/system/instance");
}

/** Fetch PostgreSQL catalog size facts: total size, per-schema breakdown, largest tables. */
export function getDatabaseFacts(): Promise<ApiResponse<DatabaseFacts>> {
  return apiFetch<ApiResponse<DatabaseFacts>>("/system/database");
}

/** Fetch backup recency and source reachability. Degrades gracefully (never 503). */
export function getBackupFacts(): Promise<ApiResponse<BackupFacts>> {
  return apiFetch<ApiResponse<BackupFacts>>("/system/backups");
}

/**
 * Fetch data-egress catalog (owner-only).
 *
 * Returns HTTP 403 for non-owner callers. Callers should handle `ApiError`
 * with `status === 403` gracefully rather than treating it as an unexpected error.
 */
export function getEgressCatalog(): Promise<ApiResponse<EgressCatalog>> {
  return apiFetch<ApiResponse<EgressCatalog>>("/system/egress");
}

/** Fetch per-butler liveness registry snapshots and session facts. */
export function getButlerHeartbeats(): Promise<ApiResponse<HeartbeatFacts>> {
  return apiFetch<ApiResponse<HeartbeatFacts>>("/system/butlers/heartbeat");
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
export function getDashboardBriefing(): Promise<Briefing> {
  return apiFetch<Briefing>("/dashboard/briefing");
}

// ---------------------------------------------------------------------------
// Chronicles editorial briefing (bu-i29ix)
// GET /api/chronicler/briefing | /attention | /kpi
// ---------------------------------------------------------------------------

interface ChroniclesEditorialParams {
  date?: string;
  tz?: string;
}

function _chroniclesQs(params: ChroniclesEditorialParams | undefined): string {
  const sp = new URLSearchParams();
  if (params?.date) sp.set("date", params.date);
  if (params?.tz) sp.set("tz", params.tz);
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

export function getChroniclesBriefing(
  params?: ChroniclesEditorialParams,
): Promise<ChroniclesBriefing> {
  return apiFetch<ChroniclesBriefing>(`/chronicler/briefing${_chroniclesQs(params)}`);
}

export function getChroniclesAttention(
  params?: ChroniclesEditorialParams,
): Promise<{ data: ChroniclesAttentionItem[]; meta?: Record<string, unknown> }> {
  return apiFetch(`/chronicler/attention${_chroniclesQs(params)}`);
}

export function getChroniclesKpi(
  params?: ChroniclesEditorialParams,
): Promise<{ data: ChroniclesKpi; meta?: Record<string, unknown> }> {
  return apiFetch(`/chronicler/kpi${_chroniclesQs(params)}`);
}

// ---------------------------------------------------------------------------
// Finance butler (GET /api/finance/*)
// ---------------------------------------------------------------------------

/** List transactions with optional filters. */
export function getFinanceTransactions(
  params?: FinanceTransactionListParams,
): Promise<PaginatedResponse<FinanceTransaction>> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.merchant) sp.set("merchant", params.merchant);
  if (params?.since) sp.set("since", params.since);
  if (params?.until) sp.set("until", params.until);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<FinanceTransaction>>(
    qs ? `/finance/transactions?${qs}` : "/finance/transactions",
  );
}

/** List subscriptions with optional status filter. */
export function getFinanceSubscriptions(
  params?: FinanceSubscriptionListParams,
): Promise<PaginatedResponse<FinanceSubscription>> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<FinanceSubscription>>(
    qs ? `/finance/subscriptions?${qs}` : "/finance/subscriptions",
  );
}

/** List upcoming bills with urgency classification. */
export function getFinanceUpcomingBills(
  params?: FinanceUpcomingBillsParams,
): Promise<FinanceUpcomingBillsResponse> {
  const sp = new URLSearchParams();
  if (params?.days_ahead != null) sp.set("days_ahead", String(params.days_ahead));
  if (params?.include_overdue != null) sp.set("include_overdue", String(params.include_overdue));
  const qs = sp.toString();
  return apiFetch<FinanceUpcomingBillsResponse>(
    qs ? `/finance/upcoming-bills?${qs}` : "/finance/upcoming-bills",
  );
}

/** Aggregate spending summary over a date range. */
export function getFinanceSpendingSummary(
  params?: FinanceSpendingSummaryParams,
): Promise<FinanceSpendingSummary> {
  const sp = new URLSearchParams();
  if (params?.start_date) sp.set("start_date", params.start_date);
  if (params?.end_date) sp.set("end_date", params.end_date);
  if (params?.group_by) sp.set("group_by", params.group_by);
  const qs = sp.toString();
  return apiFetch<FinanceSpendingSummary>(
    qs ? `/finance/spending-summary?${qs}` : "/finance/spending-summary",
  );
}

/** List bills with optional status and payee filters. */
export function getFinanceBills(
  params?: FinanceBillListParams,
): Promise<PaginatedResponse<FinanceBill>> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.payee) sp.set("payee", params.payee);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<FinanceBill>>(
    qs ? `/finance/bills?${qs}` : "/finance/bills",
  );
}

// ---------------------------------------------------------------------------
// Travel butler endpoints (bu-0eac9)
// GET /api/travel/trips | /trips/{id} | /upcoming
// ---------------------------------------------------------------------------

/** List trips with optional status and date range filters, paginated. */
export function getTravelTrips(
  params?: TravelTripsParams,
): Promise<PaginatedResponse<TravelTrip>> {
  const sp = new URLSearchParams();
  if (params?.status) sp.set("status", params.status);
  if (params?.from_date) sp.set("from_date", params.from_date);
  if (params?.to_date) sp.set("to_date", params.to_date);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<TravelTrip>>(qs ? `/travel/trips?${qs}` : "/travel/trips");
}

/** Fetch full trip summary (legs, accommodations, reservations, docs, timeline, alerts). */
export function getTravelTripSummary(tripId: string): Promise<TravelTripSummary> {
  return apiFetch<TravelTripSummary>(`/travel/trips/${encodeURIComponent(tripId)}`);
}

/** Fetch upcoming travel with urgency-ranked pre-trip action items. */
export function getTravelUpcoming(withinDays?: number): Promise<TravelUpcomingModel> {
  const qs = withinDays != null ? `?within_days=${withinDays}` : "";
  return apiFetch<TravelUpcomingModel>(`/travel/upcoming${qs}`);
}

/** Fetch documents expiring within the given look-ahead window (default: 180 days). */
export function getTravelExpiringDocuments(
  days?: number,
): Promise<TravelExpiringDocumentsResponse> {
  const qs = days != null ? `?days=${days}` : "";
  return apiFetch<TravelExpiringDocumentsResponse>(`/travel/documents/expiring${qs}`);
}

// ---------------------------------------------------------------------------
// Home butler endpoints
// ---------------------------------------------------------------------------

export function getHomeSnapshotStatus(): Promise<HomeSnapshotStatus> {
  return apiFetch<HomeSnapshotStatus>("/home/snapshot-status");
}

export function getHomeDevices(params?: {
  domain?: string;
  area?: string;
  health?: "healthy" | "offline";
  page?: number;
  page_size?: number;
}): Promise<HomeDeviceInventoryResponse> {
  const sp = new URLSearchParams();
  if (params?.domain) sp.set("domain", params.domain);
  if (params?.area) sp.set("area", params.area);
  if (params?.health) sp.set("health", params.health);
  if (params?.page != null) sp.set("page", String(params.page));
  if (params?.page_size != null) sp.set("page_size", String(params.page_size));
  const qs = sp.toString();
  return apiFetch<HomeDeviceInventoryResponse>(`/home/devices${qs ? `?${qs}` : ""}`);
}

export function getHomeMaintenance(params?: {
  category?: string;
  status?: "overdue" | "due" | "upcoming" | "ok";
}): Promise<HomeMaintenanceItem[]> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.status) sp.set("status", params.status);
  const qs = sp.toString();
  return apiFetch<HomeMaintenanceItem[]>(`/home/maintenance${qs ? `?${qs}` : ""}`);
}

export function getHomeEnergy(params?: {
  period?: "day" | "hour";
  start?: string;
  end?: string;
}): Promise<HomeEnergyDataPoint[]> {
  const sp = new URLSearchParams();
  if (params?.period) sp.set("period", params.period);
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  const qs = sp.toString();
  return apiFetch<HomeEnergyDataPoint[]>(`/home/energy${qs ? `?${qs}` : ""}`);
}

export function getHomeEnergyTopConsumers(params?: {
  start?: string;
  end?: string;
}): Promise<HomeTopConsumer[]> {
  const sp = new URLSearchParams();
  if (params?.start) sp.set("start", params.start);
  if (params?.end) sp.set("end", params.end);
  const qs = sp.toString();
  return apiFetch<HomeTopConsumer[]>(`/home/energy/top-consumers${qs ? `?${qs}` : ""}`);
}

export function getHomeCommandLog(params?: {
  limit?: number;
  domain?: string;
}): Promise<{ data: HomeCommandLogEntry[]; meta?: Record<string, unknown> }> {
  const sp = new URLSearchParams();
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.domain) sp.set("domain", params.domain);
  const qs = sp.toString();
  return apiFetch<{ data: HomeCommandLogEntry[]; meta?: Record<string, unknown> }>(
    `/home/command-log${qs ? `?${qs}` : ""}`,
  );
}
