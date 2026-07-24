## ADDED Requirements

### Requirement: Delegated Return Wakes Are Not Briefing Contributions

A delegated-answer return wake SHALL be internal continuation work, not a
specialist briefing contribution. It SHALL not write or read a
briefing/daily/<date> state key, general.v_briefing_contributions, a combined
briefing state entry, or any briefing envelope. It SHALL not create a
daily_briefing_contribution schedule entry or deterministic briefing handler.

RFC 0010's read-only, batch aggregation exception SHALL not be reused for
delegation callbacks, task creation, answer lookup, or real-time cross-butler
coordination. The shared delegation ledger and Switchboard MCP route remain the
only permitted v1 cross-butler surfaces for this protocol.

#### Scenario: Valid delegated answer does not enter the briefing composer

- **WHEN** a valid delegate_answer callback creates or reconciles an
  asker-local delegate-return task
- **THEN** no briefing contribution or combined-briefing state SHALL be written
  or read as part of that callback
- **AND** no briefing envelope, composer input, or user-facing delivery SHALL
  be produced by the wake protocol

#### Scenario: Later bounded briefing producer stays independent

- **WHEN** a later deterministic briefing job uses delegate_ask to request
  factual domain context
- **THEN** it SHALL use the same Switchboard-mediated delegation contract
  without direct sibling-schema access
- **AND** the existence of a return task SHALL not authorize same-day composer,
  envelope, quiet-window, or owner-notification behavior
