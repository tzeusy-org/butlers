# Gmail Connector Attachment Handling

Status: Draft (normative target-state spec)  
Issue: `butlers-0bz3.7`  
Depends on: `docs/connectors/interface.md`, `docs/connectors/gmail.md`, `docs/modules/calendar.md`

## 1. Purpose
This specification expands Gmail attachment handling so the connector can ingest
the real attachment mix from personal email while keeping ingestion latency
bounded.

Goals:
- Expand supported attachment MIME types beyond image + small PDF only.
- Replace eager-download-for-everything with metadata-first lazy fetching.
- Preserve direct, deterministic handling for calendar `.ics` files.
- Define a future extension point for structured HTML receipt extraction.

Non-goals:
- Building provider-agnostic document parsing in this phase.
- Implementing sender-specific HTML extractors in this phase.
- Supporting archives/binaries outside the explicit MIME allowlist in this spec.

## 2. Current State and Gap
Current Gmail connector behavior (`src/butlers/connectors/gmail.py`):
- `SUPPORTED_ATTACHMENT_TYPES` is a small `frozenset` (`jpeg/png/gif/webp/pdf`).
- A single global `MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024` applies.
- Supported attachments are downloaded and blob-stored eagerly during ingest.

Gap:
- Personal email routinely includes higher-value files outside this allowlist:
  spreadsheets, DOCX, forwarded `.eml`, large PDFs, and calendar `.ics`.
- Eager download of all attachments inflates ingest latency and storage for data
  no butler ever accesses.

## 3. Attachment Type and Size Policy
The Gmail connector MUST enforce both:
1. Per-MIME size limit.
2. Global hard ceiling of `25 MB` (Gmail attachment maximum).

### 3.1 Supported MIME Types (Target)

| Category | MIME type(s) | Per-file limit | Fetch mode |
| --- | --- | --- | --- |
| Images | `image/jpeg`, `image/png`, `image/gif`, `image/webp` | 5 MB | lazy |
| PDF | `application/pdf` | 15 MB | lazy |
| Spreadsheets | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, `application/vnd.ms-excel`, `text/csv` | 10 MB | lazy |
| Documents | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `message/rfc822` | 10 MB | lazy |
| Calendar | `text/calendar` | 1 MB | eager |

Rules:
- Files above category limit MUST be skipped and recorded as oversized.
- Files at/under category limit but above `25 MB` MUST be skipped by global cap.
- Unsupported MIME types MUST NOT be fetched or stored; metadata MAY be logged.

## 4. `SUPPORTED_ATTACHMENT_TYPES` Contract Update
`src/butlers/connectors/gmail.py` MUST move from a flat allowlist constant to a
policy map that can express per-type limits and fetch mode.

Required definition pattern:

```python
ATTACHMENT_POLICY = {
    "application/pdf": {"max_size_bytes": 15 * 1024 * 1024, "fetch_mode": "lazy"},
    "text/calendar": {"max_size_bytes": 1 * 1024 * 1024, "fetch_mode": "eager"},
    # ...all supported MIME types from section 3.1...
}

SUPPORTED_ATTACHMENT_TYPES = frozenset(ATTACHMENT_POLICY.keys())
GLOBAL_MAX_ATTACHMENT_SIZE_BYTES = 25 * 1024 * 1024
```

Normative requirement:
- `_extract_attachments()` MUST keep using `SUPPORTED_ATTACHMENT_TYPES` for MIME
  eligibility.
- `_process_attachments()` MUST apply `ATTACHMENT_POLICY[media_type]` size and
  fetch behavior, plus global cap.

## 5. Lazy Attachment Fetching Model

### 5.1 Ingest-Time Behavior
At ingest time, the Gmail connector MUST:
1. Parse MIME parts and collect attachment metadata.
2. For each supported, in-limit non-calendar attachment:
   - Write metadata reference row.
   - Do not download payload bytes.
3. For `text/calendar` in-limit attachments:
   - Download immediately.
   - Store in BlobStore.
   - Mark as fetched.

Target ingest latency objective:
- Metadata-only path SHOULD keep per-email attachment handling under `100 ms`
  excluding `.ics` eager fetch.

### 5.2 Attachment Reference Persistence
Switchboard schema MUST include `attachment_refs`:

```sql
CREATE TABLE switchboard.attachment_refs (
    message_id TEXT NOT NULL,
    attachment_id TEXT NOT NULL,
    filename TEXT NULL,
    media_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    fetched BOOLEAN NOT NULL DEFAULT FALSE,
    blob_ref TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (message_id, attachment_id)
);
```

