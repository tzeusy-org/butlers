# Google Drive Module

## Purpose

The Google Drive module provides MCP tools for butlers to read, write, search, and organize files in the user's Google Drive. It centralizes butler-produced outputs under a `butlers/` folder hierarchy while allowing read/write access to arbitrary Drive locations. This is NOT a file store for butler internals — it serves the user by enabling document cataloging, file discovery, content reading, and output publishing to their cloud drive.

## Requirements

### Requirement: GoogleDriveConfig Schema
Configuration is declared under `[modules.google_drive]` in `butler.toml`.

#### Scenario: Config structure
- **WHEN** `[modules.google_drive]` is configured in butler.toml
- **THEN** it SHALL include:
  - `account` (optional string — Google account email to use, defaults to primary)
  - `max_read_size_bytes` (optional int, default 10485760 — 10 MB cap for `drive_read_file`)
  - `butler_folder_name` (optional string, default `"butlers"` — root folder name for butler outputs)

#### Scenario: Config validation
- **WHEN** config is provided with valid fields
- **THEN** the config is validated and normalized (account stripped if present)

#### Scenario: Config without account (primary)
- **WHEN** config is provided without an `account` field
- **THEN** the module SHALL use the primary Google account at startup

#### Scenario: Pydantic extra fields rejected
- **WHEN** an unrecognized field is present in `[modules.google_drive]`
- **THEN** a `ValidationError` SHALL be raised (extra="forbid")

### Requirement: Authentication and Scope Validation
The module resolves Google OAuth credentials for the configured account and validates required scopes. It reuses the existing Google OAuth infrastructure — no new auth flow is needed.

#### Scenario: OAuth credential resolution for specific account
- **WHEN** `on_startup` is called with `account = "work@gmail.com"` in config
- **THEN** the module SHALL call `resolve_google_credentials(store, caller="google_drive", account="work@gmail.com")`
- **AND** cache the resolved credentials for all subsequent API calls

#### Scenario: OAuth credential resolution for primary (default)
- **WHEN** `on_startup` is called without `account` in config
- **THEN** the module SHALL call `resolve_google_credentials(store, caller="google_drive")` which resolves the primary account

#### Scenario: Required OAuth scopes for the module
- **WHEN** the module validates scopes at startup
- **THEN** it SHALL verify that the resolved account's `granted_scopes` include `drive` (full read/write access)
- **AND** `drive.readonly` is NOT sufficient for the module since it writes files
- **AND** if the required scope is missing, startup SHALL fail with a message directing the user to re-authorize at `/api/oauth/google/start?account_hint=<email>&force_consent=true` with the `drive` scope

#### Scenario: Scope addition via existing dashboard
- **WHEN** a user needs to add Drive scopes to an existing Google account
- **THEN** they SHALL use the existing dashboard at the deployment URL (e.g., `https://<host>/butlers/`) to re-authorize
- **AND** the existing OAuth flow at `/api/oauth/google/start` handles scope upgrades with `force_consent=true`
- **AND** no new OAuth endpoints or dashboard pages are required

#### Scenario: Account not connected
- **WHEN** the module starts with `account = "nonexistent@gmail.com"`
- **AND** no `google_accounts` row exists for that email
- **THEN** startup SHALL log a warning and enter degraded mode (it SHALL NOT raise); the butler continues to boot
- **AND** every Drive tool SHALL return a descriptive not-configured error directing the user to connect the account via the dashboard OAuth flow until the account is authorized

### Requirement: Google OAuth and Rate Limiting
The Google provider handles OAuth token refresh and rate-limited retries.

#### Scenario: OAuth token refresh
- **WHEN** the access token expires or is not cached
- **THEN** a refresh-token exchange is performed against `https://oauth2.googleapis.com/token` using the refresh token for the configured Google account
- **AND** the new token is cached with an early-expiry safety margin (60s before actual expiry)
- **AND** on successful refresh, `google_accounts.last_token_refresh_at` SHALL be updated

