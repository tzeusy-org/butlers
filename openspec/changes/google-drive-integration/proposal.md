## Why

Users manage significant parts of their digital life through Google Drive — personal documents, shared files, organized folders — but butlers have no visibility into this content and no ability to read, write, or organize files on the user's behalf. Adding Google Drive as both a connector (metadata indexing) and a module (read/write tools) lets butlers catalog the user's document landscape, find files by name or content, and produce outputs directly into the user's Drive. This uses only the free Google Drive API (no paid Workspace APIs) and reuses the existing Google OAuth infrastructure.

## What Changes

- **Google Drive connector**: A new standalone connector process that watches the user's Drive via the `changes.list` API (polling with `pageToken` checkpoint). It ingests file metadata events (created, modified, trashed, renamed, moved, shared) as `ingest.v1` envelopes. It does NOT download file contents — only metadata (filenames, modified times, folder structure, MIME types, sharing status). Multi-account support via `public.google_accounts`. Source channel: `google_drive`, provider: `google_drive`.
- **Google Drive module**: A new butler module (`google_drive`) that provides MCP tools for reading, writing, searching, and organizing files in Google Drive. Auto-creates a `butlers/` folder in the user's Drive for centralized butler output, with per-butler subfolders (`butlers/{butler_name}/`). Butlers can also read/write to arbitrary Drive locations.
- **Ingest envelope extensions**: New `SourceChannel` value `google_drive` and `SourceProvider` value `google_drive` for the `ingest.v1` envelope schema, plus a valid channel-provider pairing.
- **Scope tracking**: Google Drive requires the `drive` scope (or `drive.file` for limited access). Connected accounts must have this scope granted for the connector and module to operate.

**Not in scope**: This is NOT a file store for butlers (Drive is too slow/expensive for that). It is useful insofar as cloud drive utility helps the user — cataloging personal documents, finding files, organizing. No Dropbox or other cloud storage providers.

## Capabilities

### New Capabilities
- `connector-google-drive`: Standalone connector that polls Google Drive `changes.list` API for file metadata events across all connected Google accounts. Follows connector-base-spec patterns: heartbeat, checkpoint persistence, filtered event batch flush, replay queue, source filter gate, Prometheus metrics.
- `module-google-drive`: Butler module providing MCP tools for Google Drive file operations: list, search, read, write, create folders, move files. Auto-creates `butlers/` folder hierarchy. Implements Module ABC with `register_tools`, `migrations`, `on_startup`, `on_shutdown`.

### Modified Capabilities
- `connector-base-spec`: Add `google_drive` to `SourceChannel` and `SourceProvider` enums, plus valid channel-provider pairing `google_drive`/`google_drive`.

## Impact

- **Database**: New migration for `module-google-drive` tables (drive file metadata cache, butler folder registry). Connector uses existing `connectors` schema for filtered events and cursor store.
- **Ingest envelope**: `SourceChannel` and `SourceProvider` enums extended with `google_drive`. Channel-provider validation updated.
- **Google OAuth**: Requires `drive` scope in `granted_scopes` on `public.google_accounts`. Reuses existing OAuth flow — no new OAuth endpoints needed, just scope addition during re-authorization.
- **Connector deployment**: New container/process for the Google Drive connector alongside existing Gmail/Telegram connectors.
- **butler.toml**: New `[modules.google_drive]` config section with optional `account` field (email string, defaults to primary Google account).
- **Docker/Compose**: New service definition for the Google Drive connector process.
- **Dependencies**: `google-api-python-client` and `google-auth` (already present for Gmail/Calendar).
