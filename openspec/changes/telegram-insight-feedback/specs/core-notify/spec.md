## MODIFIED Requirements

### Requirement: notify.v1 Envelope Schema

The notify envelope SHALL include `schema_version` ("notify.v1"), `origin_butler` (requesting butler's name), `delivery` (intent, channel, message, optional recipient/subject/emoji), and optional `request_context` for reply/react targeting.

#### Scenario: Send intent envelope
- **WHEN** `notify(channel="telegram", message="Hello", intent="send")` is called
- **THEN** a `notify.v1` envelope is constructed with `delivery.intent="send"`, `delivery.channel="telegram"`, and `delivery.message="Hello"`
- **AND** `origin_butler` matches the calling butler's name


#### Scenario: Production insight feedback survives the delivery envelope
- **WHEN** the broker supplies feedback_actions for an owner Telegram insight delivery
- **THEN** the production notify.v1 envelope SHALL preserve each candidate UUID and server-created timestamp as a typed feedback binding
- **AND** Messenger SHALL render one signed feedback row per binding through its existing owner-recipient delivery gate
- **AND** absent feedback bindings and non-Telegram delivery SHALL preserve prior behavior
