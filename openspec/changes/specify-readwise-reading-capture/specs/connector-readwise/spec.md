## ADDED Requirements

### Requirement: Readwise Connector Identity and Authentication

The Readwise connector SHALL run as a single-account, owner-only process that authenticates with a
static bearer token resolved from the owner's companion entity. It SHALL NOT implement multi-account
discovery.

#### Scenario: Single owner identity

- **WHEN** the Readwise connector starts
- **THEN** `source.channel = "reading"`, `source.provider = "readwise"`, and
  `source.endpoint_identity = "readwise:owner"`
- **AND** it SHALL resolve the access token via `resolve_owner_entity_info(pool,
  "readwise_token")` (secured `entity_info` type, mirroring `steam_api_key`)
- **AND** no per-account discovery loop exists — there is exactly one connector instance

#### Scenario: Token validation at startup

- **WHEN** the connector resolves a `readwise_token` value
- **THEN** it SHALL validate the token via `GET https://readwise.io/api/v2/auth/`
- **AND** a 204 response SHALL be treated as valid; any other status SHALL be treated as invalid
  credentials

#### Scenario: Credential absent at startup

- **WHEN** no `readwise_token` exists on the owner's companion entity
- **THEN** the connector SHALL start in idle mode (health = `degraded`, no polling loop active)
- **AND** it SHALL periodically re-check for a credential (interval mirrors the Steam dynamic
  account re-scan pattern, default 300 seconds) rather than exiting

#### Scenario: Credential revoked or rotated

- **WHEN** a previously valid `readwise_token` starts returning 401/403 from the auth-check or
  export endpoint
- **THEN** the connector SHALL transition to `error` health status, stop attempting further export
  calls until the next credential re-check, and SHALL NOT silently continue with a stale token
- **AND** it SHALL NOT claim any data was captured during the outage window

### Requirement: Export Poll and Cursor Pagination

The connector SHALL poll `GET https://readwise.io/api/v2/export/` and fully drain its
cursor-paginated response before advancing its incremental watermark.

#### Scenario: Poll interval

- **WHEN** the connector runs steady-state (a cursor already exists)
- **THEN** it SHALL poll on a configurable interval (default 1800 seconds / 30 minutes)

#### Scenario: In-cycle pagination drain

- **WHEN** a poll cycle begins
- **THEN** the connector SHALL request `/v2/export/` with `updatedAfter` set to the stored
  watermark (see "Incremental Cursor Persistence")
- **AND** for each response containing a non-null `nextPageCursor`, it SHALL issue a follow-up
  request with `pageCursor=<nextPageCursor>` before processing is considered complete for that
  cycle
- **AND** it SHALL NOT advance the stored watermark until every page in the cycle has been
  processed (a crash mid-pagination replays the whole cycle from the last-saved watermark, which is
  safe per the dedup identity in "ingest.v1 Field Mapping and Content Tier")

### Requirement: First-Poll Full Backfill

On first connect, with no stored cursor, the connector SHALL perform Readwise's documented
recommended initial-sync pattern: a full export with no `updatedAfter` filter.

#### Scenario: No stored cursor triggers full backfill

- **WHEN** the connector starts and `cursor_store.load_cursor(pool, "readwise", "readwise:owner")`
  returns `None`
- **THEN** it SHALL request `/v2/export/` with no `updatedAfter` parameter, draining all pages
- **AND** every returned highlight SHALL be processed through the same event-mapping and evidence
  path as steady-state polling (no separate "baseline, no event" mode — unlike Steam's owned-games
  baseline, a personal reading library is bounded and every existing highlight is genuine capture
  content, not baseline noise)

#### Scenario: Backfill sets the first watermark

- **WHEN** the full backfill completes
- **THEN** the connector SHALL persist a watermark equal to the maximum `updated` value observed
  across the backfilled set (or the poll start time if the library was empty)

### Requirement: Incremental Cursor Persistence

The connector SHALL persist its watermark using the shared `cursor_store`, keyed by connector type
and endpoint identity, advancing only after a fully-drained and fully-submitted poll cycle.

#### Scenario: Cursor storage shape

