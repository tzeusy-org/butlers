## ADDED Requirements

### Requirement: Participant count and chat type envelope enrichment
The Telegram user client connector SHALL include participant count and chat type metadata in submitted envelopes to enable downstream group-aware interaction scoring and cost gating.

#### Scenario: Envelope includes participant_count
- **WHEN** the connector builds an ingest.v1 envelope for a message
- **THEN** the `sender` section MUST include `participant_count` (integer) reflecting the number of participants in the chat
- **AND** the connector MUST query `chat.participants_count` via the Telethon client to obtain this value
- **AND** the value MUST be cached per chat_id with a TTL of 1 hour to avoid API rate limits

#### Scenario: Envelope includes chat_type
- **WHEN** the connector builds an ingest.v1 envelope for a message
- **THEN** the `sender` section MUST include `chat_type` with one of: `"private"` (DM), `"group"` (small group), `"supergroup"` (Telegram supergroup), `"channel"` (broadcast)
- **AND** the value MUST be derived from the Telethon chat entity type

#### Scenario: DM messages have participant_count of 2
- **WHEN** the connector processes a message from a private (DM) chat
- **THEN** `participant_count` MUST be 2 (the owner and the other party)
- **AND** `chat_type` MUST be `"private"`

### Requirement: Connector-level participant gating for interaction eligibility
The connector SHALL gate interaction-eligible processing for chats exceeding a configurable participant threshold.

#### Scenario: Large group gating threshold
- **WHEN** a chat has `participant_count` exceeding `max_interaction_group_size` (default: 20)
- **THEN** the connector MUST set `control.interaction_eligible = false` in the envelope
- **AND** the envelope MAY still be submitted for signal extraction and routing purposes

#### Scenario: Batch envelopes for large groups
- **WHEN** a batch conversation-history envelope is built for a chat exceeding the threshold
- **THEN** the connector MAY skip submission entirely or submit with `policy_tier = "metadata_only"`

#### Scenario: Gating telemetry
- **WHEN** the connector gates a message due to participant count
- **THEN** it SHOULD emit an OTel counter `butlers.telegram_user_client.interaction_gated` with attributes `{chat_type, participant_count_bucket}`

#### Scenario: Below-threshold chats are unaffected
- **WHEN** a chat has `participant_count` at or below `max_interaction_group_size`
- **THEN** `control.interaction_eligible` MUST default to `true` (or be omitted)
- **AND** the envelope MUST be submitted normally
