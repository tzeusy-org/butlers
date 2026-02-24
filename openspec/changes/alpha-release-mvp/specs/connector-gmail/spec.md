# Gmail Connector

## Purpose
The Gmail connector ingests emails from a user's Gmail inbox in near real-time, keeping butlers current with inbox-driven life events, tasks, and facts without manual forwarding. It supports both polling mode (history delta, default for v1) and Pub/Sub push mode (production, near real-time). The connector implements a sophisticated policy pipeline: label filtering gates ingestion, triage rules assign ingestion tiers (full, metadata-only, skip), priority tiers order the queue, and per-MIME attachment policies control fetch behavior. Backfill mode supports dashboard-triggered historical email processing.

## ADDED Requirements

### Requirement: Gmail Connector Identity and Authentication
The Gmail connector authenticates via Google OAuth and resolves credentials from the butler database.

#### Scenario: OAuth-based authentication with DB-first resolution
- **WHEN** the Gmail connector starts
- **THEN** it resolves Google OAuth credentials (`client_id`, `client_secret`, `refresh_token`) from DB-backed secret storage via `CredentialStore`
- **AND** resolution order is: connector-local DB override (`CONNECTOR_BUTLER_DB_NAME`), then shared credential DB (`BUTLER_SHARED_DB_NAME`, default `butlers`)
- **AND** startup fails fatally if credentials are missing in DB

#### Scenario: OAuth bootstrap requirement
- **WHEN** deploying the Gmail connector
- **THEN** the dashboard OAuth bootstrap flow must be completed first so Google credentials are stored in DB
- **AND** the connector has no env-var-based OAuth credential fallback — DB-only

#### Scenario: Connector identity
- **WHEN** the Gmail connector starts
- **THEN** `source.channel="email"`, `source.provider="gmail"`, and `source.endpoint_identity` identifies the mailbox (e.g., `"gmail:user:alice@gmail.com"`)

### Requirement: Ingestion Modes
The connector supports two ingestion modes with different latency/complexity trade-offs.

#### Scenario: Polling mode (default for v1)
- **WHEN** `GMAIL_PUBSUB_ENABLED` is false (default)
- **THEN** the connector polls Gmail `users.history.list` at `GMAIL_POLL_INTERVAL_S` (default 60 seconds) interval
- **AND** fetches changed message IDs from history, then full message payload/metadata for each, normalizes to `ingest.v1`, and submits to Switchboard
- **AND** trade-off: simpler setup (no Pub/Sub topic or webhook), higher latency (~60s), sufficient for most v1 use cases

#### Scenario: Pub/Sub push mode (production)
- **WHEN** `GMAIL_PUBSUB_ENABLED=true` with a configured `GMAIL_PUBSUB_TOPIC`
- **THEN** the connector:
  1. Creates a Gmail watch subscription via `users.watch` pointing to the Pub/Sub topic
  2. Starts an HTTP webhook server on `GMAIL_PUBSUB_WEBHOOK_PORT` (default 40083)
  3. On push notification, immediately fetches history changes via `users.history.list`
  4. Fetches message payload/metadata, normalizes, and submits
  5. Auto-renews watch subscription before expiration (`GMAIL_WATCH_RENEW_INTERVAL_S`, default 86400 = 1 day)
- **AND** safety-net polling runs alongside Pub/Sub (minimum every 5 minutes) to catch missed notifications

#### Scenario: Webhook token authentication
- **WHEN** `GMAIL_PUBSUB_WEBHOOK_TOKEN` is configured
- **THEN** the webhook endpoint verifies `Authorization: Bearer <token>` header on incoming requests
- **AND** requests without valid tokens are rejected with `{"status": "unauthorized"}`

#### Scenario: Watch lifecycle
- **WHEN** the watch subscription is active
- **THEN** it is created on connector startup, auto-renewed when approaching expiration (configurable), and expires after ~7 days if not renewed
- **AND** the connector logs watch expiration timestamps for monitoring

### Requirement: ingest.v1 Field Mapping

