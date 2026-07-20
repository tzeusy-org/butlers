# dashboard-briefing Specification

## Purpose

The dashboard briefing is the editorial opening of the dashboard home page: a templated greeting, a deterministic headline classifying the state of the system, and an LLM-elaborated paragraph that names what is true right now in butler voice. The briefing is composed server-side and returned as a single object the frontend renders verbatim.

This spec defines the wire contract (`GET /api/dashboard/briefing`), the `Briefing` response schema, the six-class state classification taxonomy, the headline table, the LLM elaboration prompt and parameters, the deterministic fallback, the per-owner 5-minute caching contract, and the post-generation voice lint.

The headline is classified from the SAME composed attention model the Overview dashboard page renders (bu-gcz9e.1, "one attention model on the dashboard"), not a second, independently-maintained one: butler liveness comes from `GET /api/butlers/board`'s canonical verdict, audit-derived issues from the shared audit-group CTE also used by the Issues page, pending approvals from the same all-pools fan-out the Settings Console uses, failed notifications from `GET /api/notifications/stats`, and QA state from the same last-patrol-failed / dispatched / novel-findings priority the Overview page's QA summarization uses.

bu-gcz9e.2 pins this cross-surface contract with a consistency test driven from SHARED fixtures: `tests/dashboard/test_briefing_attention_contract.py` (this endpoint's classification) and `frontend/src/components/overview/model.contract.test.ts` (the `dashboard-overview` attention list) both read the same named scenarios from `frontend/src/components/overview/__fixtures__/attention-contract-scenarios.json` and assert each scenario's `state_class` implies matching row-count/severity bounds on the other surface. A tripped QA circuit breaker (`GET /api/qa/summary`'s `circuit_breaker.tripped`) is part of this pinned contract (bu-y2xqi): the QA-derived attention item (the scenario below) checks circuit-breaker state FIRST, before the failed-patrol check, matching the `dashboard-overview` spec's "A tripped QA circuit breaker surfaces as an attention row" scenario -- a breaker trip with no failed patrol / dispatched / novel signal now composes `state_class = "urgent"`, never `"quiet"`.

## Requirements
### Requirement: Briefing Response Schema

The endpoint `GET /api/dashboard/briefing` SHALL return a JSON object with exactly six fields: `greet`, `headline`, `elaboration`, `source`, `state_class`, `generated_at`. The schema MUST be stable across implementation changes.

#### Scenario: Response shape on success

- **WHEN** an authenticated owner calls `GET /api/dashboard/briefing`
- **THEN** the response is HTTP 200
- **AND** the body is a JSON object with the six required fields
- **AND** `greet` matches `"Good {time_of_day}."` for one of the five time_of_day values
- **AND** `headline` is the templated body for the computed `state_class`
- **AND** `source` is one of `"llm"` or `"fallback"`
- **AND** `state_class` is one of `"urgent"`, `"busy"`, `"mild"`, `"degraded-quiet"`, `"degraded"`, `"quiet"`
- **AND** `generated_at` is an ISO 8601 timestamp recording the wall-clock time at which the Briefing object was finalized, set once per composition regardless of whether `source` is `"llm"` or `"fallback"` and regardless of how long the underlying LLM call took

### Requirement: Attention Item Sources

The endpoint SHALL populate `state.attention_items` from five sources before classification:
butler liveness, grouped error entries from the `dashboard_audit_log` table, pending
approvals, failed notifications, and QA state. An attention item SHALL represent either a
live state or a time-bounded recent failure; historical aggregates SHALL remain outside
`state.attention_items` and SHALL NOT affect briefing classification, headline, or
elaboration. Each source is fetched independently and concurrently; a failure in one
source MUST NOT prevent the others from contributing.

#### Scenario: Board-derived attention items (butler liveness)

- **WHEN** `GET /api/butlers/board`'s canonical per-row `activity` verdict for a `"butler"`-type row is one of `"offline"`, `"quarantined"`, or `"overdue"`
- **THEN** that row is added to `state.attention_items` as a single attention item
- **AND** `"offline"` and `"quarantined"` carry `severity = "high"`; `"overdue"` carries `severity = "medium"`
- **AND** `source` is `"board"`
- **WHEN** a row's activity is `"unknown"` (that butler's own heartbeat/schema is unreachable, or its registry `last_seen_at` is clock-skewed more than 5 minutes into the future) and the board's registry query itself did not fail
- **THEN** that row is likewise added with `severity = "medium"`
- **WHEN** the board's registry query itself failed, uniformly degrading every row's activity to `"unknown"`
- **THEN** no attention item is fabricated per butler from that systemic outage
- **AND** the failure is instead tracked as the `"board"` degraded source (see the degraded-sources scenario below)

#### Scenario: Audit-derived attention items

- **WHEN** a grouped `dashboard_audit_log` error has a parseable `last_seen_at` in the
  closed interval `[now - 12 hours, now]`
- **THEN** it is appended to `state.attention_items` using its first-line error summary
- **AND** it receives `severity = "high"` when any row in the group originated from a
  scheduled session (`trigger_source` starts with `"schedule:"`)
- **AND** it receives `severity = "medium"` when none of the rows in the group were
  schedule-triggered
- **AND** `source` is `"audit_log"`
- **WHEN** a grouped audit error was last seen more than 12 hours ago, or lacks a
  parseable `last_seen_at`
- **THEN** it is historical context and SHALL NOT be appended to
  `state.attention_items`

This means a recurring scheduled-task failure raises `state_class` to `"urgent"` only
while it is within the current operational horizon. Ad-hoc errors that do not originate
from a schedule are surfaced as `"medium"` while current, so they contribute to
`"busy"` or `"mild"` without forcing `"urgent"`.

#### Scenario: Approvals-derived attention item

- **WHEN** one or more pending approvals exist across any butler's `pending_actions` table
- **THEN** a single attention item is added with `severity = "medium"` naming the total pending count
- **AND** `source` is `"approval"`

#### Scenario: Notification-derived attention item

- **WHEN** briefing composition requests `GET /api/notifications/stats` with
  `since = now - 24 hours` and `until = now`, captured once for the composition, and that
  bounded response has `failed` greater than zero
- **THEN** a single attention item is added with `severity = "medium"` naming the failed
  count in the last 24 hours
- **AND** `source` is `"notification"`
- **WHEN** the same bounded response has `failed = 0` while an all-time notification
  total is greater than zero
- **THEN** no notification attention item is added

Only recent failed deliveries are a genuine attention-worthy signal. Lifetime delivery
totals remain available on the Notifications page and SHALL NOT affect briefing state.

#### Scenario: QA-derived attention item

- **WHEN** `GET /api/qa/summary`'s circuit breaker (computed from `public.healing_attempts` the same way `qa.py`'s `/api/qa/circuit-breaker` and dispatch-admission gate do) is tripped
- **THEN** a single attention item is added with `severity = "high"` and `source =
  "qa"` naming the tripped breaker and its consecutive-failure count
