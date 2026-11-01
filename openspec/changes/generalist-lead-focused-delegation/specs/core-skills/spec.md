## MODIFIED Requirements

### Requirement: System Prompt Loading from CLAUDE.md

The `read_system_prompt(config_dir, butler_name)` function SHALL read the system prompt from `<config_dir>/CLAUDE.md`. If the file is missing or empty, a default prompt `"You are the {butler_name} butler."` is returned. The resolved text SHALL serve as the domain's guidance document and SHALL NOT be the system prompt of a goal invocation, because domains no longer spawn reasoning sessions and a lead is governed by the versioned lead policy and owner profile; the text reaches an invocation only through `guidance.load` or a brief's guidance reference.

Source: RFC 0038 D1, D2, D15
Scope: v1-mandatory

#### Scenario: CLAUDE.md present and non-empty
- **WHEN** `read_system_prompt(config_dir, butler_name)` is called and `CLAUDE.md` exists with content
- **THEN** the file content is returned after include resolution and shared snippet appending

#### Scenario: CLAUDE.md missing
- **WHEN** `read_system_prompt(config_dir, butler_name)` is called and `CLAUDE.md` does not exist
- **THEN** the default prompt `"You are the {butler_name} butler."` is returned

#### Scenario: CLAUDE.md empty
- **WHEN** `CLAUDE.md` exists but contains only whitespace
- **THEN** the default prompt is returned

#### Scenario: Goal invocation does not receive a domain CLAUDE.md as system prompt
- **WHEN** the goal runner launches a lead or child invocation
- **THEN** no domain's `CLAUDE.md` text is placed in the invocation's system prompt
- **AND** the invocation can read that text only by loading the domain's guidance

### Requirement: SKILL.md Format

Skills SHALL live in `<config_dir>/skills/<name>/` directories. Each skill directory must have a valid kebab-case name matching `^[a-z][a-z0-9]*(-[a-z0-9]+)*$`. The `SKILL.md` file within each skill directory provides the skill description. A skill SHALL be published as domain guidance rather than loaded by a domain-spawned session, and a companion skill SHALL accompany an obligation rather than a prompt-dispatched scheduled task, because scheduled prompts become obligation admissions.

Source: RFC 0038 D2, D5
Scope: v1-mandatory

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
- **WHEN** a SKILL.md serves as the companion skill for an obligation that replaced a prompt-dispatched scheduled task
- **THEN** it documents the capability sequence a goal admitted by that obligation should follow
- **AND** it specifies admission context assumptions (e.g., "Admitted by the daily `upcoming-travel-check` obligation at 08:00 UTC")
- **AND** it covers both the action path and the no-op path
- **AND** outbound notifications use `notify(intent="send")` — not `intent="reply"` — because obligation goals have no incoming message to reply to

### Requirement: AGENTS.md Read/Write Access

The implementation SHALL provide the behavior described by this requirement.
`read_agents_md(config_dir)` reads the AGENTS.md file, returning empty string if absent. `write_agents_md(config_dir, content)` writes/overwrites. `append_agents_md(config_dir, content)` appends to existing content. These SHALL be used only by developer and deployment tooling: a goal invocation has no filesystem access and no capability that reaches these functions, so runtime instances do not write agent notes to AGENTS.md. Runtime notes reach domain guidance only through the domain's `owner_authority` notes `record` capability (`REQ-core-skills-003`, RFC 0038 D2), which exposed invocations cannot write.

Neither `write_agents_md` nor `append_agents_md` SHALL be reachable from any runtime-facing MCP tool, directly or through a thin wrapper. Importable developer/deployment helpers do not confer a model capability. Any future capability that proposes changes to git-tracked roster `AGENTS.md` content MUST submit the proposal through `propose_amendment` and the corresponding roster target adapter; the tracked file changes only after an explicit owner accept act. This conditional proposal path grants no tool or automatic adoption authority and does not govern writes to the separate D2 guidance-notes store, whose `record` capability remains defined by `REQ-core-skills-003`.

