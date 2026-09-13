## ADDED Requirements

### Requirement: Local-only private-content purpose lane

Model selection and same-tier failover SHALL treat a dispatch as `private_content` only from trusted
WhatsApp or Telegram source context, and SHALL exclude every model whose canonical model ID does not
begin `ollama/` before provider setup or invocation. A non-local selection MAY proceed only when a
current operator routing rule explicitly matches `purpose=private_content`, explicitly targets that
model, and has successful audit evidence at least as new as the current rule revision.

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
- **WHEN** an old audit predates the current rule revision or audit evidence cannot be read
- **THEN** the remote model remains refused

### Requirement: Content-blind purpose-lane evidence

Dispatch attempts and token-usage evidence SHALL carry the closed purpose lane without using a raw
connector identity as a butler or purpose label.

#### Scenario: Private discretion spend is attributed safely
- **WHEN** a WhatsApp or Telegram discretion adapter reports usage
- **THEN** the usage and dispatch evidence records `private_content`
- **AND** its grouping identity contains no phone, chat, sender, recipient, or thread identifier
