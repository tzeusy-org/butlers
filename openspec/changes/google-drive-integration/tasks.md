## 1. Ingest Envelope Extensions

- [ ] 1.1 Add `google_drive` to `SourceChannel` enum in the ingest envelope model.
- [ ] 1.2 Add `google_drive` to `SourceProvider` enum in the ingest envelope model.
- [ ] 1.3 Add `google_drive`/`google_drive` to the valid channel-provider pairing validation.
- [ ] 1.4 Update existing ingest envelope tests to cover the new channel-provider pair.

## 2. Google Drive Module — Schema and Config

- [ ] 2.1 Create `GoogleDriveConfig` Pydantic model with fields: `account` (optional str), `max_read_size_bytes` (int, default 10485760), `butler_folder_name` (str, default `"butlers"`). Set `extra="forbid"`.
- [ ] 2.2 Create Alembic migration for `google_drive_butler_folders` table: `(butler_name TEXT, account_email TEXT, folder_id TEXT NOT NULL, folder_path TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (butler_name, account_email))`.
- [ ] 2.3 Register the module in `src/butlers/modules/registry.py` with name `"google_drive"`.

## 3. Google Drive Module — Core Implementation

- [ ] 3.1 Create `src/butlers/modules/google_drive.py` implementing the `Module` ABC with `name="google_drive"`, `dependencies=[]`, `config_schema=GoogleDriveConfig`.
- [ ] 3.2 Implement `on_startup()`: resolve Google credentials via `resolve_google_credentials(store, caller="google_drive", account=config.account)`, validate `drive` scope in `granted_scopes`, create HTTP client for Drive API.
- [ ] 3.3 Implement `on_shutdown()`: close HTTP client.
- [ ] 3.4 Implement OAuth token refresh with `google_accounts.last_token_refresh_at` update, early-expiry margin (60s), and credential redaction in error messages.
- [ ] 3.5 Implement rate-limit retry for Drive API calls: retry on 403/429/503 up to 3 times with exponential backoff (base 1.0s).

## 4. Google Drive Module — Butler Folder Hierarchy

- [ ] 4.1 Implement `_ensure_butler_folder(butler_name)`: check `google_drive_butler_folders` table for cached ID, verify folder existence via `files.get`, create if missing via `files.create` (MIME `application/vnd.google-apps.folder`), cache in DB.
- [ ] 4.2 Implement root `butlers/` folder creation at Drive root on first write.
- [ ] 4.3 Implement per-butler subfolder creation (`butlers/{butler_name}/`).
- [ ] 4.4 Implement folder re-creation when cached folder ID refers to a deleted folder.

## 5. Google Drive Module — MCP Tools

- [ ] 5.1 Implement `drive_list_files(folder_id?, query?)`: call `files.list` with folder parent filter, user query combination, pagination up to 1000 items, truncated flag.
- [ ] 5.2 Implement `drive_get_file_metadata(file_id)`: call `files.get` with detailed fields, return metadata dict or `{"status": "not_found"}`.
- [ ] 5.3 Implement `drive_read_file(file_id)`: size check, MIME type routing (text files via `files.get?alt=media`, Google Docs via `files.export` as `text/plain`, Google Sheets as `text/csv`, Slides as `text/plain`), binary file rejection with metadata response.
- [ ] 5.4 Implement `drive_write_file(folder_id?, name, content, mime_type?)`: auto-ensure butler folder if no folder_id, MIME type inference from extension, `files.create` with media upload, return file_id and web_view_link.
- [ ] 5.5 Implement `drive_create_folder(parent_id?, name)`: default to butler subfolder if no parent_id, create via `files.create` with folder MIME type.
- [ ] 5.6 Implement `drive_move_file(file_id, new_parent_id)`: call `files.update` with `addParents`/`removeParents`, handle not-found.
- [ ] 5.7 Implement `drive_search_files(query, limit?)`: call `files.list` with `fullText contains` query, optional limit, return results or empty list.

## 6. Google Drive Module — Tool Metadata and Registration

- [ ] 6.1 Implement `register_tools()`: register all 7 MCP tools on the butler's FastMCP server.
- [ ] 6.2 Implement `tool_metadata()`: declare `drive_write_file` content as sensitive, `drive_move_file` file_id and new_parent_id as sensitive.
- [ ] 6.3 Implement `migration_revisions()`: return `"google_drive"`.

## 7. Google Drive Module — Tests

- [ ] 7.1 Write tests for `GoogleDriveConfig` validation: valid config, missing fields, extra fields rejected, account defaulting.
- [ ] 7.2 Write tests for `on_startup`: credential resolution, scope validation failure, account not found.
- [ ] 7.3 Write tests for butler folder hierarchy: creation, caching, re-creation after deletion.
- [ ] 7.4 Write tests for `drive_list_files`: folder listing, query filtering, pagination, root default.
- [ ] 7.5 Write tests for `drive_get_file_metadata`: found, not found.
- [ ] 7.6 Write tests for `drive_read_file`: text file, Google Doc export, Google Sheet CSV export, size limit, binary rejection.
- [ ] 7.7 Write tests for `drive_write_file`: default butler folder, explicit folder, MIME inference.
- [ ] 7.8 Write tests for `drive_create_folder`, `drive_move_file`, `drive_search_files`.

## 8. Google Drive Connector — Core Implementation

