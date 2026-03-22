## ADDED Requirements

### Requirement: AGENTS.md write falls back to state KV store

When the roster directory is read-only (e.g., ConfigMap mount in k8s), `write_agents_md()` and `append_agents_md()` in `core/skills.py` SHALL catch `OSError`/`PermissionError` and persist the content in the butler's `state` KV table under the key `_agents_md_notes`.

#### Scenario: Write succeeds on writable filesystem
- **WHEN** `write_agents_md()` is called and the roster directory is writable
- **THEN** content is written to `{config_dir}/AGENTS.md` (existing behavior, no change)

#### Scenario: Write falls back to DB on read-only filesystem
- **WHEN** `write_agents_md()` is called and the roster directory is read-only
- **THEN** content is stored in the `state` KV table with key `_agents_md_notes`
- **AND** no error is raised to the caller

#### Scenario: Append falls back to DB on read-only filesystem
- **WHEN** `append_agents_md()` is called and the roster directory is read-only
- **THEN** the existing DB value for `_agents_md_notes` is loaded, the new content is appended, and the updated value is stored back

### Requirement: AGENTS.md read merges file and DB sources

`read_agents_md()` SHALL read from the filesystem first, then from the `state` KV table. If both sources have content, they SHALL be concatenated (file content first, then DB content, separated by a newline).

#### Scenario: Read from filesystem only
- **WHEN** `read_agents_md()` is called and `AGENTS.md` exists on disk and no DB entry exists
- **THEN** the file content is returned

#### Scenario: Read from DB only
- **WHEN** `read_agents_md()` is called and `AGENTS.md` does not exist on disk and a DB entry exists at `_agents_md_notes`
- **THEN** the DB content is returned

#### Scenario: Read merges both sources
- **WHEN** `read_agents_md()` is called and both the file and DB entry exist
- **THEN** the file content and DB content are concatenated with a newline separator

### Requirement: DB access is optional

The DB fallback SHALL only be attempted when a database pool is available (passed to the skill functions). If no pool is available (e.g., during CLI-only operations), filesystem-only behavior is preserved.

#### Scenario: No DB pool available
- **WHEN** `write_agents_md()` is called without a DB pool and the filesystem is read-only
- **THEN** the `PermissionError` is logged as a warning and the write is silently dropped
