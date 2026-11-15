## ADDED Requirements

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

## MODIFIED Requirements

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
