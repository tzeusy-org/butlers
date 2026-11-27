# Telegram Module Delta

## ADDED Requirements

### Requirement: Typing Indicator Dispatch for Goal Lifecycle

Under Messenger custody, the module SHALL use `sendChatAction` only for a destination that
resolves uniquely to the same verified owner as the admitted Telegram bot message. The initial
typing attempt SHALL be available independently of the later goal-lifecycle keepalive. Any later
keepalive SHALL track active goal holds and stop when the final eligible hold is released.

ID: REQ-module-telegram-005
Source: RFC 0038 D5 and D7; RFC 0017 section 2.7; bu-z0b0ah.4 acceptance 1, 2, and 5
Scope: v1-mandatory

#### Scenario: Initial typing uses the verified owner chat

- **WHEN** Messenger authorizes initial typing for a newly admitted eligible goal
- **THEN** the module requests `sendChatAction` with action `typing` for that chat

#### Scenario: Concurrent goal holds do not stack loops

- **WHEN** a second eligible goal acquires a typing hold for a chat that already has a hold
- **THEN** the existing four-second keepalive is reused

#### Scenario: Ending a goal releases its hold

- **WHEN** the goal lifecycle reports reply delivery, failure, Stop or lease lapse
- **THEN** the typing hold is released and refresh stops when no eligible hold remains

#### Scenario: Unparseable or unresolved destination

- **WHEN** the original message target cannot be parsed, the chat is shared, or its live owner
  binding is unavailable, ambiguous, or has changed
- **THEN** the module performs no provider action and starts no keepalive

#### Scenario: Typing API failure

- **WHEN** Telegram rejects an initial typing request or its outcome becomes uncertain
- **THEN** the module reports a fixed failure category without provider payload or exception
  details, the lead can start, and the initial request is not replayed

### Requirement: Receipt Acknowledgement Style Configuration

The module SHALL accept the authorized action selected by the control plane through Messenger.
It SHALL NOT read a second acknowledgement style or enable first receipt through module
credential configuration. The existing module user and bot credential settings keep their
current validation and resolution.

ID: REQ-module-telegram-006
Source: RFC 0038 D1 and D7; bu-z0b0ah.4 acceptance 7 and 9
Scope: v1-mandatory

#### Scenario: Ack style does not depend on credential configuration

- **WHEN** existing module user or bot credentials are configured
- **THEN** those settings neither enable owner acknowledgements nor change the selected style

#### Scenario: A missing transport client cannot enable an action

- **WHEN** an acknowledgement cannot use an authorized module transport
- **THEN** it has no provider side effect and cannot veto the admitted goal
