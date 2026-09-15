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

## ADDED Requirements

### Requirement: Exact Audit-To-Issues Evidence Door
`GET /api/issues/group-for-audit/{audit_id}` SHALL resolve one `public.audit_log` row to the exact Issues group the feed itself would compute, and SHALL state absence explicitly rather than returning an empty result the caller could render as calm.

#### Scenario: A failure row resolves to the group identity the feed computes
- **WHEN** `GET /api/issues/group-for-audit/{audit_id}` is called for an
  `audit_log` row whose `result` is `error`
- **THEN** the group is resolved through the same `normalized_errors` CTE the
  feed uses, so the returned `issue_key` is byte-identical to the feed's and the
  occurrence count includes every row that normalizes onto the same
  `error_summary`
- **AND** the response carries an `issues_href` that opens the Issues page on
  exactly that one group, in the window the answer was computed in

#### Scenario: The resolution window widens to contain the row
- **WHEN** the caller does not pin a window
- **THEN** the server selects the narrowest window from the Issues page's own
  ladder (`24h`, `7d`, `30d`, `all`) that actually contains the audit row, so a
  failure older than the page's seven-day default is not resolved against a view
  that structurally cannot hold its group
- **AND** the chosen window is returned with the answer and preserved by the
  link, so the destination shows the group the answer describes

#### Scenario: Absence is stated, and is distinct from an unavailable lookup
- **WHEN** the named row is not an error row, or its group has no occurrences
  inside the resolved window
- **THEN** the response is `found: false` with a `reason` distinguishing the two
  cases, and carries no link to a group that does not exist
- **AND** when the lookup itself cannot be performed the endpoint returns 503,
  never `found: false` — "we could not check" and "there is nothing there" are
  different claims
- **AND** an `audit_id` naming no row at all returns 404
- **AND** the Audit Log renders these three outcomes as three distinct things: a
  link, an explicit statement of absence naming its scope, and a
  `SourceDegradedNote`