Recommended indexes:
- `(fetched, created_at DESC)` for lazy-fetch queueing/inspection.
- `(media_type, created_at DESC)` for analytics and policy audits.

### 5.3 On-Demand Fetch
When a butler requires content for an unfetched attachment:
1. Resolve `attachment_refs` row by `(message_id, attachment_id)`.
2. Download bytes from Gmail attachment API.
3. Store in BlobStore and persist `blob_ref`, `fetched=true`.
4. Return `blob_ref` for existing `get_attachment(storage_ref)` workflow.

Idempotency requirement:
- Repeated lazy fetch requests for the same attachment MUST return the existing
  `blob_ref` once materialized.

## 6. Calendar `.ics` Direct Routing
`text/calendar` attachments MUST bypass LLM routing classification.

Switchboard triage rule (deterministic pre-classification):

```yaml
type: mime_type
condition: text/calendar
action:
  route_to: calendar
```

Requirements:
- `.ics` attachments are always eagerly fetched (subject to `1 MB` limit).
- Calendar parsing extracts iCalendar entities (`VEVENT`, `VTODO` at minimum).
- The event is forwarded directly to the receiving butler's calendar module
  (or Switchboard calendar module when centrally configured).
- Failure to parse `.ics` MUST be visible as structured error state; it MUST NOT
  silently drop the attachment.

## 7. Envelope and Tooling Surface
The ingest payload attachment array MUST carry metadata for lazy-fetched files so
downstream routing/runtime can request content later.

Minimum attachment metadata fields in `payload.attachments[]`:
- `media_type`
- `filename` (nullable)
- `size_bytes`
- `message_id`
- `attachment_id`
- `fetched`
- `storage_ref` (nullable; populated when eager or after lazy fetch)

Compatibility rule:
- Existing consumers using `storage_ref` MUST continue to work for eager paths.
- Lazy paths MUST expose enough identity to fetch and then call
  `get_attachment(storage_ref)`.

## 8. Structured HTML Extraction Extension Point (Future)
This phase defines extension contracts only.

Target extension interface:
- Sender template registry keyed by sender domain (for example
  `amazon.com`, `uber.com`).
- Extractor returns:
  - `structured_payload` (`JSONB`) with sender-specific normalized fields.
  - `extraction_version` for schema evolution.
  - `confidence` and parse diagnostics.

Placement:
- Structured payload is stored alongside `normalized_text` and raw message data,
  not as a replacement.

Scope guard:
- Implementation of concrete sender extractors is separate follow-up work.

## 9. Metrics Contract
The connector and ingest pipeline MUST emit:
- `attachment_fetched_eager`
- `attachment_fetched_lazy`
- `attachment_skipped_oversized`
- `attachment_type_distribution` (counter by MIME type)

Recommended metric attributes (low cardinality):
- `media_type`
- `fetch_mode` (`eager`/`lazy`)
- `result` (`success`/`error`/`skipped_oversized`)

Metrics MUST NOT include high-cardinality IDs (`message_id`, `attachment_id`,
sender email address).

## 10. Migration Plan

### Phase A - Schema
1. Add migration creating `switchboard.attachment_refs`.
2. Add required indexes.
3. Deploy with no behavior change (table unused is acceptable).

### Phase B - Connector policy constants
1. Introduce `ATTACHMENT_POLICY` + `GLOBAL_MAX_ATTACHMENT_SIZE_BYTES`.
2. Keep `SUPPORTED_ATTACHMENT_TYPES` as derived allowlist keys.
3. Add tests for MIME eligibility and per-type/global size enforcement.

### Phase C - Lazy fetch behavior
1. Switch non-calendar supported attachments to metadata-only writes.
2. Preserve eager fetch for `text/calendar`.
3. Add lazy materialization path and idempotent re-fetch semantics.

### Phase D - Routing + observability
1. Add deterministic `.ics` triage rule before LLM classification.
2. Emit required attachment metrics.
3. Verify dashboard/ops visibility for oversized skips and fetch mix.

### Rollback
- If lazy-fetch path regresses, temporarily re-enable eager fetch for the full
  allowlist behind a feature flag while keeping `attachment_refs` writes active.
- `.ics` direct route remains deterministic and should not roll back to LLM
  classification.

## 11. Acceptance Checklist
- Expanded MIME type support with per-type size limits is defined.
- Lazy attachment fetching model and `attachment_refs` schema are defined.
- `.ics` eager-fetch exception is defined.
- `.ics` direct routing to calendar module is defined.
- Structured HTML extraction extension point is defined.
- Migration phases for `attachment_refs` + runtime cutover are defined.
- `SUPPORTED_ATTACHMENT_TYPES` update contract is defined.
