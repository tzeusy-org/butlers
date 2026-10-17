# Draft candidate: private rolling-summary custody

Status: NOT ADOPTED. Future additions require exact review/adoption and owning integrations. Full current requirements and prior scenarios are retained.

## MODIFIED Requirements

### Requirement: Effective system prompt receipt

Every newly admitted runtime session SHALL persist the exact effective system-prompt bytes supplied
to the runtime together with a deterministic SHA-256 digest, total UTF-8 byte count, and an ordered
provenance list naming every composition source and its present, shadowed, or unavailable
state. Provenance metadata SHALL contain no source content or absolute filesystem path, and legacy
session rows MAY remain without a receipt.

After independent exact adoption and all owned custody integrations, for a private-summary turn only, private rolling history SHALL be a separate untrusted user overlay, never effective_system_prompt or its persisted provenance/digest/bytes. Exact receipt still describes actual system bytes supplied; no receipt falsification hides the overlay. Private binding/custody metadata stays outside client prompt/session projections. Owning spawner/session integration preserves the boundary for every adapter.

ID: REQ-core-sessions-001
Source: core-sessions § Effective system prompt receipt; private summary custody R1; RFC 0003/0007 unchanged wire/source identity
Scope: proposed-owner-gated


#### Scenario: Fixed roster tree produces a deterministic receipt
- **WHEN** the same synthetic roster tree and dynamic composition inputs are composed twice
- **THEN** both receipts contain the same effective prompt, digest, byte count, source order, per-source byte counts, and per-source digests

#### Scenario: Optional source is unavailable
- **WHEN** an optional composition source cannot be read and existing availability policy permits the session to continue
- **THEN** the session receipt names that source as unavailable without content
- **AND** the stored effective prompt and digest describe exactly the bytes supplied to the runtime

#### Scenario: Receipt construction fails before invocation
- **WHEN** the effective bytes cannot be digested or persisted atomically with session creation
- **THEN** the runtime adapter is not invoked
- **AND** no unreceipted new session is presented as executed

#### Scenario: Exact system receipt remains derivative-free

- **WHEN** private rolling history is supplied as runtime user context
- **THEN** stored system prompt/digest/bytes/provenance exactly match the derivative-free system input
- **AND** the protected system-prompt door cannot resolve/return overlay or private binding

## ADDED Requirements

### Requirement: Private Runtime Overlay Custody

After exact review/adoption and owning integrations, the session domain SHALL distinguish durable safe base prompt from leased transient private user context. No protected derivative text/extract/provenance/dereferenceable binding may persist in prompt, effective_system_prompt, tools/result/error/read models/process/trace/audit or automatic memory input. Private bindings stay in final private custody domain; existing request/ingestion/turn identifiers are not summary-access capabilities. Private content-blind composition/use receipts SHALL cover the complete actual user input: base/overlay/effective-user-prompt digests and exact byte/codepoint counts, composition version and order/framing, exact persisted system-receipt linkage, and canonical owner/thread/message/request/attempt/generation/source/custody-epoch/lease/runtime-mode identity. They SHALL be admitted before adapter invocation in private custody, without storing prompt/derivative bytes, public dereference capability or a false claim that stored base is the complete runtime input. The adapter receives the full admitted composition; a digest or marker alone cannot fulfill useful summary execution. These receipts/leases follow source/thread revocation and disposal; final deletion removes them and no receipt can reconstruct retired text.

Writes and reads of potential copies SHALL honor custody epoch and admitted raw/base projection. Revoked/unknown capture state is not ordinary safe result evidence and cannot be repaired by reference hydration. Normal raw current message, admitted source-backed answer, actual executed-effect audit, accounting/error/cancellation/provenance and exact system receipt remain. Cross-session search/QA/log/copy doors and private_content labels grant no overlay access. Runtime/provider/adapter/resume/temp-file behavior are owned release gates; deployed compliance is UNKNOWN.

ID: REQ-core-sessions-002
Source: R1 final_prompt/session_create and DTO readers; core-spawner lifecycle; original clauses 1–8
Scope: proposed-owner-gated

#### Scenario: Stored session prompt cannot expose late-bound history

- **WHEN** session_create precedes an eligible private-context invocation
- **THEN** sessions.prompt contains only safe base prompt with honest omission framing, never late-bound derivative
- **AND** list/detail/summary/search models cannot disclose overlay/binding; exact system receipt stays independently true
- **AND** the private content-blind receipt binds the complete actual base-plus-overlay-plus-current-message user input and exact system receipt, with truthful order/counts/source/attempt/epoch provenance
- **AND** the actual adapter receives that complete input; safe stored base or digest alone is not full intended execution

#### Scenario: Output and tools are not an automatic safe-copy exception

- **WHEN** output/tool/error/process fields contain protected payload or admission is unknown
- **THEN** copy is ineligible for session_complete/client/episode/tool or notification egress
- **AND** ordinary admitted raw-source answers and actual executed-effect audit remain independent

#### Scenario: Crash or deletion cannot restore a session overlay

- **WHEN** source/thread revocation races restart/retry/completion
- **THEN** durable epoch fences old lease and transient/captured copies are cleared without recovery-row copies
- **AND** only safe lifecycle evidence survives; no implicit generation/native resume repairs it

#### Scenario: Provider-native continuity is not private custody proof

- **WHEN** runtime would reuse transcript/cache/provider handle containing earlier private context
- **THEN** overlay use is refused unless exact verified mode meets every-holder disposal
- **AND** current turn/Stop ambiguity retain their own spine and marked raw-tail fallback preserves service
