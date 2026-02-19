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
export interface Schedule {
  id: string;
  name: string;
  cron: string;
  prompt: string;
  source: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Payload for creating a new schedule. */
export interface ScheduleCreate {
  name: string;
  cron: string;
  prompt: string;
}

/** Payload for updating an existing schedule (all fields optional). */
export interface ScheduleUpdate {
  name?: string;
  cron?: string;
  prompt?: string;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

/** A key-value state entry from a butler's state store. */
export interface StateEntry {
  key: string;
  value: Record<string, unknown>;
  updated_at: string; // ISO 8601
}

/** Request body for setting a state value. */
export interface StateSetRequest {
  value: Record<string, unknown>;
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
