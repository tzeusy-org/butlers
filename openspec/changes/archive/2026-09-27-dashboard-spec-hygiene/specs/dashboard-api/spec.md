## ADDED Requirements

### Requirement: Dashboard Query Defaults and Refresh

The frontend SHALL fetch dashboard data through shared query hooks with a 30-second default stale time, at most one retry (never retrying an HTTP 401), no background refetch while the tab is hidden, and mutation success invalidating the reads it affects. Refresh cadence for bus-covered reads is specified in `dashboard-shell` "Bus-Aware Poll Architecture".

#### Scenario: Default query behavior
- **WHEN** a dashboard read runs with no per-hook override
- **THEN** its data is considered fresh for 30 seconds
- **AND** a failed read is retried at most once, and an HTTP 401 is not retried
- **AND** interval refetches pause while the browser tab is hidden

#### Scenario: Mutations refresh the reads they affect
- **WHEN** a mutation succeeds
- **THEN** the dashboard invalidates the cached reads whose data the mutation changed, so the affected views refetch without a manual reload

#### Scenario: Debounced search
- **WHEN** `useSearch(query)` is called
- **THEN** the query is debounced by 300ms and only fires when the query length is at least 2 characters

#### Scenario: Conditional query enablement
- **WHEN** a hook receives a nullable identifier (e.g., `useButler(name)`)
- **THEN** the query does not execute until the identifier is available

### Requirement: Audit Log Credential-Key Filter

`GET /api/audit-log?key=` SHALL filter `public.audit_log` rows by canonical credential key, using the same key format as the `/secrets` focus key and the normalisation defined in `core-credentials`. The full read contract is specified in `dashboard-audit-log` "Audit Log Read API".

#### Scenario: Filter by canonical credential key
- **WHEN** `GET /api/audit-log?key=u:google&limit=50` is called
- **THEN** the response is `PaginatedResponse<AuditLogEntry>` filtered to `public.audit_log` rows whose normalised `target` equals the canonical credential key `u:google`
- **AND** the canonical credential-key format matches the focus-key format used by the `/secrets` page: `u:<provider>`, `s:<KEY>`, `c:<id>`
- **AND** a normalisation function (defined in `core-credentials`) is applied to match against existing `target` values written by other writers (including rows written in non-canonical formats)
- **AND** `?since=`, `?actor=`, `?action=`, and `?limit=` are combinable with `?key=`
- **AND** the response uses the `PaginatedResponse<T>` envelope (RFC 0007), not the `ApiResponse<T>` envelope

#### Scenario: Unknown credential key returns empty page
- **WHEN** `GET /api/audit-log?key=u:does-not-exist` is called
- **THEN** the response is an empty `PaginatedResponse` with `meta.total = 0` and `meta.has_more = false`

### Requirement: Cross-Domain API Route Rules

The API SHALL publish its complete route inventory through the FastAPI-generated OpenAPI schema (`/openapi.json`); each route's request and response contract is owned by the per-domain requirement or capability spec that names it. This requirement carries only the cross-domain route rules below.

#### Scenario: Single-session detail is served only by the cross-butler route
- **WHEN** a client needs the detail of one session
- **THEN** it calls the cross-butler fan-out `GET /api/sessions/{id}`, which resolves pinned rows and deep links without a `?butler=` hint because session ids are globally unique
- **AND** no butler-scoped session detail route is mounted; `GET /api/butlers/{name}/sessions` serves only the butler-scoped list

#### Scenario: Ingestion-events list uses the cursor envelope and channels filter
- **WHEN** `GET /api/ingestion/events` is called with `limit` and an optional opaque `cursor` taken from the preceding response
- **THEN** the response uses the cursor envelope `{ "data": T[], "meta": { "next_cursor": string | null, "has_more": boolean } }` and returns no `total` or `offset`
- **AND** `channels` is the primary comma-separated source-channel filter
- **AND** the single-value `source_channel` parameter is accepted only as server-side compatibility, is not exposed by the frontend client, and is ignored when `channels` is also present

#### Scenario: Schedule Execution Semantics
- **WHEN** the dashboard displays or interprets schedule data
- **THEN** `Schedule.source` describes the schedule origin (`toml` for TOML-defined, `db` for dashboard-created); it is NOT the execution mode
- **AND** runtime-mode schedules (those with a `prompt`) execute through `spawner.trigger(..., trigger_source="schedule:<task-name>")` and correlate with `sessions` rows
- **AND** native-mode schedules (those with `dispatch_mode = "job"` and `job_name`) execute deterministic Python jobs directly and may not create `sessions` rows
- **AND** the dashboard treats schedule status fields (`enabled`, `next_run_at`, `last_run_at`) as authoritative regardless of execution mode
- **AND** schedule failures for both execution modes surface through `GET /api/issues` as `scheduled_task_failure:<schedule-name>`

