## MODIFIED Requirements

### Requirement: System Prompt Composition
The goal runner SHALL compose lead input from lead policy, owner profile, fenced goal input,
domain/guidance index and recovery ledger summary under RFC0038 D15, and child input from the D9
brief and its governing guidance references. Domain daemons SHALL NOT compose reasoning-session
prompts. Domain CLAUDE.md and shared snippets SHALL be versioned guidance resolved by shared core;
other context SHALL arrive through stamped `context.search`, domain reads and `guidance.load`.
The old eight-layer prompt, automatic memory/settings/blind-spot/routing suffixes and situational
injection SHALL NOT remain an alternate goal prompt path. Required guidance failures SHALL NOT
substitute generated or DB-defined identity. Returned evidence SHALL preserve honest missing-source
and fail-closed disclosure without widening grants or making private custody reachable.
Every adapter SHALL receive byte-identical runner-composed input and SHALL NOT resolve includes,
reorder sources or add a system-prompt source. Existing declared shared-snippet order and exact
separators SHALL be preserved within loaded guidance, not appended as goal layers. Literal bounded
owner operations and their source/trust/version metadata SHALL NOT become automatic prompt input;
provenance or delimiters SHALL NOT be represented as semantic isolation or authority.
The separate D18/RFC0015 engineering executor SHALL retain its launching guidance, approved shared
snippets, anonymized payload and isolated shell/worktree contract, without situational injection;
it SHALL NOT exempt staffer triage goals or re-enable domain sessions. Historical legacy modes,
acknowledgements and deployment rollback deadlines SHALL confer no goal prompt-selection authority.
Unavailable, malformed, denied or expired legacy selection SHALL fail closed without content
disclosure, partial adapter input, silent legacy continuation or automatic overlay activation.

ID: REQ-core-spawner-004
Source: specify-roster-identity-owner-operations-overlay design D1-D2 and D7; heart-and-soul/vision.md Rule 5
Scope: v1-mandatory

#### Scenario: System prompt with includes
- **WHEN** shared core loads required domain guidance, or launching guidance for a separate D18 engineering executor, with valid bare references or an allowed HTML include
- **THEN** the server resolves it under the corresponding guidance contract before returning or handing off its declared bytes
- **AND** include-looking owner operations remain literal, and a goal does not use domain guidance as a replacement system prompt

#### Scenario: Context preamble injected when signals active
- **WHEN** active situational-context signals exist while the runner prepares an invocation
- **THEN** no situational-context preamble is injected into a goal or D18 executor prompt
- **AND** relevant context is retrieved only through an authorized stamped capability; lead policy/profile or child brief retains its governing role

#### Scenario: No context preamble when no signals
- **WHEN** no active situational-context signals exist
- **THEN** no context preamble or placeholder separator is added
- **AND** the prescribed lead/child inputs and any explicitly loaded guidance retain their defined bytes and order

#### Scenario: Context query failure does not block invocation
- **WHEN** a legacy situational-context query would fail during prompt preparation
- **THEN** invocation does not depend on that retired query and no implicit context query or preamble is added
- **AND** an authorized context-read failure is reported content-blind with honest source availability, without substituting a prompt source or widening authority

#### Scenario: Every allowed layer has stable precedence
- **WHEN** a lead or child invocation and its versioned guidance are prepared
- **THEN** D15 lead policy/profile and prescribed context or the D9 child brief determine the input, with deterministic declared bytes and separators
- **AND** shared guidance keeps BUTLER_SKILLS.md before MCP_LOGGING.md for declared participants
- **AND** settings such as timezone, locale, local date/time, week start, currency and measurement system may arrive through admitted context or authorized stamped reads, never automatic old eight-layer suffixes
- **AND** stored owner operations keep source/trust/version provenance without becoming another system-prompt source

#### Scenario: Overlay store failure preserves identity
- **WHEN** the operations store is unavailable, malformed or denied to a reader
- **THEN** no domain-spawner overlay query or fallback changes the goal's lead policy/profile or child brief
- **AND** no legacy row, generated prompt or cross-domain credential substitutes for the failed read
- **AND** authorized-read operational evidence is content-blind and reports the unavailable source honestly

#### Scenario: Unnamed prompt source blocks invocation
- **WHEN** composition is asked to inject a system-prompt source outside the prescribed lead/child inputs or the separate engineering-executor contract
- **THEN** the request fails before any runtime adapter starts
- **AND** no partial prompt is delivered

#### Scenario: Runtime adapters preserve composed bytes
- **WHEN** the same runner-composed lead/child input, or separately governed executor input, is dispatched through supported adapters
- **THEN** each receives byte-identical input
- **AND** adapter transport does not resolve roster files, reorder sources or append instructions

#### Scenario: Natural-language conflict is not reported as structural isolation
- **WHEN** owner operations conflict with versioned guidance in natural language
- **THEN** stored provenance and literal bytes remain truthful without automatic injection into a goal prompt
- **AND** no response, audit or test claims delimiters guarantee obedience, authenticate text, increase a grant or provide semantic isolation

#### Scenario: Legacy rollback uses the compatibility selector only
- **WHEN** historical mode state or a request names `legacy_full_replacement` during an old approved window
- **THEN** goal composition refuses that selector even with acknowledgement and an existing legacy head
- **AND** a separately authorized operational restore is not a second in-process goal mode
- **AND** history, least privilege and content-blind evidence remain intact, with no exception inherited by another agent

#### Scenario: Pre-cutover hold preserves existing selection without owner attribution
- **WHEN** historical migration evidence records `precutover_legacy_hold`
- **THEN** it preserves the fact of the old selection without claiming owner action or structural goal-prompt authority
- **AND** no goal invocation consumes the selector, no new agent is assigned the hold and no API permits re-entry
- **AND** leaving obsolete state does not rewrite its provenance or silently activate operations text

#### Scenario: Selected legacy rollback expires closed
- **WHEN** an obsolete rollback selector is presented with missing, malformed or expired deployment rollback configuration
- **THEN** selection fails before adapter invocation with fixed content-blind category `legacy_rollback_expired`
- **AND** it neither continues legacy replacement nor automatically selects `roster_overlay`
- **AND** even an open historical deadline cannot authorize a D15 exception; separately authorized safe exit preserves history
