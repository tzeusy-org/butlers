# GitHub Owner-Activity Connector

## Purpose

Defines the contract for a least-privilege GitHub owner-activity connector, grounded in
the official authenticated-user Events API
(<https://docs.github.com/en/rest/activity/events>). The connector polls the owner's own
GitHub activity via a fine-grained Personal Access Token and submits normalized `ingest.v1`
events to the Switchboard as occupation-category evidence, implementing the shared
connector base contract (`connector-base-spec`) in polling mode only. This spec is a
prerequisite contract; no code, migration, credential, or runtime described here exists
yet.

## ADDED Requirements

### Requirement: Credential Type and Least Privilege

The connector SHALL authenticate exclusively with a fine-grained GitHub Personal Access
Token scoped to the `Events` user permission (read). Classic PATs SHALL NOT be accepted.

#### Scenario: Fine-grained PAT accepted

- **WHEN** an owner registers a GitHub account with a fine-grained PAT scoped to `Events`
  (read) only
- **THEN** the connector SHALL accept the credential and use it to authenticate polling
  requests via `Authorization: Bearer <token>`

#### Scenario: Classic PAT rejected

- **WHEN** an owner attempts to register a classic PAT (identifiable by GitHub's classic
  token prefix/format, or by a scope-introspection call revealing OAuth-scope-style
  grants rather than fine-grained permissions)
- **THEN** the connector's account-registration path SHALL reject the credential with a
  clear reason that classic PATs are not accepted for least-privilege reasons
- **AND** no account row SHALL be created and no credential SHALL be persisted

#### Scenario: No-credential and invalid-credential startup

- **WHEN** the connector starts and an account row exists but its `entity_info` credential
  is missing, malformed, or absent
- **THEN** that account SHALL be skipped (degraded mode) and logged, without preventing
  other accounts from starting

### Requirement: Account and Credential Storage Model

GitHub accounts SHALL be modeled as rows in `public.github_accounts`, each linked to a
companion `public.entities` row (role `github_account`) that anchors the PAT in
`public.entity_info` (`info_type = 'github_pat'`, `secured = true`), mirroring the existing
Steam connector's account-registry pattern.

#### Scenario: Multi-account discovery at startup

- **WHEN** the GitHub connector starts
- **THEN** it SHALL query `public.github_accounts` for all rows with `status = 'active'`
- **AND** for each qualifying account, it SHALL resolve the PAT from the account's
  companion entity in `entity_info` (type `github_pat`)
- **AND** it SHALL spawn independent polling loops per account
- **AND** startup SHALL succeed even if some accounts fail credential resolution
  (degraded mode — failed accounts are logged and skipped)

#### Scenario: Per-account connector identity

- **WHEN** a polling loop runs for an account with `github_login = "octocat"`
- **THEN** `source.channel = "github"`, `source.provider = "github"`, and
  `source.endpoint_identity = "github:user:octocat"`

#### Scenario: At most one primary account

- **WHEN** two accounts are registered
- **THEN** at most one SHALL have `is_primary = true`, enforced by a partial unique index
  mirroring `ix_steam_accounts_primary_singleton`

#### Scenario: Credential revocation detected mid-operation

- **WHEN** a previously valid PAT is revoked or expires (subsequent poll returns `401`)
- **THEN** the account's health SHALL transition to `error`
- **AND** the connector SHALL stop polling that account on the base schedule (no
  quota-wasting retries against a dead credential) until the credential is re-verified via
  probe or reconnect
- **AND** other accounts' polling loops SHALL be unaffected

### Requirement: Event Retrieval Endpoint and Scope

The connector SHALL poll exactly `GET /users/{username}/events`, authenticated as the
account owner so private events are included. It SHALL NOT use `/events` (global),
`/users/{username}/events/public`, `/users/{username}/received_events`,
`/users/{username}/events/orgs/{org}`, `/orgs/{org}/events`, or any repository- or
network-scoped events endpoint.

#### Scenario: Authenticated request includes private events

- **WHEN** the connector polls `GET /users/{username}/events` with a valid PAT for that
  exact username
- **THEN** the response SHALL include both public and private events for that user

#### Scenario: Received-events and org-dashboard endpoints excluded

- **WHEN** implementing this connector
- **THEN** no code path SHALL call `/received_events`, `/events/orgs/{org}`, or
  `/orgs/{org}/events` — these describe activity from repos/users the account follows or
  organization-dashboard events, not the owner's own authored activity

### Requirement: Pagination and Conditional-Request Behavior

The connector SHALL use `page`/`per_page` pagination and ETag conditional requests to
minimize redundant data transfer and rate-limit consumption.

#### Scenario: 200 page response

- **WHEN** a poll's `GET` request returns `200` with a body and an `ETag` header
- **THEN** the connector SHALL persist the returned `ETag` for the next poll's
  `If-None-Match` header
- **AND** SHALL process the returned events per the cursor/dedup requirement below

#### Scenario: 304 not-modified response

- **WHEN** a poll's `GET` request with `If-None-Match: "<prior-etag>"` returns `304`
- **THEN** the connector SHALL treat this as "no new events," advance no cursor, record a
  successful poll for health purposes, and SHALL NOT count the request against the
  primary rate-limit budget accounting the same way a full `200` poll is counted

#### Scenario: Pagination across multiple pages

- **WHEN** a `200` response's events extend beyond one page and the cursor's last
  processed event `id` has not yet been reached
- **THEN** the connector SHALL request subsequent pages (`per_page=100`) until either the
  cursor's `id` is encountered or the provider's page limit is exhausted (at most 300
  events / 30 days total, per the provider's own timeline bound)

#### Scenario: X-Poll-Interval governs cadence

- **WHEN** a response includes an `X-Poll-Interval` header
- **THEN** the connector's next scheduled poll for that account SHALL NOT occur sooner
  than that many seconds after the current poll
- **AND** an increased `X-Poll-Interval` (signaling high server load) SHALL be honored
  immediately, not only on the next restart

### Requirement: Cursor Persistence, Restart Safety, and First-Poll Baseline

The connector SHALL persist per-account cursors keyed on GitHub's monotonically increasing
event `id`, and SHALL establish a baseline on first poll without emitting synthetic
historical events.

#### Scenario: Cursor storage

- **WHEN** a poll cycle completes successfully with new events processed
- **THEN** the connector SHALL persist to `connectors.github_cursors`: `endpoint_identity`,
  `last_event_id` (the newest processed event's `id`), `last_event_created_at` (that
  event's provider `created_at`, for observability only — not used as the ordering key),
  `last_etag`, and `last_poll_at`

#### Scenario: Restart resumes from persisted cursor

- **WHEN** the connector restarts after a crash or planned restart
- **THEN** it SHALL load each active account's cursor from `connectors.github_cursors` and
  resume polling using `last_event_id` as the stopping condition, without re-emitting
  already-processed events (harmless even on accidental replay, via the Switchboard's
  dedup layer)

#### Scenario: First-poll baseline establishes no synthetic history

- **WHEN** an account has no persisted cursor (first-ever poll)
- **THEN** the connector SHALL record the newest event's `id` (and `created_at`) as the
  baseline cursor
- **AND** SHALL NOT emit `ingest.v1` events for any event already present in that first
  response
- **AND** subsequent polls SHALL emit events only for `id` values greater than the
  baseline

#### Scenario: Provider history window is a hard limit, not an implementation gap

- **WHEN** an account is connected
- **THEN** no future poll, backfill, or replay mechanism defined by this connector SHALL
  claim to recover occupation evidence older than the account's connection time, because
  `GET /users/{username}/events` exposes at most 300 events from the last 30 days and has
  no deeper history parameter

### Requirement: Event Allowlist and Type Mapping

The connector SHALL emit `ingest.v1` events only for an explicit allowlist of GitHub event
types that constitute genuine owner-authored activity.

#### Scenario: Allowlisted event types

- **WHEN** a polled event's `type` is one of `PushEvent`, `PullRequestEvent`,
  `PullRequestReviewEvent`, `PullRequestReviewCommentEvent`, `IssuesEvent`,
  `IssueCommentEvent`, or `CreateEvent` (branch/tag/repo creation)
- **THEN** the connector SHALL map it to a normalized activity evidence record and submit
  it via `ingest.v1`

#### Scenario: Non-allowlisted event types are dropped, not errored

- **WHEN** a polled event's `type` is `WatchEvent` (starring), `ForkEvent`, `DeleteEvent`,
  `PublicEvent`, `MemberEvent`, `GollumEvent` (wiki), or any type not in the allowlist
- **THEN** the connector SHALL advance its cursor past that event but SHALL NOT submit an
  `ingest.v1` event or record it as filtered — a non-allowlisted event type is a design
  exclusion, not a filter-rule outcome, and recording it in `connectors.filtered_events`
  would misrepresent normal, expected non-activity as a policy block

### Requirement: Deduplication and Late/Out-of-Order Event Handling

The connector SHALL deduplicate by GitHub's event `id` and SHALL correctly handle the
provider's disclosed eventual-consistency behavior (30 seconds to 6 hours of latency).

#### Scenario: Duplicate event ID across polls

- **WHEN** the same GitHub event `id` appears in two separate polls (e.g. due to a cursor
  rewind after a crash)
- **THEN** `event.external_event_id = "github:<account_github_user_id>:<event.id>"` SHALL
  be identical both times
- **AND** the Switchboard's existing dedup layer SHALL treat the second submission as a
  duplicate, returning the original `request_id`

#### Scenario: Late-arriving event does not break cursor ordering

- **WHEN** a poll returns an event whose `created_at` is earlier than an already-processed
  event's `created_at`, but whose `id` is greater than the persisted cursor's
  `last_event_id`
- **THEN** the connector SHALL process it normally (it is a genuinely late arrival caused
  by provider-side eventual consistency, not a duplicate or an error)
- **AND** SHALL preserve its true (older) `created_at` in the emitted event rather than
  substituting the observation time

#### Scenario: Cursor never rewinds based on created_at

- **WHEN** determining whether an event has already been processed
- **THEN** the connector SHALL compare only `id` values against the persisted cursor,
  never `created_at` — the provider's own latency disclosure makes `created_at` ordering
  unsafe as a dedup or stopping key

### Requirement: Private-Repository Sensitivity and Redaction

The connector SHALL apply tiered ingestion based on the source repository's visibility,
never including private-repository content beyond a fixed, bounded summary.

#### Scenario: Private-repository event uses metadata tier

- **WHEN** an allowlisted event's `repo` resolves to a private repository
- **THEN** `control.ingestion_tier = "metadata"`, `payload.raw = null`
- **AND** `payload.normalized_text` SHALL contain only the event type, the repository's
  full name (`owner/repo`), and a bounded count (e.g. commit count for a push)
- **AND** SHALL NOT contain commit messages, PR/issue titles or bodies, review comment
  bodies, diffs, or file paths

#### Scenario: Public-repository event may use full tier

- **WHEN** an allowlisted event's `repo` resolves to a public repository
- **THEN** `control.ingestion_tier = "full"` is permitted, with `payload.raw` carrying the
  complete provider payload and `payload.normalized_text` including richer detail (e.g. a
  pull request title)

#### Scenario: Redaction extends to filtered/errored event storage

- **WHEN** a private-repository event is recorded to `connectors.filtered_events` due to
  an error or filter rule
- **THEN** the same redaction rule SHALL apply to the stored `full_payload` and
  `subject_or_preview` fields as applies to a normally-submitted event's
  `payload`/`normalized_text`

### Requirement: Deterministic Downstream Routing

The connector SHALL submit exclusively through the Switchboard's standard `ingest.v1` MCP
path. It SHALL NOT call Chronicler, or any other butler, directly.

#### Scenario: Standard ingest submission

- **WHEN** the connector has a normalized event ready to submit
- **THEN** it SHALL call the Switchboard's `ingest` MCP tool with a complete `ingest.v1`
  envelope, exactly as every other connector does — no direct database write to any
  Chronicler-owned table and no direct MCP call to the Chronicler butler

#### Scenario: Global skip rule bypasses LLM classification

- **WHEN** the Switchboard receives an `ingest.v1` envelope with `source_channel =
  "github"`
- **THEN** a global `ingestion_rules` row (`scope='global'`, matching
  `source_channel='github'`, `action='skip'`) SHALL prevent LLM classification and
  butler-session spawning for that event, mirroring the existing `activitywatch` skip rule
  (RFC 0003 Amendment 2)

#### Scenario: Durable evidence table is the future Chronicler read surface

- **WHEN** an event passes ingestion
- **THEN** the connector SHALL also persist it to `connectors.github_events` (mirroring
  `connectors.activitywatch_events`), which is the intended future read surface for a
  Chronicler `github.activity` projection adapter
- **AND** filing that adapter's own `chronicler_compatibility` declaration is explicitly
  out of scope for this connector contract

### Requirement: Rate Limiting

The connector SHALL implement the connector base contract's rate-limiting requirement,
interpreted for GitHub's two distinct limit types.

#### Scenario: Primary quota soft degradation

- **WHEN** `X-RateLimit-Remaining` drops below 10% of `X-RateLimit-Limit`
- **THEN** the connector SHALL extend its own poll interval for that account (never below
  the `X-Poll-Interval` floor) rather than stopping polling outright

#### Scenario: Secondary abuse-detection limit

- **WHEN** a poll request returns `403` with a `Retry-After` header (secondary rate
  limiting / abuse detection, distinct from primary quota exhaustion)
- **THEN** the connector SHALL honor `Retry-After` exactly per the base contract and mark
  the account `degraded` while backing off

#### Scenario: Rate-limit backoff is per-account

- **WHEN** one account is rate-limited (primary or secondary)
- **THEN** other accounts' polling loops and rate-limit budgets SHALL be unaffected

### Requirement: Transient Failure Handling

The connector SHALL retry transient failures with exponential backoff without prematurely
marking an account unhealthy.

#### Scenario: Transient network or 5xx failure

- **WHEN** a poll request fails with a network error or an HTTP `5xx` status
- **THEN** the connector SHALL retry with exponential backoff per the connector base
  contract
- **AND** SHALL NOT transition the account to `error` health until the base contract's
  consecutive-failure threshold is exceeded

### Requirement: Account Isolation

Independent GitHub accounts SHALL have fully independent polling loops, cursors, rate-limit
state, and health status.

#### Scenario: One account's failure does not affect another

- **WHEN** one account's credential is revoked, its polling loop is backing off, or its
  poll request fails transiently
- **THEN** every other active account's polling loop, cursor advancement, and health
  status SHALL be unaffected

### Requirement: Honest Empty and Unavailable State

The connector SHALL represent absence and unavailability honestly, without implying data
freshness the provider does not guarantee.

#### Scenario: No accounts connected

- **WHEN** the connector starts with zero rows in `public.github_accounts` with
  `status = 'active'`
- **THEN** it SHALL start in idle mode (health = `degraded`, no active polling loops)
- **AND** SHALL periodically re-scan for newly registered active accounts

#### Scenario: Provider-latency disclosure in health/status output

- **WHEN** the connector's health or heartbeat status is queried
- **THEN** the payload SHALL include a fixed, non-configurable field disclosing that the
  GitHub Events API is not real-time and observed latency can range from 30 seconds to 6
  hours
- **AND** no dashboard, log, or operator-facing surface fed by this connector SHALL imply
  a fresher guarantee than that disclosure

### Requirement: Secrets Passport Inventory and Probe Behavior

`github_pat` SHALL be a generic User credential type participating in the existing,
content-blind Secrets Passport inventory and probe surfaces — not a connector-owned OAuth
exception.

#### Scenario: github_pat appears in generic inventory

- **WHEN** an owner has registered a `github_pat` credential on any entity
- **THEN** `GET /api/secrets/inventory` SHALL include it in the `user` array using the
  same evidence-over-value contract (fingerprint, last-verified timestamp, probe result,
  WhatBreaks) as every other generic User credential — never the raw token value

#### Scenario: Probe uses a minimal read-only endpoint

- **WHEN** `POST /api/secrets/user/github/probe` is invoked
- **THEN** the connector SHALL call `GET /rate_limit` (a zero-scope, low-cost endpoint that
  confirms the token authenticates) and report only success/failure and the fixed
  `capability_categories = ["occupation-activity"]`
- **AND** SHALL NOT return or log the token value, the resolved `github_login`, or the raw
  provider response body

### Requirement: Explicit Non-Goals

This connector SHALL NOT implement any of the following; each is an explicit exclusion,
not a deferred phase of this contract.

#### Scenario: No webhooks, GitHub App, or OAuth App

- **WHEN** implementing this connector
- **THEN** it SHALL be PAT-based polling only — no webhook receiver, no GitHub App
  installation flow, no OAuth App authorization flow

#### Scenario: No write access

- **WHEN** implementing this connector
- **THEN** it SHALL perform no write, comment, mutation, or Actions-control call of any
  kind against GitHub — read-only polling only

#### Scenario: No repository analytics or Actions visibility

- **WHEN** implementing this connector
- **THEN** it SHALL NOT expose repository traffic analytics, dependency graphs, star/fork
  counts, or GitHub Actions workflow-run status — this is owner-activity perception, not
  repository observability

#### Scenario: No hard real-time SLA

- **WHEN** describing or documenting this connector anywhere (dashboard, logs, health
  output, operator-facing copy)
- **THEN** no text SHALL promise a fixed freshness guarantee (e.g. "5-minute updates") that
  the provider's own disclosed 30s-6h latency cannot support