#### Scenario: Dashboard conversation stream and Stop semantics
- **WHEN** the dashboard submits a user turn
- **THEN** the API opens durable control keyed by the immutable `message_id` before external ingress, and only the caller with the `dispatch` claim may invoke `ingest.v1`
- **AND** a caller observing `accepted` observes the original request, while `pending` or `cancelling` yields `INGEST_IN_PROGRESS` plus `done` and never creates a replay
- **AND** confirmed cancellation yields `SESSION_CANCELLED`; an unprovable recovered predecessor yields `TURN_OUTCOME_UNKNOWN`; both suppress automatic replay
- **AND** the raw `ConversationCancelResponse` documents exactly one truthful Stop result (`cancelled`, `already_finished`, or unconfirmed), while non-2xx failures retain `ErrorResponse`

#### Scenario: Notifications degraded source is named, not rendered as an all-clear
- **WHEN** `GET /api/notifications` or `GET /api/notifications/stats` returns
  HTTP 200 but the Switchboard notifications source was unreachable (so the
  counts are zero placeholders and the list page is empty)
- **THEN** the response carries `source_available: false` (following the
  fleet-wide degraded-envelope convention); the field is absent or `true` when
  the source answered
- **AND** the frontend notifications page SHALL NOT render the fabricated zeros
  as a truthful tally — the stats tiles show an em-dash (not a green `0.0%`
  failure rate) and the feed shows a named `SourceDegradedNote` naming the
  unreachable source rather than the calm "No notifications found" empty state
- **AND** a reachable-but-empty source (`source_available` absent or `true`
  with genuine zeros) keeps its honest zeros and empty state

#### Scenario: Manual retry re-sends a failed notification and links the new attempt
- **WHEN** `POST /api/notifications/{id}/retry` is called on a notification
  whose stored `status` is `failed`
- **THEN** the backend re-invokes delivery in-process (the same
  approval-push-runtime pattern the dashboard-API process already uses to
  call `deliver()` without a `switchboard_client` MCP connection), using the
  envelope persisted at delivery time (`metadata.notify_request`) or, for
  older rows that predate it, a synthetic envelope built from the row's own
  `channel`/`recipient`/`message` columns
- **AND** the original notification is flipped to `status = 'read'` with a
  `metadata.retried_to` marker pointing at the new attempt's id, regardless
  of whether the new attempt itself succeeds or fails again — a human has
  acted on it either way, and a retry that fails again is its own new,
  independently actionable row rather than a reason to leave the original
  stuck in `failed`
- **AND** the response reports the new attempt's own `status` (`sent` or
  `failed`) and `error`, never a fabricated success for the original
