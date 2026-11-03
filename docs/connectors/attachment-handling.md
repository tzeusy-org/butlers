# Attachment Handling
> **Purpose:** Specify how the Gmail connector handles email attachments with per-type size policies, lazy fetching, and calendar file routing.
> **Audience:** Contributors.
> **Prerequisites:** [Connector Interface](overview.md), [Gmail Connector](gmail.md), [Blob Storage](../data_and_storage/blob-storage.md).

## Overview

The attachment handling specification expands Gmail connector capabilities beyond the original small image/PDF allowlist. It introduces per-MIME-type size limits, a metadata-first lazy fetching model that keeps ingest latency bounded, eager handling for calendar `.ics` files, and a future extension point for structured HTML receipt extraction. This replaces the former flat `SUPPORTED_ATTACHMENT_TYPES` frozenset with a richer `ATTACHMENT_POLICY` map.

## Supported MIME Types

The connector enforces both per-MIME-type size limits and a global hard ceiling of **25 MB** (Gmail's attachment maximum).

| Category | MIME Types | Per-file Limit | Fetch Mode |
|---|---|---|---|
| Images | `image/jpeg`, `image/png`, `image/gif`, `image/webp` | 5 MB | lazy |
| PDF | `application/pdf` | 15 MB | lazy |
| Spreadsheets | `.xlsx`, `.xls`, `text/csv` | 10 MB | lazy |
| Documents | `.docx`, `message/rfc822` | 10 MB | lazy |
| Calendar | `text/calendar` | 1 MB | eager |

Rules:
- Files above the category limit are skipped and recorded as oversized.
- Files at or under the category limit but above 25 MB are skipped by the global cap.
- Unsupported MIME types are never fetched or stored; metadata may be logged.

The policy is expressed as `ATTACHMENT_POLICY` in `src/butlers/connectors/gmail.py`, mapping each MIME type to its `max_size_bytes` and `fetch_mode`. `SUPPORTED_ATTACHMENT_TYPES` is derived as `frozenset(ATTACHMENT_POLICY.keys())`. `_extract_attachments()` uses the allowlist for MIME eligibility; `_process_attachments()` enforces per-type and global size limits.

## Lazy Fetching Model

At ingest time, the connector writes metadata-only reference rows to `switchboard.attachment_refs` (keyed by `message_id, attachment_id`) rather than downloading payload bytes. This keeps per-email handling under 100ms. The table tracks `filename`, `media_type`, `size_bytes`, `fetched` (boolean), and `blob_ref` (nullable).

When the content is needed, Switchboard's `attachment_materialize` requests an on-demand fetch (see [Fetching lazy refs](#fetching-lazy-refs)): the connector downloads from the Gmail API, stores the bytes in BlobStore and updates `fetched=true` and `blob_ref`. Repeated requests return the existing `blob_ref` (idempotent), and a replayed ingest of the same message keeps it.

The connector reaches the table as `switchboard.attachment_refs` (its pool has no search path) and stores blobs under the connector prefix `{bucket}/connectors/` with the `BLOB_S3_*` credentials it resolves at start (`src/butlers/connectors/blob_store.py`, shared with the Telegram connector). Without them, eager and on-demand fetches are skipped.

## Calendar `.ics` Direct Routing

`text/calendar` attachments bypass LLM routing via a seeded global ingestion rule (`mime_type: text/calendar -> route_to:relationship`, in `switchboard.ingestion_rules`). They are always eagerly fetched (subject to 1 MB limit), parsed for `VEVENT`/`VTODO` entities, and forwarded to the calendar module. Parse failures produce structured errors rather than silent drops.

## Envelope Contract

The ingest payload `attachments[]` array carries `media_type`, `filename`, `size_bytes`, `message_id`, `attachment_id`, `fetched` (boolean), and `storage_ref` (nullable). For an eager, in-cap `storage_ref`: an `image/*` attachment is retrieved with `attachment_view(storage_ref)`, which returns a real MCP image content block (the correct channel for vision input, never a JSON payload) and refuses (a typed `status: "refused"` result) anything over the vision size cap. A non-image attachment is retrieved with `get_attachment(storage_ref)`, which embeds the blob as base64 JSON only up to a 64KB inline cap — see `src/butlers/tools/attachments.py`. Both tools, like `attachment_read`, serve only refs under the calling butler's RFC 0016 prefix `{bucket}/{butler}/` or the connector prefix `{bucket}/connectors/`; any other ref returns an `attachment_forbidden` refusal without being fetched. A lazy attachment carries `source_message_id` and `source_attachment_id`; the routed prompt lists it as `status=not_fetched` (distinct from `status=unavailable`, a fetch that failed) and says it cannot be retrieved in that session, because `attachment_materialize` is dark until the RFC 0038 cutover. One builder, `build_attachments_block` in `src/butlers/routing_guidance.py`, writes the ATTACHMENTS section for both the primary and recovery route paths.

### Reading PDFs by page

`attachment_read(storage_ref, page_start=1, page_count=10)` reads a stored PDF of any size up to 20 MB, so documents over the `get_attachment` inline cap are readable. It extracts text locally with pypdfium2 (no external service) and returns at most 20 pages and 16,000 characters a call as a `pages` list of `{page, text}` entries, with `next_page_start` naming the next unread page. A page with no text layer (a scan) returns empty text with `no_text_layer: true`. Extraction runs in a child interpreter (`src/butlers/tools/_pdf_extract.py`, started with `python -I` and an empty environment) capped at 512 MB of address space and 15 seconds. The 16,000-character budget is applied per page through `count_chars()` before any text is copied, and no page is opened once it is spent, so a decompression-bomb page costs at most the child, which is refused as `extraction_limit_exceeded`. The child sets its own rlimits before importing PDFium. At most two children run at once per daemon; a caller queues up to 30 seconds for a slot and is otherwise refused as `extraction_busy`, so a burst of hostile reads cannot multiply the per-child cap past the pod memory limit. A non-PDF (by extension or by bytes), an encrypted or corrupt PDF, an oversized blob, a page range past the end, or a ref outside `{bucket}/{butler}/` and `{bucket}/connectors/` returns a typed refusal (`status: "refused"`, `reason`: `unsupported_media_type`, `encrypted_pdf`, `corrupt_pdf`, `attachment_too_large`, `invalid_page_range`, `attachment_forbidden`, `extraction_limit_exceeded`, `extraction_busy`, ...).

Page text is untrusted (`content_trust: "untrusted"`): it travels only as JSON string data, and the D4 catalog declaration marks the output untrusted so a goal that reads it becomes exposed under RFC 0038 D6. The tool is a dark build: the daemon catalogs it in `_finalize_tool_catalog`, then disables it on FastMCP (`GOAL_RUNTIME_DARK_TOOLS` in `src/butlers/core_tools/_media.py`), so today's classify-and-route sessions cannot list or call it until the goal-runtime cutover presents it.

### Viewing scanned pages

`attachment_page_view(storage_ref, page=1)` renders one PDF page to a PNG for a page `attachment_read` marked `no_text_layer`. It runs in the same child (`render` mode of `src/butlers/tools/_pdf_extract.py`) under the same 512 MB and 15-second limits and two shared slots, so text reads and renders together never exceed two children. The scale is fixed from the page size before drawing: at most 200 dpi, 1568 px on the long edge and 1,150,000 px in area. The parent does not trust the child's header on its own: it reads the width and height from the PNG's IHDR chunk and rejects, as `extraction_limit_exceeded`, any output over 4 MB, without a PNG signature, whose IHDR dimensions differ from the header, are below 1 px or fall outside those bounds, or that names a page other than the one requested. The result is a metadata dict (`content_trust: "untrusted"`, page, page count, width, height) followed by an MCP image content block. Scope, size and media refusals match `attachment_read`. The tool is dark (`GOAL_RUNTIME_DARK_TOOLS`) and, because its output is vision input, presenting it also waits on a model with a passing RFC 0036 exact-path vision proof (bu-s1u4br).

### Fetching lazy refs

`attachment_materialize(message_id, attachment_id)` is a Switchboard tool. Only the Gmail connector holds Gmail credentials, so the request travels through the `attachment_refs` row (columns added by `sw_055`):

1. The tool first checks that the ref belongs to the request the calling session is handling: the pair must be the `source_message_id` and `source_attachment_id` of an attachment in the routing context the daemon recorded for the runtime session bound to the call (`get_current_runtime_session_routing_context`). Anything else, including a call with no bound session or a session-id locator without an invocation binding, is refused as `attachment_not_in_request` before the table is read, so no session can fetch another message's or account's attachment by id and the refusal reveals nothing about which refs exist.
2. The tool marks the row requested (`fetch_requested_at`) and polls it for up to 20 seconds.
3. Each account runtime of the connector polls every 2 seconds and claims up to two requested, unfetched rows whose `endpoint_identity` is its own (or NULL, for rows written before `sw_055`) with `FOR UPDATE SKIP LOCKED`, stamping `fetch_claimed_at`. A claim older than 120 seconds is reclaimed.
4. A row whose declared `size_bytes` (the decoded size Gmail reported) is over the 25 MB global cap gets `fetch_error = 'too_large'` with no download, so the larger base64 response is never buffered. Otherwise the runtime calls `fetch_attachment`, which still refuses downloaded bytes over the cap, since a declared size can be wrong. A failure sets `fetch_error = 'fetch_failed'`, including a failure to record the stored blob on the row. The runtime deletes the blob only when the row definitely does not name it (no unfetched row matched, or a re-read shows another ref); when the outcome is unknown and the re-read fails it keeps the blob, since an orphan wastes storage but a dangling ref loses the file. A retry downloads again.
5. If a batch is interrupted (an exception or the runtime stopping), the runtime releases the claims it has not finished, so the next poll retries them without waiting out the 120-second window. Both the release and `fetch_error` match the runtime's own `fetch_claimed_at`, so a claim another runtime has reclaimed is never released or failed.

The tool returns `{"status": "ready", "storage_ref", "media_type", "filename", "size_bytes", "content_trust": "untrusted"}`, a refusal (`attachment_not_in_request`, `attachment_not_found`, `attachment_too_large` with the declared `size_bytes`, `fetch_failed`, `invalid_reference`), or `{"status": "pending", "retry_after_s"}` when the connector has not answered in time. Calling again after `fetch_failed` retries. A fetched row returns its existing ref with no download. The ref it returns is readable with `attachment_read`, `attachment_view` and `get_attachment`.

The tool declares RFC 0038 D4 metadata (effect class read, untrusted sensitive output, no recipient parameters; `message_id` and `attachment_id` are not D7 selection-bound, because the binding is to the routed request, not to an owner selection record) and is dark (`GOAL_RUNTIME_DARK_TOOLS`): Switchboard catalogs it and does not list it to classify-and-route sessions before the cutover.

## Metrics

Four attachment-specific Prometheus counters are defined in `src/butlers/connectors/metrics.py`: `connector_attachment_fetched_eager_total`, `connector_attachment_fetched_lazy_total`, `connector_attachment_skipped_oversized_total`, and `connector_attachment_type_distribution_total`. All include `connector_type`, `endpoint_identity`, and `media_type` labels. High-cardinality IDs are excluded.

## Migration Plan

Implementation is phased: (A) schema -- add `attachment_refs` table and indexes; (B) policy constants; (C) lazy fetch behavior for non-calendar attachments; (D) `.ics` ingestion rule and attachment metrics. Eager fetch can be re-enabled behind a feature flag if lazy fetch regresses.

## Structured HTML Extraction (Future)

An extension point is defined for sender-specific HTML receipt extraction (e.g., Amazon, Uber) producing `structured_payload` (JSONB) alongside `normalized_text`. Concrete extractors are separate follow-up work.

## Verification

To confirm the attachment handling policy is enforced as specified:

```bash
# 1. ATTACHMENT_POLICY constants match the documented per-MIME size limits
python3 - <<'EOF'
from butlers.connectors.gmail import ATTACHMENT_POLICY
for mime, policy in sorted(ATTACHMENT_POLICY.items()):
    print(f"{mime}: max={policy['max_size_bytes']//1024//1024}MB fetch={policy['fetch_mode']}")
EOF
# Expected: jpeg/png/gif/webp at 5MB lazy, pdf at 15MB lazy,
#           xlsx/xls/csv at 10MB lazy, docx/rfc822 at 10MB lazy,
#           text/calendar at 1MB eager

# 2. Attachment metrics emit after emails with attachments are processed
curl -s "http://localhost:9090/api/v1/query?query=connector_attachment_type_distribution_total" \
  | python3 -m json.tool | grep -E "media_type|value"
# Expected: non-zero counts for MIME types seen in processed emails

# 3. Oversized attachments are skipped and counted (not silently dropped)
curl -s "http://localhost:9090/api/v1/query?query=connector_attachment_skipped_oversized_total" \
  | python3 -m json.tool | grep value
# Expected: counter exists; increments when attachments exceed per-type size limit

# 4. Calendar .ics files are eagerly fetched (fetched=true in ingest payload)
# After receiving a calendar invite email, check the attachment entry:
psql -h localhost -U butlers -d butlers -c \
  "SELECT payload->'attachments' AS attachments FROM switchboard.ingestion_events
   WHERE source_channel='email'
   AND payload->'attachments' @> '[{\"media_type\": \"text/calendar\"}]'
   ORDER BY received_at DESC LIMIT 1;" 2>/dev/null | python3 -m json.tool
# Expected: attachment entry with fetched=true and non-null storage_ref

# 5. Lazy fetch attachments have fetched=false until on-demand retrieval
psql -h localhost -U butlers -d butlers -c \
  "SELECT payload->'attachments'->0->'fetched' AS fetched
   FROM switchboard.ingestion_events
   WHERE source_channel='email'
   AND jsonb_array_length(payload->'attachments') > 0
   ORDER BY received_at DESC LIMIT 3;" 2>/dev/null
# Expected: false for non-calendar attachments; they are metadata-only until fetched on demand
```

## Implementation Notes

- `IngestPayloadV1.normalized_text` may be `""` (a captionless photo or document); never synthesize a
  placeholder such as `"[Photo]"` for vision media (`telegram_bot.py::_extract_normalized_text`).
- Per-media-id idempotency checks a process-local cache, then `switchboard.media_refs` keyed by
  `(connector_type, endpoint_identity, external_message_id, media_id)`; Gmail's `attachment_refs`
  has no connector namespace, so do not reuse it.
- `ModelFeature.VISION` stays `UNKNOWN` (fails closed) until a `public.model_catalog` row declares
  `capabilities: {"vision": true}`; `Spawner._run` requires it for any `image/*` attachment.
- `tests/core/test_attachment_view.py::test_no_other_tool_source_embeds_a_data_base64_field`
  guards the inline base64 cap repo-wide.
- `tests/tools/test_attachments.py::test_attachment_read_pages_and_refusals` pins the paged read
  bounds and refusals; `test_attachment_page_view_renders_bounded_png_or_refuses` pins the render
  bounds; `tests/contracts/test_tool_presentation_inventory.py` pins that `attachment_read` and
  `attachment_page_view` are cataloged but not listed.
- `tests/tools/test_attachment_materialize.py` pins the routed-request binding and that an unbound
  ref is refused without a query.
- `roster/switchboard/tests/test_attachment_materialize_integration.py` pins the request, claim,
  fetch, idempotence, replay and refusal paths, a failed or late blob record, an interrupted
  batch and a reclaimed claim against a real database.

## Related Pages

- [Gmail Connector](gmail.md) -- Gmail-specific ingestion details
- [Connector Interface](overview.md) -- Shared connector contract
- [Blob Storage](../data_and_storage/blob-storage.md) -- Where attachment bytes are stored
- [Connector Metrics](metrics.md) -- Standard Prometheus instrumentation
