## MODIFIED Requirements

### Requirement: AGENTS.md Read/Write Access

The implementation SHALL provide the behavior described by this requirement.
`read_agents_md(config_dir)` reads the AGENTS.md file, returning empty string if absent. `write_agents_md(config_dir, content)` writes/overwrites. `append_agents_md(config_dir, content)` appends to existing content. These are used by runtime instances for runtime agent notes.

The functions `read_agents_md(config_dir)`, `write_agents_md(config_dir, content)`, and `append_agents_md(config_dir, content)` manage the `AGENTS.md` file that runtime LLM sessions use for persistent agent notes. When the config directory is read-only, these functions SHALL fall back to the butler's `state` KV table using the key `_agents_md_notes`. The DB pool is passed as an optional parameter; when `None`, filesystem-only behavior is preserved.

#### Scenario: AGENTS.md present and writable
- **WHEN** `read_agents_md(config_dir)` is called and `AGENTS.md` exists
- **THEN** the file content is returned (existing behavior)

#### Scenario: AGENTS.md absent
- **WHEN** `read_agents_md(config_dir)` is called and `AGENTS.md` does not exist
- **THEN** an empty string is returned (existing behavior)

#### Scenario: Write to writable filesystem
- **WHEN** `write_agents_md(config_dir, content)` is called and the directory is writable
- **THEN** content is written to `{config_dir}/AGENTS.md` (existing behavior)

#### Scenario: Write falls back to DB on read-only filesystem
- **WHEN** `write_agents_md(config_dir, content, db_pool=pool)` is called and the directory is read-only
- **THEN** content is stored in the `state` KV table with key `_agents_md_notes`
- **AND** no error is raised

#### Scenario: Append falls back to DB on read-only filesystem
- **WHEN** `append_agents_md(config_dir, content, db_pool=pool)` is called and the directory is read-only
- **THEN** the existing DB value for `_agents_md_notes` is loaded, new content is appended, and the result is stored back

#### Scenario: Read merges file and DB sources
- **WHEN** `read_agents_md(config_dir, db_pool=pool)` is called and both `AGENTS.md` on disk and a `_agents_md_notes` DB entry exist
- **THEN** file content and DB content are concatenated (file first, DB second, newline separator)

#### Scenario: No DB pool and read-only filesystem
- **WHEN** `write_agents_md(config_dir, content, db_pool=None)` is called and the directory is read-only
- **THEN** a warning is logged and the write is silently dropped

#### Scenario: Read existing AGENTS.md
- **WHEN** `read_agents_md(config_dir)` is called and AGENTS.md exists
- **THEN** the file content is returned

#### Scenario: Write AGENTS.md
- **WHEN** `write_agents_md(config_dir, content)` is called
- **THEN** AGENTS.md is created or overwritten with the given content

#### Scenario: Append to AGENTS.md
- **WHEN** `append_agents_md(config_dir, content)` is called
- **THEN** the content is appended to the existing AGENTS.md content

#### Scenario: AGENTS.md content boundary
- **WHEN** a runtime instance or developer writes to AGENTS.md
- **THEN** the written content follows the AGENTS.md Content Principles: general-purpose butler information only (identity, tool summaries, behavioral guidelines, skill references, and runtime notes)
- **AND** multi-step workflows, extensive examples, and classification taxonomies are directed to skills instead

#### Scenario: AGENTS.md references skills for workflows
- **WHEN** AGENTS.md mentions a capability that has a dedicated skill
- **THEN** it provides a brief description (one sentence) and directs to the skill (e.g., "For the complete bill review workflow, consult the `bill-reminder` skill.")
- **AND** it does NOT duplicate the skill's content inline