#### Scenario: Rate-limit retry
- **WHEN** a Google Drive API request returns 403 (rate limit), 429, or 503
- **THEN** the request is retried up to 3 times with exponential backoff (base 1.0s)

#### Scenario: Credential redaction in errors
- **WHEN** an error message might contain credential values
- **THEN** patterns like `client_secret=...`, `refresh_token=...`, `access_token=...` are redacted before logging or returning to the caller

### Requirement: Butler Folder Hierarchy
The module auto-creates a centralized folder hierarchy for butler outputs in the user's Drive.

#### Scenario: Root butler folder auto-creation
- **WHEN** a butler calls any write tool (`drive_write_file`, `drive_create_folder`) without specifying a `folder_id`
- **THEN** the module SHALL ensure a root folder named by `butler_folder_name` config (default `"butlers"`) exists at the Drive root
- **AND** if the folder does not exist, it SHALL be created via `files.create` with `mimeType = "application/vnd.google-apps.folder"`
- **AND** the folder ID SHALL be cached in `google_drive_butler_folders` table

#### Scenario: Per-butler subfolder auto-creation
- **WHEN** a write tool is called and the root butler folder exists
- **THEN** the module SHALL ensure a subfolder named `{butler_name}` exists inside the root folder
- **AND** if the subfolder does not exist, it SHALL be created
- **AND** the subfolder ID SHALL be cached in `google_drive_butler_folders` table

#### Scenario: Folder existence verification
- **WHEN** a cached folder ID is used for a write operation
- **THEN** the module SHALL verify the folder still exists via `files.get` before writing
- **AND** if the folder has been deleted, it SHALL be re-created and the cache updated

#### Scenario: Default folder_id for write tools
- **WHEN** `drive_write_file` or `drive_create_folder` is called without an explicit `folder_id`
- **THEN** the default target SHALL be the butler's subfolder (`butlers/{butler_name}/`)
- **AND** explicit `folder_id` overrides the default (allows writing to arbitrary Drive locations)

### Requirement: MCP Tool — drive_list_files
Lists files in a Drive folder or matching a query.

#### Scenario: List files in a folder
- **WHEN** `drive_list_files(folder_id="abc123")` is called
- **THEN** the module SHALL call `files.list` with `q="'abc123' in parents and trashed=false"` and `fields="files(id,name,mimeType,modifiedTime,size,parents,shared,owners)"`
- **AND** return a list of file metadata dicts

#### Scenario: List files with query filter
- **WHEN** `drive_list_files(folder_id="abc123", query="name contains 'report'")` is called
- **THEN** the module SHALL combine the folder parent filter with the user query: `q="'abc123' in parents and trashed=false and name contains 'report'"`

#### Scenario: List files without folder (root)
- **WHEN** `drive_list_files()` is called without `folder_id`
- **THEN** the module SHALL list files in the user's My Drive root: `q="'root' in parents and trashed=false"`

#### Scenario: Pagination
- **WHEN** the file list exceeds 100 items (default page size)
- **THEN** the module SHALL paginate using `pageToken` and return all results up to 1000 items
- **AND** a `truncated` flag SHALL indicate if more results exist

### Requirement: MCP Tool — drive_get_file_metadata
Returns detailed metadata for a single file without downloading content.

#### Scenario: Get metadata for existing file
- **WHEN** `drive_get_file_metadata(file_id="abc123")` is called
- **THEN** the module SHALL call `files.get` with `fields="id,name,mimeType,modifiedTime,createdTime,size,parents,shared,sharingUser,owners,webViewLink,description"`
- **AND** return the metadata as a dict

#### Scenario: File not found
- **WHEN** `drive_get_file_metadata` is called with a nonexistent file ID
- **THEN** the tool SHALL return `{"status": "not_found", "file": null}`

### Requirement: MCP Tool — drive_read_file
Downloads and returns file content for text-representable files.

