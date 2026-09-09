# Telegram Bot Connector — Delta

## MODIFIED Requirements

### Requirement: Lifecycle Reactions

Lifecycle reactions SHALL be applied downstream by the pipeline (via `core/channel_reactions.py`
and the Messenger butler's telegram tools), not by this transport-only connector. The connector
SHALL neither send nor track reactions. The emoji mapping below documents the cross-component
behavior for reference. Reaction dispatch — and, alongside it, typing-indicator dispatch — is
gated by the `telegram-receipt-acknowledgement` capability's configured ack style and owner-only
filter: this requirement's mapping and failure-handling scenarios describe what happens when
dispatch occurs, not an unconditional guarantee that it occurs for every message.

#### Scenario: Reaction emoji mapping

- **WHEN** an ingested message progresses through the pipeline and the configured ack style
  includes `reaction` for that message
- **THEN** reactions are applied:
  - In-progress: eyes emoji (`:eye:`)
  - Success: checkmark emoji (`:done:`)
  - Failure: alien emoji (`:space invader:`)

#### Scenario: Reaction API failure

- **WHEN** Telegram rejects a reaction (e.g., unsupported chat type, expected 400 error)
- **THEN** processing continues and a debug log is emitted (non-fatal — reactions are best-effort)

#### Scenario: Reaction dispatch is gated by ack style

- **WHEN** the `telegram-receipt-acknowledgement` capability's configured ack style for a butler is
  `typing` or `none`
- **THEN** no reaction is applied for that butler's ingested messages, regardless of the emoji
  mapping above

#### Scenario: Reaction dispatch is gated by owner-only filtering

- **WHEN** an ingested message's sender does not resolve to the owner per the
  `telegram-receipt-acknowledgement` capability's Owner-Only Acknowledgement Filtering requirement
- **THEN** no reaction is applied for that message, regardless of the configured ack style
