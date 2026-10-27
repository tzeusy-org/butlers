## MODIFIED Requirements

### Requirement: CLAUDE.md as System Prompt Entry Point
Domain `CLAUDE.md` SHALL be the entry point for versioned domain guidance, not the system-prompt
root of a goal invocation. A lead's governing system prompt SHALL come from the versioned lead
policy and owner profile; a child's SHALL come from its brief under RFC0038 D9/D15. Domain daemons
SHALL NOT compose reasoning-session prompts. Other domain content SHALL be retrieved through
stamped guidance/context/domain-read capabilities, not injected as an extra system-prompt source.
Domain `CLAUDE.md` SHALL contain only `@AGENTS.md`; domain `AGENTS.md` SHALL begin with
`@../shared/AGENTS.md`. Shared core SHALL resolve required references before returning versioned
guidance, with canonical-roster confinement and cycle/missing/read-failure bounds. Staffer
participation SHALL follow its explicit infrastructure and concrete staffer contracts, including
QA's approved opt-out. A required missing, blank, escaping, cyclic or unreadable guidance graph
SHALL fail content-blind without partial text, unresolved syntax, generated identity or DB fallback.
Historical `roster_overlay`, `precutover_legacy_hold` and `legacy_full_replacement` records SHALL
retain truthful provenance but SHALL NOT select a goal prompt or re-enable domain reasoning.
Owner acknowledgement and old rollback configuration SHALL NOT create a D15 exception. The D18
engineering-executor case SHALL remain distinct from ordinary staffer goals and domain daemons.

ID: REQ-butler-base-spec-001
Source: heart-and-soul/vision.md Rules 5 and 6; specify-roster-identity-owner-operations-overlay design D1-D3
Scope: v1-mandatory

#### Scenario: System prompt composition
- **WHEN** the spawner invokes a runtime instance
- **THEN** the lead uses lead policy and owner profile with D15 fenced input, guidance index and recovery ledger, and the child uses its brief and governing guidance references
- **AND** domain CLAUDE.md, mutable operations text and historical selectors never replace or precede those governing sources as another system prompt
- **AND** domain daemons do not compose or spawn reasoning-session prompts

#### Scenario: Interactive response mode
- **WHEN** a runtime instance receives a REQUEST CONTEXT JSON block with a `source_channel` field (e.g., `telegram_bot`, `email`)
- **THEN** the lead policy governs interactive response mode
- **AND** selects from response styles: React (emoji only), Affirm (acknowledgment), Follow-up (clarifying question), Answer (substantive response), or React+Reply (emoji + response)
- **AND** a lead does not inherit a domain daemon's session prompt

#### Scenario: CLAUDE.md delegates to AGENTS.md via file reference
- **WHEN** a butler's `CLAUDE.md` is authored
- **THEN** CLAUDE.md contains `@AGENTS.md` as its sole content and shared core resolves it before returning versioned guidance
- **AND** no runtime adapter performs reference expansion
- **AND** `<!-- @include path -->` remains the distinct non-recursive HTML-include contract

#### Scenario: AGENTS.md composes shared instructions
- **WHEN** a butler's `AGENTS.md` is authored
- **THEN** its first line is `@../shared/AGENTS.md` and resolution returns shared instructions before the domain-specific remainder
- **AND** Travel is a conforming domain butler, not an exception
- **AND** this shared guidance is not automatically injected into a goal system prompt

#### Scenario: Invalid roster identity blocks invocation
- **WHEN** a required `CLAUDE.md -> AGENTS.md` guidance graph is missing, blank, cyclic, escaping or has an unresolved/unreadable required bare reference
- **THEN** the server rejects the required guidance load and a child requiring it cannot start with a substitute identity
- **AND** no owner overlay, generated default or database row rescues the invalid graph
- **AND** evidence contains only the agent and fixed failure category, not prompt content

#### Scenario: Legacy rollback mode is an explicit temporary exception
- **WHEN** a historical record or owner request names `legacy_full_replacement`, including an acknowledged request within the old rollback window
- **THEN** it is labeled as historical identity-replacing selection and cannot select a goal prompt or domain reasoning session
- **AND** any operational restore requires separate authorization, preserves history and grants, and creates no permanent or per-agent D15 exception

#### Scenario: Existing agent begins in pre-cutover compatibility hold
- **WHEN** migration history contains an existing agent's seeded `precutover_legacy_hold`
- **THEN** the record remains historical unreviewed state without fabricated owner selection
- **AND** no goal runtime interprets it as a live selector, permits hold re-entry or assigns a new agent to that hold
- **AND** later authorized migration preserves the record rather than silently activating an overlay