#### Scenario: Read a plain text file
- **WHEN** `drive_read_file(file_id="abc123")` is called on a text file (MIME type `text/*`)
- **THEN** the module SHALL call `files.get` with `alt=media` to download the content
- **AND** return `{"content": "<file_text>", "mime_type": "<type>", "name": "<filename>", "size_bytes": <size>}`

#### Scenario: Read a Google Doc (export)
- **WHEN** `drive_read_file(file_id="abc123")` is called on a Google Doc (`application/vnd.google-apps.document`)
- **THEN** the module SHALL call `files.export` with `mimeType="text/plain"` to export the content
- **AND** return the exported text content

#### Scenario: Read a Google Sheet (export as CSV)
- **WHEN** `drive_read_file(file_id="abc123")` is called on a Google Sheet (`application/vnd.google-apps.spreadsheet`)
- **THEN** the module SHALL call `files.export` with `mimeType="text/csv"` to export the content
- **AND** return the exported CSV content

#### Scenario: Read a Google Slides (export as text)
- **WHEN** `drive_read_file(file_id="abc123")` is called on Google Slides (`application/vnd.google-apps.presentation`)
- **THEN** the module SHALL call `files.export` with `mimeType="text/plain"` to export the content

#### Scenario: Size limit enforcement
- **WHEN** `drive_read_file` is called on a file larger than `max_read_size_bytes` (default 10 MB)
- **THEN** the tool SHALL return `{"status": "too_large", "size_bytes": <actual>, "max_bytes": <limit>, "name": "<filename>"}` without downloading

#### Scenario: Binary file handling
- **WHEN** `drive_read_file` is called on a binary file (images, videos, archives)
- **THEN** the tool SHALL return `{"status": "binary_file", "mime_type": "<type>", "name": "<filename>", "size_bytes": <size>, "web_view_link": "<url>"}` without downloading

### Requirement: MCP Tool — drive_write_file
Creates or uploads a file to Google Drive.

#### Scenario: Write text file to butler folder (default)
- **WHEN** `drive_write_file(name="report.txt", content="Report content...", mime_type="text/plain")` is called without `folder_id`
- **THEN** the module SHALL auto-ensure the butler subfolder exists
- **AND** create the file in `butlers/{butler_name}/` via `files.create` with the provided content as media upload
- **AND** return `{"file_id": "<id>", "name": "<name>", "folder": "<folder_id>", "web_view_link": "<url>", "mime_type": "<inferred_or_given_type>"}` (the `folder` value is the resolved Drive folder ID, usable directly in subsequent Drive calls)

#### Scenario: Write file to specific folder
- **WHEN** `drive_write_file(folder_id="xyz789", name="data.csv", content="a,b,c\n1,2,3", mime_type="text/csv")` is called
- **THEN** the file SHALL be created in the specified folder, overriding the default butler folder

#### Scenario: MIME type inference
- **WHEN** `drive_write_file` is called without `mime_type`
- **THEN** the MIME type SHALL be inferred from the file extension: `.txt` → `text/plain`, `.csv` → `text/csv`, `.json` → `application/json`, `.md` → `text/markdown`, `.html` → `text/html`
- **AND** if the extension is unrecognized, `application/octet-stream` SHALL be used

#### Scenario: Duplicate filename handling
- **WHEN** a file with the same name already exists in the target folder
- **THEN** Google Drive's native behavior SHALL apply (allows duplicate names — Drive uses file IDs, not names, for identity)
- **AND** the response SHALL include the new file's unique `file_id`

### Requirement: MCP Tool — drive_create_folder
Creates a folder in Google Drive.

#### Scenario: Create folder in butler hierarchy (default)
- **WHEN** `drive_create_folder(name="reports")` is called without `parent_id`
- **THEN** the folder SHALL be created inside the butler's subfolder (`butlers/{butler_name}/reports`)
- **AND** return `{"folder_id": "<id>", "name": "<name>", "parent_path": "<parent_folder_id>", "web_view_link": "<url>"}` (the `parent_path` value is the resolved Drive parent folder ID, not a human-readable path)

