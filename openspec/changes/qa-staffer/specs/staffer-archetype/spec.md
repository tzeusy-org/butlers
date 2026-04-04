# Staffer Archetype

## MODIFIED Requirements

### Requirement: Extensibility for Future Staffers
The staffer archetype SHALL accommodate future infrastructure agents beyond switchboard and messenger without requiring architectural changes.

#### Scenario: Adding a new staffer to the roster
- **WHEN** a new infrastructure agent is needed (e.g., QA staffer for log inspection, issue triage, and automated PR creation)
- **THEN** it is created following the same roster conventions as any agent: `roster/{staffer-name}/` with `butler.toml` (with `type = "staffer"`), `MANIFESTO.md` (infrastructure contract), `CLAUDE.md`, `AGENTS.md`
- **AND** the daemon engine, module system, and tool composition model work without modification
- **AND** staffer-specific behaviors (routing exclusion, briefing exclusion) are automatically applied based on `config.type`

#### Scenario: Future staffers may have unique permissions
- **WHEN** a future staffer has different access requirements (e.g., QA staffer needs read-only cross-butler log access plus codebase R/W)
- **THEN** the `[butler.permissions]` section accommodates this via scoped `cross_butler_access` lists
- **AND** additional permission dimensions MAY be added to the permissions section in future changes without modifying the core type system

#### Scenario: QA Staffer as concrete example
- **WHEN** the QA staffer is added to the roster at `roster/qa/`
- **THEN** it validates the extensibility contract: `type = "staffer"`, `cross_butler_access = ["*"]`, infrastructure contract MANIFESTO.md, scheduler-driven patrol loop, all running on the standard daemon engine
- **AND** no changes to the core staffer archetype code were required
