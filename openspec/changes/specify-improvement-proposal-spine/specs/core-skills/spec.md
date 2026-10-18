## MODIFIED Requirements

### Requirement: AGENTS.md Read/Write Access

The implementation SHALL provide the behavior described by this requirement.
`read_agents_md(config_dir)` reads the AGENTS.md file, returning empty string if absent. `write_agents_md(config_dir, content)` writes/overwrites. `append_agents_md(config_dir, content)` appends to existing content. These are used by runtime instances for runtime agent notes.

**[TARGET-STATE]** The paragraph above describes these functions' intended consumer ("runtime instances") in the abstract; as of this requirement's prior text, no registered MCP tool on any butler actually calls either function from a runtime session — both remain reachable only from developer/deployment tooling (migrations, CLI scripts, test fixtures) and from each other (`append_agents_md` composes `read_agents_md`/`write_agents_md`). This requirement now makes that boundary normative rather than incidental: neither `write_agents_md` nor `append_agents_md` SHALL be reachable from any runtime-facing MCP tool surface. A butler runtime session (the ephemeral LLM CLI spawned per trigger) SHALL have no direct call path, via any registered MCP tool, to either function. Any future capability that lets a runtime session propose agent-notes content MUST route that content through `propose_amendment` (the `improvement-proposal-spine` capability's server-derived-actor append function) and the corresponding target adapter, never through a direct call to either function. Developer and deployment tooling retains unrestricted direct access — this constraint applies only to the runtime MCP tool surface.

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

#### Scenario: [TARGET-STATE] No runtime MCP tool reaches either write function directly
- **WHEN** the registered MCP tool surface for any butler is enumerated
- **THEN** no tool resolves, directly or through a thin wrapper, to `write_agents_md` or `append_agents_md`

#### Scenario: [TARGET-STATE] A future runtime-notes tool routes through propose_amendment
- **WHEN** a future capability adds an MCP tool that lets a runtime session suggest agent-notes content
- **THEN** that tool calls `propose_amendment` with `target_kind="roster_prompt"` (or the appropriate roster target kind) rather than calling `write_agents_md`/`append_agents_md` directly
- **AND** the content only reaches `AGENTS.md` after an explicit accept act through the corresponding target adapter
