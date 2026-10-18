## MODIFIED Requirements

### Requirement: Tiered Email Ingestion Policy

The connector SHALL implement a three-tier ingestion policy to process emails in proportion to value. Connector-side triage actions SHALL choose only the ingestion tier; they SHALL NOT select a target butler, and Tier 1 mail SHALL reach goal admission without classification or routing.

Source: RFC 0038 D1, D5; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: Tier 1 — full pipeline
- **WHEN** a message's triage action is `route_to`, `low_priority_queue`, `pass_through`, or unknown, and it is not an automated reply or bounce (see "Automated replies and bounces are metadata-only")
- **THEN** the full `ingest.v1` envelope with complete `payload.raw` is submitted to the Switchboard and handed to goal admission with no LLM classification or routing step
- **AND** a `route_to` action selects no target butler
- **AND** the admitted goal is never owner-origin by virtue of the email alone; origin follows owner-channel authorization of the channel and sender
- **AND** `low_priority_queue` mail is admitted at low admission priority as today's queue ordering does, and as non-owner mail it starts exposed

#### Scenario: Tier 2 — metadata-only pipeline
- **WHEN** a message's triage action is `metadata_only`
- **THEN** a slim envelope with `payload.raw=null`, `payload.normalized_text=<subject only>`, and `control.ingestion_tier="metadata"` is submitted
- **AND** Switchboard bypasses LLM classification and persists a metadata reference only

#### Scenario: Automated replies and bounces are metadata-only
- **WHEN** a message's headers mark it as a bounce (`Content-Type: multipart/report` with `report-type=delivery-status`, or a `mailer-daemon`/`postmaster` sender with a `message/delivery-status` part) or as an automated reply (`Auto-Submitted: auto-replied`, `X-Autoreply`, `X-Autorespond`, or `Precedence: auto_reply`)
- **THEN** the connector assigns Tier 2 with reason `bounce` or `auto_reply` on `butlers_connector_gmail_tier_assigned_total`, and the slim envelope carries `control.automated_kind` with the same value
- **AND** a global rule resolving to `skip` still skips the message, while `route_to`, `low_priority_queue` and `pass_through` do not raise it to Tier 1
- **AND** `Auto-Submitted: auto-generated` or `no`, `Precedence: bulk` or `list`, list mail, and missing or malformed headers are ordinary mail, so detection never drops a message

#### Scenario: Tier 3 — skip
- **WHEN** a message's triage action is `skip` or label exclusion matched
- **THEN** the connector does not submit to Switchboard
- **AND** skip counters are incremented for auditability — no silent drops

#### Scenario: Default tier is always Tier 1
- **WHEN** no triage rule matches a message that is not an automated reply or bounce
- **THEN** the default action is `pass_through` which maps to Tier 1 (safety: never silently drop potentially important mail)

#### Scenario: Policy pipeline order
- **WHEN** the policy pipeline runs for a message
- **THEN** the order is: (1) label include/exclude filter → (2) source filter gate → (3) triage rule evaluation for ingestion tier → (4) policy tier assignment for queue ordering → (5) Switchboard submission

### Requirement: Attachment Handling

The connector SHALL implement metadata-first lazy fetching with per-MIME-type size limits and fetch mode policies. Fetched attachments are stored in the S3-compatible blob store; blob refs use the `s3://` scheme. No attachment type SHALL cause an envelope to be routed to a butler or module; attachment references travel with the envelope into goal admission.

Source: RFC 0038 D1, D4, D6; RFC 0003 disposition
Scope: v1-mandatory

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
- **AND** on-demand fetch occurs when a goal invocation needs the content, through a grant-scoped read capability whose output is untrusted, with idempotent re-fetch semantics

#### Scenario: Eager fetch — calendar attachments stored in S3
- **WHEN** a `text/calendar` attachment is within the 1 MB limit
- **THEN** it is downloaded immediately at ingest time and stored in the S3-compatible BlobStore
- **AND** the `blob_ref` column SHALL contain an `s3://` URI
- **AND** the `.ics` reference travels with the envelope into goal admission; it is not routed directly to the calendar module or any butler

#### Scenario: Attachment reference persistence
- **WHEN** attachment metadata is collected at ingest time
- **THEN** a row is written to `switchboard.attachment_refs` with `message_id`, `attachment_id`, `filename`, `media_type`, `size_bytes`, `fetched` (boolean), `blob_ref` (nullable, `s3://` scheme when populated)