- **WHEN** a poll cycle completes successfully
- **THEN** the connector SHALL call `cursor_store.save_cursor(pool, "readwise", "readwise:owner",
  json.dumps({"updated_after": "<ISO 8601 max observed 'updated'>"}), parent_endpoint_identity=NO_PARENT)`

#### Scenario: Resume after restart

- **WHEN** the connector restarts
- **THEN** it SHALL call `cursor_store.load_cursor(pool, "readwise", "readwise:owner")` and resume
  polling with that `updated_after` value as the next `updatedAfter` request parameter
- **AND** a replayed poll cycle is safe because the per-highlight event identity (see below) makes
  re-submission of an unchanged highlight an idempotent no-op at the Switchboard dedup layer

### Requirement: ingest.v1 Field Mapping and Content Tier

Each captured or updated highlight SHALL be submitted as a metadata-tier `ingest.v1` envelope whose
event identity is derived from Readwise's own `updated` timestamp, keeping highlight/note text out
of the envelope itself.

#### Scenario: Highlight event envelope shape

- **WHEN** the connector processes a highlight row from the export response
- **THEN** the `ingest.v1` envelope SHALL be:
  - `source.channel = "reading"`, `source.provider = "readwise"`,
    `source.endpoint_identity = "readwise:owner"`
  - `event.type = "highlight_captured"`
  - `event.external_event_id = "readwise:highlight:<highlight_id>:<updated>"` where `<updated>` is
    the highlight's provider-assigned `updated` ISO 8601 value verbatim
  - `event.observed_at` = poll timestamp (RFC 3339)
  - `sender.identity = "readwise:owner"`
  - `payload.raw = null`
  - `payload.normalized_text` = a short, non-sensitive summary containing only the book title
    (e.g. `"Captured highlight from <book title>"`), never the highlight text, note, or tags
  - `control.ingestion_tier = "metadata"`
  - `control.idempotency_key = event.external_event_id`
  - `control.policy_tier = "default"`

#### Scenario: Global metadata-only policy pre-resolution

- **WHEN** the Switchboard evaluates an envelope whose `external_event_id` matches the
  `readwise:highlight:` prefix
- **THEN** a seeded global `substring` `ingestion_rules` row (mirroring
  `030_switchboard_spotify_spoken_metadata_only.py` and
  `025_switchboard_steam_status_skip.py`) SHALL pre-resolve `metadata_only` triage
- **AND** the envelope SHALL be persisted without spawning LLM classification or butler routing
- **AND** this rule SHALL NOT match any other channel/provider's event-id prefix

#### Scenario: Full content stored only in connector-owned evidence

- **WHEN** the connector processes a highlight row
- **THEN** the full highlight text, note, book title/author, location, category, tags, and source
  URL SHALL be upserted into `connectors.readwise_highlights` (see "Durable Evidence Surface")
- **AND** none of those fields SHALL appear in the `ingest.v1` envelope's `payload` — this is the
  mechanism that keeps highlight content out of the LLM-classification path (see design.md D8)

### Requirement: Update and Deduplication Identity

The connector SHALL treat re-polling an unchanged highlight as an idempotent no-op and a genuinely
edited highlight as a new, truthful event, using Readwise's own `updated` field as the sole
authority — never a connector-side guess.

#### Scenario: Unchanged highlight re-poll is a no-op

- **WHEN** a highlight is returned again with the same `updated` value as previously captured
- **THEN** the resulting `event.external_event_id` is identical to the prior submission
- **AND** the Switchboard's dedup layer SHALL return the existing `request_id` with
  `duplicate=true`; no new evidence row mutation occurs beyond the idempotent upsert

#### Scenario: Edited highlight is a distinct event

- **WHEN** a highlight is returned with an `updated` value later than the last-captured value for
  that `highlight_id`
- **THEN** the connector SHALL treat it as a new event with a new `external_event_id`
- **AND** it SHALL upsert `connectors.readwise_highlights` in place (same `highlight_id`, updated
  text/note/updated_at fields) rather than inserting a duplicate row

#### Scenario: Equal-timestamp siblings across a pagination boundary

- **WHEN** two distinct highlights share the same `updated` timestamp and land on either side of a
  `pageCursor` page boundary within one poll cycle