- **AND** no further QA checks are considered because a tripped breaker means the QA
  staffer has stopped dispatching entirely
- **WHEN** the breaker is not tripped, and the most recent non-running QA patrol has
  `status = 'error'` in the closed interval `[now - 24 hours, now]`
- **THEN** a single attention item is added with `severity = "high"` and `source =
  "qa"`, and no further QA checks are considered
- **AND** a null `error_detail` does not suppress that attention item, while a
  non-`error` status does not create one solely because it has `error_detail`
- **WHEN** neither higher-precedence state applies and
  `GET /api/qa/summary` reports `kpis.active_cases_now` greater than zero
- **THEN** a single attention item is added with `severity = "medium"` and `source =
  "qa"` naming the active investigation count
- **WHEN** neither higher-precedence state applies and only
  `stats_24h.dispatched_investigations` or `stats_24h.novel_findings` is greater than
  zero
- **THEN** no QA attention item is added because completed dispatches and findings are
  time-bounded activity rather than active failure state
- **WHEN** the QA tables are not provisioned on this deployment (undefined relation)
- **THEN** QA is treated as legitimately absent, contributing no attention item and no degraded source
- **WHEN** the `public.qa_patrols` query succeeds but the circuit-breaker query against `public.healing_attempts` fails for a reason other than the table being un-provisioned
- **THEN** the QA source is recorded in `state.degraded_sources`, but the patrol-derived
  signal already fetched still contributes normally -- one query's failure does not
  discard another query's successful result

#### Scenario: Attention item source fetch failure

- **WHEN** any of the five source fetches (board, audit, approvals, notifications, QA) fails with an exception, or a source explicitly reports itself unreachable (e.g. `NotificationStats.source_available = false`)
- **THEN** that source's items are omitted from `state.attention_items`
- **AND** the endpoint logs a WARNING and continues with the remaining sources
- **AND** the source's name is recorded in `state.degraded_sources`
- **AND** `state_class` is computed from whatever items were successfully retrieved, per the Degraded class scenario below

