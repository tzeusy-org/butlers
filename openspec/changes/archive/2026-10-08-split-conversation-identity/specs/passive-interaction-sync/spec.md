## MODIFIED Requirements

### Requirement: Message-based interaction detection

The relationship butler SHALL run a scheduled job (`interaction_sync`) that scans `switchboard.message_inbox` for recent messages on user-to-person channels, groups them by chat context, and creates direction-aware, group-size-annotated interaction facts for resolved contacts.

#### Scenario: Group-aware pre-grouping by chat identity
- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `switchboard.message_inbox` grouped by `(chat identity, source_channel, DATE(received_at))` instead of `(source_sender_identity, source_channel, DATE(received_at))`
- **AND** the chat identity SHALL be `request_context->>'external_conversation_id'`, falling back to `request_context->>'source_thread_identity'` for rows without it and then to `source_sender_identity`
- **AND** consecutive messages in one Telegram user-client chat SHALL form one chat group even though each carries a distinct per-message `source_thread_identity` reply target
- **AND** it SHALL collect the DISTINCT sender identities per chat per day from `request_context->'source_sender_identities'` when present, falling back to the scalar `source_sender_identity` otherwise
- **AND** it SHALL skip messages where `request_context->>'interaction_eligible'` is `'false'`

#### Scenario: Batch envelopes carry per-sender identities
- **WHEN** a connector submits a buffered/batch envelope covering several senders
- **THEN** `sender.identity` MUST remain the collapsed sentinel `'multiple'` for backward compatibility
- **AND** the ingest path MUST persist the real per-sender identities to `request_context.source_sender_identities` (a JSON array, derived from `sender.participants`)
- **AND** it MUST persist `sender.owner_sender_id` to `request_context.owner_sender_identity` when the connector reports one
- **AND** `interaction_sync` MUST NOT treat the literal `'multiple'` (or `'unknown'`) as a sender identity
- **AND** single-message envelopes MUST omit both keys, keeping `request_context` unchanged

#### Scenario: Message count is not inflated by the sender fan-out
- **WHEN** the grouping query expands each inbox row to one row per (message, sender) pair
- **THEN** the `message_count` recorded in fact metadata MUST count DISTINCT inbox rows, not fanned-out pairs

#### Scenario: Participant count gate
- **WHEN** the interaction_sync job processes a chat group
- **THEN** it SHALL read `participant_count` from `request_context` if available
- **AND** it SHALL fall back to COUNT(DISTINCT source_sender_identity) in the group if `participant_count` is absent
- **AND** if the resolved participant count exceeds 20, the entire chat group MUST be skipped
- **AND** for DM chats (only one non-owner sender), `group_size` MUST be 1

#### Scenario: Connector-reported owner identity outranks role lookup
- **WHEN** `request_context.owner_sender_identity` is present for a chat group
- **THEN** owner presence SHALL be determined by testing that identity for membership in the group's sender set
- **AND** that sender MUST be excluded from contact resolution, counting as `skipped_owner`
- **AND** this MUST hold even when the owner entity carries no identifier fact for the channel (the normal case for WhatsApp, where resolution runs through phone numbers)
- **AND** when the key is absent, the job SHALL fall back to role-based detection via `public.entities.roles`

#### Scenario: Direction detection from owner presence
- **WHEN** the interaction_sync job processes senders in a chat group
- **THEN** it SHALL partition senders into owner and non-owner sets
- **AND** if the owner sent at least one message in the chat on that day, non-owner contacts SHALL receive an outgoing interaction fact (direction='outgoing')
- **AND** non-owner contacts SHALL always receive an incoming interaction fact (direction='incoming') for their own messages
- **AND** the owner's own sender_identity MUST be excluded from contact resolution (no self-interaction)

#### Scenario: Outgoing deduplication via hour offset
- **WHEN** the interaction_sync job creates both incoming and outgoing facts for the same contact on the same day
- **THEN** incoming facts MUST use the existing channel hour offsets (telegram=0, whatsapp=1, email=2)
- **AND** outgoing facts MUST use offset +12 (telegram=12, whatsapp=13, email=14)
- **AND** this MUST prevent collision under the existing `interaction_log()` deduplication contract

#### Scenario: Group size in fact metadata
- **WHEN** the interaction_sync job creates an interaction fact for a contact in a group chat
- **THEN** the fact's metadata MUST include `group_size` equal to the participant count of the chat
- **AND** DM interactions MUST omit `group_size` or set it to 1

#### Scenario: Detect Telegram user client conversations
- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `switchboard.message_inbox` for messages where `request_context->>'source_channel'` is `'telegram_user_client'` and `received_at` is within the scan window

#### Scenario: Detect WhatsApp user client conversations
- **WHEN** `interaction_sync` runs
- **THEN** it SHALL apply the same scan-and-resolve logic for messages where `request_context->>'source_channel'` is `'whatsapp_user_client'`

#### Scenario: Detect email conversations
- **WHEN** `interaction_sync` runs
- **THEN** it SHALL apply the same scan-and-resolve logic for messages where `request_context->>'source_channel'` is `'email'`
- **AND** sender resolution SHALL match the sender email address against `relationship.entity_facts` rows with `predicate = 'has-email'`

#### Scenario: Interaction fact creation
- **WHEN** a (entity_id, date, channel, direction) group is resolved
- **THEN** the job SHALL call `interaction_log()` with:
  - `entity_id` = the resolved entity UUID
  - `type` = the source channel name (e.g., `'telegram_user_client'`)
  - `direction` = `'incoming'` or `'outgoing'` as determined by owner presence
  - `occurred_at` = the date with direction-appropriate hour offset
  - `metadata` = `{"source": "interaction_sync", "message_count": N, "group_size": G}`

#### Scenario: Unresolved senders are skipped
- **WHEN** a sender identity does not resolve to an active `relationship.entity_facts` triple for the expected predicate
- **THEN** the job SHALL skip that sender without error
- **AND** it SHALL increment a `skipped_unresolved` counter in the return stats

#### Scenario: Sender resolution uses the canonical channel resolver
- **WHEN** the job resolves sender identities to entities
- **THEN** it SHALL use the shared channel resolver (`resolve_contacts_by_channel_bulk`) rather than an exact-equality triple lookup
- **AND** it MUST therefore honour the canonical `telegram:<bare>` handle prefix and the WhatsApp JID → phone cross-reference

#### Scenario: A total resolution failure is not reported as an empty result
- **WHEN** the job has a non-empty set of sender identities to resolve and resolves none of them
- **THEN** it MUST NOT report a successful zero-fact run
- **AND** it SHALL increment `errors` and set `resolution_degraded` to `true` in the return stats
- **AND** the rationale is that the shared resolver is fail-open by contract (a database error yields all-unresolved rather than raising), which is otherwise indistinguishable from "none of these senders is a known contact"

#### Scenario: Owner messages are excluded from contact resolution
- **WHEN** the resolved entity has role `'owner'` in `public.entities.roles`
- **THEN** the job SHALL skip that entity for contact resolution (no self-interaction)
- **AND** the owner's presence as a sender SHALL be used solely to determine direction for other participants
