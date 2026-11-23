# Telegram Bot Connector Delta

## MODIFIED Requirements

### Requirement: Lifecycle Reactions

Receipt and lifecycle acknowledgement SHALL be owned downstream by the goal runtime and
Messenger. This transport connector SHALL neither send nor track acknowledgement state nor read
the acknowledgement style. The existing authenticated Telegram message mapping retains the
original reply target as specified by `REQ-connector-telegram-bot-001`. Admission and Messenger
apply the owner gate and configured mode before any downstream provider action. The goal
acknowledgement contract takes effect only behind the separate goal-runtime switch.

ID: REQ-connector-telegram-bot-007
Source: RFC 0038 D7 and D11; bu-z0b0ah.4 acceptance 4 and 11; REQ-connector-telegram-bot-001
Scope: v1-mandatory

#### Scenario: Reaction emoji mapping

- **WHEN** an eligible owner Telegram bot goal uses a reaction-inclusive acknowledgement style
- **THEN** Messenger may apply eyes at first admission, thumbs-up after owner reply delivery, and
  alien on failure or partial settlement, as defined by the receipt contract

#### Scenario: Reaction API failure

- **WHEN** Telegram rejects a downstream reaction or its outcome is uncertain
- **THEN** the goal can proceed and the connector performs no additional action

#### Scenario: Reaction dispatch is gated by ack style

- **WHEN** the goal runtime's owner-channel acknowledgement style is `typing` or `none`
- **THEN** the acknowledgement applies no reaction

#### Scenario: Reaction dispatch is gated by owner-only filtering

- **WHEN** the sender or chat fails the receipt contract's independent owner checks
- **THEN** the acknowledgement applies no reaction, regardless of style

#### Scenario: Connector remains transport-only

- **WHEN** the connector accepts an update under either runtime setting
- **THEN** it retains transport evidence for inbox admission without emitting or tracking
  typing, reactions or progress
