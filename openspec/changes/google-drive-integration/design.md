## Context

Butlers currently integrates with Google via Gmail (connector), Calendar (module), and Contacts (module). All three reuse the shared `google_accounts` registry, companion entity credential storage, and multi-account OAuth flow. Google Drive is the next Google integration — it fits naturally into this existing infrastructure.

The Google Drive API is free for consumer Google accounts (no paid Workspace license required). The `changes.list` endpoint provides an efficient polling mechanism with a server-side `pageToken` that returns only changes since the last poll — ideal for the connector's checkpoint-after-acceptance pattern. File content download via `files.get?alt=media` is also free and quota-regulated (default 20,000 reads/100s per user).

The connector and module serve different purposes: the connector is a passive metadata indexer that feeds file events into the Switchboard for butler awareness (e.g., "the user just modified their tax return"), while the module gives butlers active read/write file tools (e.g., "save this report to the user's Drive").

## Goals / Non-Goals

**Goals:**
- Watch Google Drive file metadata events across all connected Google accounts via a standalone connector process
- Provide MCP tools for butlers to read, write, search, and organize Google Drive files
- Centralize butler-produced outputs under a `butlers/` folder in the user's Drive
- Reuse existing Google OAuth infrastructure (no new OAuth endpoints, just scope addition)
- Follow established connector-base-spec and Module ABC patterns exactly

**Non-Goals:**
- Downloading or indexing file contents in the connector (metadata only — privacy/cost)
- Using Google Drive as a file store for butler internals (too slow/expensive)
- Supporting Dropbox, OneDrive, or other cloud storage providers
- Real-time push notifications via Drive API webhooks (polling with `changes.list` is sufficient for v1)
- Google Workspace-only features (admin SDK, shared drives with domain policies)
- Full-text content indexing (the module's `search_files` uses Drive's built-in search, not local indexing)

## Decisions

### D1: Source channel and provider naming

**Decision:** Use `google_drive` for both `SourceChannel` and `SourceProvider` (underscore, not hyphen). The endpoint identity format is `google_drive:user:<email>`.