#### Scenario: Gmail field mapping
- **WHEN** a Gmail email is normalized to `ingest.v1`
- **THEN** the mapping is:
  - `source.channel` = `"email"`
  - `source.provider` = `"gmail"` (must be `gmail`, not `imap`)
  - `source.endpoint_identity` = `"gmail:user:<email_address>"`
  - `event.external_event_id` = Gmail message ID (or history event ID when message ID is absent)
  - `event.external_thread_id` = Gmail `threadId`
  - `event.observed_at` = connector-observed timestamp (RFC3339)
  - `sender.identity` = normalized sender address from `From` header
  - `payload.raw` = full Gmail API message payload (Tier 1) or `null` (Tier 2)
  - `payload.normalized_text` = normalized subject + body text (Tier 1) or subject only (Tier 2)
  - `control.idempotency_key` = `"gmail:<endpoint_identity>:<message_id>"`

### Requirement: History ID Cursor Persistence
The connector tracks its position in Gmail's history stream via a persistent cursor.

#### Scenario: Cursor model
- **WHEN** the Gmail connector processes messages
- **THEN** it persists a `GmailCursor` containing `history_id` (Gmail's sequential history ID) and `last_updated_at` (ISO 8601 timestamp) to `CONNECTOR_CURSOR_PATH`

#### Scenario: Checkpoint-after-acceptance
- **WHEN** messages are ingested
- **THEN** the cursor advances only after successful ingest acceptance from Switchboard
- **AND** on restart, it replays from the last safe history ID (harmless due to dedup)

### Requirement: Label Filtering
Gmail label include/exclude policy gates ingestion before tier evaluation.

#### Scenario: Label filter precedence
- **WHEN** a Gmail message has labels
- **THEN** exclude takes precedence over include
- **AND** empty include list means all labels allowed except explicitly excluded ones

#### Scenario: Default label exclusions
- **WHEN** no explicit label config is set
- **THEN** `SPAM` and `TRASH` are excluded by default
- **AND** label comparisons are case-insensitive (normalized to uppercase)

#### Scenario: Label filter implementation
- **WHEN** a message's labels match an exclude rule
- **THEN** `LabelFilterPolicy.evaluate()` returns `(False, "label_excluded:<label>")` and `butlers_connector_gmail_label_filter_total` counter is incremented with `filter_action=excluded`

#### Scenario: Label filter configuration
- **WHEN** `GMAIL_LABEL_INCLUDE` and `GMAIL_LABEL_EXCLUDE` are configured
- **THEN** they accept comma-separated label strings
- **AND** multiple connectors for the same account may use different label filters (e.g., one for INBOX, one for finance labels)

### Requirement: Tiered Email Ingestion Policy
The connector implements a three-tier ingestion policy to process emails in proportion to value.

#### Scenario: Tier 1 — full pipeline
- **WHEN** a message's triage action is `route_to`, `low_priority_queue`, `pass_through`, or unknown
- **THEN** the full `ingest.v1` envelope with complete `payload.raw` is submitted for LLM classification, routing, and butler processing

#### Scenario: Tier 2 — metadata-only pipeline
- **WHEN** a message's triage action is `metadata_only`
- **THEN** a slim envelope with `payload.raw=null`, `payload.normalized_text=<subject only>`, and `control.ingestion_tier="metadata"` is submitted
- **AND** Switchboard bypasses LLM classification and persists a metadata reference only

#### Scenario: Tier 3 — skip
- **WHEN** a message's triage action is `skip` or label exclusion matched
- **THEN** the connector does not submit to Switchboard
- **AND** skip counters are incremented for auditability — no silent drops

#### Scenario: Default tier is always Tier 1
- **WHEN** no triage rule matches a message
- **THEN** the default action is `pass_through` which maps to Tier 1 (safety: never silently drop potentially important mail)

#### Scenario: Policy pipeline order
- **WHEN** the policy pipeline runs for a message
- **THEN** the order is: (1) label include/exclude filter → (2) triage rule evaluation for ingestion tier → (3) policy tier assignment for queue ordering → (4) Prometheus counter emission

### Requirement: Policy Tier Assignment
The connector assigns policy tiers for Switchboard queue ordering using a `PolicyTierAssigner` with first-match-wins rules.

#### Scenario: Known contact → high priority
- **WHEN** the sender address is in the known-contact set (loaded from `GMAIL_KNOWN_CONTACTS_PATH` JSON file cache)
- **THEN** `policy_tier="high_priority"` with rule `"known_contact"`

#### Scenario: Reply to outbound mail → high priority
- **WHEN** the `In-Reply-To` header references a message ID from the user's sent items
- **THEN** `policy_tier="high_priority"` with rule `"reply_to_outbound"`

#### Scenario: Direct correspondence → interactive
- **WHEN** the user's address (`GMAIL_USER_EMAIL`) is in `To` or `Cc`, there is no `List-Unsubscribe` header, and no bulk `Precedence` header
- **THEN** `policy_tier="interactive"` with rule `"direct_correspondence"`

#### Scenario: Fallback → default
- **WHEN** no priority rule matches
- **THEN** `policy_tier="default"` with rule `"fallback_default"`

#### Scenario: Policy tier telemetry
- **WHEN** a policy tier is assigned
- **THEN** `butlers_connector_gmail_priority_tier_assigned_total` counter is incremented with labels `endpoint_identity`, `policy_tier`, `assignment_rule`

### Requirement: Triage Rules
Connector-side triage rules evaluated before ingest to determine ingestion tier.

#### Scenario: Sender domain rule
- **WHEN** a triage rule has `rule_type=sender_domain`
- **THEN** the sender's domain is compared with exact or suffix match against the condition domain

#### Scenario: Sender address rule
- **WHEN** a triage rule has `rule_type=sender_address`
- **THEN** the normalized sender address is compared for exact match

#### Scenario: Header condition rule
- **WHEN** a triage rule has `rule_type=header_condition`
- **THEN** the specified header is checked with operation `present`, `equals`, or `contains`

#### Scenario: Label match rule
- **WHEN** a triage rule has `rule_type=label_match`
- **THEN** the message's Gmail label IDs (uppercase) are checked for the specified label

#### Scenario: Rule priority and default
- **WHEN** multiple triage rules are defined
- **THEN** rules are evaluated in priority order (first match wins)
- **AND** if no rule matches, the default action is `pass_through` (Tier 1)

### Requirement: Attachment Handling
The connector implements metadata-first lazy fetching with per-MIME-type size limits and fetch mode policies.

#### Scenario: Attachment policy map (ATTACHMENT_POLICY)
- **WHEN** the connector processes attachments
- **THEN** it uses the `ATTACHMENT_POLICY` dict keyed by MIME type:
  - Images (`image/jpeg`, `image/png`, `image/gif`, `image/webp`): 5 MB max, **lazy** fetch
  - PDF (`application/pdf`): 15 MB max, **lazy** fetch
  - Spreadsheets (`.xlsx`, `.xls`, `.csv`): 10 MB max, **lazy** fetch
  - Documents (`.docx`, `message/rfc822`): 10 MB max, **lazy** fetch
  - Calendar (`text/calendar`): 1 MB max, **eager** fetch (downloaded immediately)
- **AND** unsupported MIME types (not in `SUPPORTED_ATTACHMENT_TYPES`) are silently skipped

#### Scenario: Global attachment size cap
- **WHEN** an attachment exceeds `GLOBAL_MAX_ATTACHMENT_SIZE_BYTES` (25 MB — Gmail's hard ceiling)
- **THEN** it is skipped regardless of per-type limit
- **AND** `connector_attachment_skipped_oversized_total` metric is incremented

#### Scenario: Lazy fetch — metadata only at ingest time
- **WHEN** a supported non-calendar attachment is within size limits
- **THEN** only metadata (reference, size, MIME type, filename) is recorded at ingest time — no payload download
- **AND** on-demand fetch occurs when a butler actually needs the content, with idempotent re-fetch semantics

#### Scenario: Eager fetch — calendar attachments
- **WHEN** a `text/calendar` attachment is within the 1 MB limit
- **THEN** it is downloaded immediately at ingest time and stored in BlobStore
- **AND** `.ics` attachments bypass LLM routing classification and route directly to the calendar module

#### Scenario: [TARGET-STATE] Attachment reference persistence
- **WHEN** attachment metadata is collected at ingest time
- **THEN** a row is written to `switchboard.attachment_refs` with `message_id`, `attachment_id`, `filename`, `media_type`, `size_bytes`, `fetched` (boolean), `blob_ref` (nullable)

#### Scenario: Attachment metrics
- **WHEN** attachments are processed
- **THEN** counters track: `connector_attachment_fetched_eager_total`, `connector_attachment_fetched_lazy_total`, `connector_attachment_skipped_oversized_total`, `connector_attachment_type_distribution_total`

### Requirement: Backfill Mode
The connector implements the optional backfill polling protocol for dashboard-triggered historical email processing.

#### Scenario: Backfill poll loop
- **WHEN** `CONNECTOR_BACKFILL_ENABLED=true` (default)
- **THEN** every `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60) seconds, the connector calls `backfill.poll(connector_type, endpoint_identity)` on Switchboard
- **AND** backfill uses a dedicated semaphore with at most `CONNECTOR_MAX_INFLIGHT - 1` concurrent slots, reserving at least one for live ingestion

#### Scenario: Backfill job model
- **WHEN** `backfill.poll` returns a pending job
- **THEN** the job contains: `job_id`, `date_from`/`date_to` (YYYY-MM-DD bounds), `rate_limit_per_hour` (default 100), `daily_cost_cap_cents` (default 500), `cursor` (server-side resume state), `target_categories` (optional filter)

#### Scenario: Gmail history traversal for backfill
- **WHEN** the connector processes a backfill job
- **THEN** it uses `users.messages.list` with date-bounded queries (`after:YYYY/MM/DD before:YYYY/MM/DD`)
- **AND** each message is normalized using the same tiered ingestion rules as live mode
- **AND** backfill does not process drafts, sent mail, or trash (inbox/label-scoped only)

#### Scenario: Backfill rate limiting
- **WHEN** backfill is active
- **THEN** `rate_limit_per_hour` is enforced via a token bucket with refill rate `rate_limit_per_hour / 3600` tokens/second
- **AND** Gmail API quota (250 quota units/second per user) is also enforced; whichever limit is more restrictive applies

#### Scenario: Backfill progress reporting and stop conditions
- **WHEN** backfill processes messages
- **THEN** progress is reported via `backfill.progress(...)` every `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` messages (default 50)
- **AND** server-side cursor is persisted via the progress call
- **AND** if `backfill.progress` returns `paused`, `cancelled`, or `cost_capped`, the connector stops backfill immediately

#### Scenario: Backfill cost tracking
- **WHEN** backfill messages are submitted
- **THEN** estimated LLM cost per message is tracked from payload size and per-token cost estimate
- **AND** `cost_spent_cents` is reported on each progress call (connector estimates only; Switchboard enforces cap)

#### Scenario: Backfill capability advertisement
- **WHEN** the connector sends heartbeats with backfill enabled
- **THEN** `capabilities.backfill=true` is included in heartbeat metadata
- **AND** the dashboard uses this to enable/disable backfill controls for this connector

#### Scenario: Backfill non-interference with live cursor
- **WHEN** backfill traverses historical messages
- **THEN** the live ingestion cursor checkpoint is not modified
- **AND** backfill cursor is maintained server-side in `backfill_jobs.cursor` via MCP

### Requirement: [TARGET-STATE] Selective Email Backfill Strategy
Dashboard-triggered, cost-aware historical email processing with recommended category windows.

#### Scenario: MCP-mediated orchestration
- **WHEN** a backfill job is created from the dashboard
- **THEN** the flow is: dashboard API → Switchboard MCP `create_backfill_job` → `backfill_jobs` row → connector polls → connector traverses history → connector submits via `ingest.v1` → connector reports progress via MCP

#### Scenario: Recommended category windows
- **WHEN** selective batch backfill is configured
- **THEN** recommended windows are: finance (7 years), health (all available), relationship/direct (2-3 years), travel (2 years), newsletters/marketing (skip)

#### Scenario: [TARGET-STATE] On-demand backfill
- **WHEN** a user question triggers historical search (e.g., "When did I last visit Dr. Smith?")
- **THEN** `email_search_and_ingest(query, max_results?)` MCP tool is invoked
- **AND** default/maximum `max_results` is 50; results are ingested immediately

#### Scenario: Backfill audit and consent
- **WHEN** a backfill job is initiated
- **THEN** explicit opt-in confirmation is required in dashboard UX
- **AND** lifecycle actions (create, pause, resume, cancel, complete, error, cost cap) are audit logged

### Requirement: [TARGET-STATE] Email Metadata Storage for Tier 2
Tier 2 records are stored in a dedicated reference table.

#### Scenario: Tier 2 metadata persistence
- **WHEN** a Tier 2 email is accepted
- **THEN** it is stored in `switchboard.email_metadata_refs` with `endpoint_identity`, `gmail_message_id`, `thread_id`, `sender`, `subject`, `received_at`, `labels`, `summary`, `tier=2`

#### Scenario: On-demand body retrieval
- **WHEN** a butler needs the full body of a Tier 2 email
- **THEN** it is fetched on demand from Gmail API by message ID
- **AND** fetching does not auto-promote to Tier 1

#### Scenario: Tier 2 retention
- **WHEN** `email_metadata_refs` records age
- **THEN** default retention is 90 days with scheduled pruning

### Requirement: Multiple Concurrent Connectors
Multiple Gmail connector instances can run concurrently for different accounts or policy slices.

#### Scenario: Per-account isolation
- **WHEN** multiple Gmail connectors run
- **THEN** each has a unique `CONNECTOR_ENDPOINT_IDENTITY` and its own `CONNECTOR_CURSOR_PATH`
- **AND** each may use different label filters (e.g., one for INBOX, another for finance labels)

#### Scenario: Uniqueness boundary
- **WHEN** deduplication is evaluated
- **THEN** the boundary is `(CONNECTOR_PROVIDER, CONNECTOR_CHANNEL, CONNECTOR_ENDPOINT_IDENTITY, external_event_id)`

#### Scenario: Horizontal replicas
- **WHEN** multiple instances share the same endpoint identity
- **THEN** explicit coordination/lease ownership for the cursor is required
- **AND** duplicate accepted ingest responses are treated as success

### Requirement: Environment Variables

#### Scenario: Required variables
- **WHEN** the Gmail connector starts
- **THEN** `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=gmail`, `CONNECTOR_CHANNEL=email`, `CONNECTOR_ENDPOINT_IDENTITY`, `CONNECTOR_CURSOR_PATH` must be set
- **AND** database connectivity (`DATABASE_URL` or `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`) must be configured for OAuth credential resolution

#### Scenario: Pub/Sub variables (optional)
- **WHEN** Pub/Sub mode is desired
- **THEN** `GMAIL_PUBSUB_ENABLED=true`, `GMAIL_PUBSUB_TOPIC`, and optionally `GMAIL_PUBSUB_WEBHOOK_PORT` (default 40083), `GMAIL_PUBSUB_WEBHOOK_PATH` (default `/gmail/webhook`), `GMAIL_PUBSUB_WEBHOOK_TOKEN` are set

#### Scenario: Runtime control variables
- **WHEN** the connector starts
- **THEN** `GMAIL_POLL_INTERVAL_S` (default 60), `GMAIL_WATCH_RENEW_INTERVAL_S` (default 86400), `GMAIL_LABEL_INCLUDE`, `GMAIL_LABEL_EXCLUDE`, `GMAIL_USER_EMAIL`, `GMAIL_KNOWN_CONTACTS_PATH`, `CONNECTOR_MAX_INFLIGHT` (default 8), `CONNECTOR_HEALTH_PORT` (default 40082) are optionally configurable

#### Scenario: Backfill variables
- **WHEN** backfill is configured
- **THEN** `CONNECTOR_BACKFILL_ENABLED` (default true), `CONNECTOR_BACKFILL_POLL_INTERVAL_S` (default 60), `CONNECTOR_BACKFILL_PROGRESS_INTERVAL` (default 50) are optionally configurable

### Requirement: Health Status

#### Scenario: Health model
- **WHEN** the Gmail connector's health is queried
- **THEN** it returns: `status` (`healthy`/`unhealthy`), `uptime_seconds`, `last_checkpoint_save_at`, `last_ingest_submit_at`, `source_api_connectivity` (`connected`/`disconnected`/`unknown`), `timestamp`