#### Scenario: Create folder in specific location
- **WHEN** `drive_create_folder(parent_id="xyz789", name="archive")` is called
- **THEN** the folder SHALL be created inside the specified parent folder

### Requirement: MCP Tool — drive_move_file
Moves a file from one folder to another.

#### Scenario: Move file to new parent
- **WHEN** `drive_move_file(file_id="abc123", new_parent_id="xyz789")` is called
- **THEN** the module SHALL call `files.update` with `addParents=new_parent_id` and `removeParents=<current_parent_id>`
- **AND** return `{"status": "ok", "file_id": "<id>", "name": "<name>", "new_parent_id": "<id>"}`

#### Scenario: File not found
- **WHEN** `drive_move_file` is called with a nonexistent file ID
- **THEN** the tool SHALL return `{"status": "not_found", "error": "File not found"}`

### Requirement: MCP Tool — drive_search_files
Searches files across the user's Drive using Drive API's built-in full-text search.

#### Scenario: Full-text search
- **WHEN** `drive_search_files(query="tax return 2025")` is called
- **THEN** the module SHALL call `files.list` with `q="fullText contains 'tax return 2025' and trashed=false"` and `fields="files(id,name,mimeType,modifiedTime,size,parents,shared,owners,webViewLink)"`
- **AND** return matching files sorted by relevance (Drive's default ordering)

#### Scenario: Search with limit
- **WHEN** `drive_search_files(query="report", limit=10)` is called
- **THEN** at most 10 results SHALL be returned

#### Scenario: Empty results
- **WHEN** no files match the search query
- **THEN** the tool SHALL return `{"files": [], "total": 0}`

### Requirement: Module Identity and Dependencies

#### Scenario: Module identity
- **WHEN** the module is registered
- **THEN** `name` SHALL be `"google_drive"`
- **AND** `dependencies` SHALL be `[]` (no module dependencies)
- **AND** `config_schema` SHALL be `GoogleDriveConfig`

### Requirement: Tool Registration

#### Scenario: Tool inventory
- **WHEN** `register_tools(mcp, config, db)` is called
- **THEN** the following 7 tools SHALL be registered: `drive_list_files`, `drive_get_file_metadata`, `drive_read_file`, `drive_write_file`, `drive_create_folder`, `drive_move_file`, `drive_search_files`

### Requirement: Tool Metadata for Approval Sensitivity

#### Scenario: Write tools declared sensitive
- **WHEN** `tool_metadata()` is called
- **THEN** it SHALL return `ToolMeta(arg_sensitivities={"content": True})` for `drive_write_file`
- **AND** `ToolMeta(arg_sensitivities={"file_id": True, "new_parent_id": True})` for `drive_move_file`

#### Scenario: Read tools not declared
- **WHEN** `tool_metadata()` is called
- **THEN** no entries SHALL exist for `drive_list_files`, `drive_get_file_metadata`, `drive_read_file`, `drive_search_files`

### Requirement: Database Schema Migration
The module provides an Alembic migration for its butler folder registry table.

#### Scenario: Migration creates table
- **WHEN** the Alembic migration runs
- **THEN** `google_drive_butler_folders` (butler_name TEXT, account_email TEXT, folder_id TEXT NOT NULL, folder_path TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (butler_name, account_email)) SHALL be created

#### Scenario: Migration branch label
- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"google_drive"` as the Alembic branch label

### Requirement: HTTP Client Lifecycle

#### Scenario: Client initialization
- **WHEN** `on_startup` completes credential resolution
- **THEN** an HTTP client SHALL be created for Google Drive API calls with:
  - Base URL `https://www.googleapis.com/drive/v3/`
  - `Authorization: Bearer <access_token>` header (refreshed automatically)
  - Default timeout of 30 seconds

#### Scenario: Client cleanup
- **WHEN** `on_shutdown` is called
- **THEN** the HTTP client SHALL be closed