**Rationale:** Follows the existing convention where channel names match their primary domain (`email`, `telegram`, `voice`). Google Drive is a distinct channel — file events are not email, not chat, not voice. Using `google_drive` (with underscore) matches Python enum conventions used elsewhere in the codebase (`live-listener` notwithstanding — that's a legacy format).

**Alternative considered:** Reusing `channel=cloud_storage` with `provider=google_drive`. Rejected — over-generalization for a single provider, and the base spec's channel-provider validation requires explicit pairings anyway.

### D2: Connector ingests metadata only — no content download

**Decision:** The connector submits `ingest.v1` envelopes with `ingestion_tier=metadata` and `payload.raw=null`. The `payload.normalized_text` contains a structured summary: filename, MIME type, modified time, parent folder path, sharing status.

**Rationale:** File content download is expensive (quota, bandwidth, storage) and raises privacy concerns. The connector's purpose is awareness — "the user's tax return was modified" — not content extraction. Butlers that need content use the `google_drive` module's `read_file` tool on-demand.

**Alternative considered:** Tier 1 (full payload) for small text files. Rejected — complicates the connector with size/type filtering, and content is available on-demand via the module anyway.

### D3: Polling via changes.list with pageToken checkpoint

**Decision:** Use the Drive `changes.list` API with a persisted `pageToken` as the checkpoint cursor. Poll at `GDRIVE_POLL_INTERVAL_S` (default 300 seconds / 5 minutes).

**Rationale:** `changes.list` is the Drive API's designed mechanism for detecting modifications. The `pageToken` is an opaque server-side cursor — resuming from a persisted token yields only changes since the last poll. This maps directly to the connector-base-spec's checkpoint-after-acceptance pattern.

The default 5-minute polling interval is conservative because: (a) Drive file changes are less time-sensitive than email, (b) the free API quota is 20,000 requests per 100 seconds per user, but polling less frequently is polite, (c) the `changes.list` call returns batches so high-frequency changes are still captured.

**Alternative considered:** Drive API push notifications (webhooks via `changes.watch`). Rejected for v1 — requires a publicly-routable HTTPS endpoint, certificate management, and webhook renewal every 24 hours. Polling is simpler and sufficient. Can be added later as an optimization (similar to Gmail's Pub/Sub mode).

### D4: Module auto-creates `butlers/` folder hierarchy

**Decision:** On first use of any write tool, the module auto-creates a `butlers/` folder at the Drive root, and a `butlers/{butler_name}/` subfolder for the calling butler. Folder IDs are cached in the state store after creation.

**Rationale:** Centralizing butler output in a known location makes it discoverable by the user. Per-butler subfolders prevent naming collisions when multiple butlers write files.

**Implementation:** `_ensure_butler_folder(butler_name)` checks the state store for a cached folder ID, verifies it still exists via `files.get`, creates if missing, and caches the result. The `butlers/` root folder is created first, then the butler subfolder inside it.

**Alternative considered:** Configurable root folder name. Deferred — `butlers/` is a sensible default and can be made configurable later without breaking changes.

### D5: Module MCP tools surface area

**Decision:** Seven MCP tools covering the essential file operations:

| Tool | Purpose |
|------|---------|
| `drive_list_files(folder_id?, query?)` | List/search files in a folder or globally |
| `drive_get_file_metadata(file_id)` | Get detailed metadata without downloading |
| `drive_read_file(file_id)` | Download and return file content (text files, small docs) |
| `drive_write_file(folder_id?, name, content, mime_type?)` | Create/upload a file |
| `drive_create_folder(parent_id?, name)` | Create a folder |
| `drive_move_file(file_id, new_parent_id)` | Move a file between folders |
| `drive_search_files(query)` | Full-text search via Drive API |

**Rationale:** This covers the CRUD operations butlers actually need. Read and write tools enable butlers to consume user documents and produce outputs. Search leverages Drive's built-in full-text search (no local indexing needed). Folder operations support organization.

**Read file constraints:** `drive_read_file` has a size limit (default 10 MB) and only returns text-representable content. Binary files return metadata with a download link. Google Docs/Sheets/Slides are exported as plain text or CSV via the `files.export` endpoint.

**Naming convention:** Tools prefixed with `drive_` to avoid collision with other file-related tools. Consistent with `calendar_`, `ha_`, etc.

### D6: Multi-account follows established Gmail connector pattern

**Decision:** The connector discovers accounts from `shared.google_accounts` where `status = 'active'` and `drive` or `drive.readonly` is in `granted_scopes`. It spawns an independent poll loop per account. The module resolves credentials for the configured account (or primary if not specified).

**Rationale:** Identical to the Gmail connector's multi-account pattern (D8 in multi-account-google change). Reuses `GDriveAccountLoop` pattern per-account with independent cursors, error isolation, and dynamic discovery.

### D7: Connector event types

**Decision:** The connector maps Drive change types to a normalized event type in the `ingest.v1` envelope's `normalized_text`:

- File created → `"file_created: <filename> (<mime_type>) in <folder_path>"`
- File modified → `"file_modified: <filename> (<mime_type>) at <modified_time>"`
- File trashed → `"file_trashed: <filename>"`
- File renamed → `"file_renamed: <old_name> → <new_name>"`
- File moved → `"file_moved: <filename> from <old_folder> to <new_folder>"`
- Sharing changed → `"sharing_changed: <filename> (<sharing_status>)"`

The `changes.list` API returns a flat list of changed file IDs with their current state. Detecting renames/moves requires comparing current metadata against the previously-seen state cached in the connector's local state. For v1, the connector tracks a lightweight file metadata cache (file_id → name, parent_id, mime_type) to detect these deltas.

### D8: Database schema for the module

**Decision:** Two module tables:

```sql
-- Cached folder IDs for butler output hierarchy
CREATE TABLE google_drive_butler_folders (
    butler_name TEXT NOT NULL,
    account_email TEXT NOT NULL,
    folder_id TEXT NOT NULL,
    folder_path TEXT NOT NULL,  -- e.g., "butlers/general"
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (butler_name, account_email)
);
```

The connector does NOT need its own schema tables beyond what the `connectors` schema already provides (cursor_store, filtered_events). The per-file metadata cache for detecting renames/moves is stored in the state store as a JSONB blob (keyed by endpoint identity), not a separate table — it is an internal optimization, not user-facing data.

## Risks / Trade-offs

**[Risk] Drive API quota exhaustion** → The free quota is generous (20,000 requests per 100 seconds per user) but a misconfigured poll interval or large initial sync could consume it. **Mitigation:** Conservative default poll interval (300s). Rate-limit retry with exponential backoff on 403/429 responses. Per-account quota tracking via Prometheus metrics.

**[Risk] Large file downloads via module** → A butler calling `drive_read_file` on a 1 GB video file would be expensive and useless. **Mitigation:** Size cap (10 MB default, configurable). MIME type filtering — binary files return metadata only. Google Docs/Sheets/Slides use `files.export` which naturally produces smaller text representations.

**[Risk] Stale file metadata cache for rename/move detection** → The connector's local metadata cache may drift if changes occur while the connector is down. **Mitigation:** On startup, the connector does a full metadata sweep of changed files since the last checkpoint. Worst case, a missed rename/move event is logged as a generic "file_modified" instead — not a correctness problem.

**[Risk] `butlers/` folder deleted by user** → If the user manually deletes the `butlers/` folder, subsequent writes would fail. **Mitigation:** The `_ensure_butler_folder` function verifies folder existence before every write and re-creates if missing. Folder ID is re-cached after recreation.

**[Risk] Google Docs export format ambiguity** → `drive_read_file` on a Google Doc could produce different formats. **Mitigation:** Default export format is `text/plain` for Docs, `text/csv` for Sheets, `text/plain` for Slides. Configurable via optional `export_format` parameter.

**[Trade-off] Metadata-only connector vs. content indexing** → We sacrifice content awareness for privacy and cost. A butler won't know *what* a document says until it's explicitly asked to read it via the module. Accepted — this is the right default for a user-federated system. Content can be fetched on-demand.

**[Trade-off] Polling vs. push notifications** → Polling at 5-minute intervals means up to 5 minutes of latency for file change awareness. Accepted for v1 — file changes are less latency-sensitive than messages. Push notifications (webhooks) can be added later if demand warrants.

## Migration Plan

1. **Add `google_drive` to SourceChannel/SourceProvider enums** — Update ingest envelope validation. No data migration needed.
2. **Create module Alembic migration** — `google_drive_butler_folders` table. No existing data affected.
3. **Deploy connector** — New container/service in docker-compose. No impact on existing connectors.
4. **Enable module in butlers** — Add `[modules.google_drive]` to target butler.toml files. No breaking changes — module is opt-in.
5. **Scope addition** — Users with existing Google accounts need to re-authorize with `drive` scope via dashboard OAuth flow (`force_consent=true`). Accounts without `drive` scope are simply skipped by the connector and module (fail-fast with actionable message).

**Rollback:** Remove module config from butler.toml, stop connector container, drop `google_drive_butler_folders` table. Enum additions to SourceChannel/SourceProvider are backward-compatible and don't need rollback.

## Open Questions

1. **Shared Drives support**: Should the connector index Shared Drives (formerly Team Drives) in addition to My Drive? The `changes.list` API supports `includeItemsFromAllDrives=true` but requires `supportsAllDrives=true` on all calls. Recommendation: defer to v2 — focus on My Drive for v1.

2. **File content in connector for specific types**: Should the connector eagerly fetch content for very small text files (< 10 KB)? This would provide immediate awareness without a module round-trip. Recommendation: no for v1 — keep the metadata-only contract clean.

3. **Google Drive activity notifications**: Should file events trigger user notifications via the Switchboard's notify mechanism? Recommendation: yes for specific event types (shared with user, large batch modifications) — but this is routing/classification policy, not connector/module scope.
