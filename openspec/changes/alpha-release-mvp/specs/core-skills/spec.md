# Skills

## Purpose
Provides infrastructure for loading butler system prompts from `CLAUDE.md`, managing runtime agent notes in `AGENTS.md`, discovering skill directories, and composing system prompts with include directives and shared snippets.

## ADDED Requirements

### Requirement: System Prompt Loading from CLAUDE.md
The `read_system_prompt(config_dir, butler_name)` function reads the system prompt from `<config_dir>/CLAUDE.md`. If the file is missing or empty, a default prompt `"You are the {butler_name} butler."` is returned.

#### Scenario: CLAUDE.md present and non-empty
- **WHEN** `read_system_prompt(config_dir, butler_name)` is called and `CLAUDE.md` exists with content
- **THEN** the file content is returned after include resolution and shared snippet appending

#### Scenario: CLAUDE.md missing
- **WHEN** `read_system_prompt(config_dir, butler_name)` is called and `CLAUDE.md` does not exist
- **THEN** the default prompt `"You are the {butler_name} butler."` is returned

#### Scenario: CLAUDE.md empty
- **WHEN** `CLAUDE.md` exists but contains only whitespace
- **THEN** the default prompt is returned

### Requirement: Include Directive Resolution
Lines matching `<!-- @include path/to/file.md -->` in system prompts are replaced with the contents of the referenced file. Paths are resolved relative to the roster directory (`config_dir.parent`). Path traversal (`..` segments) is rejected. Includes are not recursive.

#### Scenario: Valid include directive
- **WHEN** a CLAUDE.md line is `<!-- @include shared/NOTIFY.md -->`
- **THEN** the line is replaced with the contents of `roster/shared/NOTIFY.md`

#### Scenario: Path traversal rejected
- **WHEN** an include path contains `..` segments
- **THEN** a warning is logged and the directive is preserved as-is (not resolved)

#### Scenario: Missing include file
- **WHEN** the referenced include file does not exist
- **THEN** a warning is logged and the directive is preserved as-is

### Requirement: Shared Snippet Appending
After include resolution, the system prompt has shared snippets appended in a stable order: first `roster/shared/BUTLER_SKILLS.md`, then `roster/shared/MCP_LOGGING.md`. Each is appended with a blank line separator if the file exists and is non-empty.

#### Scenario: Shared files appended
- **WHEN** `roster/shared/BUTLER_SKILLS.md` and `roster/shared/MCP_LOGGING.md` exist
- **THEN** their contents are appended to the system prompt in order

#### Scenario: Shared file missing
- **WHEN** `roster/shared/BUTLER_SKILLS.md` does not exist
- **THEN** no content is appended for that file

### Requirement: SKILL.md Format
Skills live in `<config_dir>/skills/<name>/` directories. Each skill directory must have a valid kebab-case name matching `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. The `SKILL.md` file within each skill directory provides the skill description.

#### Scenario: Valid skill directory name
- **WHEN** a directory named `executing-plans` exists under `skills/`
- **THEN** `is_valid_skill_name("executing-plans")` returns `True`

#### Scenario: Invalid skill directory name
- **WHEN** a directory named `MySkill` exists under `skills/`
- **THEN** `is_valid_skill_name("MySkill")` returns `False` and the directory is logged as a warning and skipped

### Requirement: Skill Directory Discovery
`list_valid_skills(skills_dir)` returns all valid skill subdirectories sorted by name, skipping files and invalid directory names.

#### Scenario: Multiple skills discovered
- **WHEN** `skills/` contains directories `a-skill/`, `b-skill/`, and a file `readme.txt`
- **THEN** `list_valid_skills()` returns `[Path("a-skill"), Path("b-skill")]` (files skipped)

#### Scenario: No skills directory
- **WHEN** `get_skills_dir(config_dir)` is called and `skills/` does not exist
- **THEN** `None` is returned

### Requirement: AGENTS.md Read/Write Access
`read_agents_md(config_dir)` reads the AGENTS.md file, returning empty string if absent. `write_agents_md(config_dir, content)` writes/overwrites. `append_agents_md(config_dir, content)` appends to existing content. These are used by runtime instances for runtime agent notes.

#### Scenario: Read existing AGENTS.md
- **WHEN** `read_agents_md(config_dir)` is called and AGENTS.md exists
- **THEN** the file content is returned

#### Scenario: Write AGENTS.md
- **WHEN** `write_agents_md(config_dir, content)` is called
- **THEN** AGENTS.md is created or overwritten with the given content

#### Scenario: Append to AGENTS.md
- **WHEN** `append_agents_md(config_dir, content)` is called
- **THEN** the content is appended to the existing AGENTS.md content
