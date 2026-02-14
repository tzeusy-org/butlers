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
  ExpireStaleActionsResponse,
  RuleConstraintSuggestion,
  ActivityFeedItem,
  ApiResponse,
  AuditEntry,
  AuditLogParams,
  ButlerConfigResponse,
  ButlerSkill,
  ButlerSummary,
  ContactDetail,
  ContactListResponse,
  ContactParams,
  CostSummary,
  DailyCost,
  ErrorResponse,
  Gift,
  Group,
  GroupListResponse,
  GroupParams,
  HealthResponse,
  Interaction,
  Issue,
  Label,
  Loan,
  Note,
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
  TraceDetail,
  TraceParams,
  TraceSummary,
  TriggerResponse,
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
  EntityParams,
  GeneralCollection,
  GeneralEntity,
  RegistryEntry,
  RoutingEntry,
  RoutingLogParams,
  UpcomingDate,
  Episode,
  EpisodeParams,
  Fact,
  FactParams,
  MemoryActivity,
  MemoryRule,
  MemoryStats,
  RuleParams,
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
      const body = (await response.json()) as ErrorResponse;
      if (body.error) {
        code = body.error.code;
        message = body.error.message;
      }
    } catch {
      // Response body is not valid JSON — fall through to defaults.
    }

    throw new ApiError(code, message, response.status);
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
export function getButler(name: string): Promise<ApiResponse<ButlerSummary>> {
  return apiFetch<ApiResponse<ButlerSummary>>(`/butlers/${encodeURIComponent(name)}`);
}

/** Fetch configuration files for a specific butler. */
export function getButlerConfig(name: string): Promise<ApiResponse<ButlerConfigResponse>> {
  return apiFetch<ApiResponse<ButlerConfigResponse>>(
    `/butlers/${encodeURIComponent(name)}/config`,
  );
}

/** Build a URLSearchParams from session query parameters. */
function sessionSearchParams(params?: SessionParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.butler != null && params.butler !== "") sp.set("butler", params.butler);
  if (params?.trigger_source != null && params.trigger_source !== "")
    sp.set("trigger_source", params.trigger_source);
  if (params?.status != null && params.status !== "all") sp.set("status", params.status);
  if (params?.since != null && params.since !== "") sp.set("since", params.since);
  if (params?.until != null && params.until !== "") sp.set("until", params.until);
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

/** Build a URLSearchParams from notification query parameters. */
function notificationSearchParams(params?: NotificationParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  if (params?.butler != null) sp.set("butler", params.butler);
  if (params?.channel != null) sp.set("channel", params.channel);
  if (params?.status != null) sp.set("status", params.status);
  if (params?.since != null) sp.set("since", params.since);
  if (params?.until != null) sp.set("until", params.until);
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

/** Fetch active issues across all butlers. */
export function getIssues(): Promise<ApiResponse<Issue[]>> {
  return apiFetch<ApiResponse<Issue[]>>("/issues");
}

// ---------------------------------------------------------------------------
// Costs
// ---------------------------------------------------------------------------

/** Fetch aggregate cost summary, optionally scoped to a time period. */
export function getCostSummary(period?: string): Promise<ApiResponse<CostSummary>> {
  const params = period ? `?period=${period}` : "";
  return apiFetch<ApiResponse<CostSummary>>(`/costs/summary${params}`);
}

/** Fetch daily cost breakdown. */
export function getDailyCosts(): Promise<ApiResponse<DailyCost[]>> {
  return apiFetch<ApiResponse<DailyCost[]>>("/costs/daily");
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
): Promise<TriggerResponse> {
  return apiFetch<TriggerResponse>(
    `/butlers/${encodeURIComponent(name)}/trigger`,
    {
      method: "POST",
      body: JSON.stringify({ prompt }),
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
// Traces
// ---------------------------------------------------------------------------

/** Fetch a paginated list of traces across all butlers. */
export function getTraces(
  params?: TraceParams,
): Promise<PaginatedResponse<TraceSummary>> {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const path = qs ? `/traces?${qs}` : "/traces";
  return apiFetch<PaginatedResponse<TraceSummary>>(path);
}

/** Fetch a single trace by ID. */
export function getTrace(traceId: string): Promise<ApiResponse<TraceDetail>> {
  return apiFetch<ApiResponse<TraceDetail>>(
    `/traces/${encodeURIComponent(traceId)}`,
  );
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
// Relationship / CRM
// ---------------------------------------------------------------------------

/** Build URLSearchParams from contact query parameters. */
function contactSearchParams(params?: ContactParams): URLSearchParams {
  const sp = new URLSearchParams();
  if (params?.q != null && params.q !== "") sp.set("q", params.q);
  if (params?.label != null && params.label !== "") sp.set("label", params.label);
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

/** Fetch a single contact by ID. */
export function getContact(contactId: string): Promise<ContactDetail> {
  return apiFetch<ContactDetail>(
    `/relationship/contacts/${encodeURIComponent(contactId)}`,
  );
}

/** Fetch notes for a contact. */
export function getContactNotes(contactId: string): Promise<Note[]> {
  return apiFetch<Note[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/notes`,
  );
}

/** Fetch interactions for a contact. */
export function getContactInteractions(contactId: string): Promise<Interaction[]> {
  return apiFetch<Interaction[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/interactions`,
  );
}

/** Fetch gifts for a contact. */
export function getContactGifts(contactId: string): Promise<Gift[]> {
  return apiFetch<Gift[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/gifts`,
  );
}

/** Fetch loans for a contact. */
export function getContactLoans(contactId: string): Promise<Loan[]> {
  return apiFetch<Loan[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/loans`,
  );
}

/** Fetch activity feed for a contact. */
export function getContactFeed(contactId: string): Promise<ActivityFeedItem[]> {
  return apiFetch<ActivityFeedItem[]>(
    `/relationship/contacts/${encodeURIComponent(contactId)}/feed`,
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

/** Fetch a paginated list of collections. */
export function getCollections(
  params?: { offset?: number; limit?: number },
): Promise<PaginatedResponse<GeneralCollection>> {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<GeneralCollection>>(
    qs ? `/general/collections?${qs}` : "/general/collections",
  );
}

/** Fetch entities within a specific collection. */
export function getCollectionEntities(
  collectionId: string,
  params?: { offset?: number; limit?: number },
): Promise<PaginatedResponse<GeneralEntity>> {
  const sp = new URLSearchParams();
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  const base = `/general/collections/${encodeURIComponent(collectionId)}/entities`;
  return apiFetch<PaginatedResponse<GeneralEntity>>(qs ? `${base}?${qs}` : base);
}

/** Fetch a paginated list of entities with optional search/filter. */
export function getEntities(
  params?: EntityParams,
): Promise<PaginatedResponse<GeneralEntity>> {
  const sp = new URLSearchParams();
  if (params?.q) sp.set("q", params.q);
  if (params?.collection) sp.set("collection", params.collection);
  if (params?.tag) sp.set("tag", params.tag);
  if (params?.offset != null) sp.set("offset", String(params.offset));
  if (params?.limit != null) sp.set("limit", String(params.limit));
  const qs = sp.toString();
  return apiFetch<PaginatedResponse<GeneralEntity>>(
    qs ? `/general/entities?${qs}` : "/general/entities",
  );
}

/** Fetch a single entity by ID. */
export function getEntity(
  entityId: string,
): Promise<ApiResponse<GeneralEntity>> {
  return apiFetch<ApiResponse<GeneralEntity>>(
    `/general/entities/${encodeURIComponent(entityId)}`,
  );
}

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