A source that is legitimately absent (an un-migrated table on a deployment that has not provisioned that module) is NOT recorded as degraded -- only a genuine failure (dropped connection, timeout, permission error) is.

### Requirement: State Classification

The endpoint SHALL classify the current dashboard state into one of six `state_class` values using a deterministic function over the attention list, butler health, and the set of sources that failed to answer (`state.degraded_sources`).

#### Scenario: Urgent class

- **WHEN** at least one attention item has severity `high`
- **THEN** `state_class` is `"urgent"`
- **AND** `headline` is `"{n} things need you now."` if there is more than one high-severity item, or `"One thing needs you now."` if exactly one

#### Scenario: Busy class

- **WHEN** there are three or more attention items
- **AND** none of them are severity `high`
- **THEN** `state_class` is `"busy"`
- **AND** `headline` is `"Things are busy with {total} items waiting."`

#### Scenario: Mild class

- **WHEN** there are one or two attention items
- **AND** none are severity `high`
- **THEN** `state_class` is `"mild"`
- **AND** `headline` is `"Things are quiet, with {n} exception."` for n == 1, or `"Things are quiet, with {n} exceptions."` for n == 2

#### Scenario: Degraded-quiet class

- **WHEN** there are zero attention items
- **AND** at least one butler is `degraded` or `error`
- **THEN** `state_class` is `"degraded-quiet"`
- **AND** `headline` is `"Quiet, but {n} butler is degraded."` for n == 1, or `"Quiet, but {n} butlers are degraded."` for n > 1

This holds even when `state.degraded_sources` is also non-empty: a known, real signal (a specific butler reporting degraded/error) is more actionable than a vague "some data is missing" notice, so `"degraded-quiet"` outranks `"degraded"`.

#### Scenario: Degraded class

- **WHEN** there are zero attention items
- **AND** no butler is known `degraded` or `error`
- **AND** `state.degraded_sources` is non-empty (one or more of board/audit/approvals/notifications/QA failed to answer)
- **THEN** `state_class` is `"degraded"`
- **AND** `headline` is `"One source could not be reached, so this may be incomplete."` for exactly one degraded source, or `"{n} sources could not be reached, so this may be incomplete."` for n > 1
- **AND** the LLM elaboration step is skipped entirely; `elaboration` is always the templated fallback and `source` is always `"fallback"` (the true state is unknown by definition, so only the deterministic paragraph is safe to return)

`"degraded"` always outranks `"quiet"`: a source that failed to answer must never be indistinguishable from a truthful all-clear.

#### Scenario: Quiet class

- **WHEN** there are zero attention items
- **AND** all butlers report `healthy`
- **AND** `state.degraded_sources` is empty (every source answered)
- **THEN** `state_class` is `"quiet"`
- **AND** `headline` is `"Everything is in hand."`

### Requirement: Time-of-Day Greeting

The endpoint SHALL compute `state.now` in the owner's configured general timezone, compute `time_of_day` from that owner-local `state.now.hour`, and return a templated greeting.

#### Scenario: Time-of-day buckets

- **WHEN** `state.now.hour` is less than 5
- **THEN** `greet` is `"Good late-night."`

- **WHEN** `state.now.hour` is greater than or equal to 5 and less than 12
- **THEN** `greet` is `"Good morning."`

- **WHEN** `state.now.hour` is greater than or equal to 12 and less than 17
- **THEN** `greet` is `"Good afternoon."`

- **WHEN** `state.now.hour` is greater than or equal to 17 and less than 21
- **THEN** `greet` is `"Good evening."`

- **WHEN** `state.now.hour` is greater than or equal to 21
- **THEN** `greet` is `"Good night."`

### Requirement: LLM Elaboration

The endpoint SHALL call the local catalog-backed runtime adapter path with a pinned prompt to produce a one-to-three sentence elaboration paragraph. The prompt MUST encode the dashboard voice rules. The runtime call MUST use the synthetic butler identity `__dashboard_briefing__`, resolve runtime/model/args/timeout from `public.model_catalog` at the `trivial` complexity tier, and run without MCP tools.

#### Scenario: Prompt receives bounded dashboard context