- **THEN** each highlight's event identity already includes its own `highlight_id`, so both are
  submitted as distinct, individually-idempotent events regardless of page boundary placement — no
  additional tie-break bookkeeping is required beyond the per-highlight identity already specified

### Requirement: Deletion Reconciliation

Because Readwise's export endpoint silently omits deleted highlights by default, the connector
SHALL run a separate, bounded reconciliation poll to detect and tombstone deletions rather than
inferring deletion from absence in the normal incremental poll.

#### Scenario: Reconciliation poll scope and cadence

- **WHEN** the reconciliation poll runs (default interval 24 hours, configurable)
- **THEN** it SHALL request `/v2/export/?includeDeleted=true&ids=<batched previously-captured
  user_book_ids>` restricted to book IDs already present in `connectors.readwise_highlights`
- **AND** it SHALL NOT pass `includeDeleted=true` on the normal incremental poll (that would
  re-return every already-tombstoned highlight indefinitely)

#### Scenario: Deletion tombstones evidence without a new ingest event

- **WHEN** the reconciliation poll observes `is_deleted: true` for a highlight previously captured
  with `deleted_at IS NULL`
- **THEN** the connector SHALL set `connectors.readwise_highlights.deleted_at` for that row
- **AND** it SHALL NOT submit a new `ingest.v1` event for the deletion — a deletion retracts
  previously captured evidence and is not new content

#### Scenario: Reconciliation failure does not block incremental polling

- **WHEN** the reconciliation poll fails (network error, rate limit, credential issue)
- **THEN** it SHALL log the failure and retry on its next scheduled interval
- **AND** the normal incremental poll loop SHALL continue unaffected

### Requirement: Truthful Annotation Provenance

Captured highlight/book evidence SHALL represent only that an annotation was saved in Readwise. No
reading-duration, completion percentage, or reading-session inference SHALL be derived from
highlight creation or update.

#### Scenario: No fabricated duration or completion

- **WHEN** any consumer (connector evidence, a future Chronicler/Education adapter, or a dashboard
  surface) projects Readwise-sourced evidence
- **THEN** it SHALL NOT assert a reading duration, a "finished reading" state, or any percentage
  completion derived from highlight count, highlight timing, or book metadata
- **AND** the evidence SHALL be labeled as "highlight/annotation captured", not "reading session"

#### Scenario: Independence from the existing calendar/health-fact reading signal

- **WHEN** Readwise-sourced evidence and `ReadingInferredAdapter`'s calendar-titled or
  `health.facts` `reading_session` evidence both exist for the same time period
- **THEN** they SHALL remain two independently sourced, independently labeled signals — this
  change SHALL NOT modify `ReadingInferredAdapter`'s existing calendar/health-fact behavior or
  merge the two signal types into one inferred episode

### Requirement: Durable Evidence Surface

The connector SHALL persist bounded, idempotent, least-privilege evidence for each captured
highlight in a connector-owned table.

#### Scenario: Evidence upsert key and fields

