## MODIFIED Requirements

### Requirement: CLAUDE.md as System Prompt Entry Point
Each admitted roster agent's git-tracked `CLAUDE.md` SHALL be the mandatory root of the identity
segment for every runtime session spawned as that agent. `CLAUDE.md` SHALL delegate to
`AGENTS.md` through a bare `@AGENTS.md` reference resolved by the shared core-skills seam. For a
domain butler, `AGENTS.md` SHALL begin with `@../shared/AGENTS.md`; staffer participation in shared
domain instructions SHALL instead follow the explicit staffer-archetype and concrete staffer
contract. Mutable database text SHALL NOT replace or suppress the resolved roster identity.
After identity resolution, only the layers enumerated by `core-spawner / System Prompt Composition`
may join the final system prompt. A missing, blank, escaping, cyclic, or otherwise unresolved
required roster root SHALL block runtime invocation; no generic generated prompt, database row, or
runtime-adapter behavior may stand in for that identity.
The sole temporary exception SHALL be an explicitly owner-selected `legacy_full_replacement` mode
during the approved migration rollback window. That mode SHALL be represented as identity-replacing,
SHALL NOT be represented as roster-plus-overlay composition, and SHALL become unavailable only
through a later reviewed legacy-retirement change.

ID: REQ-butler-base-spec-001
Source: heart-and-soul/vision.md Rules 5 and 6; specify-roster-identity-owner-operations-overlay design D1-D3
Scope: v1-mandatory

#### Scenario: System prompt composition
- **WHEN** the spawner invokes a runtime instance for an admitted roster agent
- **THEN** the final system prompt begins with the fully resolved git-tracked roster identity segment
- **AND** any active owner-operations overlay and dynamic context follow it only in the closed order defined by `core-spawner`
- **AND** mutable or dynamic text never replaces, deletes, or precedes the roster identity

#### Scenario: Interactive response mode
- **WHEN** a runtime instance receives a REQUEST CONTEXT JSON block with a `source_channel` field (e.g., `telegram_bot`, `email`)
- **THEN** it engages interactive response mode as defined in the butler's CLAUDE.md
- **AND** selects from response styles: React (emoji only), Affirm (acknowledgment), Follow-up (clarifying question), Answer (substantive response), or React+Reply (emoji + response)

#### Scenario: CLAUDE.md delegates to AGENTS.md via file reference
- **WHEN** a roster agent's `CLAUDE.md` is authored
- **THEN** it contains `@AGENTS.md` as its sole content
- **AND** the shared core-skills resolver expands that reference before any runtime adapter is invoked
- **AND** the separate `<!-- @include path -->` directive remains governed by the non-recursive HTML-include contract

#### Scenario: AGENTS.md composes shared instructions
- **WHEN** a domain butler's `AGENTS.md` is authored or validated
- **THEN** its first line is `@../shared/AGENTS.md`
- **AND** the resolved identity contains the shared instructions before the domain-specific remainder
- **AND** Travel is evaluated as a conforming domain butler, not as an exception

#### Scenario: Invalid roster identity blocks invocation
- **WHEN** the required `CLAUDE.md -> AGENTS.md` identity graph is missing, blank, cyclic, escapes the roster root, or contains an unresolved required bare reference
- **THEN** prompt composition fails before a runtime adapter starts
- **AND** no owner overlay or generated default is used as substitute identity
- **AND** the failure evidence names only the agent and fixed failure category, not prompt content

#### Scenario: Legacy rollback mode is an explicit temporary exception
- **WHEN** an authenticated owner has selected `legacy_full_replacement` during the approved rollback window
- **THEN** the runtime may use the preserved compatibility selector for that agent
- **AND** owner projections identify the mode as replacing roster identity rather than as an overlay
- **AND** the exception supplies no permanent doctrine amendment or default for another agent
