## ADDED Requirements

### Requirement: Local-only private-content purpose lane

Model selection and same-tier failover SHALL treat a dispatch as `private_content` only from trusted
WhatsApp or Telegram source context. A candidate is owner-local only when its catalog row uses the
OpenCode runtime, its canonical model ID begins `ollama/`, and the exact provider origin captured for
that dispatch is loopback or the RFC 0008 owner-local `ollama` Tailnet service. Missing, malformed,
unreadable, or other provider configuration SHALL NOT establish locality, and the configuration
accepted by the gate SHALL be the same configuration passed to the adapter. A non-local selection
MAY proceed only when one database snapshot proves that the current operator routing rule still
exists at the captured revision, explicitly matches `purpose=private_content`, explicitly targets
that model, and has successful owner audit evidence at least as new as that live revision.

#### Scenario: Local model wins private-content discretion
- **WHEN** a WhatsApp or Telegram discretion call has both local and higher-priority non-local candidates
- **THEN** the local `ollama/` candidate is invoked
- **AND** the non-local candidate is not initialized or called

#### Scenario: Only remote model is available
- **WHEN** a private-content call has no eligible local model and no current audited remote override
- **THEN** routing refuses before provider setup or invocation
- **AND** it records bounded `private_content_remote_refused` evidence without prompt, system prompt, message, sender, recipient, or thread content

#### Scenario: Explicit current audited override
- **WHEN** the first matching operator rule explicitly matches `purpose=private_content`, targets the selected remote model, and has current successful audit evidence
- **THEN** routing may invoke that model
- **AND** it records `audited_remote_override` with the rule identifier and purpose lane but no private content

#### Scenario: Stale or unverifiable override fails closed
- **WHEN** an old audit predates the current rule revision, the selected rule is updated or deleted before authorization, or audit evidence cannot be read
- **THEN** the remote model remains refused

#### Scenario: Ollama-shaped metadata is not locality proof
- **WHEN** an `ollama/` catalog entry uses a non-OpenCode runtime or an unapproved remote endpoint
- **THEN** routing treats that entry as non-local and refuses it without a current audited override
- **AND** no adapter is initialized or called

#### Scenario: Captured owner-local origin is reused
- **WHEN** an OpenCode `ollama/` entry resolves through the RFC 0008 owner-local Ollama origin
- **THEN** routing passes the exact captured provider configuration to the adapter
- **AND** a concurrent provider-config change cannot redirect that dispatch

### Requirement: Content-blind purpose-lane evidence

Dispatch attempts and token-usage evidence SHALL carry a separate closed `purpose_lane` without
using a raw connector identity as a butler or purpose-lane label. Existing open-ended spend-purpose
fields retain their established meaning.

#### Scenario: Private discretion spend is attributed safely
- **WHEN** a WhatsApp or Telegram discretion adapter reports usage
- **THEN** the usage and dispatch evidence records `private_content`
- **AND** its grouping identity contains no phone, chat, sender, recipient, or thread identifier
