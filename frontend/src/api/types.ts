/**
 * TypeScript interfaces matching the backend Pydantic models
 * defined in src/butlers/api/models/__init__.py.
 */

// ---------------------------------------------------------------------------
// Base response wrappers
// ---------------------------------------------------------------------------

/** Extensible metadata bag attached to every API response. */
export interface ApiMeta {
  [key: string]: unknown;
}

/** Generic API response wrapper: { data: T, meta: {...} } */
export interface ApiResponse<T> {
  data: T;
  meta: ApiMeta;
}

/** Structured error payload. */
export interface ErrorDetail {
  code: string;
  message: string;
  butler?: string | null;
  details?: Record<string, unknown> | null;
}

/** Standard error response envelope. */
export interface ErrorResponse {
  error: ErrorDetail;
}

// ---------------------------------------------------------------------------
// Pagination
// ---------------------------------------------------------------------------

/** Pagination metadata for list endpoints. */
export interface PaginationMeta {
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

/** API response wrapper for paginated list endpoints. */
export interface PaginatedResponse<T> {
  data: T[];
  meta: PaginationMeta;
}

// ---------------------------------------------------------------------------
// Domain summaries
// ---------------------------------------------------------------------------

/** Lightweight butler representation for list views. */
export interface ButlerSummary {
  name: string;
  status: string;
  port: number;
  /** Agent type: "butler" (user-facing) or "staffer" (infrastructure). */
  type: "butler" | "staffer";
  /** Short description from the butler's config. Absent when not configured. */
  description?: string | null;
  /** Number of sessions started in the last 24 hours. Always present; 0 when none. */
  sessions_24h: number;
  /** ISO-8601 timestamp of the most recent session start. Null when no sessions exist. */
  last_session_started_at?: string | null;
}

/**
 * Container-boundary-safe process facts for the butler Overview tab.
 * `pid` is intentionally absent.
 */
export interface ProcessFacts {
  /** Docker service or container name derived from BUTLERS_HOST. Null when running locally. */
  container_name: string | null;
  /** Butler MCP port. */
  port: number;
  /** Seconds elapsed since the butler first registered in the switchboard. Null when unavailable. */
  registered_duration_seconds: number | null;
  /** Roster-relative config path, e.g. "roster/general/butler.toml". */
  config_path: string;
}

/** Per-module health status returned by GET /api/butlers/:name/modules. */
export interface ModuleStatus {
  name: string;
  enabled: boolean;
  status: string;
  phase?: string | null;
  error?: string | null;
}

/** Extended butler representation returned by GET /api/butlers/:name. */
export interface ButlerDetail extends ButlerSummary {
  db_name?: string | null;
  db_schema?: string | null;
  modules: { name: string; enabled: boolean; config?: Record<string, unknown> | null }[];
  schedules: { name: string; cron: string; prompt?: string | null }[];
  skills: string[];
  /** Process facts card data for the Overview tab. Null when detail extension is unavailable. */
  process_facts?: ProcessFacts | null;
}

/** Butler configuration files returned by GET /api/butlers/:name/config. */
export interface ButlerConfigResponse {
  butler_toml: Record<string, unknown>;
  claude_md: string | null;
  agents_md: string | null;
  manifesto_md: string | null;
}

/** Lightweight session representation for list views. */
export interface SessionSummary {
  id: string;
  butler?: string;
  prompt: string;
  trigger_source: string;
  request_id?: string | null;
  success: boolean | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  model?: string | null;
  complexity?: string | null;
}

/** Full session detail returned by the single-session endpoint. */
export interface SessionDetail {
  id: string;
  butler: string;
  prompt: string;
  trigger_source: string;
  result: string | null;
  tool_calls: unknown[];
  duration_ms: number | null;
  trace_id: string | null;
  request_id: string | null;
  cost: Record<string, unknown> | null;
  started_at: string;
  completed_at: string | null;
  success: boolean | null;
  error: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  parent_session_id: string | null;
  complexity?: string | null;
  resolution_source?: string | null;
  process_log?: {
    pid?: number | null;
    exit_code?: number | null;
    command?: string | null;
    stderr?: string | null;
    runtime_type?: string | null;
    created_at?: string | null;
    expires_at?: string | null;
  } | null;
}

/** Query parameters for session list endpoints. */
export interface SessionParams {
  offset?: number;
  limit?: number;
  butler?: string;
  trigger_source?: string;
  request_id?: string;
  status?: string; // "all" | "success" | "failed"
  since?: string;
  until?: string;
}

/** Lightweight notification representation for list views. */
export interface NotificationSummary {
  id: string;
  source_butler: string;
  channel: string;
  recipient: string | null;
  message: string;
  metadata: Record<string, unknown> | null;
  status: string;
  effective_status: string | null;
  error: string | null;
  session_id: string | null;
  trace_id: string | null;
  created_at: string;
}

/** Health-check response. */
export interface HealthResponse {
  status: string;
}

// ---------------------------------------------------------------------------
// Notifications
// ---------------------------------------------------------------------------

/** Aggregate notification statistics. */
export interface NotificationStats {
  total: number;
  sent: number;
  failed: number;
  by_channel: Record<string, number>;
  by_butler: Record<string, number>;
}

/** Query parameters for notification list endpoints. */
export interface NotificationParams {
  offset?: number;
  limit?: number;
  butler?: string;
  channel?: string;
  status?: string;
  since?: string;
  until?: string;
}

// ---------------------------------------------------------------------------
// Issues
// ---------------------------------------------------------------------------

/** Active issue detected across butler infrastructure. */
export interface Issue {
  severity: string;
  type: string;
  butler: string;
  description: string;
  link: string | null;
  error_message?: string | null;
  occurrences?: number;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  butlers?: string[];
}

// ---------------------------------------------------------------------------
// Activity / Timeline
// ---------------------------------------------------------------------------

/** A timeline event from the activity feed. */
export interface ActivityEvent {
  id: string;
  butler: string;
  type: string; // "session", "schedule", "notification", "startup", etc.
  summary: string;
  timestamp: string; // ISO 8601
  task_name?: string;
}

/** A unified timeline event from GET /api/timeline. */
export interface TimelineEvent {
  id: string;
  type: string; // "session", "error", "notification", etc.
  butler: string;
  timestamp: string; // ISO 8601
  summary: string;
  data: Record<string, unknown>;
}

/** Cursor-based pagination metadata for the timeline endpoint. */
export interface TimelineMeta {
  cursor: string | null;
  has_more: boolean;
}

/** Response shape from GET /api/timeline. */
export interface TimelineResponse {
  data: TimelineEvent[];
  meta: TimelineMeta;
}

/** Query parameters for the timeline endpoint. */
export interface TimelineParams {
  limit?: number;
  butler?: string[];
  event_type?: string[];
  before?: string;
}

// ---------------------------------------------------------------------------
// Costs
// ---------------------------------------------------------------------------

/** Aggregate cost summary across all butlers. */
export interface CostSummary {
  total_cost_usd: number;
  total_sessions: number;
  total_input_tokens: number;
  total_output_tokens: number;
  by_butler: Record<string, number>;
  by_model: Record<string, number>;
}

/** Cost data for a single day. */
export interface DailyCost {
  date: string;
  cost_usd: number;
  sessions: number;
  input_tokens: number;
  output_tokens: number;
}

/** A session ranked by cost. */
export interface TopSession {
  session_id: string;
  butler: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  model: string;
  started_at: string;
}

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

/** A scheduled task belonging to a butler. */
export type ScheduleDispatchMode = "prompt" | "job";

/** Shared job arguments payload shape for deterministic schedule mode. */
export type ScheduleJobArgs = Record<string, unknown>;

/** A scheduled task belonging to a butler. */
export interface Schedule {
  id: string;
  name: string;
  cron: string;
  prompt: string | null;
  dispatch_mode?: ScheduleDispatchMode | null;
  job_name?: string | null;
  job_args?: ScheduleJobArgs | null;
  complexity?: string | null;
  source: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Payload for creating a new schedule. */
export interface PromptScheduleCreate {
  name: string;
  cron: string;
  dispatch_mode?: "prompt";
  prompt: string;
  complexity?: string;
}

/** Payload for creating a new deterministic job schedule. */
export interface JobScheduleCreate {
  name: string;
  cron: string;
  dispatch_mode: "job";
  job_name: string;
  job_args?: ScheduleJobArgs;
  complexity?: string;
}

/** Payload for creating a schedule (prompt or deterministic job mode). */
export type ScheduleCreate = PromptScheduleCreate | JobScheduleCreate;

/** Payload for updating an existing schedule (all fields optional). */
export interface ScheduleUpdate {
  name?: string;
  cron?: string;
  prompt?: string | null;
  dispatch_mode?: ScheduleDispatchMode;
  job_name?: string | null;
  job_args?: ScheduleJobArgs | null;
  complexity?: string | null;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Calendar workspace
// ---------------------------------------------------------------------------

/** Workspace mode toggle for /butlers/calendar. */
export type CalendarWorkspaceView = "user" | "butler";

/** Unified source categories for calendar entries. */
export type UnifiedCalendarSourceType =
  | "provider_event"
  | "scheduled_task"
  | "butler_reminder"
  | "manual_butler_event";

/** Freshness state returned by workspace source metadata. */
export type CalendarWorkspaceSyncState = "fresh" | "stale" | "syncing" | "failed";

/** Normalized event row returned by GET /api/calendar/workspace. */
export interface UnifiedCalendarEntry {
  entry_id: string;
  view: CalendarWorkspaceView;
  source_type: UnifiedCalendarSourceType;
  source_key: string;
  title: string;
  start_at: string;
  end_at: string;
  timezone: string;
  all_day: boolean;
  calendar_id: string | null;
  provider_event_id: string | null;
  butler_name: string | null;
  schedule_id: string | null;
  reminder_id: string | null;
  rrule: string | null;
  cron: string | null;
  until_at: string | null;
  status: string;
  sync_state: CalendarWorkspaceSyncState | null;
  editable: boolean;
  metadata: Record<string, unknown>;
}

/** Source-level freshness metadata for workspace rendering. */
export interface CalendarWorkspaceSourceFreshness {
  source_id: string;
  source_key: string;
  source_kind: string;
  lane: CalendarWorkspaceView;
  provider: string | null;
  calendar_id: string | null;
  butler_name: string | null;
  display_name: string | null;
  writable: boolean;
  metadata: Record<string, unknown>;
  cursor_name: string | null;
  last_synced_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  full_sync_required: boolean;
  sync_state: CalendarWorkspaceSyncState;
  staleness_ms: number | null;
}

/** Butler lane descriptor used by butler-view layouts. */
export interface CalendarWorkspaceLaneDefinition {
  lane_id: string;
  butler_name: string;
  title: string;
  source_keys: string[];
}

/** Response payload for GET /api/calendar/workspace. */
export interface CalendarWorkspaceReadResponse {
  entries: UnifiedCalendarEntry[];
  source_freshness: CalendarWorkspaceSourceFreshness[];
  lanes: CalendarWorkspaceLaneDefinition[];
}

/** Sync capability flags in workspace metadata. */
export interface CalendarWorkspaceCapabilitiesSync {
  global: boolean;
  by_source: boolean;
}

/** Workspace capability switches. */
export interface CalendarWorkspaceCapabilities {
  views: CalendarWorkspaceView[];
  filters: Record<string, boolean>;
  sync: CalendarWorkspaceCapabilitiesSync;
}

/** Writable user-lane calendar descriptor. */
export interface CalendarWorkspaceWritableCalendar {
  source_key: string;
  provider: string | null;
  calendar_id: string;
  display_name: string | null;
  butler_name: string | null;
}

/** Response payload for GET /api/calendar/workspace/meta. */
export interface CalendarWorkspaceMetaResponse {
  capabilities: CalendarWorkspaceCapabilities;
  connected_sources: CalendarWorkspaceSourceFreshness[];
  writable_calendars: CalendarWorkspaceWritableCalendar[];
  lane_definitions: CalendarWorkspaceLaneDefinition[];
  default_timezone: string;
  primary_calendar_id: string | null;
}

/** Request payload for PUT /api/calendar/workspace/primary. */
export interface SetPrimaryCalendarRequest {
  butler_name: string;
  calendar_id: string;
}

/** Response payload for PUT /api/calendar/workspace/primary. */
export interface SetPrimaryCalendarResponse {
  old_calendar_id: string | null;
  new_calendar_id: string;
  persisted: boolean;
}

/** Query parameters for GET /api/calendar/workspace. */
export interface CalendarWorkspaceParams {
  view: CalendarWorkspaceView;
  start: string;
  end: string;
  timezone?: string;
  butlers?: string[];
  sources?: string[];
}

/** Request payload for POST /api/calendar/workspace/sync. */
export interface CalendarWorkspaceSyncRequest {
  all?: boolean;
  source_key?: string;
  source_id?: string;
  butler?: string;
}

/** One sync trigger attempt result. */
export interface CalendarWorkspaceSyncTarget {
  butler_name: string;
  source_key: string | null;
  calendar_id: string | null;
  status: string;
  detail: string | null;
  error: string | null;
}

/** Response payload for POST /api/calendar/workspace/sync. */
export interface CalendarWorkspaceSyncResponse {
  scope: "all" | "source";
  requested_source_key: string | null;
  requested_source_id: string | null;
  targets: CalendarWorkspaceSyncTarget[];
  triggered_count: number;
}

/** Allowed mutation actions for user-view calendar events. */
export type CalendarWorkspaceUserMutationAction = "create" | "update" | "delete";

/** Allowed actions for butler-lane event mutations. */
export type CalendarWorkspaceButlerMutationAction =
  | "create"
  | "update"
  | "delete"
  | "toggle";

/** Request payload for POST /api/calendar/workspace/user-events. */
export interface CalendarWorkspaceUserMutationRequest {
  butler_name: string;
  action: CalendarWorkspaceUserMutationAction;
  request_id?: string;
  payload: Record<string, unknown>;
}

/** Response payload for calendar workspace mutation endpoints. */
export interface CalendarWorkspaceMutationResponse {
  action: CalendarWorkspaceUserMutationAction | CalendarWorkspaceButlerMutationAction;
  tool_name: string;
  request_id: string | null;
  result: Record<string, unknown>;
  projection_version: string | null;
  staleness_ms: number | null;
  projection_freshness: Record<string, unknown> | null;
}

/** Request payload for POST /api/calendar/workspace/butler-events. */
export interface CalendarWorkspaceButlerMutationRequest {
  butler_name: string;
  action: CalendarWorkspaceButlerMutationAction;
  request_id?: string;
  payload: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** A key-value state entry from a butler's state store.
 *
 * ``value`` can be any JSON-serialisable type (object, array, scalar, or null)
 * because the underlying JSONB column places no shape restrictions on stored
 * values.
 */
export interface StateEntry {
  key: string;
  value: unknown;
  updated_at: string; // ISO 8601
}

/** Request body for setting a state value.
 *
 * ``value`` accepts any JSON-serialisable type, matching the same contract as
 * ``StateEntry.value``.
 */
export interface StateSetRequest {
  value: unknown;
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

/** A single search result from the global search endpoint. */
export interface SearchResult {
  id: string;
  butler: string;
  type: string;
  title: string;
  snippet: string;
  url: string;
}

/** Grouped search results keyed by category. */
export interface SearchResults {
  entities: SearchResult[];
  contacts: SearchResult[];
  sessions: SearchResult[];
  state: SearchResult[];
  [key: string]: SearchResult[];
}

/** Query parameters for the search endpoint. */
export interface SearchParams {
  q: string;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Audit Log
// ---------------------------------------------------------------------------

/** A single audit log entry. */
export interface AuditEntry {
  id: string;
  butler: string;
  operation: string;
  request_summary: Record<string, unknown>;
  result: string; // "success" | "error"
  error: string | null;
  user_context: Record<string, unknown>;
  created_at: string; // ISO 8601
}

/** Query parameters for the audit log endpoint. */
export interface AuditLogParams {
  offset?: number;
  limit?: number;
  butler?: string;
  operation?: string;
  since?: string;
  until?: string;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

/** A skill available to a butler. */
export interface ButlerSkill {
  name: string;
  content: string;
}

// ---------------------------------------------------------------------------
// Trigger
// ---------------------------------------------------------------------------

/** Response from triggering a butler CC session. */
export interface TriggerResponse {
  session_id: string;
  success: boolean;
  output: string;
}

// ---------------------------------------------------------------------------
// MCP debugging
// ---------------------------------------------------------------------------

/** A tool exposed by a butler's MCP server. */
export interface ButlerMcpTool {
  name: string;
  description: string | null;
  input_schema: Record<string, unknown> | null;
}

/** Request body for calling an MCP tool. */
export interface ButlerMcpToolCallRequest {
  tool_name: string;
  arguments?: Record<string, unknown>;
}

/** Response from calling an MCP tool. */
export interface ButlerMcpToolCallResponse {
  tool_name: string;
  arguments: Record<string, unknown>;
  result: unknown;
  raw_text: string | null;
  is_error: boolean;
}

// ---------------------------------------------------------------------------
// Relationship / CRM
// ---------------------------------------------------------------------------

/** A label that can be attached to contacts or groups. */
export interface Label {
  id: string;
  name: string;
  color: string | null;
}

/** Lightweight contact representation for list views. */
export interface ContactSummary {
  id: string;
  full_name: string;
  first_name: string | null;
  last_name: string | null;
  nickname: string | null;
  email: string | null;
  phone: string | null;
  labels: Label[];
  last_interaction_at: string | null;
  warmth?: number | null;
}

/** A single contact_info entry (phone, email, address, etc.).
 * When secured=true and value is null, the value is masked. Use revealContactSecret() to fetch it.
 */
export interface ContactInfoEntry {
  id: string;
  type: string;
  value: string | null; // null when secured=true and not yet revealed
  is_primary: boolean;
  secured: boolean;
  parent_id: string | null;
}

/** Full contact detail with all fields including identity fields. */
export interface ContactDetail extends ContactSummary {
  notes: string | null;
  birthday: string | null;
  company: string | null;
  job_title: string | null;
  address: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  // Identity fields
  roles: string[];
  entity_id: string | null;
  contact_info: ContactInfoEntry[];
  preferred_channel: string | null;
}

/** Request body for PATCH /contacts/{id}. */
export interface ContactPatchRequest {
  full_name?: string | null;
  first_name?: string | null;
  last_name?: string | null;
  nickname?: string | null;
  company?: string | null;
  job_title?: string | null;
  roles?: string[] | null;
  preferred_channel?: string | null;
}

/** Request body for POST /contacts/{id}/merge. */
export interface ContactMergeRequest {
  source_contact_id: string;
}

/** Response for POST /contacts/{id}/merge. */
export interface ContactMergeResponse {
  target_contact_id: string;
  source_contact_id: string;
  contact_info_moved: number;
  entity_merged: boolean;
}

/** Response for GET /owner/setup-status. */
export interface OwnerSetupStatus {
  entity_id: string | null;
  has_name: boolean;
  has_telegram: boolean;
  has_telegram_chat_id: boolean;
  has_email: boolean;
}

/** Request body for POST /contacts/{id}/contact-info. */
export interface CreateContactInfoRequest {
  type: string;
  value: string;
  is_primary?: boolean;
  secured?: boolean;
  parent_id?: string | null;
}

/** Response for POST /contacts/{id}/contact-info. */
export interface CreateContactInfoResponse {
  id: string;
  contact_id: string;
  type: string;
  value: string;
  is_primary: boolean;
  secured: boolean;
  parent_id: string | null;
}

/** Request body for PATCH /contacts/{id}/contact-info/{info_id}. */
export interface PatchContactInfoRequest {
  type?: string | null;
  value?: string | null;
  is_primary?: boolean | null;
}

/** A contact group. */
export interface Group {
  id: string;
  name: string;
  description: string | null;
  member_count: number;
  labels: Label[];
  created_at: string;
  updated_at: string;
}

/** An upcoming date (birthday, anniversary, etc.). */
export interface UpcomingDate {
  contact_id: string;
  contact_name: string;
  date_type: string;
  date: string;
  days_until: number;
}

/** Paginated contact list response. */
export interface ContactListResponse {
  contacts: ContactSummary[];
  total: number;
}

// ---------------------------------------------------------------------------
// Unlinked contacts / entity disambiguation
// ---------------------------------------------------------------------------

/** A candidate entity that might match an unlinked contact. */
export interface EntityLinkSuggestion {
  entity_id: string;
  canonical_name: string;
  entity_type: string;
  score: number;
  name_match: string;
  aliases: string[];
}

/** Compact view of a contact that has no entity_id linked. */
export interface UnlinkedContactSummary {
  id: string;
  full_name: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  company: string | null;
  suggestions: EntityLinkSuggestion[];
}

/** Paginated list of unlinked contacts with pre-computed suggestions. */
export interface UnlinkedContactsResponse {
  contacts: UnlinkedContactSummary[];
  total: number;
}

/** Request body for POST /contacts/{id}/link-entity. */
export interface LinkEntityRequest {
  entity_id: string;
}

/** Response for POST /contacts/{id}/link-entity. */
export interface LinkEntityResponse {
  contact_id: string;
  entity_id: string;
}

/** Request body for POST /contacts/{id}/create-entity. */
export interface CreateAndLinkEntityRequest {
  canonical_name?: string;
  entity_type?: string;
  aliases?: string[];
  metadata?: Record<string, unknown>;
}

/** Response for POST /contacts/{id}/create-entity. */
export interface CreateAndLinkEntityResponse {
  contact_id: string;
  entity_id: string;
  canonical_name: string;
}

/** Response payload for a manual contacts sync trigger. */
export interface ContactsSyncTriggerResponse {
  provider: string;
  mode: "incremental" | "full";
  fetched: number | null;
  applied: number | null;
  skipped: number | null;
  deleted: number | null;
  provider_total: number | null;
  summary: Record<string, unknown>;
  message: string | null;
}

/** Paginated group list response. */
export interface GroupListResponse {
  groups: Group[];
  total: number;
}

/** Query parameters for the contacts list endpoint. */
export interface ContactParams {
  q?: string;
  label?: string;
  archived?: boolean;
  offset?: number;
  limit?: number;
}

/** Query parameters for the groups list endpoint. */
export interface GroupParams {
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** A health measurement record. */
export interface Measurement {
  id: string;
  type: string;
  value: Record<string, unknown>; // JSONB
  measured_at: string;
  notes: string | null;
  created_at: string;
}

/** A medication record. */
export interface Medication {
  id: string;
  name: string;
  dosage: string;
  frequency: string;
  schedule: unknown[];
  active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

/** A dose log entry for a medication. */
export interface Dose {
  id: string;
  medication_id: string;
  taken_at: string;
  skipped: boolean;
  notes: string | null;
  created_at: string;
}

/** A health condition record. */
export interface HealthCondition {
  id: string;
  name: string;
  status: string;
  diagnosed_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

/** A symptom record. */
export interface Symptom {
  id: string;
  name: string;
  severity: number;
  condition_id: string | null;
  occurred_at: string;
  notes: string | null;
  created_at: string;
}

/** A meal record. */
export interface Meal {
  id: string;
  type: string;
  description: string;
  nutrition: Record<string, unknown> | null;
  eaten_at: string;
  notes: string | null;
  created_at: string;
}

/** A health research note. */
export interface HealthResearch {
  id: string;
  title: string;
  content: string;
  tags: string[];
  source_url: string | null;
  condition_id: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// General / Switchboard
// ---------------------------------------------------------------------------

/** A collection in the General butler entity store. */
export interface GeneralCollection {
  id: string;
  name: string;
  description: string | null;
  entity_count: number;
  created_at: string;
}

/** An entity in the General butler entity store. */
export interface GeneralEntity {
  id: string;
  collection_id: string;
  collection_name: string | null;
  tags: string[];
  data: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** A bucket in the collection size distribution histogram. */
export interface GeneralSizeHistogramBucket {
  bracket: string; // e.g. "0", "1-10", "11-100", "101+"
  count: number;
}

/** Aggregated statistics from GET /api/general/stats (bu-iuol4.31). */
export interface GeneralStats {
  total_collections: number;
  total_entities: number;
  last_modified_collection: string | null;
  largest_collection_size: number;
  size_histogram: GeneralSizeHistogramBucket[];
}

// ---------------------------------------------------------------------------
// Health — new endpoints (bu-iuol4.24)
// ---------------------------------------------------------------------------

/**
 * A single latest-measurement entry as returned by
 * GET /api/health/measurements/latest?types=X,Y.
 * `null` means no measurement of that type has been recorded yet.
 */
export interface LatestMeasurementEntry {
  measured_at: string;
  value: Record<string, unknown>;
  unit: string | null;
  metadata: Record<string, unknown> | null;
}

/**
 * Response shape for GET /api/health/measurements/latest?types=X,Y,Z.
 * Keys are measurement type slugs; values are the latest entry or null.
 */
export interface MeasurementsLatestResponse {
  measurements: Record<string, LatestMeasurementEntry | null>;
}

/** A single stage within a sleep session. */
export interface SleepStage {
  stage: string; // "awake" | "light" | "deep" | "rem"
  duration_minutes: number;
  start_time: string | null;
}

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
export interface MeasurementSource {
  name: string;
  last_sample_at: string | null;
  sample_count: number;
}

/** Response shape for GET /api/health/measurements/sources. */
export type MeasurementSourcesResponse = MeasurementSource[];

/** Query parameters for measurement endpoints. */
export interface MeasurementParams {
  type?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for medication endpoints. */
export interface MedicationParams {
  active?: boolean;
  offset?: number;
  limit?: number;
}

/** Query parameters for symptom endpoints. */
export interface SymptomParams {
  name?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for meal endpoints. */
export interface MealParams {
  type?: string;
  since?: string;
  until?: string;
  offset?: number;
  limit?: number;
}

/** Query parameters for research endpoints. */
export interface ResearchParams {
  q?: string;
  tag?: string;
  offset?: number;
  limit?: number;
}

/** A routing log entry from the Switchboard. */
export interface RoutingEntry {
  id: string;
  source_butler: string;
  target_butler: string;
  tool_name: string;
  success: boolean;
  duration_ms: number | null;
  error: string | null;
  created_at: string;
}

/** A butler registry entry from the Switchboard. */
export interface RegistryEntry {
  name: string;
  endpoint_url: string;
  description: string | null;
  modules: unknown[];
  last_seen_at: string | null;
  eligibility_state: string;
  quarantined_at: string | null;
  quarantine_reason: string | null;
  registered_at: string;
}

/** Response from setting a butler's eligibility state. */
export interface SetEligibilityResponse {
  name: string;
  previous_state: string;
  new_state: string;
}

/** A single segment in the eligibility timeline. */
export interface EligibilitySegment {
  state: string;
  start_at: string;
  end_at: string;
}

/** 24h eligibility timeline for a butler. */
export interface EligibilityHistoryResponse {
  butler_name: string;
  segments: EligibilitySegment[];
  window_start: string;
  window_end: string;
}

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
export interface Episode {
  id: string;
  butler: string;
  session_id: string | null;
  content: string;
  importance: number;
  reference_count: number;
  consolidated: boolean;
  created_at: string;
  last_referenced_at: string | null;
  expires_at: string | null;
  metadata: Record<string, unknown>;
}

/** A consolidated fact from the mid-term memory tier. */
export interface Fact {
  id: string;
  subject: string;
  predicate: string;
  content: string;
  importance: number;
  confidence: number;
  decay_rate: number;
  permanence: string;
  source_butler: string | null;
  source_episode_id: string | null;
  session_id: string | null;
  supersedes_id: string | null;
  entity_id: string | null;
  entity_name: string | null;
  object_entity_id: string | null;
  object_entity_name: string | null;
  validity: string;
  scope: string;
  reference_count: number;
  created_at: string;
  last_referenced_at: string | null;
  last_confirmed_at: string | null;
  tags: string[];
  metadata: Record<string, unknown>;
}

/** A behavioral rule from the long-term memory tier. */
export interface MemoryRule {
  id: string;
  content: string;
  scope: string;
  maturity: string;
  confidence: number;
  decay_rate: number;
  permanence: string;
  effectiveness_score: number;
  applied_count: number;
  success_count: number;
  harmful_count: number;
  source_episode_id: string | null;
  source_butler: string | null;
  created_at: string;
  last_applied_at: string | null;
  last_evaluated_at: string | null;
  tags: string[];
  metadata: Record<string, unknown>;
}

/** Aggregated statistics across all memory tiers. */
export interface MemoryStats {
  total_episodes: number;
  unconsolidated_episodes: number;
  total_facts: number;
  active_facts: number;
  fading_facts: number;
  total_rules: number;
  candidate_rules: number;
  established_rules: number;
  proven_rules: number;
  anti_pattern_rules: number;
}

/** A recent memory activity event. */
export interface MemoryActivity {
  id: string;
  type: string;
  summary: string;
  butler: string | null;
  created_at: string;
}

/** Query parameters for episode list endpoints. */
export interface EpisodeParams {
  butler?: string;
  consolidated?: boolean;
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
  offset?: number;
  limit?: number;
}

/** Query parameters for rule list endpoints. */
export interface RuleParams {
  q?: string;
  scope?: string;
  maturity?: string;
  offset?: number;
  limit?: number;
}

// ---------------------------------------------------------------------------
// Entities (Knowledge Graph)
// ---------------------------------------------------------------------------

/** Lightweight entity representation for list views. */
export interface EntitySummary {
  id: string;
  canonical_name: string;
  entity_type: string;
  aliases: string[];
  roles: string[];
  fact_count: number;
  linked_contact_id: string | null;
  unidentified: boolean;
  source_butler: string | null;
  source_scope: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  dunbar_tier: number | null;
  dunbar_score: number | null;
}

/** Response for POST /memory/entities/{id}/merge. */
export interface MergeEntityResponse {
  target_entity_id: string;
  source_entity_id: string;
  facts_repointed: number;
  facts_superseded: number;
  edge_facts_repointed: number;
  edge_facts_superseded: number;
  aliases_added: number;
}

/** A single entity_info row (credentials, identifiers, etc.). */
export interface EntityInfoEntry {
  id: string;
  type: string;
  value: string | null; // null when secured=true and not revealed
  label: string | null;
  is_primary: boolean;
  secured: boolean;
}

/** Request body for creating an entity_info entry. */
export interface CreateEntityInfoRequest {
  type: string;
  value: string;
  label?: string | null;
  is_primary?: boolean;
  secured?: boolean;
}

/** Response from creating an entity_info entry. */
export interface CreateEntityInfoResponse {
  id: string;
  entity_id: string;
  type: string;
  value: string;
  label: string | null;
  is_primary: boolean;
  secured: boolean;
}

/** Request body for updating an entity_info entry. */
export interface UpdateEntityInfoRequest {
  type?: string;
  value?: string;
  label?: string | null;
  is_primary?: boolean;
}

/** Request body for updating entity core fields. */
export interface UpdateEntityRequest {
  canonical_name?: string;
  entity_type?: string;
  aliases?: string[];
  metadata?: Record<string, unknown>;
  roles?: string[];
}

/** Full entity detail including recent facts and linked contact info. */
export interface EntityDetail extends EntitySummary {
  metadata: Record<string, unknown>;
  recent_facts: Fact[];
  recent_facts_total: number;
  recent_facts_offset: number;
  recent_facts_limit: number;
  recent_facts_has_more: boolean;
  linked_contact_name: string | null;
  entity_info: EntityInfoEntry[];
}

/** Query parameters for entity detail endpoints. */
export interface EntityDetailParams {
  facts_offset?: number;
  facts_limit?: number;
}

/** Response from GET /relationship/owner/entity-info. */
export interface OwnerEntityInfoResponse {
  entity_id: string;
  entity_name: string;
  entries: EntityInfoEntry[];
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

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

/** Compact contact object linked from an approval action. */
export interface TargetContact {
  id: string;
  name: string;
  roles: string[];
}

export interface ApprovalAction {
  id: string;
  butler: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  status: string;
  requested_at: string;
  agent_summary?: string | null;
  session_id?: string | null;
  expires_at?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  execution_result?: Record<string, unknown> | null;
  approval_rule_id?: string | null;
  target_contact?: TargetContact | null;
}

export interface ApprovalRule {
  id: string;
  tool_name: string;
  arg_constraints: Record<string, unknown>;
  description: string;
  created_from?: string | null;
  created_at: string;
  expires_at?: string | null;
  max_uses?: number | null;
  use_count: number;
  active: boolean;
}

export interface RuleConstraintSuggestion {
  action_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  suggested_constraints: Record<string, unknown>;
}

export interface ApprovalMetrics {
  total_pending: number;
  total_approved_today: number;
  total_rejected_today: number;
  total_auto_approved_today: number;
  total_expired_today: number;
  avg_decision_latency_seconds?: number | null;
  auto_approval_rate: number;
  rejection_rate: number;
  failure_count_today: number;
  active_rules_count: number;
}

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

export interface ApprovalActionApproveRequest {
  create_rule?: boolean;
}

export interface ApprovalActionRejectRequest {
  reason?: string | null;
}

export interface ApprovalRuleCreateRequest {
  tool_name: string;
  arg_constraints: Record<string, unknown>;
  description: string;
  expires_at?: string | null;
  max_uses?: number | null;
}

export interface ApprovalRuleFromActionRequest {
  action_id: string;
  constraint_overrides?: Record<string, unknown> | null;
}

export interface ExpireStaleActionsResponse {
  expired_count: number;
  expired_ids: string[];
}

export interface AutonomySuggestionVelocity {
  avg_seconds?: number | null;
  sample_count: number;
  fast_approval: boolean;
  updated_at?: string | null;
}

export interface AutonomySuggestion {
  id: string;
  suggestion_type: "promotion" | "demotion";
  pattern_fingerprint: string;
  tool_name: string;
  representative_args: Record<string, unknown>;
  status: "pending" | "confirmed" | "dismissed" | "superseded";
  approval_count_at_creation: number;
  scope_description: string;
  created_at: string;
  decided_at?: string | null;
  decided_by?: string | null;
  resulting_rule_id?: string | null;
  cooldown_until?: string | null;
  dismissal_reason?: string | null;
  velocity?: AutonomySuggestionVelocity | null;
}

export interface AutonomySuggestionParams {
  status?: string;
  suggestion_type?: string;
  limit?: number;
  offset?: number;
}

export interface AutonomySuggestionDismissRequest {
  reason?: string | null;
  cooldown_days?: number;
}

// ---------------------------------------------------------------------------
// OAuth / Secrets management types
// ---------------------------------------------------------------------------

export type OAuthCredentialState =
  | "connected"
  | "not_configured"
  | "expired"
  | "missing_scope"
  | "redirect_uri_mismatch"
  | "unapproved_tester"
  | "unknown_error";

export interface OAuthCredentialStatus {
  provider: string;
  state: OAuthCredentialState;
  connected: boolean;
  scopes_granted: string[] | null;
  remediation: string | null;
  detail: string | null;
}

export interface GoogleAccount {
  id: string;
  email: string | null;
  display_name: string | null;
  is_primary: boolean;
  status: "active" | "revoked" | "expired";
  granted_scopes: string[];
  connected_at: string;
  last_token_refresh_at: string | null;
}

export interface GoogleAccountStatus {
  has_refresh_token: boolean;
  has_app_credentials: boolean;
  granted_scopes: string[];
  missing_scopes: string[];
  token_valid: boolean;
  last_token_refresh_at: string | null;
}

export interface SetPrimaryAccountResponse {
  success: boolean;
  account: GoogleAccount;
}

export interface DisconnectAccountResponse {
  success: boolean;
  message: string;
  auto_promoted_id: string | null;
}

export interface OAuthStatusResponse {
  google: OAuthCredentialStatus;
  accounts: GoogleAccount[] | null;
}

export interface GoogleCredentialStatusResponse {
  client_id_configured: boolean;
  client_secret_configured: boolean;
  refresh_token_present: boolean;
  scope: string | null;
  oauth_health: OAuthCredentialState;
  oauth_health_remediation: string | null;
  oauth_health_detail: string | null;
}

export interface UpsertAppCredentialsRequest {
  client_id: string;
  client_secret: string;
}

export interface UpsertAppCredentialsResponse {
  success: boolean;
  message: string;
}

export interface DeleteCredentialsResponse {
  success: boolean;
  deleted: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// CLI auth (device-code flow) types
// ---------------------------------------------------------------------------

export type CLIAuthSessionState =
  | "starting"
  | "awaiting_auth"
  | "success"
  | "failed"
  | "expired";

export type CLIAuthHealthState =
  | "authenticated"
  | "not_authenticated"
  | "unavailable"
  | "probe_failed";

export interface CLIAuthProvider {
  name: string;
  display_name: string;
  runtime: string;
  auth_mode: "device_code" | "api_key";
  authenticated: boolean;
  health: CLIAuthHealthState | null;
  health_detail: string | null;
  token_path: string | null;
  env_var: string | null;
}

export interface CLIAuthStartResponse {
  session_id: string;
  state: CLIAuthSessionState;
  auth_url: string | null;
  device_code: string | null;
  message: string | null;
}

export interface CLIAuthSessionResponse {
  session_id: string;
  state: CLIAuthSessionState;
  auth_url: string | null;
  device_code: string | null;
  message: string | null;
  provider: string | null;
}

export interface CLIAuthApiKeyResponse {
  provider: string;
  stored: boolean;
  message: string | null;
}

export interface CLIAuthTestResponse {
  provider: string;
  success: boolean;
  detail: string | null;
}

// ---------------------------------------------------------------------------
// Generic secrets management types
// ---------------------------------------------------------------------------

/** Metadata for a single secret. Values are never exposed in responses. */
export interface SecretEntry {
  key: string;
  category: string;
  description: string | null;
  is_sensitive: boolean;
  is_set: boolean;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  source: string;
}

/** Request body for creating or updating a secret (PUT). */
export interface SecretUpsertRequest {
  value: string;
  category?: string | null;
  description?: string | null;
  is_sensitive?: boolean | null;
  expires_at?: string | null;
}

/** Known secret categories for grouping. */
export type SecretCategory = "core" | "telegram" | "email" | "google" | "gemini" | "general";

/** Predefined secret key templates with descriptions and auto-detected categories. */
export interface SecretTemplate {
  key: string;
  description: string;
  category: SecretCategory;
}

// ---------------------------------------------------------------------------
// Backfill job types (switchboard ingestion history)
// ---------------------------------------------------------------------------

export type BackfillJobStatus =
  | "pending"
  | "active"
  | "paused"
  | "completed"
  | "cancelled"
  | "cost_capped"
  | "error";

/** A summarised backfill job for list endpoints (cursor omitted). */
export interface BackfillJobSummary {
  id: string;
  connector_type: string;
  endpoint_identity: string;
  target_categories: string[];
  date_from: string;
  date_to: string;
  rate_limit_per_hour: number;
  daily_cost_cap_cents: number;
  status: BackfillJobStatus;
  rows_processed: number;
  rows_skipped: number;
  cost_spent_cents: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
}

/** Full backfill job entry including cursor. */
export interface BackfillJobEntry extends BackfillJobSummary {
  cursor: Record<string, unknown> | null;
}

/** Request body for creating a backfill job. */
export interface CreateBackfillJobRequest {
  connector_type: string;
  endpoint_identity: string;
  target_categories?: string[];
  date_from: string;
  date_to: string;
  rate_limit_per_hour?: number;
  daily_cost_cap_cents?: number;
}

/** Response body for lifecycle actions (pause/cancel/resume). */
export interface BackfillLifecycleResponse {
  job_id: string;
  status: string;
}

/** Query parameters for backfill job list. */
export interface BackfillJobParams {
  status?: BackfillJobStatus;
  connector_type?: string;
  endpoint_identity?: string;
  offset?: number;
  limit?: number;
}

/** A connector entry from the connector_registry table. */
export interface ConnectorEntry {
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
  checkpoint_cursor: string | null;
  checkpoint_updated_at: string | null;
}

// ---------------------------------------------------------------------------
// Thread affinity types
// ---------------------------------------------------------------------------

/** Global thread affinity settings. */
export interface ThreadAffinitySettings {
  enabled: boolean;
  ttl_days: number;
  thread_overrides: Record<string, string>;
  updated_at: string | null;
}

/** Request body for PATCH /api/switchboard/thread-affinity/settings. */
export interface ThreadAffinitySettingsUpdate {
  enabled?: boolean;
  ttl_days?: number;
}

/** A single per-thread override entry. */
export interface ThreadOverrideEntry {
  thread_id: string;
  mode: string;
}

/** Request body for PUT /api/switchboard/thread-affinity/overrides/:thread_id. */
export interface ThreadOverrideUpsert {
  mode: string;
}

// ---------------------------------------------------------------------------
// Connector statistics and analytics types (docs/connectors/statistics.md)
// ---------------------------------------------------------------------------

export type IngestionPeriod = "24h" | "7d" | "30d";

/** Today's ingestion summary attached to a connector list entry. */
export interface ConnectorDaySummary {
  messages_ingested: number;
  messages_failed: number;
  uptime_pct: number | null;
}

/** A connector with current liveness and today's stats (GET /api/connectors). */
export interface ConnectorSummary {
  connector_type: string;
  endpoint_identity: string;
  liveness: string; // "online" | "stale" | "offline"
  state: string;    // "healthy" | "degraded" | "error"
  error_message: string | null;
  version: string | null;
  uptime_s: number | null;
  last_heartbeat_at: string | null;
  first_seen_at: string;
  today: ConnectorDaySummary | null;
}

/** Full connector detail (GET /api/connectors/:type/:identity). */
export interface ConnectorDetail extends ConnectorSummary {
  instance_id: string | null;
  registered_via: string;
  checkpoint: ConnectorCheckpoint | null;
  counters: ConnectorCounters | null;
  settings: Record<string, unknown> | null;
}

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

/** Full stats response for a single connector (GET /api/connectors/:type/:identity/stats). */
export interface ConnectorStats {
  connector_type: string;
  endpoint_identity: string;
  period: IngestionPeriod;
  summary: ConnectorStatsSummary;
  timeseries: ConnectorStatsBucket[];
}

/** Period-scoped ingestion overview statistics from GET /api/switchboard/ingestion/overview. */
export interface IngestionOverviewStats {
  period: IngestionPeriod;
  total_ingested: number;
  total_skipped: number;
  total_metadata_only: number;
  llm_calls_saved: number;
  active_connectors: number;
  tier1_full_count: number;
  tier2_metadata_count: number;
  tier3_skip_count: number;
}

/** One row in the cross-connector summary. */
export interface ConnectorSummaryEntry {
  connector_type: string;
  endpoint_identity: string;
  liveness: string;
  messages_ingested: number;
  messages_failed: number;
}

/** Cross-connector aggregate summary (GET /api/connectors/summary). */
export interface CrossConnectorSummary {
  period: IngestionPeriod;
  total_connectors: number;
  connectors_online: number;
  connectors_stale: number;
  connectors_offline: number;
  total_messages_ingested: number;
  total_messages_failed: number;
  overall_error_rate_pct: number;
  by_connector: ConnectorSummaryEntry[];
}

/** One row in the fanout matrix. */
export interface ConnectorFanoutEntry {
  connector_type: string;
  endpoint_identity: string;
  targets: Record<string, number>; // butler_name -> message_count
}

/** Fanout distribution response (GET /api/connectors/fanout). */
export interface ConnectorFanout {
  period: IngestionPeriod;
  matrix: ConnectorFanoutEntry[];
}

// ---------------------------------------------------------------------------
// Ingestion event lineage types (GET /api/switchboard/ingestion/events/*)
// ---------------------------------------------------------------------------

/**
 * All possible lifecycle statuses for an ingestion event from the unified timeline.
 * - ingested: processed successfully
 * - filtered: dropped by a rule
 * - error: processing failed
 * - replay_pending: replay requested, awaiting processing
 * - replay_complete: replay succeeded
 * - replay_failed: replay attempt failed
 */
export type IngestionEventStatus =
  | "ingested"
  | "filtered"
  | "error"
  | "replay_pending"
  | "replay_complete"
  | "replay_failed";

/** One ingestion event from shared.ingestion_events (list view). */
export interface IngestionEventSummary {
  id: string; // UUIDv7 — the request_id
  received_at: string | null;
  source_channel: string | null;
  source_provider: string | null;
  source_endpoint_identity: string | null;
  source_sender_identity: string | null;
  source_thread_identity: string | null;
  external_event_id: string | null;
  dedupe_key: string | null;
  dedupe_strategy: string | null;
  ingestion_tier: string | null;
  policy_tier: string | null;
  triage_decision: string | null;
  triage_target: string | null;
  /** Unified timeline status. Defaults to 'ingested' for legacy rows. */
  status: IngestionEventStatus;
  /** Human-readable reason why this event was filtered or errored. */
  filter_reason: string | null;
  /** Detailed error context for error-status events (e.g. exception message). */
  error_detail: string | null;
}

/** Response body from POST /api/ingestion/events/{id}/replay. */
export interface IngestionEventReplayResponse {
  id: string;
  status: IngestionEventStatus;
}

/** One butler session spawned in response to an ingestion event. */
export interface IngestionEventSession {
  id: string; // session UUID
  butler_name: string;
  trigger_source: string | null;
  started_at: string | null;
  completed_at: string | null;
  success: boolean | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost: Record<string, unknown> | null;
  trace_id: string | null;
  model: string | null;
}

/** Per-butler breakdown within an IngestionEventRollup. */
export interface ButlerRollupEntry {
  sessions: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

/** Aggregate cost/token totals for all sessions linked to one ingestion event. */
export interface IngestionEventRollup {
  request_id: string;
  total_sessions: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost: number;
  by_butler: Record<string, ButlerRollupEntry>;
}

/** Query parameters for GET /api/switchboard/ingestion/events. */
export interface IngestionEventsParams {
  limit?: number;
  offset?: number;
  source_channel?: string;
  /** Filter by event status. Omit to return all events. */
  status?: IngestionEventStatus;
}

// ---------------------------------------------------------------------------
// Education
// ---------------------------------------------------------------------------

/** A directed edge in the mind map DAG. */
export interface MindMapEdge {
  parent_node_id: string;
  child_node_id: string;
  edge_type: string;
}

/** A concept node in a mind map. */
export interface MindMapNode {
  id: string;
  mind_map_id: string;
  label: string;
  description: string | null;
  depth: number;
  mastery_score: number;
  mastery_status: string;
  ease_factor: number;
  repetitions: number;
  next_review_at: string | null;
  last_reviewed_at: string | null;
  effort_minutes: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** A mind map with optional nested nodes and edges. */
export interface MindMap {
  id: string;
  title: string;
  root_node_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  nodes: MindMapNode[];
  edges: MindMapEdge[];
}

/** A recorded quiz response for a concept node. */
export interface QuizResponse {
  id: string;
  node_id: string;
  mind_map_id: string;
  question_text: string;
  user_answer: string | null;
  quality: number;
  response_type: string;
  session_id: string | null;
  responded_at: string;
  evaluator_notes: string | null;
  node_label: string | null;
}

/** An analytics snapshot for a mind map. */
export interface AnalyticsSnapshot {
  id: string | null;
  mind_map_id: string;
  snapshot_date: string;
  metrics: Record<string, unknown>;
  created_at: string | null;
  trend: AnalyticsSnapshotTrendEntry[];
}

/** A single entry in the analytics trend series. */
export interface AnalyticsSnapshotTrendEntry {
  id: string;
  mind_map_id: string;
  snapshot_date: string;
  metrics: Record<string, unknown>;
  created_at: string;
}

/** A teaching flow entry with mastery summary. */
export interface TeachingFlow {
  mind_map_id: string;
  title: string;
  status: string;
  session_count: number;
  started_at: string | null;
  last_session_at: string | null;
  mastery_pct: number;
}

/** Per-topic entry in cross-topic analytics. */
export interface CrossTopicEntry {
  mind_map_id: string;
  title: string;
  mastery_pct: number;
  retention_rate_7d: number | null;
  velocity: number;
}

/** Cross-topic comparative analytics. */
export interface CrossTopicAnalytics {
  topics: CrossTopicEntry[];
  strongest_topic: string | null;
  weakest_topic: string | null;
  portfolio_mastery: number;
}

/** Aggregate mastery statistics for a mind map. */
export interface MasterySummary {
  mind_map_id: string;
  total_nodes: number;
  mastered_count: number;
  learning_count: number;
  reviewing_count: number;
  unseen_count: number;
  diagnosed_count: number;
  avg_mastery_score: number;
  struggling_node_ids: string[];
}

/** A node due for spaced-repetition review. */
export interface PendingReviewNode {
  node_id: string;
  label: string;
  ease_factor: number;
  repetitions: number;
  next_review_at: string;
  mastery_status: string;
}

/** One snapshot entry in an analytics trend time-series (from /analytics/trend). */
export interface AnalyticsTrendEntry {
  id: string | null;
  mind_map_id: string;
  snapshot_date: string;
  metrics: Record<string, unknown>;
  created_at: string | null;
}

/** Analytics trend time-series for a mind map (from /analytics/trend). */
export interface AnalyticsTrendResponse {
  mind_map_id: string;
  days: number;
  trend: AnalyticsTrendEntry[];
}

/** A concept node identified as struggling (from /struggling-nodes). */
export interface StrugglingNodeEntry {
  node_id: string;
  node_label: string;
  mastery_score: number;
  mastery_status: string;
  reason: string;
}

/** List of struggling nodes for a mind map (from /struggling-nodes). */
export interface StrugglingNodesResponse {
  mind_map_id: string;
  nodes: StrugglingNodeEntry[];
}

/** Request body for submitting a new curriculum request. */
export interface CurriculumRequestBody {
  topic: string;
  goal?: string | null;
}

/** Response body for a submitted curriculum request. */
export interface CurriculumRequestResponse {
  status: string;
  topic: string;
}

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
export interface IngestionRule {
  id: string;
  scope: string;
  rule_type: string;
  condition: Record<string, unknown>;
  action: string;
  priority: number;
  enabled: boolean;
  name: string | null;
  description: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

/** Request body for POST /api/switchboard/ingestion-rules. */
export interface IngestionRuleCreate {
  scope: string;
  rule_type: string;
  condition: Record<string, unknown>;
  action: string;
  priority: number;
  enabled?: boolean;
  name?: string | null;
  description?: string | null;
}

/** Request body for PATCH /api/switchboard/ingestion-rules/:id. All fields optional. */
export interface IngestionRuleUpdate {
  scope?: string | null;
  condition?: Record<string, unknown> | null;
  action?: string | null;
  priority?: number | null;
  enabled?: boolean | null;
  name?: string | null;
  description?: string | null;
}

/** Sample envelope for dry-run ingestion rule testing. */
export interface IngestionRuleTestEnvelope {
  sender_address?: string;
  source_channel?: string;
  headers?: Record<string, string>;
  mime_parts?: string[];
  raw_key?: string;
}

/** Request body for POST /api/switchboard/ingestion-rules/test. */
export interface IngestionRuleTestRequest {
  envelope: IngestionRuleTestEnvelope;
  scope?: string;
}

/** Result of a dry-run ingestion rule test. */
export interface IngestionRuleTestResult {
  matched: boolean;
  decision: string | null;
  target_butler: string | null;
  matched_rule_id: string | null;
  matched_rule_type: string | null;
  reason: string;
}

/** Response envelope for POST /api/switchboard/ingestion-rules/test. */
export interface IngestionRuleTestResponse {
  data: IngestionRuleTestResult;
}

/** Query params for GET /api/switchboard/ingestion-rules. */
export interface IngestionRuleListParams {
  scope?: string;
  rule_type?: string;
  action?: string;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Model catalog
// ---------------------------------------------------------------------------

/** Valid complexity tier values for the model catalog. */
export type ComplexityTier = "trivial" | "medium" | "high" | "extra_high" | "discretion" | "self_healing";

/** Per-model pricing (USD per 1M tokens). Keyed by model_id. */
export interface ModelPricingEntry {
  input_per_million: number;
  output_per_million: number;
}

/** Map of model_id → pricing. */
export type PricingMap = Record<string, ModelPricingEntry>;

/** A single entry in the shared model catalog. */
export interface ModelCatalogEntry {
  id: string;
  alias: string;
  runtime_type: string;
  model_id: string;
  extra_args: string[];
  complexity_tier: ComplexityTier;
  enabled: boolean;
  priority: number;
  session_timeout_s: number;
  /** Rolling 24h token usage (from ledger aggregation). */
  usage_24h: number;
  /** Rolling 30d token usage (from ledger aggregation). */
  usage_30d: number;
  /** Configured 24h token limit; null = unlimited. */
  limit_24h: number | null;
  /** Configured 30d token limit; null = unlimited. */
  limit_30d: number | null;
}

/** Request body for creating a catalog entry. */
export interface ModelCatalogCreate {
  alias: string;
  runtime_type: string;
  model_id: string;
  extra_args?: string[];
  complexity_tier?: ComplexityTier;
  enabled?: boolean;
  priority?: number;
  session_timeout_s?: number;
}

/** Request body for updating a catalog entry (all fields optional). */
export interface ModelCatalogUpdate {
  alias?: string;
  runtime_type?: string;
  model_id?: string;
  extra_args?: string[];
  complexity_tier?: ComplexityTier;
  enabled?: boolean;
  priority?: number;
  session_timeout_s?: number;
}

/** A single per-butler model override joined with catalog alias. */
export interface ButlerModelOverride {
  id: string;
  butler_name: string;
  catalog_entry_id: string;
  alias: string;
  enabled: boolean;
  priority: number | null;
  complexity_tier: ComplexityTier | null;
}

/** One item in a batch upsert request for butler model overrides. */
export interface ButlerModelOverrideUpsert {
  catalog_entry_id: string;
  enabled?: boolean;
  priority?: number | null;
  complexity_tier?: ComplexityTier | null;
}

/** Response from the model test endpoint. */
export interface ModelTestResult {
  success: boolean;
  reply: string | null;
  error: string | null;
  duration_ms: number;
}

/** Response from the resolve-model preview endpoint. */
export interface ResolveModelResponse {
  butler_name: string;
  complexity: string;
  runtime_type: string | null;
  model_id: string | null;
  extra_args: string[];
  session_timeout_s: number | null;
  resolved: boolean;
  /** True when either window's usage meets or exceeds its configured limit. */
  quota_blocked: boolean;
  usage_24h: number;
  limit_24h: number | null;
  usage_30d: number;
  limit_30d: number | null;
}

/** Request body for PUT /api/settings/models/{entry_id}/limits. */
export interface TokenLimitsRequest {
  limit_24h: number | null;
  limit_30d: number | null;
}

/** Response from PUT /api/settings/models/{entry_id}/limits. */
export interface TokenLimitsResponse {
  catalog_entry_id: string;
  limit_24h: number | null;
  limit_30d: number | null;
  deleted: boolean;
}

/** Window selector for POST /api/settings/models/{entry_id}/reset-usage. */
export type UsageWindow = "24h" | "30d" | "both";

/** Request body for POST /api/settings/models/{entry_id}/reset-usage. */
export interface ResetUsageRequest {
  window: UsageWindow;
}

/** Response from GET /api/settings/models/{entry_id}/usage. */
export interface TokenUsageDetail {
  catalog_entry_id: string;
  usage_24h: number;
  usage_30d: number;
  limit_24h: number | null;
  limit_30d: number | null;
  reset_24h_at: string | null;
  reset_30d_at: string | null;
  percent_24h: number | null;
  percent_30d: number | null;
}

// ---------------------------------------------------------------------------
// Provider configuration
// ---------------------------------------------------------------------------

/** A single provider configuration entry. */
export interface ProviderConfig {
  provider_type: string;
  display_name: string;
  config: Record<string, unknown>;
  enabled: boolean;
}

/** Request body for creating a provider. */
export interface ProviderConfigCreate {
  provider_type: string;
  display_name: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
}

/** Request body for updating a provider (all fields optional). */
export interface ProviderConfigUpdate {
  display_name?: string;
  config?: Record<string, unknown>;
  enabled?: boolean;
}

/** Response from the provider test-connectivity endpoint. */
export interface ProviderConnectivityResult {
  success: boolean;
  provider_type: string;
  url: string | null;
  status_code: number | null;
  error: string | null;
  latency_ms: number;
}

// ---------------------------------------------------------------------------
// WhatsApp connector types
// ---------------------------------------------------------------------------

/** Connection/session state for the WhatsApp account. */
export type WhatsAppState =
  | "connected"
  | "disconnected"
  | "pair_required"
  | "not_configured";

/** Status of an ongoing QR pairing attempt. */
export type WhatsAppPairStatus = "waiting" | "paired" | "expired";

/** Response from GET /api/connectors/whatsapp/status */
export interface WhatsAppStatusResponse {
  state: WhatsAppState;
  /** Masked phone number, e.g. '+1 *** *** 7890', or null if not connected. */
  phone: string | null;
  /** ISO datetime when the account was first paired, or null. */
  paired_at: string | null;
  /** ISO datetime of the last successful sync, or null. */
  last_sync_at: string | null;
  /** Whether the Go bridge subprocess is currently running. */
  bridge_running: boolean;
}

/** Response from POST /api/connectors/whatsapp/pair/start */
export interface WhatsAppPairStartResponse {
  /** Base64-encoded PNG data URI: 'data:image/png;base64,...' */
  qr_data_uri: string;
  /** ISO datetime when this QR code expires. */
  expires_at: string;
}

/** Response from GET /api/connectors/whatsapp/pair/poll */
export interface WhatsAppPairPollResponse {
  status: WhatsAppPairStatus;
  /** Phone number when status === 'paired', otherwise null. */
  phone: string | null;
}

/** Response from GET /api/connectors/whatsapp/health */
export interface WhatsAppHealthResponse {
  state: WhatsAppState;
  bridge_running: boolean;
  uptime_seconds: number | null;
  last_event_at: string | null;
}

/** Response from POST /api/connectors/whatsapp/disconnect */
export interface WhatsAppDisconnectResponse {
  success: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Spotify connector types
// ---------------------------------------------------------------------------

/** Connection state for the Spotify account. */
export type SpotifyState =
  | "connected"
  | "disconnected"
  | "error"
  | "not_configured"
  | "needs_auth"
  | "needs_reauth";

/** Response from GET /api/spotify/status */
export interface SpotifyStatusResponse {
  connected: boolean;
  state: SpotifyState;
  spotify_user_id: string | null;
  display_name: string | null;
  account_type: string | null;
  last_sync_at: string | null;
  error: string | null;
  /** True when stored scopes are insufficient for current requirements. */
  needs_reauth: boolean;
  /** Scopes that are required but were not granted. */
  missing_scopes: string[];
}

/** Response from POST /api/spotify/oauth/start */
export interface SpotifyOAuthStartResponse {
  authorization_url: string;
}

/** Request body for POST /api/spotify/config */
export interface SpotifyConfigRequest {
  client_id: string;
}

/** Response from POST /api/spotify/config */
export interface SpotifyConfigResponse {
  configured: boolean;
}

/** Response from POST /api/spotify/disconnect */
export interface SpotifyDisconnectResponse {
  disconnected: boolean;
}

// ---------------------------------------------------------------------------
// OwnTracks connector types
// ---------------------------------------------------------------------------

/** Connection state for the OwnTracks webhook connector. */
export type OwnTracksState = "active" | "idle" | "not_configured";

/** Response from GET /api/connectors/owntracks/status */
export interface OwnTracksStatusResponse {
  state: OwnTracksState;
  /** ISO datetime of the last received webhook event, or null. */
  last_event_at: string | null;
  /** Number of events received today (UTC day). */
  events_today: number;
  /** Whether a bearer token is currently configured. */
  token_configured: boolean;
}

/** Response from GET /api/connectors/owntracks/config */
export interface OwnTracksConfigResponse {
  /** The full webhook URL the OwnTracks app should POST to. */
  webhook_url: string;
  /** Host portion only (for display). */
  host: string;
}

/** Response from POST /api/connectors/owntracks/token/generate */
export interface OwnTracksTokenResponse {
  /** The newly generated bearer token (shown once; store securely). */
  token: string;
}

// ---------------------------------------------------------------------------
// Home Assistant settings types
// ---------------------------------------------------------------------------

/** Connection state for the Home Assistant integration. */
export type HomeAssistantState = "connected" | "disconnected" | "not_configured";

/** Response from GET /api/settings/home-assistant */
export interface HomeAssistantStatusResponse {
  state: HomeAssistantState;
  /** Whether a HA URL is stored in CredentialStore. */
  url_configured: boolean;
  /** Whether a HA access token is stored in CredentialStore. */
  token_configured: boolean;
  /** Base origin of the HA URL (e.g. 'http://homeassistant.local:8123'), or null. */
  masked_url: string | null;
}

/** Request body for POST /api/settings/home-assistant */
export interface HomeAssistantConfigRequest {
  /** Home Assistant base URL (e.g. http://homeassistant.local:8123). */
  url: string;
  /** Long-lived access token from Home Assistant. */
  token: string;
}

/** Response from POST /api/settings/home-assistant */
export interface HomeAssistantConfigResponse {
  success: boolean;
  message: string;
  /** Base origin of the stored HA URL, or null on failure. */
  masked_url: string | null;
}

/** Response from DELETE /api/settings/home-assistant */
export interface HomeAssistantDeleteResponse {
  success: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Dunbar tier ranking
// ---------------------------------------------------------------------------

/** A single contact's Dunbar tier ranking entry. */
export interface DunbarEntry {
  contact_id: string;
  entity_id: string;
  canonical_name: string;
  dunbar_tier: number;
  dunbar_score: number;
  dunbar_tier_override: boolean;
  warmth?: number | null;
  avatar_url?: string | null;
  aliases?: string[];
  last_interaction_at?: string | null;
}

/** Response from GET /api/relationship/dunbar/ranking */
export interface DunbarRankingResponse {
  entries: DunbarEntry[];
  owner_entity_id: string | null;
}

// ---------------------------------------------------------------------------
// Contact interactions (bu-iuol4.22)
// ---------------------------------------------------------------------------

/** A single interaction event for a contact (GET /contacts/{id}/interactions). */
export interface ContactInteraction {
  ts: string;
  direction: "in" | "out" | "drafted";
  text: string;
}

/** Response from GET /api/relationship/contacts/{contact_id}/interactions?limit=N */
export interface ContactInteractionsResponse {
  contact_id: string;
  interactions: ContactInteraction[];
}

// ---------------------------------------------------------------------------
// Overdue contacts (bu-iuol4.22)
// ---------------------------------------------------------------------------

/** A single overdue contact entry (GET /contacts/overdue?days=N). */
export interface OverdueContact {
  contact_id: string;
  name: string;
  tier: number;
  owed_days: number;
  last_contact_date: string | null;
  target_cadence_days: number;
}

/** Response from GET /api/relationship/contacts/overdue?days=N */
export interface OverdueContactsResponse {
  contacts: OverdueContact[];
}

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

/** A single message in a dashboard conversation. */
export interface Message {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  tool_calls: MessageToolCall[] | null;
  error: string | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  duration_ms: number | null;
  session_id: string | null;
  request_id: string | null;
  created_at: string;
}

/** Summary of a dashboard conversation (list view). */
export interface ConversationSummary {
  id: string;
  butler_name: string;
  title: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  message_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_duration_ms: number;
}

/** Query params for GET /api/butlers/{name}/conversations. */
export interface ConversationListParams {
  status?: "active" | "archived";
  limit?: number;
  offset?: number;
}

/** Request body for POST /api/butlers/{name}/conversations. */
export interface CreateConversationRequest {
  message: string;
  title?: string;
}

/** Request body for POST /api/butlers/{name}/conversations/{id}/messages. */
export interface SendMessageRequest {
  message: string;
}

/** SSE event types emitted by the conversation streaming endpoints. */
export type ConversationSseEventType =
  | "conversation_created"
  | "token"
  | "message_complete"
  | "error"
  | "done";

/** A parsed SSE event from the conversation streaming endpoint. */
export interface ConversationSseEvent {
  event: ConversationSseEventType;
  data: unknown;
}

// ---------------------------------------------------------------------------
// Telegram Session Auth
// ---------------------------------------------------------------------------

/** Request body for POST /api/telegram/session/send-code */
export interface TelegramSendCodeRequest {
  api_id: number;
  api_hash: string;
  phone: string;
}

/** Response from POST /api/telegram/session/send-code */
export interface TelegramSendCodeResponse {
  session_token: string;
  phone_code_hash: string;
}

/** Request body for POST /api/telegram/session/verify */
export interface TelegramVerifyCodeRequest {
  session_token: string;
  code: string;
  password?: string | null;
}

/** Response from POST /api/telegram/session/verify */
export interface TelegramVerifyCodeResponse {
  success: boolean;
  user_name: string | null;
  message: string;
}

/** Response from GET /api/telegram/session/status */
export interface TelegramSessionStatusResponse {
  has_api_id: boolean;
  has_api_hash: boolean;
  has_session: boolean;
  ready: boolean;
}

// ---------------------------------------------------------------------------
// General settings
// ---------------------------------------------------------------------------

/** Response from GET/PUT /api/settings/general. */
export interface GeneralSettings {
  timezone: string;
  timezone_label: string;
  language: string;
  date_format: string;
  time_format: string;
  week_starts_on: string;
  currency: string;
  measurement_system: "metric";
}

/** Request body for PUT /api/settings/general. */
export interface GeneralSettingsUpdate {
  timezone: string;
  language: string;
  date_format: string;
  time_format: string;
  week_starts_on: string;
  currency: string;
}

// ---------------------------------------------------------------------------
// Blob storage (S3-compatible)
// ---------------------------------------------------------------------------

/** Response from GET /api/settings/blob-storage — configuration status. */
export interface BlobStorageStatus {
  endpoint_url: string | null;
  bucket: string | null;
  region: string | null;
  has_access_key: boolean;
  has_secret_key: boolean;
  configured: boolean;
}

/** Response from POST /api/settings/blob-storage/test — connectivity probe. */
export interface BlobStorageTestResult {
  success: boolean;
  error: string | null;
  latency_ms: number;
  endpoint_url: string | null;
  bucket: string | null;
}

// ---------------------------------------------------------------------------
// Steam connector types
// ---------------------------------------------------------------------------

/** Account status for a connected Steam account. */
export type SteamAccountStatus = "active" | "suspended" | "revoked";

/** A single connected Steam account. */
export interface SteamAccountResponse {
  id: string;
  steam_id: string;
  display_name: string | null;
  profile_url: string | null;
  avatar_url: string | null;
  is_primary: boolean;
  status: SteamAccountStatus;
  connected_at: string;
  last_poll_at: string | null;
}

/** Response from GET /api/steam/accounts */
export interface SteamAccountListResponse {
  accounts: SteamAccountResponse[];
}

/** Request body for POST /api/steam/accounts */
export interface SteamConnectRequest {
  steam_id: string;
  api_key: string;
  display_name?: string | null;
}

/** Response from POST /api/steam/accounts */
export interface SteamConnectResponse {
  success: boolean;
  message: string;
  account: SteamAccountResponse;
}

/** Response from DELETE /api/steam/accounts/{id} */
export interface SteamDisconnectResponse {
  success: boolean;
  message: string;
}

/** Playtime record for a single game. */
export interface SteamGamePlaytime {
  app_id: number;
  app_name: string | null;
  total_minutes: number;
}

/** Response from GET /api/steam/playtime */
export interface SteamPlaytimeAnalytics {
  account_id: string;
  steam_id: number;
  display_name: string | null;
  days: number | null;
  total_games: number;
  total_minutes: number;
  games: SteamGamePlaytime[];
  queried_at: string;
}

// ---------------------------------------------------------------------------
// Healing attempts (self-healing + QA-originated investigations)
// ---------------------------------------------------------------------------

/** A single healing attempt record — GET /api/healing/attempts/:id */
export interface HealingAttempt {
  id: string;
  fingerprint: string;
  butler_name: string;
  status: string;
  severity: number;
  exception_type: string;
  call_site: string;
  sanitized_msg: string | null;
  branch_name: string | null;
  worktree_path: string | null;
  pr_url: string | null;
  pr_number: number | null;
  session_ids: string[];
  healing_session_id: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  error_detail: string | null;
}

/** Params for listing healing attempts */
export interface HealingAttemptsParams {
  offset?: number;
  limit?: number;
  status?: string;
}

// ---------------------------------------------------------------------------
// QA Staffer
// ---------------------------------------------------------------------------

/** Lightweight patrol record for list views — GET /api/qa/patrols */
export interface QaPatrolSummary {
  id: string;
  started_at: string;
  completed_at: string | null;
  status: string;
  findings_count: number;
  novel_count: number;
  dispatched_count: number;
  log_lookback_minutes: number;
  sources_polled: string[];
  error_detail: string | null;
}

/** A single finding record from a patrol — GET /api/qa/patrols/:id/findings */
export interface QaFindingRecord {
  id: string;
  patrol_id: string;
  fingerprint: string;
  source_type: string;
  source_butler: string;
  severity: number;
  exception_type: string;
  event_summary: string;
  call_site: string;
  occurrence_count: number;
  first_seen: string;
  last_seen: string;
  dedup_reason: string | null;
  healing_attempt_id: string | null;
  source_session_trigger_source: string | null;
  structured_evidence: Record<string, unknown> | null;
  created_at: string;
}

/** Full patrol with nested findings — GET /api/qa/patrols/:id */
export interface QaPatrolDetail extends QaPatrolSummary {
  findings: QaFindingRecord[];
}

/** A dismissal record — GET /api/qa/dismissals */
export interface QaDismissal {
  fingerprint: string;
  dismissed_until: string;
  dismissed_by: string;
  created_at: string;
}

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

/** 24h aggregate statistics */
export interface QaStats24h {
  patrols_completed: number;
  total_findings: number;
  novel_findings: number;
  dispatched_investigations: number;
}

/** All-time aggregate statistics */
export interface QaAllTimeStats {
  total_patrols: number;
  total_findings: number;
  novel_findings: number;
  dispatched_investigations: number;
}

/** QA staffer summary — GET /api/qa/summary */
export interface QaSummary {
  last_patrol: QaPatrolSummary | null;
  stats_24h: QaStats24h;
  stats_all_time: QaAllTimeStats;
  active_sources: string[];
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
  status?: string;
}

/** Params for listing known issues */
export interface QaKnownIssuesParams {
  source_butler?: string;
  severity?: number;
  dismissed?: boolean;
  offset?: number;
  limit?: number;
}

/** A single investigation record — GET /api/qa/investigations */
export interface QaInvestigation {
  id: string;
  fingerprint: string;
  butler_name: string;
  status: string;
  severity: number;
  exception_type: string;
  call_site: string;
  sanitized_msg: string | null;
  pr_url: string | null;
  pr_number: number | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

/** Params for listing investigations */
export interface QaInvestigationsParams {
  status?: string;
  offset?: number;
  limit?: number;
}

/** A single day's patrol aggregates — GET /api/qa/trends */
export interface QaTrendsDay {
  date: string;
  patrols_completed: number;
  total_findings: number;
  novel_findings: number;
  dispatched_count: number;
  success_rate: number;
}

/** Per-source finding count — GET /api/qa/trends */
export interface QaSourceBreakdown {
  source_type: string;
  count: number;
}

/** 7-day trend data — GET /api/qa/trends */
export interface QaTrends {
  days: QaTrendsDay[];
  source_breakdown: QaSourceBreakdown[];
}

/** Response from POST /api/qa/force-patrol */
export interface ForcePatrolResponse {
  accepted: boolean;
  message: string;
}

/** A recent healing attempt relevant to circuit breaker state */
export interface CircuitBreakerAttempt {
  id: string;
  status: string;
  closed_at: string;
}

/** Current state of the QA dispatch circuit breaker — GET /api/qa/circuit-breaker */
export interface CircuitBreakerStatus {
  tripped: boolean;
  threshold: number;
  recent_statuses: string[];
  recent_attempts: CircuitBreakerAttempt[];
}

/** Response from POST /api/qa/circuit-breaker/reset */
export interface CircuitBreakerResetResponse {
  reset: boolean;
  message: string;
}

/** QA repository configuration — GET /api/qa/settings/repo */
export interface QaRepoConfig {
  repo_url: string;
  clone_path: string | null;
  last_synced_at: string | null;
  last_sync_error: string | null;
  created_at: string;
  updated_at: string;
}

/** Request body for PUT /api/qa/settings/repo */
export interface QaRepoConfigUpdate {
  repo_url: string;
}

/** Response from POST /api/qa/settings/repo/sync */
export interface QaRepoSyncResponse {
  synced: boolean;
  clone_path: string | null;
  error: string | null;
}

/** A single entry in the QA repository whitelist. */
export interface QaAllowedRepo {
  id: string;
  owner: string;
  repo: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

/** Request body for adding a repository to the whitelist. */
export interface QaAllowedRepoCreate {
  owner_repo: string;
  enabled?: boolean;
}

/** Request body for toggling the enabled flag on a whitelisted repository. */
export interface QaAllowedRepoPatch {
  enabled: boolean;
}

// ---------------------------------------------------------------------------
// Runtime Config
// ---------------------------------------------------------------------------

/** Response from GET /api/butlers/{name}/runtime-config. */
export interface RuntimeConfigResponse {
  butler_name: string;
  core_groups: string[] | null;
  max_concurrent: number;
  max_queued: number;
  seeded_at: string | null;
  updated_at: string | null;
  field_tiers: Record<string, "hot" | "cold">;
}

/** Request body for PATCH /api/butlers/{name}/runtime-config. */
export interface RuntimeConfigPatch {
  core_groups?: string[] | null;
  max_concurrent?: number;
  max_queued?: number;
}

/** Response from PATCH /api/butlers/{name}/runtime-config. */
export interface RuntimeConfigPatchResponse {
  config: RuntimeConfigResponse;
  restart_required: string[];
}

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
export type GoogleHealthConnectorState =
  | "healthy"
  | "degraded"
  | "error"
  | "not_configured";

/** Response from GET /api/connectors/google-health/status. */
export interface GoogleHealthStatusResponse {
  connected: boolean;
  /** Full Google Health scope URLs on the primary account's granted_scopes. */
  scopes_granted: string[];
  /** Most recent ingest timestamp (ISO 8601), or null when none has occurred. */
  last_ingest_at: string | null;
  /** Last token refresh timestamp, or null. */
  last_token_refresh_at: string | null;
  /** Most recently observed X-RateLimit-Remaining, or null (distinct from 0). */
  rate_limit_remaining: number | null;
  /** metadata.google_health_test_mode on the primary Google account row. */
  test_mode: boolean;
  state: GoogleHealthConnectorState;
  /** Count of sleep-session ingestion events in the last 7 days. */
  sleep_sessions_7d: number;
  /** Count of daily-summary ingestion events in the last 7 days. */
  daily_summaries_7d: number;
}

/** Response from DELETE /api/connectors/google-health/disconnect. */
export interface GoogleHealthDisconnectResponse {
  success: boolean;
  message: string;
  /** Scope URLs that were stripped from granted_scopes. */
  scopes_removed: string[];
}

// ---------------------------------------------------------------------------
// Chronicler dashboard types
// ---------------------------------------------------------------------------

/** Per-source contribution within an aggregate bucket. */
export interface ChroniclerSourceBreakdownEntry {
  source_name: string;
  total_seconds: number;
  episode_count: number;
  tombstoned: boolean;
}

/** One category bucket from GET /api/chronicler/aggregate/by-category. */
export interface ChroniclerCategoryBucket {
  category: string;
  total_seconds: number;
  episode_count: number;
  source_breakdown: ChroniclerSourceBreakdownEntry[];
  /** Least-precise precision value across contributing rows. */
  precision: string;
  /** Shortest non-NULL retention_days across contributing rows, or null. */
  retention_floor_days: number | null;
}

/** Response envelope for GET /api/chronicler/aggregate/by-category. */
export interface ChroniclerCategoryBuckets {
  start_at: string;
  end_at: string;
  tz: string;
  /** Sorted by total_seconds DESC, then category ASC. */
  buckets: ChroniclerCategoryBucket[];
}

/** Query parameters for GET /api/chronicler/aggregate/by-category. */
export interface ChroniclerAggregateByCategoryParams {
  start_at: string;
  end_at: string;
  tz?: string;
  /** Comma-separated privacy tiers to include. Default: exclude restricted. */
  privacy_tier?: string;
  include_tombstoned?: boolean;
}

/** One (day, category) bucket from GET /api/chronicler/aggregate/by-day. */
export interface ChroniclerAggregateByDayRow {
  /** ISO-8601 date string YYYY-MM-DD for the bucket's calendar day. */
  day: string;
  category: string;
  total_seconds: number;
  episode_count: number;
  /** Inclusive start of the calendar day in the requested timezone. */
  day_start: string;
  /** Exclusive end of the calendar day in the requested timezone. */
  day_end: string;
  source_breakdown: ChroniclerSourceBreakdownEntry[];
  /** Least-precise precision value across contributing rows. */
  precision: string;
  /** Shortest non-NULL retention_days across contributing rows, or null. */
  retention_floor_days: number | null;
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

/** Per-subsource projection checkpoint detail. */
export interface ChroniclerSubsourceCheckpoint {
  subsource: string;
  last_run_at: string | null;
  last_error: string | null;
}

/** Runtime state for a single source adapter, joined with projection checkpoints. */
export interface ChroniclerSourceStateRow {
  source_name: string;
  chronicler_compatibility: string;
  read_surface: string | null;
  boundary_semantics: string | null;
  optional_schema: boolean;
  active: boolean;
  inactive_reason: string | null;
  last_run_at: string | null;
  last_error: string | null;
  subsource_checkpoints: ChroniclerSubsourceCheckpoint[] | null;
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
export interface ChroniclerEpisode {
  id: string;
  source_name: string;
  source_ref: string;
  episode_type: string;
  start_at: string;
  end_at: string | null;
  precision: string;
  title: string | null;
  payload: Record<string, unknown>;
  privacy: string;
  retention_days: number | null;
  tombstone_at: string | null;
  canonical_start_at: string;
  canonical_end_at: string | null;
  canonical_title: string | null;
  canonical_privacy: string;
  corrected_at: string | null;
  correction_note: string | null;
  created_at: string;
  updated_at: string;
  /**
   * Stable category string derived from `(source_name, episode_type)` by the
   * backend (`chronicler.aggregations.category_for`). Always emitted by the
   * backend; one of the values in the lane taxonomy (e.g. `work`, `calendar`,
   * `music`, ...) or `other` when the source/type pair is unmapped.
   */
  category: string;
}

/**
 * Fresh day-close cache response: prose + provenance refs.
 * Returned when cache_built_at >= all invalidating events in the window.
 */
export interface ChroniclerDayCloseFreshResponse {
  stale: false;
  prose: string;
  provenance_refs: string[];
  cache_built_at: string;
}

/**
 * Stale day-close cache response: cache exists but has been invalidated.
 * Returned when any episode/point_event/override in the window changed after cache_built_at.
 */
export interface ChroniclerDayCloseStaleResponse {
  stale: true;
  cache_built_at: string;
  last_invalidating_event_at: string;
}

/** Union of fresh and stale day-close responses. */
export type ChroniclerDayCloseResponse =
  | ChroniclerDayCloseFreshResponse
  | ChroniclerDayCloseStaleResponse;

/** Query parameters for GET /api/chronicler/aggregate/day-close. */
export interface ChroniclerDayCloseParams {
  /** ISO-8601 date string (YYYY-MM-DD) or datetime for the window start. */
  window_start: string;
  /** ISO-8601 date string (YYYY-MM-DD) or datetime for the window end. */
  window_end: string;
}

/** A single Chronicler point event (corrected view). */
export interface ChroniclerPointEvent {
  id: string;
  source_name: string;
  source_ref: string;
  event_type: string;
  occurred_at: string;
  precision: string;
  title: string | null;
  payload: Record<string, unknown>;
  privacy: string;
  retention_days: number | null;
  tombstone_at: string | null;
  canonical_occurred_at: string;
  canonical_title: string | null;
  canonical_privacy: string;
  corrected_at: string | null;
  correction_note: string | null;
  created_at: string;
  updated_at: string;
}

/** A single Chronicler override record. */
export interface ChroniclerOverride {
  id: string;
  target_kind: string;
  target_id: string;
  corrected_start_at: string | null;
  corrected_end_at: string | null;
  corrected_title: string | null;
  corrected_privacy: string | null;
  corrected_tombstone_at: string | null;
  note: string | null;
  submitted_by: string | null;
  created_at: string;
}

/**
 * Response from POST /api/chronicler/aggregate/day-close/refresh.
 * Returned when dispatch succeeds and a fresh cache row is written.
 */
export interface ChroniclerDayCloseRefreshResponse {
  cache_key: string;
  cache_built_at: string;
}

/** Request body for POST /api/chronicler/aggregate/day-close/refresh. */
export interface ChroniclerDayCloseRefreshRequest {
  /** ISO-8601 date (YYYY-MM-DD). */
  date: string;
  /** IANA timezone. Default "UTC". */
  tz?: string;
}

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

/**
 * Response from POST /api/chronicler/episodes/{id}/explain.
 * Returned when the per-episode LLM drilldown succeeds and a cache row is written.
 */
export interface ChroniclerEpisodeExplainResponse {
  episode_id: string;
  cache_key: string;
  cache_built_at: string;
}

// ---------------------------------------------------------------------------
// Relationship butler: entity-level tab types
// ---------------------------------------------------------------------------

/** A note fact for a relationship entity (predicate='contact_note'). */
export interface EntityNote {
  id: string;
  content: string;
  emotion: string | null;
  created_at: string | null;
}

/** An interaction fact for a relationship entity (predicate LIKE 'interaction_%'). */
export interface EntityInteraction {
  id: string;
  /** Predicate suffix, e.g. 'meeting' from 'interaction_meeting'. */
  type: string;
  summary: string | null;
  occurred_at: string | null;
  direction: string | null;
  group_size: string | null;
}

/** A gift fact for a relationship entity (predicate='gift'). */
export interface EntityGift {
  id: string;
  description: string | null;
  occasion: string | null;
  status: string | null;
  created_at: string | null;
}

/** A loan fact for a relationship entity (predicate='loan'). */
export interface EntityLoan {
  id: string;
  description: string | null;
  amount_cents: string | null;
  currency: string | null;
  direction: string | null;
  settled: string | null;
  settled_at: string | null;
  created_at: string | null;
}

/** A single entry in a relationship entity's unified timeline. */
export interface EntityTimelineItem {
  kind: string;
  id: string;
  content: string | null;
  valid_at: string | null;
  predicate: string;
  metadata: Record<string, unknown> | null;
}

/** A contact linked to an entity, for the entity detail page. */
export interface LinkedContactSummary {
  id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
}

/** One row of message activity for an entity, grouped by channel + thread. */
export interface MessageThreadSummary {
  source_channel: string | null;
  thread_identity: string | null;
  sender_identity: string | null;
  message_count: number;
  last_received_at: string | null;
  last_direction: string | null;
  last_snippet: string | null;
}

/** An important date for one of an entity's contacts (birthday, anniversary, etc). */
export interface EntityImportantDate {
  contact_id: string;
  contact_name: string;
  label: string;
  month: number;
  day: number;
  year: number | null;
  /** ISO date (YYYY-MM-DD) of the next future occurrence of (month, day). */
  upcoming_date: string;
}

/** Body for PATCH /entities/{id}/dunbar-tier — null clears the pin. */
export interface DunbarTierOverrideRequest {
  tier: number | null;
}

/** Response envelope for PATCH /entities/{id}/dunbar-tier. */
export interface DunbarTierOverrideResponse {
  entity_id: string;
  contact_id: string;
  tier: number | null;
  action: string;
  message: string;
}

/**
 * Relationship-scoped entity detail from GET /api/relationship/entities/{id}.
 * Separate from the memory-butler EntityDetail — this surface is activity-focused.
 */
export interface RelationshipEntityDetail {
  id: string;
  canonical_name: string;
  entity_type: string;
  aliases: string[];
  roles: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// System endpoints — GET /api/system/*
// ---------------------------------------------------------------------------

/** Software identity and process uptime facts. */
export interface InstanceFacts {
  version: string;
  uptime_seconds: number;
  started_at: string;
}

/** Disk footprint of a single butler schema. */
export interface SchemaSize {
  schema_name: string;
  size_bytes: number;
  table_count: number;
}

/** Disk footprint of a single table. */
export interface TableSize {
  schema_name: string;
  table_name: string;
  size_bytes: number;
}

/** PostgreSQL catalog size facts for the running database. */
export interface DatabaseFacts {
  total_size_bytes: number;
  schemas: SchemaSize[];
  largest_tables: TableSize[];
  growth_rate_bytes_per_day: number | null;
}

/** Single backup event in the backup history list. */
export interface BackupEvent {
  completed_at: string;
  size_bytes: number;
  status: "success" | "failed";
}

/** Backup recency and source reachability facts. */
export interface BackupFacts {
  last_backup_at: string | null;
  last_backup_size_bytes: number | null;
  backup_source_reachable: boolean;
  backup_history: BackupEvent[];
}

/** A single external actor that has received data from this instance. */
export interface EgressActor {
  actor_id: string;
  display_name: string;
  last_seen_at: string;
  total_calls: number;
  data_types: string[];
}

/** Aggregated catalog of external-actor egress events. */
export interface EgressCatalog {
  actors: EgressActor[];
  catalog_covers_from: string | null;
}

/** Per-butler liveness and session snapshot. */
export interface ButlerHeartbeat {
  name: string;
  last_heartbeat_at: string | null;
  last_session_at: string | null;
  active_session_count: number;
  heartbeat_age_seconds: number | null;
  error?: string | null;
}

/** Collection of per-butler heartbeat entries. */
export interface HeartbeatFacts {
  butlers: ButlerHeartbeat[];
}

// ---------------------------------------------------------------------------
// Dashboard briefing (GET /api/dashboard/briefing)
//
// See: openspec/changes/dashboard-overview-briefing/specs/dashboard-briefing/spec.md
// and about/heart-and-soul/design-language.md (Editorial archetype).
// ---------------------------------------------------------------------------

/** Five state classes the briefing classifier produces. */
export type BriefingStateClass =
  | "urgent"
  | "busy"
  | "mild"
  | "degraded-quiet"
  | "quiet";

/** Whether the elaboration paragraph came from the LLM or the templated fallback. */
export type BriefingSource = "llm" | "fallback";

/**
 * Server-composed briefing object the Overview page renders verbatim.
 *
 * `greet` and `headline` are deterministic templates; `elaboration` is one to
 * three sentences from Claude Haiku 4.5 with a templated fallback. `source`
 * tells the status pill which path produced the elaboration. Cached per-owner
 * for 5 minutes (the hook below mirrors that TTL).
 */
export interface Briefing {
  greet: string;
  headline: string;
  elaboration: string;
  source: BriefingSource;
  state_class: BriefingStateClass;
  generated_at: string;
}

// ---------------------------------------------------------------------------
// Chronicles editorial briefing (bu-i29ix) -- distinct from the dashboard
// briefing above. Backed by /api/chronicler/briefing|attention|kpi.
// ---------------------------------------------------------------------------

/** State classes the chronicles briefing classifier produces. */
export type ChroniclesStateClass = "urgent" | "busy" | "mild" | "quiet";

/** Source of the voice paragraph in the chronicles briefing. */
export type ChroniclesVoiceSource = "llm·cached" | "templated" | "stale";

export interface ChroniclesAttentionItem {
  kind: "anomaly" | "source_health" | "open_correction" | string;
  severity: "high" | "medium" | "low" | string;
  title: string;
  detail: string | null;
  action_href: string | null;
}

export interface ChroniclesLaneHours {
  lane: string;
  hours: number;
}

export interface ChroniclesStreaks {
  sleep: number;
  exercise: number;
}

export interface ChroniclesKpi {
  hours_by_top_lanes: ChroniclesLaneHours[];
  longest_episode_minutes: number;
  longest_episode_title: string | null;
  longest_gap_minutes: number;
  sleep_minutes: number;
  streaks: ChroniclesStreaks;
}

export interface ChroniclesRecentDay {
  date: string;
  total_minutes: number;
  top_lane: string | null;
  episode_count: number;
}

export interface ChroniclesBriefing {
  date: string;
  state_class: ChroniclesStateClass;
  headline: string;
  voice_paragraph: string;
  voice_source: ChroniclesVoiceSource;
  kpi: ChroniclesKpi;
  attention_items: ChroniclesAttentionItem[];
  recent_days: ChroniclesRecentDay[];
}

// ---------------------------------------------------------------------------
// Finance butler types (GET /api/finance/*)
// ---------------------------------------------------------------------------

export interface FinanceTransaction {
  id: string;
  posted_at: string;
  merchant: string;
  normalized_merchant: string | null;
  description: string | null;
  /** Numeric amount as string to preserve precision. */
  amount: string;
  currency: string;
  direction: "debit" | "credit";
  category: string;
  inferred_category: string | null;
  payment_method: string | null;
  account_id: string | null;
  receipt_url: string | null;
  external_ref: string | null;
  source_message_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FinanceSubscription {
  id: string;
  service: string;
  /** Numeric amount as string. */
  amount: string;
  currency: string;
  frequency: string;
  next_renewal: string;
  status: "active" | "paused" | "cancelled";
  auto_renew: boolean;
  payment_method: string | null;
  account_id: string | null;
  source_message_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FinanceBill {
  id: string;
  payee: string;
  /** Numeric amount as string. */
  amount: string;
  currency: string;
  due_date: string;
  frequency: string;
  status: "pending" | "paid" | "overdue";
  payment_method: string | null;
  account_id: string | null;
  source_message_id: string | null;
  statement_period_start: string | null;
  statement_period_end: string | null;
  paid_at: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FinanceSpendingGroup {
  key: string;
  /** Numeric amount as string. */
  amount: string;
  count: number;
}

export interface FinanceSpendingSummary {
  start_date: string;
  end_date: string;
  currency: string;
  /** Numeric total as string. */
  total_spend: string;
  groups: FinanceSpendingGroup[];
}

export interface FinanceUpcomingBillItem {
  bill: FinanceBill;
  urgency: "overdue" | "due_today" | "due_soon" | "upcoming";
  days_until_due: number;
}

export interface FinanceUpcomingBillsResponse {
  items: FinanceUpcomingBillItem[];
  /** Numeric total as string. */
  total_amount: string;
  count: number;
  days_ahead: number;
  include_overdue: boolean;
}

export interface FinanceBillListParams {
  status?: string;
  payee?: string;
  offset?: number;
  limit?: number;
}

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

export interface FinanceSpendingSummaryParams {
  start_date?: string;
  end_date?: string;
  group_by?: "category" | "merchant" | "week" | "month";
}

export interface FinanceUpcomingBillsParams {
  days_ahead?: number;
  include_overdue?: boolean;
}

// ---------------------------------------------------------------------------
// Travel butler types (bu-0eac9)
// ---------------------------------------------------------------------------

/** A travel trip container. */
export interface TravelTrip {
  id: string;
  name: string;
  destination: string;
  start_date: string;
  end_date: string;
  status: "planned" | "active" | "completed" | "cancelled";
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** A transport leg (flight, train, bus, ferry) within a trip. */
export interface TravelLeg {
  id: string;
  trip_id: string;
  type: string;
  carrier: string | null;
  departure_airport_station: string | null;
  departure_city: string | null;
  departure_at: string;
  arrival_airport_station: string | null;
  arrival_city: string | null;
  arrival_at: string;
  confirmation_number: string | null;
  pnr: string | null;
  seat: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** An accommodation (hotel, airbnb, hostel) within a trip. */
export interface TravelAccommodation {
  id: string;
  trip_id: string;
  type: string;
  name: string | null;
  address: string | null;
  check_in: string | null;
  check_out: string | null;
  confirmation_number: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** A reservation (car rental, restaurant, activity, tour) within a trip. */
export interface TravelReservation {
  id: string;
  trip_id: string;
  type: string;
  provider: string | null;
  datetime: string | null;
  confirmation_number: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** A travel document (boarding pass, visa, insurance, receipt) attached to a trip. */
export interface TravelDocument {
  id: string;
  trip_id: string;
  type: string;
  blob_ref: string | null;
  expiry_date: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

/** A single entry in a trip's chronological timeline. */
export interface TravelTimelineEntry {
  entity_type: string;
  entity_id: string;
  sort_key: string | null;
  summary: string;
}

/** An alert or pre-trip action item for a trip. */
export interface TravelAlert {
  type: string;
  message: string;
  severity: "high" | "medium" | "low";
}

/** Full trip summary with all linked entities and timeline. */
export interface TravelTripSummary {
  trip: TravelTrip;
  legs: TravelLeg[];
  accommodations: TravelAccommodation[];
  reservations: TravelReservation[];
  documents: TravelDocument[];
  timeline: TravelTimelineEntry[];
  alerts: TravelAlert[];
}

/** An upcoming trip with legs, accommodations, and days until departure. */
export interface TravelUpcomingTrip {
  trip: TravelTrip;
  legs: TravelLeg[];
  accommodations: TravelAccommodation[];
  days_until_departure: number | null;
}

/** A pre-trip action item with urgency ranking across upcoming trips. */
export interface TravelPreTripAction {
  trip_id: string;
  trip_name: string;
  type: string;
  message: string;
  severity: "high" | "medium" | "low";
  urgency_rank: number;
}

/** Upcoming travel overview with trips and urgency-ranked pre-trip actions. */
export interface TravelUpcomingModel {
  upcoming_trips: TravelUpcomingTrip[];
  actions: TravelPreTripAction[];
  window_start: string;
  window_end: string;
}

/** Params for listing trips. */
export interface TravelTripsParams {
  status?: string;
  from_date?: string;
  to_date?: string;
  offset?: number;
  limit?: number;
}

/** A document expiring within the requested look-ahead window. */
export interface TravelExpiringDocument {
  id: string;
  trip_id: string;
  type: string;
  name: string | null;
  expiry_date: string;
  days_until_expiry: number;
}

/** Response for the cross-trip expiring-documents aggregation endpoint. */
export interface TravelExpiringDocumentsResponse {
  documents: TravelExpiringDocument[];
}

// ---------------------------------------------------------------------------
// Home butler types
// ---------------------------------------------------------------------------

/** Aggregate statistics about the Home butler's entity snapshot cache. */
export interface HomeSnapshotStatus {
  total_entities: number;
  domains: Record<string, number>;
  oldest_captured_at: string | null;
  newest_captured_at: string | null;
}

/** A single device entry in the home butler device inventory. */
export interface HomeDeviceEntry {
  entity_id: string;
  state: string;
  friendly_name: string | null;
  area_name: string | null;
  domain: string;
  last_updated: string | null;
  health_status: "healthy" | "offline";
}

/** Pagination metadata for the device inventory endpoint. */
export interface HomeDevicePaginationMeta {
  page: number;
  page_size: number;
  total_count: number;
  total_pages: number;
}

/** Paginated response for the device inventory endpoint. */
export interface HomeDeviceInventoryResponse {
  data: HomeDeviceEntry[];
  meta: HomeDevicePaginationMeta;
}

/** A single time-series data point for energy consumption. */
export interface HomeEnergyDataPoint {
  timestamp: string;
  total_kwh: number;
  devices: Record<string, number>;
}

/** A top energy-consuming device entry. */
export interface HomeTopConsumer {
  entity_id: string;
  friendly_name: string | null;
  total_kwh: number;
  percentage: number;
}

/** A maintenance item with computed status. */
export interface HomeMaintenanceItem {
  id: string;
  name: string;
  category: string;
  interval_days: number;
  last_completed_at: string | null;
  next_due_at: string | null;
  status: "overdue" | "due" | "upcoming" | "ok";
  notes: string | null;
}

/** A single entry in the Home Assistant command audit log. */
export interface HomeCommandLogEntry {
  id: number;
  domain: string;
  service: string;
  target: Record<string, unknown> | null;
  data: Record<string, unknown> | null;
  result: Record<string, unknown> | null;
  context_id: string | null;
  issued_at: string;
}

// ---------------------------------------------------------------------------
// Butler logs (bu-iuol4.17)
// ---------------------------------------------------------------------------

/** Severity level for butler log lines. */
export type LogLevel = "DEBUG" | "INFO" | "WARN" | "ERROR";

/** A single structured log line from GET /api/butlers/{name}/logs. */
export interface ButlerLogLine {
  ts: string;
  level: LogLevel;
  msg: string;
  source: string | null;
  request_id: string | null;
  metadata: Record<string, unknown> | null;
}

/** Query parameters for the butler logs endpoint. */
export interface ButlerLogsParams {
  level?: LogLevel;
  since?: string;
  limit?: number;
}

/** Response shape for GET /api/butlers/{name}/logs. */
export interface ButlerLogsResponse {
  lines: ButlerLogLine[];
}

// ---------------------------------------------------------------------------
// Messenger butler (bu-iuol4.34)
// ---------------------------------------------------------------------------

/** Aggregated delivery statistics over a time window. */
export interface MessengerDeliveryStats {
  window_hours: number;
  delivered: number;
  failed: number;
  pending: number;
  retried: number;
  dead_letter: number;
  dispatched_at: string | null;
}

/** Circuit breaker state for a single channel. */
export interface MessengerCircuitChannelEntry {
  name: string;
  state: "closed" | "open" | "half_open";
  last_state_change: string | null;
  failure_rate_15m: number | null;
}

/** Circuit breaker state per channel. source is always 'db_approximation'. */
export interface MessengerCircuitStatus {
  channels: MessengerCircuitChannelEntry[];
  /** Always 'db_approximation' — derived from DB, not live in-memory state. */
  source: "db_approximation";
}

/** Outbound queue depth by channel and priority. */
export interface MessengerQueueDepth {
  total: number;
  by_channel: Record<string, number>;
  by_priority: Record<string, number>;
}

/** A single dead-letter entry. */
export interface MessengerDeadLetterEntry {
  id: string;
  channel: string;
  recipient_id: string | null;
  error_message: string | null;
  attempted_at: string | null;
  retry_count: number;
}

/** Response shape for GET /api/messenger/dead-letters. */
export interface MessengerDeadLetterSummary {
  letters: MessengerDeadLetterEntry[];
}

/** Query params for GET /api/messenger/delivery-stats. */
export interface MessengerDeliveryStatsParams {
  window_hours?: number;
}

/** Query params for GET /api/messenger/dead-letters. */
export interface MessengerDeadLettersParams {
  limit?: number;
}

// ---------------------------------------------------------------------------
// Butler analytics (bu-iuol4.16)
// ---------------------------------------------------------------------------

/** A single hourly bucket from GET /api/butlers/{name}/analytics/hourly-activity. */
export interface HourlyActivityBucket {
  hour_start: string; // ISO datetime string
  sessions_count: number;
  /** 0 = most recent hour; higher = further back. */
  hour_index: number;
}

/** Response from GET /api/butlers/{name}/analytics/hourly-activity. */
export interface HourlyActivity {
  buckets: HourlyActivityBucket[];
}

/** Query params for GET /api/butlers/{name}/analytics/hourly-activity. */
export interface HourlyActivityParams {
  window_hours?: number;
}

/** A single daily bucket from GET /api/butlers/{name}/analytics/daily-activity. */
export interface DailyActivityBucket {
  date: string; // ISO date string
  sessions_count: number;
}

/** Response from GET /api/butlers/{name}/analytics/daily-activity. */
export interface DailyActivity {
  buckets: DailyActivityBucket[];
}

/** Query params for GET /api/butlers/{name}/analytics/daily-activity. */
export interface DailyActivityParams {
  window_days?: 7 | 30;
}

/** A single kind entry from GET /api/butlers/{name}/analytics/session-kinds. */
export interface SessionKindItem {
  kind: string;
  count: number;
}

/** Response from GET /api/butlers/{name}/analytics/session-kinds. */
export interface SessionKindBreakdown {
  kinds: SessionKindItem[];
}

/** Query params for GET /api/butlers/{name}/analytics/session-kinds. */
export interface SessionKindsParams {
  window_days?: number;
}

/** Response from GET /api/butlers/{name}/analytics/latency-stats. */
export interface LatencyStats {
  /** Median session duration in ms, or null when no data in the window. */
  p50_ms: number | null;
  /** 95th-percentile session duration in ms, or null when no data in the window. */
  p95_ms: number | null;
  /** Mean session duration in ms, or null when no data in the window. */
  mean_ms: number | null;
  /** Number of sessions with a recorded duration in the window. */
  count: number;
  /** Most-frequently-used model in the window, or null when no data. */
  model: string | null;
}

/** Query params for GET /api/butlers/{name}/analytics/latency-stats. */
export interface LatencyStatsParams {
  window_days?: number;
}

// ---------------------------------------------------------------------------
// Activity feed (bu-y7lo7)
// ---------------------------------------------------------------------------

/** Discriminated event type for activity feed entries. */
export type ActivityEventType = "session_completed" | "approval_raised" | "memory_write";

/** A single event in the butler activity feed. */
export interface ButlerActivityEvent {
  /** Discriminator field identifying the event source. */
  event_type: ActivityEventType;
  /** ISO 8601 timestamp of the event. */
  ts: string;
  /** Human-readable one-line summary of the event. */
  summary: string;
  /** Optional identifier for the originating entity as a string. */
  entity_id: string | null;
  /** Source-specific payload with additional context. */
  metadata: Record<string, unknown>;
}

/** Response model for GET /api/butlers/{name}/activity-feed. */
export interface ActivityFeed {
  /** Time-ordered list of activity events, newest first. */
  events: ButlerActivityEvent[];
}

/** Query params for GET /api/butlers/{name}/activity-feed. */
export interface ActivityFeedParams {
  limit?: number;
}