- **AND** a best-effort `public.attention_ledger` event is recorded
  (`source="notify"`, `outcome="delivered"` or `"failed"`,
  `notification_ref` set to the new attempt's id) so the manual action is
  traceable the same way an automatic notify() outcome is
- **AND** `GET /api/notifications?status=terminal_failed` and the aggregate
  `failed` count in `GET /api/notifications/stats` no longer include the
  original row afterward, since it is no longer `status = 'failed'`
- **AND** `effective_status` on the original row reports `"retried"` (not
  the generic `"read"`) so the UI communicates what happened, not just that
  it was acknowledged

#### Scenario: Manual retry rejects a non-failed notification
- **WHEN** `POST /api/notifications/{id}/retry` or `.../escalate` is called
  on a notification whose stored `status` is not `failed`
- **THEN** the endpoint returns HTTP 409 without attempting delivery
- **AND** HTTP 404 is returned when `{id}` does not exist, and HTTP 503 when
  the Switchboard pool is unavailable

#### Scenario: Concurrent or replayed retry/escalate never double-delivers
- **WHEN** two `POST .../retry` (or two `.../escalate`) requests for the same
  `{id}` race — a double-click, two open tabs, or a client resending after a
  perceived timeout — and both observe `status = 'failed'` on their initial
  read
- **THEN** immediately before invoking real delivery, the backend atomically
  claims the row with a single conditional `UPDATE notifications SET status
  = 'read' WHERE id = $1 AND status = 'failed' RETURNING *`; only the first
  request's `UPDATE` can match a `'failed'` row, so exactly one request
  proceeds to redeliver and the loser's claim affects zero rows
- **AND** the losing request returns HTTP 409 without invoking delivery — the
  user is never sent the notification twice
- **AND** because the claim (not delivery success) is what leaves `'failed'`
  status, a delivery-adjacent failure after the claim — including
  `_finalize_manual_action`'s own metadata-merge write failing after a
  successful send — cannot leave the row re-claimable: a subsequent client
  retry after such a failure also observes a non-`'failed'` status and gets
  HTTP 409, never a second real send. The tradeoff is a possible orphaned row
  (already `status = 'read'` but missing its `retried_to`/`escalated_to`
  forward-link marker) rather than a duplicate delivery

#### Scenario: Manual escalate re-sends on the owner's alternate channel
- **WHEN** `POST /api/notifications/{id}/escalate` is called on a failed
  `telegram` or `email` notification
- **THEN** the backend swaps to the other channel (`telegram` -> `email`,
  `email` -> `telegram`) and resolves the owner's contact for it via
  `resolve_owner_entity_info` — the same owner-credential lookup the
  dashboard's connector actions already use for owner-directed delivery
- **AND** HTTP 422 is returned, without attempting delivery, when the
  channel is neither `telegram` nor `email` (no alternate-channel resolver
  is wired for other channels), or when the owner has no contact configured
  for the alternate channel
- **AND** on success the original notification is flipped to `status =
  'read'` with a `metadata.escalated_to` marker, following the same
  forward-link and ledger-recording contract as manual retry, and
  `effective_status` reports `"escalated"`

#### Scenario: Approvals degraded pools are named, not rendered as an empty queue
- **WHEN** `GET /api/approvals` or `GET /api/approvals/history` fans out across
  each butler's pool and one or more pools fail their query (a genuine error —
  the request still returns HTTP 200 with the summaries from the pools that
  answered)
- **THEN** the response includes `meta.sources_degraded: string[]` naming the
  dropped pools (following the fleet-wide degraded-envelope convention); the
  field is absent or empty when every queried pool answered
- **AND** the frontend approvals verdict opener SHALL NOT render the calm "No
  approvals waiting." all-clear while a pool is degraded — it names the dropped
  pools inline as a clause that suppresses the all-clear line
- **AND** the approvals queue rail SHALL NOT render the "No pending approvals."
  empty state as an all-clear while a pool is degraded — it names the dropped
  pools via a `SourceDegradedNote` (in place of the empty state when zero rows
  survived, above the rows when some did)
- **AND** a reachable queue with `meta.sources_degraded` absent or empty keeps
  its honest empty state and calm verdict

## MODIFIED Requirements

### Requirement: Issues Aggregation
`src/butlers/api/routers/issues.py` SHALL aggregate live reachability problems and grouped audit-log error history into a single issues feed, holding each acknowledgement against the condition's own recurrence epoch rather than the clock of the request that observed it.

#### Scenario: Issue aggregation
- **WHEN** `GET /api/issues` is called
- **THEN** all butlers are probed for reachability in parallel (critical severity)
- **AND** audit-log errors are grouped by normalized error message with occurrence counts and first/last-seen timestamps
- **AND** scheduled task failures are classified as critical severity
- **AND** results are sorted by recency (newest `last_seen_at` first)

#### Scenario: Capped audit groups are reported honestly
- **WHEN** more than 500 grouped audit-log errors match `GET /api/issues`'s requested window
- **THEN** the endpoint fetches one additional group only as an overflow sentinel, returns no more than the newest 500 audit-derived groups, and includes `meta.truncated: true`
- **AND** live reachability issues remain independently included in the composed feed
- **AND** when 500 or fewer audit groups match, `meta.truncated` is absent so the established complete-response envelope remains unchanged
- **AND** the frontend SHALL render a `SourceDegradedNote` that says some audit-derived issues may be missing, rather than the scoped all-clear empty state, while `meta.truncated` is true

