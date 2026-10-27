## MODIFIED Requirements

### Requirement: Full Payload Shape
The `full_payload` JSONB column SHALL store envelope metadata sufficient to reconstruct an `ingest.v1` envelope shape. For errored rows it additionally retains the raw provider payload for full-fidelity replay; for filtered rows the raw payload is redacted per the Filtered-Content Privacy Tier requirement, so replay of filtered rows is best-effort (metadata plus bounded preview only).

#### Scenario: Payload contains envelope fields
- **WHEN** a filtered event is persisted
- **THEN** `full_payload` SHALL contain the keys: `source` (channel, provider, endpoint_identity), `event` (external_event_id, external_thread_id, observed_at, plus external_conversation_id and reply_target_ref when the producer has split conversation identity from reply targeting), `sender` (identity), `payload` (raw, normalized_text), and `control` (policy_tier)
- **AND** `schema_version` SHALL be omitted (always `ingest.v1` on replay)
- **AND** for rows with status `filtered`, `full_payload.payload.raw` SHALL be empty (`{}`) — the full raw provider payload is not retained

#### Scenario: Pre-split rows replay with split identity
- **WHEN** a stored row written before the conversation-identity split is replayed and its `event` lacks `external_conversation_id`
- **THEN** replay SHALL derive the key live ingress emits today: for `telegram_bot`, from the retained Telegram update (keeping a forum topic), else from the chat prefix of `external_thread_id`; for `telegram_user_client`, `telegram:<external_thread_id>`; for `whatsapp_user_client`, `whatsapp:<external_thread_id>`
- **AND** this adapter is bounded by filtered-event retention and SHALL be removed once no pre-split row can remain

#### Scenario: Payload for error status
- **WHEN** a message fails with status `error`
- **THEN** `full_payload` SHALL contain whatever envelope fields were available at the point of failure
- **AND** incomplete payloads are acceptable — replay of error-status events MAY fail again if the root cause is not fixed
