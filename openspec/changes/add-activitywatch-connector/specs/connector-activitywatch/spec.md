# ActivityWatch Connector

## Purpose

The ActivityWatch connector polls the local (or Tailscale-reachable)
ActivityWatch REST API for desktop window-focus and AFK-status events,
classifies each focused application into a coarse app-class bucket, and
submits `ingest.v1` envelopes to the Switchboard. It is the desktop
work-activity ingestion pathway into the butler ecosystem — the only
connector that observes what the owner is doing on a computer.

## ADDED Requirements

### Requirement: Connector Identity and Role

The ActivityWatch connector SHALL bridge a local ActivityWatch install into
the butler ecosystem as a desktop-activity ingestion channel.

#### Scenario: Connector as polling client

- **WHEN** the ActivityWatch connector runs
- **THEN** it polls the ActivityWatch REST API for window-focus and
  AFK-status events on an interval (`ACTIVITYWATCH_POLL_INTERVAL_S`)
- **AND** it normalizes window-focus events into `ingest.v1` envelopes
- **AND** it submits envelopes to the Switchboard via MCP
- **AND** it is a standalone OS process (not an in-daemon module)

#### Scenario: Connector identity

- **WHEN** the ActivityWatch connector starts
- **THEN** `source.channel = "activitywatch"`,
  `source.provider = "activitywatch"`, and
  `source.endpoint_identity = "activitywatch:<machine_id>"` where
  `<machine_id>` is configured via `ACTIVITYWATCH_MACHINE_ID`

#### Scenario: Single machine per instance

- **WHEN** the connector is deployed
- **THEN** each connector instance polls exactly one machine's
  ActivityWatch REST API
- **AND** multiple machines (e.g. a desktop and a separate work laptop)
  require one connector instance per machine, each with a distinct
  `ACTIVITYWATCH_MACHINE_ID` and `ACTIVITYWATCH_BASE_URL`

#### Scenario: No account registry

- **WHEN** the connector starts
- **THEN** it resolves configuration entirely from environment variables
  (no OAuth flow, no `public.*_accounts` table) — mirroring the Home
  Assistant connector's pattern for local/self-hosted services

### Requirement: Bucket Discovery

The connector SHALL discover the ActivityWatch window-watcher and AFK-watcher
buckets by querying the buckets index rather than hardcoding bucket IDs.

#### Scenario: Window bucket required

- **WHEN** a poll cycle begins
- **THEN** the connector calls `GET /api/0/buckets` and selects the first
  bucket whose `type` is `"currentwindow"`
- **AND** if no such bucket exists, the poll cycle fails with a
  descriptive, non-fatal error (retried on the next cycle) rather than
  crashing the connector process

#### Scenario: AFK bucket optional

- **WHEN** a poll cycle begins
- **THEN** the connector also looks for a bucket whose `type` is
  `"afkstatus"`
- **AND** if absent, window-focus events are treated as active (`is_afk`
  is recorded as `NULL`, never silently dropped)

### Requirement: App-Class Bucketing

Every window-focus event SHALL be classified into one of four app-class
buckets using a static, case-insensitive substring-match table.

#### Scenario: Classification buckets

- **WHEN** the connector classifies a window-focus event's `app` field
- **THEN** it returns one of `"ide"`, `"terminal"`, `"browser"`, `"other"`
- **AND** an empty or missing `app` value classifies as `"other"`

### Requirement: Browser-Domain Correlation

The connector SHALL best-effort correlate browser window-focus activity with
the ActivityWatch web watcher while preserving the existing sensitive-evidence
boundary for raw URL and title data.

- **WHEN** the focused application is a browser
- **THEN** the connector MAY correlate it with an `aw-watcher-web` bucket
  whose type is `"web.tab.current"`
- **AND** a successful correlation stores only a validated HTTP(S) hostname
  in `browser_domain`
- **AND** absent, unavailable, malformed, or timestamp-unmatched web events
  leave `browser_domain` `NULL` while preserving the coarse `"browser"`
  app class

