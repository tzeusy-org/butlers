## MODIFIED Requirements

### Requirement: Deduplication Strategy
The Switchboard SHALL compute a stable deduplication key for each ingest submission using a priority-based strategy. Concurrency control SHALL be enforced to prevent race conditions on concurrent submissions with the same key.

#### Scenario: Priority 1 — Explicit idempotency key
- **WHEN** `control.idempotency_key` is provided
- **THEN** the dedupe key is `"idem:{channel}:{endpoint_identity}:{idempotency_key}"`

#### Scenario: Priority 2 — External event ID
- **WHEN** no explicit idempotency key is provided and `event.external_event_id` is non-placeholder (not `"placeholder"`, `"unknown"`, `"none"`, or empty)
- **THEN** the dedupe key is `"event:{channel}:{provider}:{endpoint_identity}:{external_event_id}"`

#### Scenario: Priority 3 — Content hash fallback
- **WHEN** neither explicit key nor usable event ID is available
- **THEN** the dedupe key is `"hash:{channel}:{endpoint_identity}:{sender}:{hour_bucket}:{content_hash[:16]}"` where `content_hash` is SHA256 of `bearer_scrubbed_normalized_text:sender_identity` and `hour_bucket` is the hourly time window (`YYYYMMDDHH`)
- **AND** text containing no bearer material hashes exactly as its `normalized_text`

#### Scenario: Keys never derive from withheld bearer material
- **WHEN** the submitted text contains bearer material (one-time codes, reset or magic links) that ingest withholds from the stored text
- **THEN** every stored dedupe or content-hash key (`request_context.dedupe_key`, `request_context.content_hash_key`, `public.ingestion_events.dedupe_key`) is derived from the scrubbed text and never from the raw text
- **AND** two submissions differing only in the withheld material derive the same content-hash key
- **AND** the Switchboard MUST NOT store any value from which the withheld material can be recovered by hashing candidate values

#### Scenario: Secondary content-hash lookup skips withheld bearer text
- **WHEN** a submission has a Priority 1 or 2 key and bearer material was withheld from any centrally scrubbed content subtree (including `payload.raw` even when `normalized_text` is benign), whether by ingest or by a connector that scrubbed before submitting (the envelope's `control.bearer_scrubbed` flag, or a canonical withheld-artifact placeholder in the text)
- **THEN** the Switchboard omits `request_context.content_hash_key` and skips the secondary cross-connector content-hash lookup, inside and outside the advisory lock
- **AND** primary-key deduplication still applies, so a second code with its own event ID is not dropped as a duplicate

#### Scenario: Scrub failure fails closed
- **WHEN** scrubbing for key derivation raises
- **THEN** the content hash is derived from a constant withheld-text marker and `content_hash_key` is omitted
- **AND** no key is derived from the raw text

#### Scenario: Pipeline ingress dedupe key
- **WHEN** the in-process pipeline records an ingress without a stable event id or idempotency key
- **THEN** its payload-hash dedupe key is derived from the bearer-scrubbed message text, scrubbed before the key is built

#### Scenario: Advisory lock serialization
- **WHEN** the Switchboard processes an ingest submission
- **THEN** it acquires `pg_advisory_xact_lock(hashtext(dedupe_key))` within a transaction
- **AND** inside the lock: re-checks for an existing record with the same dedupe key, and either returns the existing `request_id` with `duplicate=true` or inserts a new row into both `message_inbox` and `public.ingestion_events` atomically within the same transaction

#### Scenario: Ingest accepted response
- **WHEN** the Switchboard accepts an ingest submission
- **THEN** it returns `IngestAcceptedResponse` with: `request_id` (UUID7, canonical reference), `status` (`"accepted"`), `duplicate` (bool), `triage_decision` (string or None), `triage_target` (butler name or None)
