## ADDED Requirements

### Requirement: Recursive Bare File-Reference Resolution
The shared core-skills versioned-guidance loader SHALL resolve a line containing only `@<relative-path>.md`
relative to the file that contains it, recursively, before returning resolved guidance with its domain, name and version.
A lead SHALL use lead policy and owner profile and a child its brief; domain guidance loading
SHALL NOT compose a domain-daemon session prompt or widen the caller's grant.
Every resolved target SHALL remain within the canonical roster root. Bare-reference resolution SHALL
remain distinct from the existing roster-relative, non-recursive HTML `@include` contract.
When a bare reference participates in a required versioned-guidance graph, a missing target, roster
escape, cycle, or read failure SHALL be reported as invalid identity and SHALL NOT be preserved as
literal returned guidance. No partial graph or generic/DB fallback SHALL satisfy required guidance. Owner-operations overlay content SHALL be treated as literal and SHALL NOT be
processed by either include mechanism.

ID: REQ-core-skills-001
Source: RFC0038 D2/D9/D15; specify-roster-identity-owner-operations-overlay design D1-D2; historical source 63de6168 src/butlers/core/skills.py
Scope: v1-mandatory

#### Scenario: Nested roster references resolve in shared core
- **WHEN** CLAUDE.md contains only `@AGENTS.md` and that file begins with `@../shared/AGENTS.md`
- **THEN** shared core resolves both relative to their containing files and returns versioned guidance with shared instructions before agent-specific instructions
- **AND** no adapter repeats expansion and no domain guidance becomes an automatic goal system-prompt root

#### Scenario: Bare reference cannot escape the roster
- **WHEN** a bare reference resolves outside the canonical roster root
- **THEN** required guidance resolution fails before returning content or starting a child that requires it
- **AND** no escaped content enters guidance, prompt or failure evidence

#### Scenario: Bare reference cycle is invalid identity
- **WHEN** recursive bare references revisit a file already present on the active resolution chain
- **THEN** required guidance resolution stops with a fixed cycle category
- **AND** no partially resolved guidance or fabricated identity is returned or passed to a runtime

#### Scenario: Missing required bare target is invalid identity
- **WHEN** a required bare reference names a missing or unreadable file
- **THEN** its guidance load fails before required guidance is handed to an invocation
- **AND** neither literal unresolved syntax, a generated fallback nor a database row substitutes for it

#### Scenario: HTML includes retain their separate contract
- **WHEN** a guidance file contains an HTML `<!-- @include path.md -->` directive
- **THEN** the existing roster-relative, traversal-safe, non-recursive HTML-include behavior applies
- **AND** recursive bare-reference behavior does not make the HTML include recursive

#### Scenario: Overlay include-like text is literal
- **WHEN** a valid owner-operations overlay contains a bare reference or HTML-include-looking line
- **THEN** the line remains byte-identical overlay content
- **AND** no filesystem read is attempted on its behalf