#### Scenario: Timestamp correlation is deterministic and timezone-safe

- **WHEN** multiple web-watcher events could correlate with a browser
  window-focus timestamp
- **THEN** the connector compares timezone-aware instants as UTC
- **AND** it treats web intervals as half-open `[start, end)` ranges
- **AND** it selects the latest overlapping start time (with a deterministic
  hostname tie-break)
- **AND** it interprets offset-free ActivityWatch timestamps as UTC, per the
  server storage contract, while ignoring malformed timestamps

### Requirement: Privacy — Sensitive Window and Web Details Never Reach Normal Surfaces

Sensitive window and web details MUST never reach normal surfaces. Window
titles, raw web URLs, and web tab titles are privacy-sensitive by default and
must be excluded from every outward-facing surface; only app
class, duration, and a validated browser hostname (when available) SHALL be
projected.

#### Scenario: Envelope excludes window titles

- **WHEN** the connector builds an `ingest.v1` envelope for a window-focus
  event
- **THEN** `payload.normalized_text` contains only the app-class and
  duration
- **AND** `payload.raw` is `None` in `metadata` ingestion tier (default)
- **AND** in `full` ingestion tier, `payload.raw` contains the app-class,
  duration, and raw `app` process name — but never the window title
- **AND** neither ingestion tier includes a web URL, web tab title, or
  `browser_domain`

#### Scenario: Durable evidence table retains the title for forensics

- **WHEN** a window-focus event is persisted to
  `connectors.activitywatch_events`
- **THEN** the raw `window_title` and `app` columns are stored (nullable)
  for forensic / future-reclassification use
- **AND** a correlated raw web-watcher event (including URL and tab title)
  is retained only in `raw_payload`
- **AND** the separate `browser_domain` column contains only the validated
  hostname, never a path, query, fragment, credentials, port, or title
- **AND** this table is not read by anything except the connector (write)
  and the Chronicler adapter (read)

#### Scenario: Chronicler projection exposes only safe browser granularity

- **WHEN** the Chronicler adapter projects a window-focus row into a point
  event or episode
- **THEN** the point payload contains `app_class`, duration, and a validated
  `browser_domain` only for browser rows with a correlated domain
- **AND** screen episodes MAY contain a `browser_domain_seconds` map keyed
  only by validated hostnames
- **AND** it never reads or projects the `window_title`, `app`, or
  `raw_payload` columns

### Requirement: AFK-Aware Screen Episodes

Contiguous active (non-AFK) window-focus events SHALL collapse into
`screen_episode` rollups with a per-app-class duration breakdown.

#### Scenario: Contiguous active rows collapse

- **WHEN** consecutive active window-focus rows are separated by no more
  than the screen-gap threshold (default 10 minutes)
- **THEN** they are projected as a single `screen_episode` spanning their
  combined time range
- **AND** the episode payload records per-app-class second totals
  (`ide_seconds`, `terminal_seconds`, `browser_seconds`, `other_seconds`)
  and a `dominant_app_class`

#### Scenario: AFK rows excluded from episodes

- **WHEN** a window-focus row has `is_afk = true`
- **THEN** it does not produce a point event and its duration is excluded
  from any `screen_episode`

#### Scenario: Gap starts a new episode

- **WHEN** the gap between two consecutive active rows exceeds the
  screen-gap threshold
- **THEN** a new `screen_episode` begins rather than extending the prior one

#### Scenario: Category mapping — Work lane, occupation refinement deferred

- **WHEN** a `screen_episode` is aggregated via
  `aggregations.category_for("activitywatch.window", "screen_episode")`
- **THEN** it resolves to the `"tasks"` category (Work lane) — there is no
  dedicated `"occupation"` category yet (deferred to epic bu-whhll Tier 2,
  routine inference)
- **AND** `dominant_app_class` is carried in the episode payload so a future
  occupation-classifier can refine work-vs-not-work without re-reading raw
  evidence

### Requirement: Durable Evidence Persistence

Every accepted window-focus event SHALL be persisted to
`connectors.activitywatch_events` in addition to being submitted to the
Switchboard.