#### Scenario: Audit-derived issue group identity is window-independent and collision-resistant
- **WHEN** an audit-derived `Issue` (`audit_error_group:*` / `scheduled_task_failure:*`) is built from a grouped audit-log row
- **THEN** its `issue_key` is a hash of the group's full, untruncated normalized `error_summary` alone (`audit_grouping.audit_group_key`) — NOT a composite of a truncated display slug and the query's aggregated butler set
- **AND** two distinct error messages that happen to share the same leading substring MUST NOT produce the same `issue_key` (bu-hmdqz.4 fixed a live collision: two unrelated `RuntimeError` groups with 166 vs 2,860 occurrences shared one key under the old 80-char-truncated-slug scheme, so acknowledging one silently acknowledged both)
- **AND** the same `error_summary` MUST produce the same `issue_key` regardless of the set of butlers or schedule names the query happened to aggregate over it (that aggregate is window-dependent — e.g. single-butler in a 7-day feed query vs multi-butler in an all-time drill-down re-derivation — and is not part of the group's identity), so a group's key never disagrees between the feed and its own occurrences drill-down
- **AND** the reachability lane (`type == "unreachable"`) is unaffected and keeps composing `issue_key` as `type::butler` (`compute_issue_key`), since neither component there is a windowed aggregate

#### Scenario: Issues degraded sources are named, not rendered as an all-clear
- **WHEN** `GET /api/issues` runs its DB-backed sources (grouped audit errors,
  the acknowledgement watermarks, and the reachability condition ledger) and one
  or more fail their query for a genuine reason — a dropped connection, a
  timeout, a permission error — the request still returns HTTP 200 with whatever
  the surviving source(s) produced
- **THEN** the response includes `meta.sources_degraded: string[]` naming the
  dropped source(s) (`audit-groups`, `acks`, and/or `reachability-ledger`,
  following the fleet-wide degraded-envelope convention); the field is absent or
  empty when every source answered
- **AND** a *legitimately-absent* source — a pre-migration `public.audit_log`,
  `public.dismissed_issues`, or `public.butler_reachability_conditions` table
  surfaced as `UndefinedTableError` / a "relation does not exist" error — is NOT
  flagged (classify-before-flagging), so a genuinely empty feed is not falsely
  marked degraded
- **AND** the frontend issues panel SHALL NOT render its calm all-clear empty
  state while a source is degraded — it names the dropped source(s) via a
  `SourceDegradedNote` (in place of the empty state when zero issues survived,
  above the rows when some did)
- **AND** a reachable feed with `meta.sources_degraded` absent or empty keeps
  the honest empty state, and a hard transport error keeps the existing error
  state (the degraded note applies only to a 200 with a dropped source)

#### Scenario: An uninterrupted outage is one condition with one stable onset
- **WHEN** `GET /api/issues` probes a butler that is unreachable, repeatedly,
  with no intervening successful probe
- **THEN** every poll extends the SAME row in
  `public.butler_reachability_conditions` — advancing `last_seen_at` and
  `observations` but never `started_at` — via a single atomic upsert whose
  conflict target is the partial unique index `(butler) WHERE resolved_at IS
  NULL`, so two concurrent polls cannot open two competing episodes
- **AND** the projected `Issue` carries that episode's onset as both
  `first_seen_at` and `recurrence_at`, while `last_seen_at` reports when the
  butler was last PROBED
- **AND** an acknowledgement of that condition therefore continues to hold
  across arbitrarily many subsequent polls

#### Scenario: Recovery closes a condition and a later failure is a new recurrence
- **WHEN** a butler that had an open reachability condition answers a probe
- **THEN** that episode's `resolved_at` is stamped and it is never revived; a
  second successful probe changes nothing further
- **AND** a subsequent down transition opens a NEW episode whose `started_at` is
  strictly later than the earlier acknowledgement's watermark, so the condition
  correctly reappears in the active feed with no owner action

#### Scenario: An acknowledgement is held against the recurrence epoch, not the observation clock
- **WHEN** `list_issues` decides whether an acknowledged issue has recurred
- **THEN** it compares the ack watermark against the issue's `recurrence_at`,
  falling back to `last_seen_at` only when no separate epoch exists
- **AND** for audit-derived groups `recurrence_at` IS `last_seen_at`, so the
  established acknowledge-until-recurrence behaviour for that lane is unchanged
- **AND** `POST /api/issues/dismiss` derives a reachability key's watermark
  SERVER-side from the open episode's onset, ignoring any posted probe clock
- **AND** when the ledger cannot be read for that derivation the endpoint
  returns 503 and records no acknowledgement, rather than persisting one that is
  guaranteed to lapse on the next poll

#### Scenario: Issues empty copy names the scope it searched
- **WHEN** the Issues page renders an empty result
- **THEN** the panel's empty state and the verdict opener's all-clear name the
  active scope — the time window plus any pinned group, severity, butler, or
  text filter — instead of asserting a fleet-wide calm the request never
  established
- **AND** the page pins the feed to a single exact `issue_key` when the Audit
  door's `?group=` deep link is followed, matching the whole key rather than a
  substring, with a clearable affordance carrying an accessible name

#### Scenario: The issues feed writes the ledger it reads
- **WHEN** `GET /api/issues` completes a reachability probe round
- **THEN** the same request records that round into
  `public.butler_reachability_conditions` — the endpoint is the sole writer and
  no background poller exists — and this side effect is documented at the
  endpoint
- **AND** a genuine failure of that write is surfaced through
  `meta.sources_degraded`, never swallowed, so the feed cannot present a
  request-time fallback onset as a durable acknowledgement

#### Scenario: Issues page layout and issue rows
- **WHEN** the operator navigates to `/issues`
- **THEN** the page header reads "Issues" with subtitle "Grouped errors and warnings across all butlers, newest first."
- **AND** each issue row shows its severity, the affected butler (or "N butlers" when the group spans more than one), description, occurrence count, and first-seen and last-seen times
- **AND** an issue with a non-null `link` offers a "View" client-side link to the linked resource, and every active issue offers "Acknowledge"

#### Scenario: Single-butler issues offer direct remediation
- **WHEN** an issue names a single real butler
- **THEN** a "Run schedule now" action forces that butler's scheduler to run immediately via `POST /api/butlers/{name}/tick`
- **AND** when the issue's `type` is `"unreachable"`, a "Ping butler" action rechecks reachability immediately via a live `GET /api/butlers/{name}`
- **AND** a multi-butler issue offers neither action, since there is no single butler to target

#### Scenario: Acknowledgement holds until the group recurs
- **WHEN** the operator acknowledges an issue
- **THEN** the issue leaves the active list and the acknowledgement is persisted server-side via `POST /api/issues/dismiss`, keyed by the server-computed `issue_key` together with the issue's recurrence watermark
- **AND** the acknowledgement holds across refreshes and browsers only until the group recurs past that watermark, after which the issue reappears in the active feed with no owner action
- **AND** an acknowledgement recorded without a watermark, or for an issue type that never carries a timestamp, holds indefinitely
- **AND** the acknowledged view (`include_dismissed=true`) lists only acknowledged issues and offers "Restore" to undo an acknowledgement before it lapses

## REMOVED Requirements

### Requirement: API Endpoint Inventory

**Reason**: The per-domain endpoint tables duplicated the generated OpenAPI schema and the per-domain requirements that own each route.

**Migration**: The route list is the OpenAPI schema at `/openapi.json`; the cross-domain rules and the conversation, notification, and approvals scenarios carried here are specified in "Cross-Domain API Route Rules" in this spec.

### Requirement: TanStack Query Patterns

**Reason**: Per-hook interval tables, hook names, and cache-key literals are implementation detail and named hooks that no longer exist.

**Migration**: See "Dashboard Query Defaults and Refresh" in this spec and `dashboard-shell` "Bus-Aware Poll Architecture".

### Requirement: Audit Log

**Reason**: Described a retired switchboard table and writer helper; the audit-log read contract is owned by `dashboard-audit-log`.

**Migration**: See `dashboard-audit-log` "Audit Log Primitive" and "Audit Log Read API"; the credential-key filter is specified in "Audit Log Credential-Key Filter" in this spec.

### Requirement: Ingestion Rules and Thread Affinity

**Reason**: Hook-level detail; the rule endpoints and dry-run are owned by `ingestion-policy`, and the dashboard has no thread-affinity UI.

**Migration**: See `ingestion-policy` for `/api/switchboard/ingestion-rules` and `dashboard-ingestion-dispatch-console` "Filters Pipeline" for the rules UI.

### Requirement: Backfill Job Management

**Reason**: The backfill hooks and UI no longer exist in the dashboard.

**Migration**: None; backfill is not a dashboard surface.

### Requirement: Ingestion Analytics

**Reason**: Describes cache sharing between the deleted Overview and Connectors tabs.

**Migration**: See `dashboard-ingestion-dispatch-console` for the `/ingestion` routes.

### Requirement: Ingestion Timeline Tab Frontend Hooks

**Reason**: Hook-level detail for a tab that is now the `/ingestion` route itself.

**Migration**: See `dashboard-ingestion-dispatch-console` "Timeline Ledger".
