## ADDED Requirements

### Requirement: Recursive Bare File-Reference Resolution
The shared core-skills prompt loader SHALL resolve a line containing only `@<relative-path>.md`
relative to the file that contains it, recursively, before the prompt reaches any runtime adapter.
Every resolved target SHALL remain within the canonical roster root. Bare-reference resolution SHALL
remain distinct from the existing roster-relative, non-recursive HTML `@include` contract.
When a bare reference participates in a required roster identity graph, a missing target, roster
escape, cycle, or read failure SHALL be reported as invalid identity and SHALL NOT be preserved as
literal prompt text. Owner-operations overlay content SHALL be treated as literal and SHALL NOT be
processed by either include mechanism.

ID: REQ-core-skills-001
Source: specify-roster-identity-owner-operations-overlay design D1-D2; [Observed] src/butlers/core/skills.py
Scope: v1-mandatory

#### Scenario: Nested roster references resolve in shared core
- **WHEN** `CLAUDE.md` contains only `@AGENTS.md` and that file begins with `@../shared/AGENTS.md`
- **THEN** shared core resolves both references relative to their containing files
- **AND** the resulting identity contains shared instructions followed by agent-specific instructions
- **AND** no runtime adapter performs another reference-expansion pass

#### Scenario: Bare reference cannot escape the roster
- **WHEN** a bare reference resolves outside the canonical roster root
- **THEN** identity resolution fails before runtime invocation
- **AND** no content from the escaped path enters the prompt or failure evidence

#### Scenario: Bare reference cycle is invalid identity
- **WHEN** recursive bare references revisit a file already present in the resolution chain
- **THEN** identity resolution fails with a fixed cycle category
- **AND** no partially resolved identity is passed to a runtime

#### Scenario: Missing required bare target is invalid identity
- **WHEN** a required bare reference names a file that is missing or unreadable
- **THEN** identity resolution fails before runtime invocation
- **AND** neither literal unresolved syntax nor a generated fallback becomes the identity

#### Scenario: HTML includes retain their separate contract
- **WHEN** a roster identity file contains an HTML `<!-- @include path.md -->` directive
- **THEN** the existing roster-relative, traversal-safe, non-recursive HTML-include behavior applies
- **AND** recursive bare-reference behavior does not make the HTML include recursive

#### Scenario: Overlay include-like text is literal
- **WHEN** a valid owner-operations overlay contains a bare reference or HTML-include-looking line
- **THEN** the line remains byte-identical overlay content
- **AND** no filesystem read is attempted on its behalf