#### Scenario: Idempotent evidence writes

- **WHEN** the connector persists a window-focus event
- **THEN** it uses `ON CONFLICT (idempotency_key) DO NOTHING` so replays
  never duplicate rows
- **AND** `idempotency_key` follows
  `activitywatch:<machine_id>:<bucket_id>:<ts_iso>`

### Requirement: Bounded First-Run Backfill

The very first poll (no checkpoint yet) MUST NOT flood the system with a
machine's entire ActivityWatch history.

#### Scenario: Backfill window capped

- **WHEN** the connector starts with no prior checkpoint
- **THEN** it only fetches events from at most
  `ACTIVITYWATCH_MAX_BACKFILL_DAYS` (default 30) in the past

### Requirement: Checkpoint and Resume

The connector SHALL persist a timestamp checkpoint so restarts resume without
reprocessing or losing events.

#### Scenario: Checkpoint advances on successful batch

- **WHEN** a poll cycle completes fetching window-focus events
- **THEN** the checkpoint advances to the latest event timestamp seen in
  the batch, persisted via `cursor_store` keyed by
  `("activitywatch", "activitywatch:<machine_id>")`

### Requirement: Connector Lifecycle

The connector SHALL implement the standard connector base-contract
obligations.

#### Scenario: Heartbeat protocol

- **WHEN** the connector is running
- **THEN** it sends `connector.heartbeat.v1` envelopes on
  `CONNECTOR_HEARTBEAT_INTERVAL_S` via the `connector.heartbeat` MCP tool
  with `connector_type="activitywatch"`

#### Scenario: Health and metrics endpoint

- **WHEN** the connector starts
- **THEN** it serves `/health` and `/metrics` on `CONNECTOR_HEALTH_PORT`
  (default 40092), bound to `127.0.0.1` (no public webhook surface — unlike
  OwnTracks, this connector never receives inbound HTTP)

#### Scenario: Source filter gate and filtered-event buffer

- **WHEN** a window-focus event is evaluated against the
  `IngestionPolicyEvaluator` (scope
  `connector:activitywatch:<endpoint_identity>`)
- **THEN** a `block` decision routes the event to
  `connectors.filtered_events` instead of the Switchboard, and it is never
  persisted to the durable evidence table

#### Scenario: Graceful degradation when ActivityWatch is unreachable

- **WHEN** the configured machine is off or ActivityWatch is not running
- **THEN** the poll cycle fails with a descriptive error, health reports
  `degraded`, and polling continues on the next interval — the connector
  process does not crash or exit

### Requirement: Switchboard Registration

ActivityWatch events SHALL bypass LLM classification; their value lives in
the durable evidence table and Chronicler projection, not in natural-language
summaries.

#### Scenario: Global skip rule

- **WHEN** an `ingest.v1` envelope with `source_channel = "activitywatch"`
  reaches the Switchboard ingestion policy evaluator
- **THEN** a `scope='global'`, `rule_type='source_channel'`, `action='skip'`
  rule short-circuits LLM classification
- **AND** the envelope still lands in `public.ingestion_events` for direct
  DB querying

### Requirement: Environment Variables

The connector SHALL be configured entirely via environment variables, with
required variables validated at startup and optional variables falling back
to documented defaults.

#### Scenario: Required and optional configuration

- **WHEN** the connector starts
- **THEN** `SWITCHBOARD_MCP_URL` and `ACTIVITYWATCH_MACHINE_ID` are
  required (the connector refuses to start without either)
- **AND** `ACTIVITYWATCH_BASE_URL` (default `http://localhost:5600`),
  `ACTIVITYWATCH_POLL_INTERVAL_S` (default 60),
  `ACTIVITYWATCH_MAX_BACKFILL_DAYS` (default 30),
  `ACTIVITYWATCH_MIN_EVENT_DURATION_S` (default 0),
  `CONNECTOR_INGESTION_TIER` (default `metadata`), and
  `CONNECTOR_HEALTH_PORT` (default 40092) are optional with documented
  defaults
