## ADDED Requirements

### Requirement: Composed prompt preview and roster drift truth

The existing butler Configuration prompt section SHALL show the currently composed prompt preview
rather than treating an absent database prompt row as absence of a system prompt. It SHALL compare
current roster-source digests with the latest executed prompt receipt and display exactly one of
`matches_git`, `drifted`, or `unknown`, with bounded roster-relative changed source names and no
claim about dynamic-layer equality.

#### Scenario: Composed prompt exists without a database row
- **WHEN** a known roster butler has no database prompt-history row but its roster composition is available
- **THEN** the Configuration section renders the composed prompt
- **AND** it does not render `No system prompt configured.`

#### Scenario: Roster source changes after execution
- **WHEN** a roster file in a synthetic fixture tree changes after the newest session receipt
- **THEN** the drift projection is `drifted`
- **AND** it names the changed roster-relative source and the comparison time without exposing other prompt content

#### Scenario: No executed receipt exists
- **WHEN** no session prompt receipt exists for the butler
- **THEN** the drift projection is `unknown`, never a green match

### Requirement: Prompt preview does not expand authoring authority

The composed preview and drift projection SHALL be read-only additions. They SHALL NOT add a new
prompt authoring surface, change existing prompt PUT semantics, reinterpret a legacy database row
as an owner overlay, or modify roster/manifesto content.

#### Scenario: Existing edit control remains bounded
- **WHEN** the composed preview is rendered
- **THEN** existing prompt edit behavior remains separate from receipt and drift metadata
- **AND** neither preview nor drift performs a prompt, roster, or manifesto write
