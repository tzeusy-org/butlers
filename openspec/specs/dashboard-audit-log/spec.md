# dashboard-audit-log

## Purpose

`dashboard-audit-log` is the audit-log infrastructure primitive introduced by the settings dispatch console redesign. It is not a dashboard capability per se; it is cross-cutting infrastructure shared by every mutation endpoint in the settings refactor and all future write-bearing endpoints. It owns the `public.audit_log` table (append-only, indefinitely retained), the `audit.append()` helper that every state-changing endpoint calls inside its own transaction, and the `/api/audit-log` read API. The primitive is the prerequisite for permissions, model priority changes, spend rules/ceiling changes, webhook CRUD, approval verbs, and data ops.

## Requirements

### Requirement: Audit Log Primitive
The dashboard SHALL maintain a single, append-only audit log used by every mutation endpoint that changes system state.

#### Scenario: Audit log table shape
- **WHEN** the audit log table is provisioned
- **THEN** `public.audit_log` exists with columns `id BIGSERIAL PRIMARY KEY`, `ts TIMESTAMPTZ NOT NULL DEFAULT now()`, `actor TEXT NOT NULL`, `action TEXT NOT NULL`, `target TEXT`, `note TEXT`, `ip INET`, `request_id UUID`, `metadata JSONB`, `result TEXT`, `error TEXT` (the last three added by migration `core_122` for writer unification)
- **AND** indexes exist on `(ts DESC)`, `(action)`, and `(actor)`
- **AND** no DELETE statement against `audit_log` exists anywhere in the repository (verified by a static-check test).