- **WHEN** the connector writes captured highlight evidence
- **THEN** it SHALL upsert `connectors.readwise_highlights` keyed by `highlight_id` (stable,
  Readwise-assigned), with fields limited to: `highlight_id`, `user_book_id`, `book_title`,
  `book_author`, `content_kind` (`book | article | tweet | podcast | supplemental | unknown`, from
  Readwise's `category`), `text`, `note`, `location`, `location_type`, `source_url`, `tags`,
  `highlighted_at`, `updated_at` (Readwise's `updated`), `captured_at` (connector observation
  time), and `deleted_at` (nullable)
- **AND** it SHALL NOT store any Spotify/Steam/other-connector fields, transcripts, full book text,
  or raw unparsed API response bodies

#### Scenario: Evidence write failure does not block ingest submission

- **WHEN** the evidence upsert fails (transient DB error)
- **THEN** the connector SHALL log the failure and still attempt the passive `ingest.v1` envelope
  submission for that highlight, matching the existing Spotify spoken-session evidence-write
  failure posture

#### Scenario: Least-privilege ACL

- **WHEN** the core migration creating `connectors.readwise_highlights` runs
- **THEN** it SHALL grant DML only to `connector_writer` and SELECT only to the butler role(s) a
  future Chronicler/Education adapter needs, tolerating absent runtime roles (guarded, matching the
  `connectors.spotify_spoken_sessions` migration pattern)
- **AND** no butler SHALL receive write access through this migration

### Requirement: Rate Limiting and Error Handling

The connector SHALL respect Readwise's published rate limits and degrade gracefully on transient
failures without fabricating success.

#### Scenario: 429 handling honors Retry-After

- **WHEN** `/v2/export/` returns HTTP 429
- **THEN** the connector SHALL read the `Retry-After` header (when present) and wait at least that
  long before retrying
- **AND** in the absence of `Retry-After`, it SHALL apply exponential backoff starting at 60
  seconds, doubling up to a 3600-second cap

#### Scenario: Transient error handling

- **WHEN** an export or reconciliation call fails with a network error or 5xx status
- **THEN** the connector SHALL retry with exponential backoff (starting at 5 seconds, max 300
  seconds)
- **AND** after 5 consecutive failures, health status SHALL transition to `error`

#### Scenario: Empty library is not an error

- **WHEN** the owner's Readwise library has zero highlights
- **THEN** the connector SHALL report `healthy` status with zero events submitted, not `degraded`
  or `error`
- **AND** it SHALL NOT fabricate placeholder evidence

### Requirement: Filtered Event Batch Flush

The connector SHALL implement the connector base contract's filtered-event batch flush obligation.

#### Scenario: Error events recorded in filtered_events

- **WHEN** processing a highlight row fails (validation error, submission error)
- **THEN** the error SHALL be recorded in `connectors.filtered_events` with
  `connector_type = "readwise"`, `endpoint_identity = "readwise:owner"`,
  `source_channel = "reading"`, `status = 'error'`, and `error_detail`
- **AND** the buffer SHALL be flushed in a single batch INSERT after each poll cycle

#### Scenario: No active filters passes all events

- **WHEN** no source filter rules exist for `scope = 'connector:readwise:readwise:owner'`
- **THEN** the filter gate SHALL be a no-op and all highlight events SHALL pass through

### Requirement: Replay Queue Drain Loop

The connector SHALL check for pending replay requests after each poll cycle per the connector base
contract.

#### Scenario: Drain loop executes after poll cycle

- **WHEN** a poll cycle completes (including filtered-event flush)
- **THEN** the connector SHALL query `connectors.filtered_events` for rows with
  `status = 'replay_pending'` matching `connector_type = 'readwise'` and
  `endpoint_identity = 'readwise:owner'`, processing up to 10 items per cycle with
  `FOR UPDATE SKIP LOCKED`

### Requirement: Heartbeat Protocol

The connector SHALL send periodic heartbeat envelopes to the Switchboard for liveness tracking.

#### Scenario: Heartbeat envelope

- **WHEN** the heartbeat interval elapses (default 120 seconds)
- **THEN** the connector SHALL submit a `connector.heartbeat.v1` envelope with
  `connector.connector_type = "readwise"`, `connector.endpoint_identity = "readwise:owner"`,
  `status.state` reflecting current health, `counters.messages_ingested`,
  `counters.messages_failed`, `counters.source_api_calls`, and `checkpoint.cursor` set to the
  stored `updated_after` watermark

#### Scenario: Heartbeat failure does not crash the poll loop

- **WHEN** a heartbeat submission fails
- **THEN** the connector SHALL log a warning and continue polling on schedule

### Requirement: Prometheus Metrics

The connector SHALL export Prometheus metrics for observability, following the shared
`ConnectorMetrics` class.

#### Scenario: Readwise-specific counters

- **WHEN** the metrics endpoint is queried
- **THEN** the following are available: `connector_readwise_polls_total{status}`,
  `connector_readwise_highlights_captured_total`, `connector_readwise_highlights_updated_total`,
  `connector_readwise_highlights_deleted_total`, `connector_readwise_api_errors_total{http_status}`,
  and `connector_readwise_rate_limit_backoffs_total`
- **AND** the shared base metrics (`connector_ingest_submissions_total`,
  `connector_ingest_latency_seconds`, `connector_source_api_calls_total`,
  `connector_checkpoint_saves_total`, `connector_errors_total`) apply unchanged
