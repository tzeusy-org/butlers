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
// Traces
// ---------------------------------------------------------------------------

/** A recursive span node in a trace tree. */
export interface SpanNode {
  id: string;
  butler: string;
  prompt: string;
  trigger_source: string;
  success: boolean | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  model: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  parent_session_id: string | null;
  children: SpanNode[];
}

/** Lightweight trace representation for list views. */
export interface TraceSummary {
  trace_id: string;
  root_butler: string;
  span_count: number;
  total_duration_ms: number | null;
  started_at: string;
  status: string; // "success" | "failed" | "running" | "partial"
}

/** Full trace detail including the span tree. */
export interface TraceDetail extends TraceSummary {
  spans: SpanNode[];
}

/** Query parameters for trace list endpoints. */
export interface TraceParams {
  offset?: number;
  limit?: number;
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
}

/** Payload for creating a new deterministic job schedule. */
export interface JobScheduleCreate {
  name: string;
  cron: string;
  dispatch_mode: "job";
  job_name: string;
  job_args?: ScheduleJobArgs;
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
  nickname: string | null;
  email: string | null;
  phone: string | null;
  labels: Label[];
  last_interaction_at: string | null;
}

/** Full contact detail with additional fields. */
export interface ContactDetail extends ContactSummary {
  notes: string | null;
  birthday: string | null;
  company: string | null;
  job_title: string | null;
  address: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
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

/** A note attached to a contact. */
export interface Note {
  id: string;
  contact_id: string;
  content: string;
  created_at: string;
  updated_at: string;
}

/** An interaction record for a contact. */
export interface Interaction {
  id: string;
  contact_id: string;
  type: string;
  summary: string;
  details: string | null;
  occurred_at: string;
  created_at: string;
}

/** A gift given to or received from a contact. */
export interface Gift {
  id: string;
  contact_id: string;
  description: string;
  direction: string;
  occasion: string | null;
  date: string;
  value: number | null;
  created_at: string;
}

/** A loan between the user and a contact. */
export interface Loan {
  id: string;
  contact_id: string;
  description: string;
  direction: string;
  amount: number;
  currency: string;
  status: string;
  date: string;
  due_date: string | null;
  created_at: string;
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

/** Response payload for a manual contacts sync trigger. */
export interface ContactsSyncTriggerResponse {
  provider: string;
  mode: "incremental" | "full";
  created: number | null;
  updated: number | null;
  skipped: number | null;
  errors: number | null;
  summary: Record<string, unknown>;
  message: string | null;
}

/** Paginated group list response. */
export interface GroupListResponse {
  groups: Group[];
  total: number;
}

/** An activity feed item for a contact. */
export interface ActivityFeedItem {
  id: string;
  contact_id: string;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

/** Query parameters for the contacts list endpoint. */
export interface ContactParams {
  q?: string;
  label?: string;
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

/** A collection in the General butler. */
export interface GeneralCollection {
  id: string;
  name: string;
  description: string | null;
  entity_count: number;
  created_at: string;
}

/** An entity stored in a General butler collection. */
export interface GeneralEntity {
  id: string;
  collection_id: string;
  collection_name: string | null;
  data: Record<string, unknown>;
  tags: string[];
  created_at: string;
  updated_at: string;
}

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

/** Query parameters for entity search. */
export interface EntityParams {
  q?: string;
  collection?: string;
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
  registered_at: string;
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
  effectiveness_score: number;
  applied_count: number;
  success_count: number;
  harmful_count: number;
  source_butler: string | null;
  created_at: string;
  last_applied_at: string | null;
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
// Approvals
// ---------------------------------------------------------------------------

export interface ApprovalAction {
  id: string;
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

export interface OAuthStatusResponse {
  google: OAuthCredentialStatus;
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
// Triage rule types (switchboard ingestion filters)
// ---------------------------------------------------------------------------

/** Valid rule_type values for triage rules. */
export type TriageRuleType =
  | "sender_domain"
  | "sender_address"
  | "header_condition"
  | "mime_type";

/** Valid action values for triage rules. */
export type TriageRuleAction =
  | "skip"
  | "metadata_only"
  | "low_priority_queue"
  | "pass_through"
  | string; // route_to:<butler>

/** A persisted triage rule returned from the API. */
export interface TriageRule {
  id: string;
  rule_type: TriageRuleType;
  condition: Record<string, unknown>;
  action: TriageRuleAction;
  priority: number;
  enabled: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

/** Request body for POST /api/switchboard/triage-rules. */
export interface TriageRuleCreate {
  rule_type: TriageRuleType;
  condition: Record<string, unknown>;
  action: TriageRuleAction;
  priority: number;
  enabled?: boolean;
}

/** Request body for PATCH /api/switchboard/triage-rules/:id. */
export interface TriageRuleUpdate {
  condition?: Record<string, unknown>;
  action?: TriageRuleAction;
  priority?: number;
  enabled?: boolean;
}

/** Envelope sender for dry-run test. */
export interface TestEnvelopeSender {
  identity: string;
}

/** Envelope payload for dry-run test. */
export interface TestEnvelopePayload {
  headers?: Record<string, string>;
  mime_parts?: Array<Record<string, unknown>>;
}

/** Sample envelope for dry-run test. */
export interface TestEnvelope {
  sender: TestEnvelopeSender;
  payload?: TestEnvelopePayload;
}

/** Request body for POST /api/switchboard/triage-rules/test. */
export interface TriageRuleTestRequest {
  envelope: TestEnvelope;
  rule: TriageRuleCreate;
}

/** Result of a dry-run triage rule test. */
export interface TriageRuleTestResult {
  matched: boolean;
  decision: string | null;
  target_butler: string | null;
  matched_rule_type: string | null;
  reason: string;
}

/** Response for POST /api/switchboard/triage-rules/test. */
export interface TriageRuleTestResponse {
  data: TriageRuleTestResult;
}

/** List params for GET /api/switchboard/triage-rules. */
export interface TriageRuleListParams {
  rule_type?: TriageRuleType;
  enabled?: boolean;
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
