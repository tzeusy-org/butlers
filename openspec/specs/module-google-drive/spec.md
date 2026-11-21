# Google Drive Module

## Purpose

The Google Drive module provides MCP tools for butlers to read, write, search, and organize files in the user's Google Drive. It centralizes butler-produced outputs under a `butlers/` folder hierarchy while allowing read/write access to arbitrary Drive locations. This is NOT a file store for butler internals — it serves the user by enabling document cataloging, file discovery, content reading, and output publishing to their cloud drive.

## Requirements

### Requirement: GoogleDriveConfig Schema

Configuration SHALL be declared under `[modules.google_drive]` in `butler.toml`.

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

The module SHALL resolve Google OAuth credentials for the configured account and validate required scopes. It reuses the existing Google OAuth infrastructure — no new auth flow is needed.

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

The Google provider SHALL handle OAuth token refresh and rate-limited retries.

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

The module SHALL auto-create a centralized folder hierarchy for butler outputs in the user's Drive.

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

The tool SHALL list files in a Drive folder or matching a query.

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

The tool SHALL return detailed metadata for a single file without downloading content.

#### Scenario: Get metadata for existing file
- **WHEN** `drive_get_file_metadata(file_id="abc123")` is called
- **THEN** the module SHALL call `files.get` with `fields="id,name,mimeType,modifiedTime,createdTime,size,parents,shared,sharingUser,owners,webViewLink,description"`
- **AND** return the metadata as a dict

#### Scenario: File not found
- **WHEN** `drive_get_file_metadata` is called with a nonexistent file ID
- **THEN** the tool SHALL return `{"status": "not_found", "file": null}`

### Requirement: MCP Tool — drive_read_file

The tool SHALL download and return file content for text-representable files.

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

The tool SHALL create or upload a file to Google Drive.

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

The tool SHALL create a folder in Google Drive.

#### Scenario: Create folder in butler hierarchy (default)
- **WHEN** `drive_create_folder(name="reports")` is called without `parent_id`
- **THEN** the folder SHALL be created inside the butler's subfolder (`butlers/{butler_name}/reports`)
- **AND** return `{"folder_id": "<id>", "name": "<name>", "parent_path": "<parent_folder_id>", "web_view_link": "<url>"}` (the `parent_path` value is the resolved Drive parent folder ID, not a human-readable path)

#### Scenario: Create folder in specific location
- **WHEN** `drive_create_folder(parent_id="xyz789", name="archive")` is called
- **THEN** the folder SHALL be created inside the specified parent folder

### Requirement: MCP Tool — drive_move_file

The tool SHALL move a file from one folder to another.

#### Scenario: Move file to new parent
- **WHEN** `drive_move_file(file_id="abc123", new_parent_id="xyz789")` is called
- **THEN** the module SHALL call `files.update` with `addParents=new_parent_id` and `removeParents=<current_parent_id>`
- **AND** return `{"status": "ok", "file_id": "<id>", "name": "<name>", "new_parent_id": "<id>"}`

#### Scenario: File not found
- **WHEN** `drive_move_file` is called with a nonexistent file ID
- **THEN** the tool SHALL return `{"status": "not_found", "error": "File not found"}`

### Requirement: MCP Tool — drive_search_files

The tool SHALL search files across the user's Drive using Drive API's built-in full-text search.

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

The Google Drive module SHALL register under a fixed name with no module
dependencies and its own config schema.

#### Scenario: Module identity
- **WHEN** the module is registered
- **THEN** `name` SHALL be `"google_drive"`
- **AND** `dependencies` SHALL be `[]` (no module dependencies)
- **AND** `config_schema` SHALL be `GoogleDriveConfig`

### Requirement: Tool Registration

The module SHALL register the Drive read, write, and search tools enumerated
below. When the RFC 0038 domain switch is on it SHALL additionally register the six native Docs
and Sheets tools.

#### Scenario: Tool inventory
- **WHEN** `register_tools(mcp, config, db)` is called with the switch off
- **THEN** the following 7 tools SHALL be registered: `drive_list_files`, `drive_get_file_metadata`, `drive_read_file`, `drive_write_file`, `drive_create_folder`, `drive_move_file`, `drive_search_files`

#### Scenario: Tool inventory with the switch on
- **WHEN** `register_tools(mcp, config, db)` is called with the switch on
- **THEN** the 7 tools and `drive_create_doc`, `drive_update_doc`, `drive_create_sheet`, `drive_read_sheet`, `drive_append_sheet_rows`, `drive_update_sheet_range` SHALL be registered

### Requirement: Tool Metadata for Approval Sensitivity

The module SHALL declare the sensitive arguments of its write tools through
`tool_metadata()`, and SHALL declare no entries for its read tools.

#### Scenario: Write tools declared sensitive
- **WHEN** `tool_metadata()` is called
- **THEN** it SHALL return `ToolMeta(arg_sensitivities={"content": True})` for `drive_write_file`
- **AND** `ToolMeta(arg_sensitivities={"file_id": True, "new_parent_id": True})` for `drive_move_file`