- **WHEN** the endpoint composes an LLM elaboration
- **THEN** the user prompt includes a bounded internal state snapshot with owner-local time, attention totals, top attention item descriptions, relevant butler names, timestamps, and unhealthy butler summaries
- **AND** the snapshot includes enough source context for the paragraph to name the most important current ecosystem fact
- **AND** the public response still contains exactly the six required Briefing fields, with no additional metadata or context field

#### Scenario: LLM happy path

- **WHEN** the local runtime call returns within its configured timeout
- **AND** the response passes the post-generation voice lint
- **THEN** `elaboration` is set to the LLM response
- **AND** `source` is `"llm"`

#### Scenario: LLM timeout

- **WHEN** the local runtime call exceeds its configured timeout
- **THEN** the endpoint cancels the call
- **AND** `elaboration` is set to the templated fallback for the computed `state_class`
- **AND** `source` is `"fallback"`

#### Scenario: LLM error or empty response

- **WHEN** the LLM call raises an exception or returns an empty body
- **THEN** `elaboration` is set to the templated fallback
- **AND** `source` is `"fallback"`

### Requirement: Voice Enforcement

The endpoint SHALL run a post-generation lint over the LLM response and reject responses that contain banned tokens.

#### Scenario: Voice lint rejects banned tokens

- **WHEN** the LLM response contains an exclamation mark, an em-dash, a first-person pronoun (`I`, `we`, `us`, `our`), a future-tense marker (`will be`, `is going to`), or a hedging adverb (`currently`, `presently`, `just`, `simply`, `basically`)
- **THEN** the response is rejected
- **AND** `elaboration` falls through to the templated fallback
- **AND** `source` is `"fallback"`
- **AND** the rejection emits a `briefing.elaboration.rejected` metric

#### Scenario: Voice lint respects word boundaries

- **WHEN** the LLM response contains the substring "actually" only inside a longer word like "factually"
- **THEN** the response is not rejected for that match
- **AND** the lint check uses word-boundary regex matching

### Requirement: Per-Owner Caching

The endpoint SHALL cache the Briefing per owner contact for 5 minutes.

#### Scenario: Cache hit

- **WHEN** an owner calls the endpoint within 5 minutes of a prior successful call
- **THEN** the response is served from cache
- **AND** `generated_at` reflects the original cached generation time, not the current time

#### Scenario: Cache miss after TTL

- **WHEN** more than 5 minutes have elapsed since the last cached Briefing for the owner
- **THEN** a fresh Briefing is composed
- **AND** the cache is repopulated
- **AND** `generated_at` reflects the new generation time

#### Scenario: Successful QA breaker reset invalidates cached briefings

- **WHEN** `POST /api/qa/circuit-breaker/reset` successfully commits its reset marker
- **THEN** the in-process briefing cache is invalidated before the route returns success
- **AND** the next owner briefing request composes current state without waiting for the
  five-minute TTL
- **AND** a briefing composition that began before the reset cannot repopulate the cache
  after that invalidation, even if its own response completes later
- **WHEN** the reset request finds no tripped breaker or the reset marker write fails
- **THEN** cached briefings remain intact

### Requirement: Owner-Only Access

The endpoint SHALL be accessible only to the owner contact.

#### Scenario: Non-owner request

- **WHEN** an authenticated session that is not the owner contact calls the endpoint
- **THEN** the response is HTTP 403
- **AND** no cache entry is read or written

#### Scenario: Unauthenticated request

- **WHEN** an unauthenticated request hits the endpoint
- **THEN** the response is HTTP 401 (the standard dashboard auth gate)

### Requirement: Endpoint Robustness

The endpoint SHALL never raise to the caller. Failures internal to the briefing pipeline (LLM, lint, classification) SHALL be caught and surfaced as the templated fallback. The endpoint MAY return HTTP 500 only when the templated fallback itself fails (which implies a code or import error).

#### Scenario: Total LLM unavailability

- **WHEN** the LLM transport is unreachable (DNS failure, TLS failure, upstream 5xx)
- **THEN** the response is HTTP 200
- **AND** `source` is `"fallback"`
- **AND** the fallback paragraph is one of the six templated paragraphs

#### Scenario: Classification exception

- **WHEN** the classification function raises (a malformed state row, missing column, schema drift)
- **THEN** the endpoint logs the error
- **AND** returns `state_class = "degraded"` with the degraded templated paragraph
- **AND** `source` is `"fallback"`
- **AND** an internal error metric is emitted

A classifier exception is itself a swallowed failure and must not compose `"quiet"` any more than a swallowed source-fetch failure may.
