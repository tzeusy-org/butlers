## MODIFIED Requirements

### Requirement: System Prompt Composition
For every admitted roster agent, the spawner SHALL assemble the final system prompt from this closed
ordered list: (1) resolved roster identity, (2) shared `BUTLER_SKILLS.md` then `MCP_LOGGING.md`,
(3) an optional active owner-operations overlay, (4) general timezone/locale/date/time/week
instructions, (5) situational context, (6) blind-spot disclosure, (7) Switchboard routing
instructions for Switchboard only, and (8) memory context. No unnamed source SHALL be injected.
Every present adjacent layer SHALL be separated by exactly one blank line. An absent optional layer
SHALL contribute neither content nor separator. The owner overlay SHALL be enclosed in reserved
BEGIN/END markers identifying `source=owner_operations`, `trust=trusted_owner_input`, and its integer
version. Delimiters establish provenance only; the system SHALL NOT claim that they mechanically
prevent natural-language conflict with roster identity. Every runtime adapter SHALL receive the same
already composed bytes and SHALL NOT expand references, reorder layers, or add a system-prompt
source.
An unavailable owner-overlay store SHALL omit only the overlay, preserve roster identity, and emit a
content-blind operational warning. Existing availability behavior remains layer-specific: general
settings, situational context, Switchboard routing, and memory failures omit only their layer;
blind-spot failures retain their separate fail-closed disclosure contract. An invalid roster identity
or unknown source name SHALL block adapter invocation.

ID: REQ-core-spawner-004
Source: specify-roster-identity-owner-operations-overlay design D1-D2 and D7; heart-and-soul/vision.md Rule 5
Scope: v1-mandatory

#### Scenario: System prompt with includes
- **WHEN** the roster root contains valid bare references or an allowed HTML include
- **THEN** shared core resolves those references into the roster identity before the spawner appends another layer
- **AND** include syntax in an owner overlay remains literal

#### Scenario: Context preamble injected when signals active
- **WHEN** the spawner prepares an invocation and active situational-context signals exist
- **THEN** the context preamble appears after general settings and any active owner overlay
- **AND** it appears before blind-spot disclosure, Switchboard routing, and memory context

#### Scenario: No context preamble when no signals
- **WHEN** the spawner prepares an invocation and no active situational-context signals exist
- **THEN** no situational-context layer or separator is added
- **AND** all other present layers retain their defined order

#### Scenario: Context query failure does not block invocation
- **WHEN** the situational-context query fails after roster identity is valid
- **THEN** the failure is logged as a content-blind warning
- **AND** invocation proceeds without the situational-context layer
- **AND** roster identity and every other available authorized layer remain unchanged

#### Scenario: Every allowed layer has stable precedence
- **WHEN** all eight allowed layers are present for Switchboard
- **THEN** the final prompt contains them in the exact normative order with one blank line between adjacent layers
- **AND** the owner overlay carries its source, trust, and version markers

#### Scenario: Overlay store failure preserves identity
- **WHEN** the owner-overlay query is unavailable, malformed, or denied to the runtime reader
- **THEN** the spawner continues with the resolved roster identity and other authorized layers
- **AND** no legacy full-replacement row or generated prompt is substituted
- **AND** operational evidence contains no roster or overlay text

#### Scenario: Unnamed prompt source blocks invocation
- **WHEN** composition is asked to inject a system-prompt source outside the closed list
- **THEN** the request fails before any runtime adapter starts
- **AND** no partial prompt is delivered

#### Scenario: Runtime adapters preserve composed bytes
- **WHEN** the same composed prompt is dispatched through any supported runtime adapter
- **THEN** each adapter receives the byte-identical composed system prompt
- **AND** adapter-specific transport does not resolve roster files, reorder layers, or append instructions

#### Scenario: Natural-language conflict is not reported as structural isolation
- **WHEN** trusted owner overlay text contradicts a roster instruction in natural language
- **THEN** the structural prompt still contains both delimited sources in the defined order
- **AND** no response, audit event, or test result claims the delimiter guarantees model obedience or semantic isolation