#### Scenario: Read tools not declared
- **WHEN** `tool_metadata()` is called
- **THEN** no entries SHALL exist for `drive_list_files`, `drive_get_file_metadata`, `drive_read_file`, `drive_search_files`

### Requirement: Database Schema Migration

The module SHALL provide Alembic migrations for its butler folder registry table and its effect
receipt table.

#### Scenario: Migration creates table
- **WHEN** the Alembic migration runs
- **THEN** `google_drive_butler_folders` (butler_name TEXT, account_email TEXT, folder_id TEXT NOT NULL, folder_path TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), PRIMARY KEY (butler_name, account_email)) SHALL be created

#### Scenario: Receipt table migration
- **WHEN** migration `google_drive_002` runs
- **THEN** `google_drive_effect_receipts` (effect_id UUID PRIMARY KEY, tool TEXT NOT NULL, file_id TEXT, receipt JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now()) SHALL be created, and its downgrade SHALL drop it

#### Scenario: Migration branch label
- **WHEN** `migration_revisions()` is called
- **THEN** it SHALL return `"google_drive"` as the Alembic branch label

### Requirement: HTTP Client Lifecycle

The module SHALL own an authenticated HTTP client for the Google Drive API,
created once credentials resolve at startup and closed at shutdown.

#### Scenario: Client initialization
- **WHEN** `on_startup` completes credential resolution
- **THEN** an HTTP client SHALL be created for Google Drive API calls with:
  - Base URL `https://www.googleapis.com/drive/v3/`
  - `Authorization: Bearer <access_token>` header (refreshed automatically)
  - Default timeout of 30 seconds

#### Scenario: Client cleanup
- **WHEN** `on_shutdown` is called
- **THEN** the HTTP client SHALL be closed

### Requirement: Destination Audience Approval

Write, create-folder and move capabilities SHALL inspect the complete effective
destination ACL and resolve every user permission through the existing owner
identity resolver before mutation. Non-owner, group, domain, public or unresolved
audiences SHALL require approval. Unavailable or incomplete ACL reads SHALL
refuse even an approved replay. Exact stored arguments SHALL bind the concrete
destination and server-computed optional `destination_audience_digest`; the
digest SHALL confer no authority and changed snapshots SHALL refuse.

#### Scenario: Shared destination and changed audience
- **WHEN** a write reaches a known shared destination
- **THEN** the complete concrete call SHALL park before any provider mutation
- **AND** a changed permission identity, role, inheritance or visibility at replay SHALL refuse

#### Scenario: Shared default bootstrap
- **WHEN** the default path needs a folder under a known shared parent
- **THEN** only the exact existing create-folder call SHALL park before creation
- **AND** its receipt SHALL state the original operation is incomplete
- **AND** approval SHALL NOT authorize original content, which requires a fresh retry and audience check

#### Scenario: Owner-private default bootstrap
- **WHEN** every verified parent and final audience is owner-only
- **THEN** existing default creation and upload SHALL remain automatic

### Requirement: Owner-Private Target Guard

Before every write, the native Docs and Sheets tools SHALL read the target with `files.get`
(fields `ownedByMe`, `driveId`, `permissions`). The target is the parent folder for a create and
the file for an update or append. The write SHALL proceed only when `ownedByMe` is true, no
`driveId` is present, and every non-deleted permission is a `user` permission for the connected
account's own address. Otherwise the tool SHALL return `{"status": "target_shared", "reach": {...}}`
with counts by permission type and role and no address, and SHALL send no write request. An
unreadable permission list SHALL refuse.

ID: REQ-module-google-drive-001
Source: owner direction 2026-10-15 (epic bu-kqme7f); RFC 0038 D4 owner rules 8 and 9
Scope: v1-mandatory

#### Scenario: Shared targets are refused
- **WHEN** the target has a permission for another user, a group, a domain or anyone, is not owned by the connected account, or is in a Shared Drive
- **THEN** the tool SHALL return `target_shared` and the fake Drive SHALL record no write request

#### Scenario: Deleted grants do not count
- **WHEN** the only extra permission is marked deleted
- **THEN** the write SHALL proceed

### Requirement: MCP Tool — drive_create_doc

`drive_create_doc(title, content, content_format="markdown"|"text", folder_id=None)` SHALL upload
the sanitised content with conversion to a native Google Doc (`application/vnd.google-apps.document`)
into the owner-private folder (default `butlers/{butler_name}/`), mark the file with
`appProperties {"butlers_created_by": "<butler_name>"}`, and return `file_id`, `web_view_link` and
the mime type. It SHALL read `about.importFormats` once per process; when `text/markdown` is not
importable it SHALL import `text/plain` and return `"format_degraded": "markdown_imported_as_text"`.
Content over 1 MB SHALL return `too_large` without calling Google.

ID: REQ-module-google-drive-002
Source: epic bu-kqme7f; RFC 0038 D4, D12
Scope: v1-mandatory

