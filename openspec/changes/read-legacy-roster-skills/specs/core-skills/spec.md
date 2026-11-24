## ADDED Requirements

### Requirement: Legacy Session Skill Reads

The legacy daemon MCP surface SHALL register `skill_read(name)` in the `infra` core tool group. It SHALL accept a kebab-case name and read its canonical UTF-8 `SKILL.md` in the serving butler's roster. An existing roster alias MAY resolve to the exact same name in the shared roster skill directory. Lookups SHALL stay within these definitions. Successful reads SHALL return the name, content and a SHA-256 digest of the returned UTF-8 bytes. Invalid names, missing and out-of-scope definitions, and unavailable content SHALL have stable typed errors. The capability SHALL be read-only.

The assembled ordinary roster prompts, scheduled prompts, and their skill instructions SHALL use MCP for skill retrieval and domain effects. Developer instructions that require shell, direct SQL, or direct HTTP SHALL live in developer documentation or engineering-only guidance. The goal-runtime guidance corpus keeps the version-bound behavior specified for `guidance.search` and `guidance.load`.

ID: REQ-core-skills-005
Source: RFC 0038 D2, D3, D18; bu-svqpzo.8.1
Scope: v1-mandatory

#### Scenario: A legacy session reads a local or linked shared skill
- **WHEN** a caller invokes `skill_read` for an exact name in this butler's canonical skill directory
- **THEN** the ordinary daemon MCP interface SHALL return the exact text and its SHA-256 digest when other core tool groups are disabled
- **AND** a roster symlink to the exact shared definition of the same name SHALL read that shared text

#### Scenario: The named skill cannot be supplied
- **WHEN** the requested name is malformed, missing, outside the serving roster definitions, or unreadable
- **THEN** the tool SHALL return `invalid_skill_name`, `skill_not_found`, or `skill_unavailable` according to the outcome
- **AND** it SHALL expose no alternate file content or physical server path

#### Scenario: A shipped model instruction needs skill guidance
- **WHEN** a butler's assembled system or scheduled prompt directs an ordinary session to load a skill
- **THEN** the instruction SHALL use `skill_read` and keep domain work on the MCP tool surface