#### Scenario: audit.append helper contract
- **WHEN** a mutation endpoint succeeds
- **THEN** it calls `audit.append(pool_or_conn, actor, action, *, target=None, note=None, ip=None, request_id=None, metadata=None, result=None, error=None) -> int` returning the new row id (the first positional argument is an asyncpg pool or an already-acquired connection; passing a connection lets the audit insert participate in the caller's open transaction)
- **AND** the call is made INSIDE the same SQL transaction as the state change (commit only after the audit row is written)
- **AND** Prometheus counter `audit_log_appended_total{action}` is incremented after commit.

#### Scenario: audit.append raises on missing table
- **WHEN** `audit.append()` is called and `public.audit_log` does not exist (migration failed or rolled back)
- **THEN** the helper SHALL raise `AuditTableNotAvailableError` (or the equivalent SQLAlchemy `ProgrammingError`)
- **AND** the helper SHALL NOT silently skip or log-and-continue
- **AND** the calling endpoint propagates the exception; the HTTP response is `503 Service Unavailable` with body `{error: "audit_unavailable"}`
- **AND** because the transaction includes both the state change and the audit append, the state change is rolled back automatically.

#### Scenario: Fire-and-forget telemetry shim is exempt from propagation
- **WHEN** a best-effort telemetry call site invokes the `log_audit_entry()` (`butlers.api.routers.audit`) or `write_audit_entry()` (`butlers.core.audit`) compatibility shim rather than calling `audit.append()` directly inside the mutation's own transaction, for example `schedules.py`, `state.py`, `calendar_workspace.py`, `butlers.py` (dashboard butler-run/tick/trigger logging), and the daemon-side `core/audit.py` callers in `telegram.py`, `email.py`, `calendar.py`, and `spawner.py`
- **THEN** the shim SHALL catch `AuditTableNotAvailableError` and log-and-continue (best-effort, non-blocking) rather than propagate it, and SHALL NOT raise `503 Service Unavailable` to the caller
- **AND** this is a deliberate carve-out, not an instance of the "audit.append raises on missing table" scenario above: these call sites emit secondary, non-transactional telemetry about an operation that has already succeeded or is orthogonal to the audited state change (e.g. a scheduler tick, a butler-run log line, an inbound-message record), so a missing audit table must never block or roll back the primary operation
- **AND** this carve-out is distinct from the canonical mutation-endpoint path: the state-changing endpoint that owns the transaction (e.g. permissions, model priority, spend rules, webhook CRUD, approval verbs, data ops) SHALL still call `audit.append()` directly inside its own transaction and propagate `AuditTableNotAvailableError` per the scenario above; a shim call site MUST NOT be introduced as a substitute for that direct, propagating call.

### Requirement: Audit Log Read API
The dashboard SHALL expose paginated read access to the audit log.

#### Scenario: List audit entries
- **WHEN** `GET /api/audit-log?since=&from_date=&to_date=&actor=&action=&key=&result=&kind=&limit=` is called
- **THEN** the response is `PaginatedResponse[AuditLogEntry]` with rows ordered `ts DESC`
- **AND** `limit` defaults to 100 and is clamped to `≤ 1000`
- **AND** `since` accepts an ISO 8601 timestamp; `from_date` and `to_date`
  accept owner-timezone calendar-day keys or full ISO timestamps; `actor`,
  `action`, and `result` accept exact-match strings (`result` filters on the
  outcome column added by `core_122`, e.g. `success`/`error`)
- **AND** `key` filters by normalised credential key
- **AND** `kind=privileged` returns only consequence-bearing actions
  (`approval.*`, the legacy `approvals.policy` mutation, `model.*`,
  `permission.*`, `data.*`, `webhook.*`, and the defined credential lifecycle
  actions) or rows with `result = 'error'`
- **AND** omitting `kind` returns the complete audit history, including routine
  cadence rows, preserving the `?noise=all` dashboard opt-out
- **AND** each returned `AuditLogEntry` projects `metadata`/`result`/`error`
  (added by `core_122`) alongside the base columns, defaulting to `null` for
  rows that never populated them.

#### Scenario: Get audit entry by id
- **WHEN** `GET /api/audit-log/{id}` is called
- **THEN** the response is `ApiResponse[AuditLogEntry]` if the row exists, else
  `404`.

#### Scenario: Drill into an audit-derived issue group's occurrences
- **WHEN** `GET /api/issues/{issue_key}/occurrences?window=&offset=&limit=` is called for an active `audit_error_group:*` or `scheduled_task_failure:*` issue group
- **THEN** the response is `PaginatedResponse[AuditLogEntry]` containing the individual `public.audit_log` rows behind that group's occurrence count, newest first, with `meta.total` reflecting the group's true occurrence count within `window`
- **AND** the group is re-derived from the same grouping CTE used to build the Issues feed, applying the same `window` time bound (`<N>h`, `<N>d`, default `7d`, or `all`) and the same row cap as `GET /api/issues` (bu-hmdqz.4), so the occurrences and their total can never disagree with what the feed showed under that window
- **AND** `limit` defaults to 50 and is clamped to `≤ 500`; the frontend renders "Showing X of N" and a "Load more" control while more rows remain
- **AND** an `issue_key` that does not match any currently-active group within `window` returns `404`.

#### Scenario: Tolerant metadata deserialization for poisoned rows
- **WHEN** `AuditLogEntry.from_record` projects the `metadata` column and `jsonb_typeof(metadata) = 'string'` (a since-fixed write path double-JSON-encoded the value for a contiguous 2026-06-14 -> 07-05 band, bu-hmdqz.4)
- **THEN** the string is decoded as JSON; if it decodes to an object, that object is used
- **AND** if it does not decode to an object (invalid JSON, or valid JSON that isn't an object), the raw string is wrapped losslessly as `{"_raw": <string>}` instead of raising
- **AND** this MUST NOT 500 the response — a poisoned `metadata` value on any row must never take down a list or detail read of the surrounding table.

### Requirement: Audit Log Retention
The audit log SHALL be retained indefinitely. No retention job, no expiry, no deletes.

#### Scenario: No retention policy applies
- **WHEN** the system runs the daily maintenance job
- **THEN** no rows are removed from `audit_log`
- **AND** no row is updated in place (the table is append-only), except for a
  documented one-shot data-integrity repair below.

#### Scenario: One-shot structural metadata repair is not a retention violation
- **WHEN** a write-path defect causes a contiguous band of `audit_log` rows to store `metadata` as JSON-encoded text instead of an object (`jsonb_typeof(metadata) = 'string'`, bu-hmdqz.4)
- **THEN** a one-shot, batched, idempotent migration MAY normalize just the `metadata` column of the affected rows back to the correct object shape, preserving the original content losslessly (decoding valid JSON back to an object, or wrapping non-object content under `_raw`)
- **AND** this is a data-integrity repair of a poisoned write path, not an ordinary update — it MUST NOT touch `ts`, `actor`, `action`, `target`, `result`, or `error`, and MUST NOT be used as precedent for any other kind of edit
- **AND** the retention/append-only guarantee otherwise stands: no row is ever deleted, and no column other than a proven-poisoned `metadata` is ever rewritten.

#### Scenario: One-shot missing-outcome repair is not a retention violation
- **WHEN** credential probe writers historically wrote `action = 'failed'` without the required `result = 'error'` outcome
- **THEN** one idempotent migration MAY set only the missing `result` value to
  `error` for rows matching `action = 'failed' AND result IS NULL`
- **AND** it MUST NOT touch `ts`, `actor`, `action`, `target`, `note`, `error`,
  or any non-matching row, and MUST NOT be used as precedent for inferred
  outcome repairs
- **AND** the retention/append-only guarantee otherwise stands: no row is ever
  deleted or updated in place.

### Requirement: Direct Producer Outcome Attribution
The dashboard audit log SHALL preserve an explicit, producer-meaningful
`result` for every current direct production `audit_router.append` writer. The
generic `audit_router.append` compatibility contract SHALL continue to allow
callers outside that direct-producer set to omit `result`.

#### Scenario: Direct producer records its observed outcome
- **WHEN** a direct production writer appends an audit row
- **THEN** it passes an explicit `result` that describes the producer's
  observed outcome, such as `success`, `detected`, `escalated`, or `delivered`
- **AND** the choice reflects the event boundary rather than a generic fallback
- **AND** no historical audit row is rewritten to infer that outcome.

#### Scenario: Model breaker notification records confirmed delivery
- **WHEN** the model-breaker open notification is confirmed delivered to the
  owner
- **THEN** its `model_breaker_open_notified` audit row has
  `result = "delivered"`
- **AND** suppressed, deferred, or failed delivery paths retain their existing
  behavior and do not manufacture a delivered audit marker.

#### Scenario: Future direct writer cannot omit outcome attribution
- **WHEN** a new direct production `audit_router.append` call omits the
  `result` keyword
- **THEN** focused source-level regression coverage fails and identifies the
  writer's source location
- **AND** generic router callers outside the direct-producer scan remain
  compatible with an omitted result.

### Requirement: Failure-Semantic Audit Outcomes
The dashboard SHALL persist an explicit outcome for the credential probe,
model verification, approval decision, and model mutation audit writers that
feed operational evidence. A failure-semantic writer MUST store
`result = "error"`; a successful outcome writer in those families MUST store
`result = "success"`.

#### Scenario: Credential probe records an observable failure
- **WHEN** a credential probe finishes unsuccessfully
- **THEN** its audit row has `action = "failed"`, `result = "error"`, and the
  safe probe diagnostic in `error`
- **AND** a successful credential probe writes `action = "verified"` with
  `result = "success"`
- **AND** no raw credential value is written to either field.

#### Scenario: Model verification records an observable failure
- **WHEN** a verify-all run has one or more failed model checks
- **THEN** its `models.verify_all` audit row has `result = "error"` and a
  bounded aggregate failure summary in `error`
- **AND** a run with no failed checks, including an empty enabled-model set,
  writes `result = "success"`.

#### Scenario: Consequential mutation records success
- **WHEN** an approval decision or model mutation commits successfully
- **THEN** its audit row records `result = "success"` in the same outcome
  boundary as the mutation
- **AND** the existing transaction and audit-unavailable rollback contract is
  unchanged.

#### Scenario: Historical failed rows receive a narrow one-shot repair
- **WHEN** the core outcome-repair migration runs against historical audit data
- **THEN** it updates only rows where `action = "failed" AND result IS NULL`,
  setting `result = "error"`
- **AND** it does not alter `ts`, `actor`, `action`, `target`, `note`, `error`,
  or any non-matching row
- **AND** rerunning it is a no-op for already repaired rows.

### Requirement: Owner-Timezone Audit Day Bounds
The audit log read API SHALL accept owner-timezone `from_date` and `to_date`
filters in addition to the existing ISO timestamp `since` filter.

#### Scenario: Bare audit day keys resolve to owner-timezone boundaries
- **WHEN** a bare `YYYY-MM-DD` value is passed as `from_date`
- **THEN** the audit query compares `ts >=` the start of that owner-local day
- **WHEN** a bare `YYYY-MM-DD` value is passed as `to_date`
- **THEN** the audit query compares `ts <=` the final microsecond of that
  owner-local day
- **AND** a full ISO timestamp for either parameter is used as-is, while an
  invalid value returns HTTP 422.

#### Scenario: Audit From equals To includes the full owner day
- **WHEN** `from_date` and `to_date` name the same owner-local day
- **THEN** audit entries throughout that full calendar day are returned
- **AND** the dashboard From and To date inputs send those parameters without
  replacing a legacy `since` deep link.

### Requirement: Credential-Target Audit Free Text Is Withheld On Read
Every read surface that publishes a `public.audit_log` row SHALL withhold that
row's free-text columns — `note`, `error`, and `metadata` — when the row's
`target` names a credential. A credential target is any `target` whose scope
segment is a credential-key scope from `core-credentials` §Credential-Key
Normalisation Function: `u:`/`user:`, `s:`/`system:`, or `c:`/`cli:`. Rows with
any other target, or no target, are unaffected and keep publishing their free
text: this is a namespace carve-out, not a blanket gag on operator diagnostics.

This requirement NARROWS the `Audit Log Read API` projection clause ("each
returned `AuditLogEntry` projects `metadata`/`result`/`error` … alongside the
base columns"). Where the two speak about the same row, this one governs. It is
the same rule `dashboard-api` §`Secrets Inventory and Per-Credential Read
Endpoints` already applies to the secrets surfaces, extended to the general
operator log so the two cannot disagree about the same stored text.

The withholding is enforced on the `AuditLogEntry` model rather than in each
route, because the `u:`/`s:`/`c:` audit namespaces have at least four producers
(`_write_credential_audit`, `_write_system_audit`, `_write_cli_audit`,
`routers/oauth.py::_emit_oauth_audit`, `jobs/secrets_lifecycle`) and at least
three readers, and a new one of either can appear at any time. Read-side
projection at a single chokepoint is the only enforcement point that holds.

Server-side evidence is untouched. Writers keep persisting the note and the
error, and the free text still reaches `public.audit_log`,
`public.secret_probe_log`, and the `last_test_message` cache. Content blindness
is about the wire, not about destroying operator forensics — the operator reads
a raw provider string at the database, which is where it belongs.

#### Scenario: Credential-target row is published without its free text
- **WHEN** `GET /api/audit-log` or `GET /api/audit-log/{id}` returns a row whose
  `target` names a credential (e.g. `u:google`, `s:BUTLER_TELEGRAM_TOKEN`,
  `c:claude`)
- **THEN** the serialized entry's `note`, `error`, and `metadata` are `null`
- **AND** `ts`, `actor`, `action`, `target`, `result`, `ip`, and `request_id`
  are published unchanged — the row stays identifiable, attributable, and
  filterable by `?key=`
- **AND** the response body contains no part of the withheld text, including
  the provider failure tail a probe writes into `note` as
  `"Probe failed: <provider text>; probe_status=<token>"`.

#### Scenario: Withholding is visible rather than silent
- **WHEN** a credential-target row that actually carried a `note`, an `error`,
  or `metadata` is published
- **THEN** the entry carries `redacted: true`, so an operator reading a blank
  Note field learns the text was withheld rather than never recorded
- **AND** a credential-target row that carried none of the three publishes
  `redacted: false` — the marker reports a real withholding and is never
  decorative
- **AND** a non-credential row always publishes `redacted: false`.

#### Scenario: Non-credential operator rows keep their diagnostics
- **WHEN** a row whose `target` is absent or names a non-credential resource
  (e.g. `butler:qa`, `rule:7`, a request path) is published
- **THEN** its `note`, `error`, and `metadata` are published verbatim
- **BECAUSE** the general operator audit log has a real forensic claim on its
  own free text; only the credential namespaces are governed by the
  content-blindness rule the secrets surfaces already carry.

#### Scenario: The secrets deep link cannot re-expose what secrets withheld
- **WHEN** an operator follows the `meta.deep_link` that
  `GET /api/secrets/audit/<scope>/<key>` returns
  (`/audit-log?key=<canonical-key>`) and the resulting page reads
  `GET /api/audit-log?key=<canonical-key>`
- **THEN** every row on that page is a credential-target row and is therefore
  published without its free text
- **AND** the deep link remains useful: it is the full reel of a credential's
  audit rows, carrying the same `ts`/`actor`/`action` evidence the secrets
  StampRow shows, plus `result` and the row id
- **BECAUSE** a signposted path that re-publishes exactly what
  `dashboard-api` §`Secrets Audit-History and Breaks-Catalogue Endpoints`
  just stopped publishing would narrow that fix rather than close it.

#### Scenario: The Issues occurrences drill-down is covered by the same chokepoint
- **WHEN** `GET /api/issues/{issue_key}/occurrences` returns
  `PaginatedResponse[AuditLogEntry]` rows and one of them is a credential-target
  row
- **THEN** that row is published under this requirement exactly as it would be
  by `GET /api/audit-log`
- **AND** a future reader that builds an `AuditLogEntry` without going through
  `AuditLogEntry.from_record` is covered too, because the withholding is a
  property of the model rather than of any one route.

### Requirement: Credential-Target Audit Groups Are Identified Without Free Text
Every surface that derives an audit-error **group** from `public.audit_log`
SHALL identify a credential-target group by a synthetic title composed only from
structured columns persisted on that row, and SHALL NOT use the row's free text
— its `error`, `note`, or `metadata` — as any part of that identity. A
credential target is the same namespace this capability's `Credential-Target
Audit Free Text Is Withheld On Read` requirement governs: any `target` whose
scope segment is a credential-key scope from `core-credentials` §Credential-Key
Normalisation Function (`u:`/`user:`, `s:`/`system:`, `c:`/`cli:`).

This requirement NARROWS `dashboard-api` §`Issues Aggregation` ("audit-log
errors are grouped by normalized error message"). Where the two speak about the
same row, this one governs. It is the group-identity counterpart of the
row-projection rule above: without it the text that requirement withholds
per row returns as the group's title, and the pair would give opposite rules for
the same stored string.

The rule SHALL be enforced in the shared grouping CTE
(`src/butlers/api/audit_grouping.py`), not in any one router, because the same
CTE feeds the Issues feed, the briefing attention items, the occurrences
drill-down, and the audit-row-to-group resolver. A per-surface fix would let a
group's title disagree with its own drill-down, which is a 404 on a group the
feed just showed.

Server-side evidence is untouched. Writers keep persisting the note and the
error; the free text still reaches `public.audit_log`,
`public.secret_probe_log`, and the `last_test_message` cache. The operator reads
the provider's words at the database, which is where they belong.

#### Scenario: A credential probe failure's provider text is not a group title
- **WHEN** a `result = 'error'` row whose `target` names a credential is grouped
  for `GET /api/issues`, `GET /api/issues/{issue_key}/occurrences`, the
  briefing's attention items, or `GET /api/issues/group-for-audit/{audit_id}`
- **THEN** the group's `error_summary` is the synthetic title, and no part of
  the row's `error` or `note` appears in it
- **AND** no part of that text appears anywhere else in the response body
  either — not in `Issue.error_message`, not in the composed `description`, not
  in `Issue.type`, and not in `issue_key`
- **BECAUSE** `_write_credential_audit` stores the raw probe message in `error`
  via `credential_lifecycle_outcome(action='failed')`, and `error_summary` is
  the single column the group is `GROUP BY`'d on.

#### Scenario: Credential groups stay distinguishable from one another
- **WHEN** two different credentials (e.g. `u:google` and `u:notion`) each have
  failure rows in the same window
- **THEN** they remain two groups, with two occurrence counts, two
  `issue_key`s, and two independent acknowledgements
- **AND** a blanket constant summary is NOT an acceptable implementation, since
  it would collapse every credential failure in the fleet into one group and
  make one acknowledgement silently cover unrelated broken credentials.

#### Scenario: Non-credential groups keep their normalized error verbatim
- **WHEN** a `result = 'error'` row whose `target` is absent or names a
  non-credential resource (e.g. `butler:qa`, `rule:7`, a request path) is
  grouped
- **THEN** its `error_summary` is the existing normalization — the first line of
  `error`, with `/tmp/tmp<random>/` collapsed to `/tmp/.../`, falling back to
  `"Unknown error"` — published verbatim
- **BECAUSE** this is a credential-namespace carve-out, not a blanket gag: an
  operator log that cannot say what failed is not an operator log.

#### Scenario: The drill-down resolves the group the feed published
- **WHEN** `GET /api/issues/{issue_key}/occurrences` re-derives a
  credential-target group by binding the `error_summary` the feed published for
  it
- **THEN** it returns that group's rows, and the total agrees with the
  occurrence count the feed reported for the same window
- **BECAUSE** the feed and the drill-down build on the same
  `normalized_errors` CTE and bind on `error_summary`: a credential branch
  present in one and absent in the other would 404 the drill-down on a group
  the feed had just rendered.

#### Scenario: One definition of "this target names a credential"
- **WHEN** the grouping CTE tests a row's `target` and the `AuditLogEntry` model
  tests the same row's `target`
- **THEN** both evaluate the same exported pattern
  (`butlers.api.models.audit.CREDENTIAL_TARGET_PATTERN`), matching every scope
  spelling that can appear in the column — the canonical `u:`/`s:`/`c:` and the
  long forms `user:`/`system:`/`cli:`, since `public.audit_log.target` is never
  normalised on write
- **AND** a group's title and its rows' withheld columns can therefore never
  disagree about whether a namespace is credential-scoped.

### Requirement: Credential-Target Audit Group Identity Includes The Persisted Failure Category
A credential-target audit-error group SHALL be identified by the credential
**and** the cause of the failure, where the cause is a `failure_category` value
persisted on the row at write time. The synthetic group title SHALL be composed
only from `action`, `target`, and `failure_category`, and SHALL NOT be derived
from the row's `error`, `note`, or `metadata` at read time.

`failure_category` SHALL hold only a member of
`butlers.api.models.audit.PROBE_FAILURE_VOCABULARY` — `not_set`, `expired`,
`rejected`, `rate_limited`, `provider_error`, `malformed`, `unverified`,
`other` — or `NULL`. It SHALL NEVER hold a raw probe-status token, a provider
HTTP status code, a provider string, or any audit free text. The value is
*selected* out of that closed set, never *derived* from an input string, so a
new provider message cannot widen what the column can contain.

This requirement REVISES this capability's `Credential-Target Audit Groups Are
Identified Without Free Text`, which fixes group identity to the structured
columns persisted on the row without naming which of them participate. This
requirement names `failure_category` as one of them, so two rows on one
credential carrying different persisted causes are two groups rather than one.
Persisting it costs nothing new: the category was already being derived for
`TestResult.message` and discarded instead of stored. Where the two speak about
the same row, this requirement governs. Everything else in that requirement
stands unchanged, including its content-blindness rule, its distinguishability
rule, its non-credential carve-out, and its single-predicate rule.

The rule SHALL be enforced in the shared grouping CTE
(`src/butlers/api/audit_grouping.py`), for the same reason its predecessor is:
one definition for the Issues feed, the briefing attention items, the
occurrences drill-down, and the audit-row-to-group resolver.

#### Scenario: Two causes on one credential are two groups
- **WHEN** one credential (e.g. `u:google`) has a failure row categorised
  `rejected` and another categorised `rate_limited` in the same window
- **THEN** they are two groups, with two occurrence counts, two `issue_key`s,
  and two independent acknowledgements
- **AND** the published title names the cause, so an operator can tell the two
  rows apart
- **BECAUSE** acknowledging a transient throttle must not silently acknowledge a
  credential the provider has stopped accepting.

#### Scenario: Repeats of one cause stay one group
- **WHEN** one credential fails three times with the same `failure_category`
- **THEN** all three fall in one group whose `occurrences` count reports three
- **BECAUSE** the identity is the cause, not the occurrence: a feed of
  singletons would replace an over-broad group with an unreadable one.

#### Scenario: The category is stored, never recovered from withheld text
- **WHEN** a credential-audit producer writes a `result = 'error'` row
- **THEN** it passes an already-selected vocabulary member, which is stored in
  `public.audit_log.failure_category`
- **AND** no grouping surface parses `note`, `error`, or `metadata` to obtain a
  cause
- **BECAUSE** parsing the withheld text at read time would put the provider's
  own words back into a group title, which is the inversion this capability's
  withholding requirements exist to prevent.

#### Scenario: The database refuses anything outside the vocabulary
- **WHEN** any writer inserts into `public.audit_log`, including one that
  bypasses `audit.append()`
- **THEN** a `failure_category` that is neither `NULL` nor a vocabulary member
  is rejected by a CHECK constraint
- **AND** a non-member handed to `audit.append()` is clamped to `other` and the
  audit row is still written
- **BECAUSE** an audit write is fire-and-forget: a mislabelled category must
  cost the label, never the record of the failure.

#### Scenario: Every producer that can write a failure names a category
- **WHEN** a credential-audit writer can emit `action = 'failed'`
- **THEN** it passes a `failure_category`
- **AND** producers that can only emit success actions pass none, and their
  rows' `NULL` is correct rather than a gap, since the grouping CTE reads only
  `result = 'error'` rows
- **BECAUSE** one uncategorised failure path silently reopens the coarse group,
  so the guarantee is a property of the whole producer set and is verified by
  enumerating it rather than by exercising one endpoint.

#### Scenario: Rows written before the category existed keep their group
- **WHEN** a credential-target error row has `failure_category IS NULL`
- **THEN** its group title is byte-identical to the title it had before this
  change, so its `error_summary`, its `group_key`, and any acknowledgement
  already attached to it are unchanged
- **AND** such rows SHALL NOT be backfilled from `note`, `error`, or `metadata`
- **AND** a still-failing credential may therefore show a frozen uncategorised
  group beside its new categorised one until the old one ages out of the window
- **BECAUSE** the only surviving per-cause signal on a historic row is the
  withheld text, and one bounded transitional duplicate is cheaper than either
  parsing that text or orphaning every existing acknowledgement.

#### Scenario: The wire projection does not widen
- **WHEN** a credential-target row is serialized by `GET /api/audit-log`,
  `GET /api/audit-log/{id}`, or `GET /api/issues/{issue_key}/occurrences`
- **THEN** `failure_category` is not among the published fields
- **BECAUSE** `butlers.api.models.audit` remains the single enforcement point
  for what a credential row discloses; persisting the cause changes how rows
  group, and is not licence to change what each row says.

## Source References
- PLAN.md §6 Phase 1 Foundations: audit log primitive.
- Doctrine: `about/heart-and-soul/security.md` (audit trail discipline for any privileged operation).
- The audit primitive is the prerequisite for permissions, model priority changes, spend rules/ceiling changes, webhook CRUD, approval verbs, and data ops.
