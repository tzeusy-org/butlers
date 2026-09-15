# OpenSpec Spec File Format Reference

## File Location
`openspec/specs/{spec-name}/spec.md`

## Structure

```markdown
# {Main Title}

## Purpose
{1-3 sentence description of scope and value}

## ADDED Requirements

### Requirement: {Requirement Title}
{1-2 sentence description}

#### Scenario: {Scenario Name}
- **WHEN** {condition/trigger}
- **THEN** {expected outcome}
- **AND** {additional outcome if applicable}
```

## Heading Hierarchy
- H1: Main spec title (one per file)
- H2: Always "Purpose" and "ADDED Requirements"
- H3: Requirement (e.g., `### Requirement: Configuration Loading`)
- H4: Scenario (e.g., `#### Scenario: Valid config loads successfully`)

## WHEN/THEN/AND Rules
- Bullet list format (`- `)
- Bold keywords: `**WHEN**`, `**THEN**`, `**AND**`
- 1 WHEN, 1 THEN, 0-4 AND lines per scenario
- Describe *observable behavior*, not implementation details

## Formatting Conventions
| Pattern | Usage | Example |
|---------|-------|---------|
| Backticks | Code identifiers, config keys | `butler.toml`, `load_config()` |
| Bold | Keywords and type names | **WHEN**, **ButlerConfig** |
| Quotes | String values, enum options | `"suggest"`, `"fail"` |

## Requirement Title Patterns
- State: "Requirement: Configuration Loading and Validation"
- Tools: "Requirement: Calendar Event CRUD Tools"
- Lifecycle: "Requirement: Startup Phase Sequence"
- Policy: "Requirement: Conflict Detection and Resolution"

## Scenario Naming
- Success: "Scenario: Valid config loads successfully"
- Error: "Scenario: Missing required field blocks startup"
- Edge: "Scenario: Unresolved env var blocks startup"

## Special Tags
`[TARGET-STATE]` marks aspirational/unimplemented requirements:
```markdown
### Requirement: [TARGET-STATE] Calendar Sync and Projection
```

## Spec Naming Conventions
- Butler specs: `butler-{name}`
- Core specs: `core-{component}`
- Module specs: `module-{name}`
- Connector specs: `connector-{name}`
- Dashboard specs: `dashboard-{area}`

## Density Guidelines
- Purpose: 2-3 sentences
- Requirements per spec: 8-15 typical
- Scenarios per requirement: 2-5 typical
- Focus on *spirit and intent* — describe what the system does for the user,
  not internal architecture or data structures
