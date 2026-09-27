## ADDED Requirements

### Requirement: Effective system prompt receipt

Every newly admitted runtime session SHALL persist the exact effective system-prompt bytes supplied
to the runtime together with a deterministic SHA-256 digest, total UTF-8 byte count, and an ordered
provenance list naming every composition source and its present, shadowed, or unavailable
state. Provenance metadata SHALL contain no source content or absolute filesystem path, and legacy
session rows MAY remain without a receipt.

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

### Requirement: Content-blind session purpose lane

Every new session SHALL persist a closed `standard` or `private_content` purpose lane derived from
trusted trigger/channel context without inspecting prompt content. WhatsApp and Telegram sourced
sessions SHALL use `private_content`; unknown or unrelated sources SHALL use `standard`.

#### Scenario: Private routed session is labelled without content parsing
- **WHEN** trusted routing context names a WhatsApp or Telegram source
- **THEN** the session stores `purpose_lane=private_content`
- **AND** no sender, recipient, thread, message, or prompt value is stored in the lane