ID: REQ-core-skills-004
Source: RFC 0038 D2, D3, D4, D15; specify-improvement-proposal-spine design D13 fold-in
Scope: v1-mandatory

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
- **WHEN** a developer writes to AGENTS.md
- **THEN** the written content follows the AGENTS.md Content Principles: general-purpose butler information only (identity, tool summaries, behavioral guidelines, skill references, and runtime notes)
- **AND** multi-step workflows, extensive examples, and classification taxonomies are directed to skills instead

#### Scenario: AGENTS.md references skills for workflows
- **WHEN** AGENTS.md mentions a capability that has a dedicated skill
- **THEN** it provides a brief description (one sentence) and directs to the skill (e.g., "For the complete bill review workflow, consult the `bill-reminder` skill.")
- **AND** it does NOT duplicate the skill's content inline

#### Scenario: Goal invocations cannot write AGENTS.md
- **WHEN** the capability catalog presented to any lead or child invocation is enumerated
- **THEN** no capability resolves to `write_agents_md` or `append_agents_md`
- **AND** the invocation's sandbox holds no roster file it could write

#### Scenario: [TARGET-STATE] No runtime MCP tool reaches either write function directly
- **WHEN** the registered MCP tool surface for any butler is enumerated
- **THEN** no tool resolves, directly or through a thin wrapper, to `write_agents_md` or `append_agents_md`

#### Scenario: [TARGET-STATE] A future runtime-notes tool routes through propose_amendment
- **WHEN** a future capability lets a goal invocation suggest changes to git-tracked roster `AGENTS.md` agent-notes content
- **THEN** that capability calls `propose_amendment` with `target_kind="roster_prompt"` (or the appropriate roster target kind) rather than calling `write_agents_md`/`append_agents_md` directly
- **AND** the content only reaches `AGENTS.md` after an explicit owner accept act through the corresponding target adapter
- **AND** this tracked-file proposal path neither replaces nor adds an approval gate to the separate D2 guidance-notes `record` capability

## ADDED Requirements

### Requirement: Domain Guidance Corpus

Each domain's resolved `CLAUDE.md` text and each valid skill SHALL be published as versioned domain guidance identified by domain, name and version. Goal invocations SHALL find guidance through `guidance.search` and read it through `guidance.load`, and a child brief SHALL reference guidance by version. Loading guidance SHALL NOT widen an invocation's grant, and the domain's manifesto SHALL stay binding on every capability the domain registers.

ID: REQ-core-skills-002
Source: RFC 0038 D2, D8, D9
Scope: v1-mandatory

#### Scenario: Skill published as guidance
- **WHEN** a domain's `skills/` directory holds a valid skill
- **THEN** the skill is searchable through `guidance.search` and loadable through `guidance.load` under its domain, name and version

#### Scenario: Brief references guidance by version
- **WHEN** a lead delegates a child with governing guidance references
- **THEN** each reference names a guidance version
- **AND** the child loads that version even if the domain has since published a newer one

#### Scenario: Loading guidance grants nothing
- **WHEN** an invocation loads guidance that describes a capability outside its grant
- **THEN** the invocation's grant is unchanged
- **AND** a call to that capability is refused

### Requirement: Domain Guidance Notes Capability

Each domain SHALL offer a `record` capability that appends a note to the domain's guidance-notes store, the successor of AGENTS.md "Notes to self". The capability SHALL be flagged `owner_authority`, so the broker refuses it to an exposed invocation, and each note SHALL carry the writing invocation's verified goal lineage. Notes SHALL be searchable through `guidance.search` and returned by `guidance.load` with their stamps. The cutover SHALL move each domain's existing "Notes to self" content into its store.

ID: REQ-core-skills-003
Source: RFC 0038 D2, D4 and D6
Scope: v1-mandatory

#### Scenario: An unexposed goal records a note
- **WHEN** an unexposed owner goal calls a domain's notes capability under its grant
- **THEN** the note is stored with the invocation's goal lineage and later appears in that domain's guidance

#### Scenario: An exposed invocation cannot steer later sessions
- **WHEN** an exposed invocation calls a domain's notes capability
- **THEN** the broker refuses the call and nothing is stored
