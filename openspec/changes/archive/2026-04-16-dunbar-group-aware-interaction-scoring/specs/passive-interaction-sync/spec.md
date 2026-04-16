## MODIFIED Requirements

### Requirement: Message-based interaction detection
The relationship butler SHALL run a scheduled job (`interaction_sync`) that scans `switchboard.message_inbox` for recent messages on user-to-person channels, groups them by chat context, and creates direction-aware, group-size-annotated interaction facts for resolved contacts.

#### Scenario: Group-aware pre-grouping by chat identity
- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `switchboard.message_inbox` grouped by `(source_thread_identity, source_channel, DATE(received_at))` instead of `(source_sender_identity, source_channel, DATE(received_at))`
- **AND** it SHALL collect DISTINCT `source_sender_identity` values per chat per day
- **AND** it SHALL skip messages where `request_context->>'interaction_eligible'` is `'false'`

#### Scenario: Participant count gate
- **WHEN** the interaction_sync job processes a chat group
- **THEN** it SHALL read `participant_count` from `request_context` if available
- **AND** it SHALL fall back to COUNT(DISTINCT source_sender_identity) in the group if `participant_count` is absent
- **AND** if the resolved participant count exceeds 20, the entire chat group MUST be skipped
- **AND** for DM chats (only one non-owner sender), `group_size` MUST be 1

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
- **AND** sender resolution SHALL match the sender email address against `public.contact_info` entries of type `'email'`

#### Scenario: Interaction fact creation
- **WHEN** a (contact_id, date, channel, direction) group is resolved
- **THEN** the job SHALL call `interaction_log()` with:
  - `contact_id` = the resolved contact UUID
  - `type` = the source channel name (e.g., `'telegram_user_client'`)
  - `direction` = `'incoming'` or `'outgoing'` as determined by owner presence
  - `occurred_at` = the date with direction-appropriate hour offset
  - `metadata` = `{"source": "interaction_sync", "message_count": N, "group_size": G}`

#### Scenario: Unresolved senders are skipped
- **WHEN** `source_sender_identity` does not match any row in `public.contact_info` for the expected type
- **THEN** the job SHALL skip that sender without error
- **AND** it SHALL increment an `unresolved_senders` counter in the return stats

#### Scenario: Owner messages are excluded from contact resolution
- **WHEN** the resolved contact has role `'owner'` in `public.contacts.roles`
- **THEN** the job SHALL skip that contact for contact resolution (no self-interaction)
- **AND** the owner's presence as a sender SHALL be used solely to determine direction for other participants