#### Scenario: Native Doc from Markdown
- **WHEN** the tool is called with Markdown and a private folder
- **THEN** the upload metadata SHALL carry `mimeType application/vnd.google-apps.document` and the sanitised Markdown body

### Requirement: MCP Tool — drive_update_doc

`drive_update_doc(file_id, content, content_format)` SHALL replace the body of a Doc in place with a
media upload in the same import format, keeping the file id, link and revision history. It SHALL
act only on a Doc that is owner-private and carries this butler's `butlers_created_by` mark;
otherwise it SHALL return `not_created_by_butlers` and send no upload. It SHALL return the
pre-write `version`.

ID: REQ-module-google-drive-003
Source: epic bu-kqme7f
Scope: v1-mandatory

#### Scenario: Unmarked Doc
- **WHEN** the Doc has no `butlers_created_by` mark for this butler
- **THEN** the tool SHALL return `not_created_by_butlers` and no write request SHALL be sent

### Requirement: MCP Tools — Sheets

`drive_create_sheet(title, rows, folder_id=None)` SHALL create an empty native spreadsheet in the
owner-private folder, then write the rows through the Sheets API. `drive_append_sheet_rows(file_id,
rows, a1_range=None)` and `drive_update_sheet_range(file_id, a1_range, rows)` SHALL write to an
owner-private sheet. Every value write SHALL use `valueInputOption=RAW`, so a value beginning with
`=`, `+`, `-` or `@` is stored as text. `drive_read_sheet(file_id, a1_range=None)` SHALL return the
tab titles' values of every tab. More than 50,000 cells in a call SHALL return `too_large` without
calling Google. When the value write of `drive_create_sheet` fails after the file exists, the new
file SHALL be trashed and an error returned. A disabled Sheets API (`accessNotConfigured` or
`SERVICE_DISABLED`) SHALL produce an error naming the Google Sheets API, without retry. Each append
is atomic per Sheets API call; the order of concurrent appends is not guaranteed.

ID: REQ-module-google-drive-004
Source: epic bu-kqme7f
Scope: v1-mandatory

#### Scenario: Raw values
- **WHEN** a cell value is `=IMPORTXML("http://x")`
- **THEN** it SHALL be sent with `valueInputOption=RAW`

#### Scenario: Failed value write
- **WHEN** the value write fails after the file was created
- **THEN** the file SHALL be trashed and the tool SHALL return an error

### Requirement: Untrusted Content Sanitisation and Exposure

Markdown image syntax, inline and reference style, SHALL be downgraded to an ordinary link before
upload, and every `<` SHALL be escaped. When the call's verified lineage is exposed, or lineage is
missing, every URL in doc content and in string cells SHALL be written in the inert text form
(a Markdown link keeps its label and shows its target defanged); unexposed calls keep their links.
`drive_read_sheet` output is untrusted.

ID: REQ-module-google-drive-005
Source: RFC 0038 D6; REQ-capability-authorization-006
Scope: v1-mandatory

#### Scenario: Image becomes a link
- **WHEN** content contains `![x](https://evil.test/p.png)`
- **THEN** the uploaded body SHALL contain `[x](https://evil.test/p.png)` and no `![`

#### Scenario: Exposed or missing lineage
- **WHEN** lineage is exposed or absent
- **THEN** URLs SHALL be uploaded defanged, and when lineage is unexposed they SHALL be kept

### Requirement: Effect Replay Receipts

`drive_create_doc`, `drive_create_sheet` and `drive_append_sheet_rows` are `key_required`: the
broker's `effect_id` is the key. A repeated `effect_id` SHALL return the receipt stored in
`google_drive_effect_receipts` without calling Google. When a keyed call cannot read the receipt
store, it SHALL write nothing. `drive_update_doc` and `drive_update_sheet_range` are idempotent by
content and declare no key.

ID: REQ-module-google-drive-006
Source: RFC 0038 D12
Scope: v1-mandatory

#### Scenario: Replay
- **WHEN** a second call carries the same `effect_id`
- **THEN** it SHALL return the first receipt and send no request to Google

### Requirement: Catalog Metadata and Classification Rule

All six tools SHALL declare `output_sensitivity` sensitive, `output_trust` untrusted,
`owner_authority` false and empty `recipient_params`. The five write tools SHALL be effect class
`record` and `drive_read_sheet` `read`. The write tools are `record` only because
Requirement "Owner-Private Target Guard" refuses any target carrying a non-owner permission;
`drive_write_file` and `drive_create_folder` remain `communicate` because they can write into
shared folders. A change to the guard SHALL reclassify the tools. The module SHALL expose exactly
one post-write seam, a no-op until a hook is registered.

ID: REQ-module-google-drive-007
Source: RFC 0038 D4; bu-stbwjc
Scope: v1-mandatory

#### Scenario: Registered only after the switch
- **WHEN** the RFC 0038 domain switch is off
- **THEN** exactly the seven original tools SHALL be registered and no native-tool metadata declared
