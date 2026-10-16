## MODIFIED Requirements

### Requirement: Bounded Explicit Insight Feedback
The broker SHALL expose useful, not-now, and never owner-feedback verbs without
adding another global verbosity control. Feedback attribution SHALL be derived by
the server and the persisted evidence SHALL remain content-blind.

#### Scenario: Useful reverses a family hold
- **WHEN** the owner marks an insight useful after snoozing or muting its family
- **THEN** the family cooldown SHALL be removed and its category weight SHALL be eligible to return to baseline

#### Scenario: Not-now is bounded
- **WHEN** the owner marks an insight not-now
- **THEN** a future `snooze_until` SHALL be required and the family SHALL resume after that instant

#### Scenario: Never remains reversible
- **WHEN** the owner marks an insight never
- **THEN** the family SHALL receive an indefinite cooldown
- **AND** a later useful verdict SHALL reverse it

#### Scenario: Bounded doors share one behavior
- **WHEN** feedback is invoked through Switchboard MCP, REST, delivered-message action metadata, or the dashboard insight row
- **THEN** every door SHALL call the same useful, not-now, or never behavior
- **AND** no door SHALL accept a caller-asserted actor


#### Scenario: Telegram feedback is authenticated and reversible per insight
- **WHEN** the primary owner receives one insight or a digest over Telegram
- **THEN** each insight SHALL have Useful, Not now and Never actions signed in the ifb1 domain and bound to that candidate's server-created timestamp
- **AND** a tap SHALL require the primary-owner sender and chat, a valid signature, and a connector credential accepted only on the exact callback-state GET and telegram-feedback POST routes
- **AND** the server SHALL record owner@telegram attribution, derive a 72-hour not-now snooze, and call the shared broker feedback behavior
- **AND** repeating the latest verdict SHALL append no feedback row, while another verdict SHALL remain reversible
- **AND** the message SHALL mark the recorded verdict and retain all other actions, including other digest insights
- **AND** transient API failures SHALL retain the keyboard without a feedback write, while a purged insight SHALL lose its own actions
