## MODIFIED Requirements

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
below.

#### Scenario: Tool inventory
- **WHEN** `register_tools(mcp, config, db)` is called
- **THEN** the following 7 tools SHALL be registered: `drive_list_files`, `drive_get_file_metadata`, `drive_read_file`, `drive_write_file`, `drive_create_folder`, `drive_move_file`, `drive_search_files`

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
