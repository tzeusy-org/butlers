# Attachment Handling
> **Purpose:** Specify how the Gmail connector handles email attachments with per-type size policies, lazy fetching, and calendar file routing.
> **Audience:** Contributors.
> **Prerequisites:** [Connector Interface](interface.md), [Gmail Connector](gmail.md), [Blob Storage](../data_and_storage/blob-storage.md).

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

When a butler needs the actual content, it triggers an on-demand fetch: download from the Gmail API, store in BlobStore, update `fetched=true` and `blob_ref`. Repeated requests return the existing `blob_ref` (idempotent).

## Calendar `.ics` Direct Routing

`text/calendar` attachments bypass LLM routing via a deterministic triage rule (`mime_type: text/calendar -> route_to: calendar`). They are always eagerly fetched (subject to 1 MB limit), parsed for `VEVENT`/`VTODO` entities, and forwarded to the calendar module. Parse failures produce structured errors rather than silent drops.

## Envelope Contract

The ingest payload `attachments[]` array carries `media_type`, `filename`, `size_bytes`, `message_id`, `attachment_id`, `fetched` (boolean), and `storage_ref` (nullable). Existing consumers using `storage_ref` continue to work for eager paths. Lazy paths expose enough identity to trigger a fetch and then call `get_attachment(storage_ref)`.

## Metrics

Four attachment-specific Prometheus counters are defined in `src/butlers/connectors/metrics.py`: `connector_attachment_fetched_eager_total`, `connector_attachment_fetched_lazy_total`, `connector_attachment_skipped_oversized_total`, and `connector_attachment_type_distribution_total`. All include `connector_type`, `endpoint_identity`, and `media_type` labels. High-cardinality IDs are excluded.

## Migration Plan

Implementation is phased: (A) schema -- add `attachment_refs` table and indexes; (B) policy constants; (C) lazy fetch behavior for non-calendar attachments; (D) `.ics` triage rule and attachment metrics. Eager fetch can be re-enabled behind a feature flag if lazy fetch regresses.

## Structured HTML Extraction (Future)

An extension point is defined for sender-specific HTML receipt extraction (e.g., Amazon, Uber) producing `structured_payload` (JSONB) alongside `normalized_text`. Concrete extractors are separate follow-up work.

## Related Pages

- [Gmail Connector](gmail.md) -- Gmail-specific ingestion details
- [Connector Interface](interface.md) -- Shared connector contract
- [Blob Storage](../data_and_storage/blob-storage.md) -- Where attachment bytes are stored
- [Connector Metrics](metrics.md) -- Standard Prometheus instrumentation