#### Scenario: Attachment metrics
- **WHEN** attachments are processed
- **THEN** counters track: `connector_attachment_fetched_eager_total`, `connector_attachment_fetched_lazy_total`, `connector_attachment_skipped_oversized_total`, `connector_attachment_type_distribution_total`

### Requirement: [TARGET-STATE] Selective Email Backfill Strategy

The connector SHALL provide dashboard-triggered, cost-aware historical email processing with recommended category windows. Backfill SHALL be started only from the owner-authenticated dashboard; no model-reachable capability SHALL submit ingest envelopes or admit goals from searched mail. Backfilled envelopes SHALL be recorded and SHALL admit no goal by default (`module-pipeline` "Ingestion Architecture"). Only when the owner's backfill job explicitly opts a category into processing SHALL its messages be admitted, each as a `third_party`-origin goal that starts exposed, at backfill priority and within the job's cost cap.

Source: RFC 0038 D5, D7, D8
Scope: v1-mandatory

#### Scenario: MCP-mediated orchestration
- **WHEN** a backfill job is created from the dashboard
- **THEN** the flow is: dashboard API → Switchboard MCP `create_backfill_job` → `backfill_jobs` row → connector polls → connector traverses history → connector submits via `ingest.v1` → connector reports progress via MCP

#### Scenario: Recommended category windows
- **WHEN** selective batch backfill is configured
- **THEN** recommended windows are: finance (7 years), health (all available), relationship/direct (2-3 years), travel (2 years), newsletters/marketing (skip)

#### Scenario: [TARGET-STATE] On-demand backfill
- **WHEN** an owner goal needs historical mail to answer a question (e.g., "When did I last visit Dr. Smith?")
- **THEN** the lead or a child uses a grant-scoped Gmail search read capability, at most 50 results per call, whose output is untrusted and exposes the invocation
- **AND** the results return only to the invoking goal; they are not submitted as `ingest.v1` envelopes and admit no new goal

#### Scenario: Backfill audit and consent
- **WHEN** a backfill job is initiated
- **THEN** explicit opt-in confirmation is required in dashboard UX
- **AND** lifecycle actions (create, pause, resume, cancel, complete, error, cost cap) are audit logged

#### Scenario: Backfilled mail is recorded, not reasoned over, by default
- **WHEN** a backfill job whose categories are not opted into processing submits historical messages
- **THEN** each envelope is recorded and searchable, and no goal is admitted for it
- **AND** when a category is opted in, each of its messages is admitted as an exposed `third_party` goal that counts against the job's cost cap

### Requirement: Bearer material never persists

Connectors and the Switchboard ingest boundary SHALL withhold bearer material (one-time codes,
password-reset links, magic links, and Telegram login codes) from every persisted payload. Only the
fact that an auth artifact arrived SHALL be kept, as a typed placeholder recording the artifact kind,
the provider domain, and the observation time. Scrubbing SHALL NOT change the message's
ingestion-policy decision or its goal admission.

Source: RFC 0038 D1; RFC 0003 disposition
Scope: v1-mandatory

#### Scenario: One-time code is replaced by a placeholder
- **WHEN** an `ingest.v1` envelope whose text contains a one-time code (for example "Your code is 482913") reaches the ingest boundary
- **THEN** `message_inbox.raw_payload` and `message_inbox.normalized_text` SHALL NOT contain the code
- **AND** they SHALL contain a placeholder of the form `[auth-code withheld: <provider-domain>]`
- **AND** `raw_payload.control.bearer_scrubbed` SHALL be `true` and `raw_payload.bearer_artifacts` SHALL list `{kind, provider_domain, observed_at}` records
- **AND** scrubbing SHALL NOT change the message's ingestion-policy decision or its goal admission

#### Scenario: Ordinary numbers survive
- **WHEN** a message contains order numbers, dates, or amounts that are not adjacent to code wording and not part of a reset or magic link
- **THEN** the text SHALL be persisted unchanged

#### Scenario: Scrubbing is idempotent
- **WHEN** already-scrubbed text is scrubbed again
- **THEN** no further change SHALL occur

#### Scenario: Detector failure fails closed
- **WHEN** the scrubber raises while processing an envelope
- **THEN** the ingest boundary SHALL persist the message as metadata-only with `payload.raw` null and SHALL NOT persist the unscrubbed text

#### Scenario: Gmail body and metadata tiers are scrubbed
- **WHEN** the Gmail connector builds a Tier 1 or Tier 2 envelope
- **THEN** codes and reset or magic links SHALL be withheld from the subject-derived text, the normalized body, and the decoded `body.data` blobs inside `payload.raw`
