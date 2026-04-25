# Gmail Connector — S3 Blob Storage Delta

## MODIFIED Requirements

### Requirement: Attachment Handling
The connector implements metadata-first lazy fetching with per-MIME-type size limits and fetch mode policies. Fetched attachments are stored in the S3-compatible blob store; blob refs use the `s3://` scheme.

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

#### Scenario: Eager fetch — calendar attachments stored in S3
- **WHEN** a `text/calendar` attachment is within the 1 MB limit
- **THEN** it is downloaded immediately at ingest time and stored in the S3-compatible BlobStore
- **AND** the `blob_ref` column SHALL contain an `s3://` URI
- **AND** `.ics` attachments bypass LLM routing classification and route directly to the calendar module

#### Scenario: [TARGET-STATE] Attachment reference persistence
- **WHEN** attachment metadata is collected at ingest time
- **THEN** a row is written to `switchboard.attachment_refs` with `message_id`, `attachment_id`, `filename`, `media_type`, `size_bytes`, `fetched` (boolean), `blob_ref` (nullable, `s3://` scheme when populated)

#### Scenario: Attachment metrics
- **WHEN** attachments are processed
- **THEN** counters track: `connector_attachment_fetched_eager_total`, `connector_attachment_fetched_lazy_total`, `connector_attachment_skipped_oversized_total`, `connector_attachment_type_distribution_total`
