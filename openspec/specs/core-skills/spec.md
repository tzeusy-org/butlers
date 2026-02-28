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

#### Scenario: Skill content guidelines
- **WHEN** a SKILL.md file is authored
- **THEN** it contains workflow-specific documentation including any combination of: multi-step procedures with explicit MCP tool call sequences, decision frameworks and classification rules, worked examples with tool calls and expected outputs, output templates and formatting guides, memory classification taxonomies, and error handling procedures
- **AND** the frontmatter `description` field summarizes when the skill should be loaded

#### Scenario: Scheduled task companion skill format
- **WHEN** a SKILL.md serves as the companion skill for a prompt-dispatched scheduled task
- **THEN** it documents the complete tool sequence the runtime should follow when that scheduled task fires
- **AND** it specifies trigger context assumptions (e.g., "Triggered by the daily `upcoming-travel-check` schedule at 08:00 UTC")
- **AND** it covers both the action path and the no-op path
- **AND** outbound notifications use `notify(intent="send")` — not `intent="reply"` — because scheduled tasks have no incoming message to reply to

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

#### Scenario: AGENTS.md content boundary
- **WHEN** a runtime instance or developer writes to AGENTS.md
- **THEN** the written content follows the AGENTS.md Content Principles: general-purpose butler information only (identity, tool summaries, behavioral guidelines, skill references, and runtime notes)
- **AND** multi-step workflows, extensive examples, and classification taxonomies are directed to skills instead

#### Scenario: AGENTS.md references skills for workflows
- **WHEN** AGENTS.md mentions a capability that has a dedicated skill
- **THEN** it provides a brief description (one sentence) and directs to the skill (e.g., "For the complete bill review workflow, consult the `bill-reminder` skill.")
- **AND** it does NOT duplicate the skill's content inline