- [ ] 8.1 Create `src/butlers/connectors/google_drive.py` with `GDriveConnectorManager` class: account discovery from `public.google_accounts`, poll loop lifecycle dict keyed by email.
- [ ] 8.2 Create `GDriveAccountLoop` class: encapsulates per-account state (credentials, pageToken cursor, metadata cache, source filter evaluator), runs as independent asyncio task.
- [ ] 8.3 Implement account discovery at startup: query `public.google_accounts WHERE status = 'active'` filtered to accounts with `drive.readonly` or `drive` in `granted_scopes`. Spawn loop per qualifying account.
- [ ] 8.4 Implement per-account credential resolution via `resolve_google_credentials(store, account=<email>)` with auto-resolved `endpoint_identity = "google_drive:user:<email>"`.
- [ ] 8.5 Implement per-account error isolation: loop-level try/except with independent backoff/retry.

## 9. Google Drive Connector — Polling and Change Processing

- [ ] 9.1 Implement `changes.getStartPageToken` for initial cursor acquisition when no checkpoint exists.
- [ ] 9.2 Implement `changes.list` polling with `pageToken`, `includeRemoved=true`, pagination via `nextPageToken`/`newStartPageToken`.
- [ ] 9.3 Implement `GDriveCursor` model with `page_token` and `last_updated_at`, persisted via `cursor_store`.
- [ ] 9.4 Implement checkpoint-after-acceptance: advance cursor only after successful Switchboard ingest.
- [ ] 9.5 Implement local file metadata cache (file_id -> name, mime_type, parents, shared, modified_time) stored as JSONB in state store.

## 10. Google Drive Connector — Event Normalization

- [ ] 10.1 Implement change type detection by comparing current file state against metadata cache: created, modified, trashed, renamed, moved, sharing_changed, fallback.
- [ ] 10.2 Implement `payload.normalized_text` construction for each event type per the spec format strings.
- [ ] 10.3 Implement `ingest.v1` envelope construction with `ingestion_tier=metadata`, `payload.raw=null`, proper field mapping (channel, provider, endpoint_identity, event IDs, sender, idempotency_key).
- [ ] 10.4 Implement metadata cache update after each processed change (upsert or delete for trashed files).

## 11. Google Drive Connector — Filter, Metrics, Rate Limiting

- [ ] 11.1 Implement `IngestionPolicyEvaluator` integration with `scope = 'connector:google_drive:<endpoint_identity>'`.
- [ ] 11.2 Implement filtered event batch flush obligation per connector-base-spec.
- [ ] 11.3 Implement replay queue drain loop per connector-base-spec.
- [ ] 11.4 Implement standard `ConnectorMetrics` (ingest submissions, source API calls, checkpoint saves, errors, latency).
- [ ] 11.5 Implement `connector_gdrive_event_type_total` counter and `connector_gdrive_metadata_cache_size` gauge.
- [ ] 11.6 Implement Drive API rate-limit handling: honor `Retry-After`, exponential backoff with jitter (base 1s, max 60s), 5 retries.

## 12. Google Drive Connector — Multi-Account and Lifecycle

- [ ] 12.1 Implement dynamic account discovery: periodic re-scan at `GDRIVE_ACCOUNT_RESCAN_INTERVAL_S` (default 300), spawn new loops, stop loops for removed/revoked accounts.
- [ ] 12.2 Implement `connector_reload_accounts` MCP tool and SIGHUP handler for immediate re-scan.
- [ ] 12.3 Implement graceful loop shutdown on account removal: complete in-flight operations, checkpoint, stop.
- [ ] 12.4 Implement aggregated health endpoint: worst-case overall status, per-account health array.
- [ ] 12.5 Implement heartbeat protocol with `connector_type="google_drive"`, per-account heartbeats, `capabilities` dict.

## 13. Google Drive Connector — Configuration and Deployment

- [ ] 13.1 Implement environment variable handling: `SWITCHBOARD_MCP_URL`, `CONNECTOR_PROVIDER=google_drive`, `CONNECTOR_CHANNEL=google_drive`, `GDRIVE_POLL_INTERVAL_S` (300), `CONNECTOR_HEALTH_PORT` (40085), `GDRIVE_ACCOUNT_RESCAN_INTERVAL_S` (300).
- [ ] 13.2 Implement per-account config overrides via `google_accounts.metadata.google_drive` (poll_interval_s).
- [ ] 13.3 Add Docker service definition for the Google Drive connector to `docker-compose.yml`.
- [ ] 13.4 Add connector entrypoint script following the Gmail connector pattern.

## 14. Google Drive Connector — Tests

- [ ] 14.1 Write tests for multi-account discovery: qualifying accounts, missing scopes, degraded startup.
- [ ] 14.2 Write tests for polling and change processing: changes.list parsing, pagination, cursor advancement.
- [ ] 14.3 Write tests for event normalization: each event type detection, fallback, metadata cache updates.
- [ ] 14.4 Write tests for `ingest.v1` envelope construction: field mapping, idempotency key format.
- [ ] 14.5 Write tests for dynamic account discovery: add/remove accounts, graceful shutdown.
- [ ] 14.6 Write tests for rate-limit handling and error isolation between account loops.

## 15. Integration and Documentation

- [ ] 15.1 Add `drive.readonly` and `drive` to the set of requestable scopes in the OAuth start endpoint's scope builder.
- [ ] 15.2 Update roster butler.toml files to document the new `[modules.google_drive]` config section (optional, no breaking changes).
- [ ] 15.3 Write integration test: connect Google account with Drive scope, verify connector discovers it, module resolves credentials.
- [ ] 15.4 Write integration test: module creates butler folder hierarchy, writes file, reads it back.
