## MODIFIED Requirements

### Requirement: Message-based interaction detection

The relationship butler SHALL run a scheduled job (`interaction_sync`) that scans `switchboard.message_inbox` for recent messages on user-to-person channels, groups them by chat context, and creates direction-aware, group-size-annotated interaction facts for resolved contacts. For the versioned identity described below, actual persisted inbox receipt timestamps SHALL replace synthetic channel-hour timestamps; this does not claim knowledge of the sender's event clock.

ID: REQ-passive-interaction-sync-003
Source: Owner choice C for bu-8cdl1.14 (2026-10-20); RFC 0013 D4 proposed amendment in this change; current interaction_sync and interaction_log source
Scope: v1-mandatory

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
- **THEN** the new writer SHALL distinguish direction in the versioned source identity and SHALL use actual persisted receipt timestamps, without channel-hour or +12 offsets
- **AND** the retained scenario heading names the superseded mechanism, not a requirement to keep generating synthetic timestamps
- **AND** incoming and outgoing facts SHALL remain distinct even when their actual timestamps are identical; legacy offset rows SHALL remain readable and unchanged

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
- **WHEN** a (entity_id, UTC receipt date, channel, endpoint, chat identity, direction) group is resolved
- **THEN** the job SHALL call `interaction_log()` with:
  - `entity_id` = the resolved entity UUID
  - `type` = the source channel name (e.g., `'telegram_user_client'`)
  - `direction` = `'incoming'` or `'outgoing'` as determined by owner presence
  - `occurred_at` = the actual `received_at` of the deterministic contributing inbox anchor defined below
  - `metadata` = `{"source": "interaction_sync", "message_count": N, "group_size": G}` plus versioned identity, anchor and receipt-time provenance
- **AND** the writer SHALL preserve the existing server-produced expected-signal attestation and canonical contact-to-entity resolution; caller metadata MUST NOT confer this authority

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

## ADDED Requirements

### Requirement: Source-aware receipt-time interaction identity

The Relationship message-sync writer SHALL bind a versioned, source-aware identity to a real persisted observation time, independently of synthetic hours and replay time. This identity SHALL apply only to passive message sync, leaving calendar, manual interaction and group-tool contracts unchanged.

ID: REQ-passive-interaction-sync-004
Source: Owner choice C for bu-8cdl1.14 (2026-10-20); RFC 0013 D4 proposed amendment in this change; existing memory store_fact explicit temporal idempotency key
Scope: v1-mandatory

#### Scenario: Logical unit and actual timestamp

- **WHEN** the job forms an eligible chat/day group
- **THEN** its logical unit SHALL include tenant, Relationship scope, canonical entity UUID, source channel, server-attested endpoint, canonical chat identity, UTC receipt date and direction
- **AND** chat identity SHALL retain the existing external-conversation, thread, then sender fallback order, recording which identity kind was used
- **AND** the incoming anchor SHALL be the earliest retained inbox row carrying that contact's resolved sender identity; the outgoing anchor SHALL be the earliest retained owner row in that same group
- **AND** ties SHALL be ordered by persisted inbox UUID; a batch row MAY anchor several contacts or both directions, without inventing per-message times absent from that batch
- **AND** `occurred_at` SHALL be that anchor's timezone-aware `received_at`, normalized to UTC without changing the instant, with provenance identifying the inbox row and the `received_at` clock
- **AND** a source message/event identity, when actually persisted, SHALL be retained as evidence with its identity kind; a per-message reply target MUST NOT replace an available canonical conversation identity
- **AND** receipt time MUST NOT be described as original send time, recovered message time, or precise per-message time inside a batch

#### Scenario: Two channels and both directions on the same day

- **WHEN** the same resolved contact appears on two supported channels, or in incoming and outgoing observations on the same UTC receipt day
- **THEN** each channel/endpoint/chat/direction unit SHALL have its own identity even when timestamps coincide
- **AND** at most one incoming and one outgoing fact SHALL contribute per contact, channel, endpoint, chat and UTC receipt day; additional messages in that unit MUST NOT add scoring facts
- **AND** distinct chats SHALL remain distinct units rather than collide through contact/channel/day alone; this deliberately replaces RFC 0013 D4's broader per-contact/channel/day cap, without introducing per-message scoring
- **AND** direction weights, group-size division, participant eligibility, owner exclusion, reciprocal engagement and calendar scoring SHALL remain unchanged

#### Scenario: Replay and concurrency return the original binding

- **WHEN** an unchanged logical unit is retried, a checkpoint is replayed, later messages extend that unit, or concurrent workers submit it
- **THEN** the writer SHALL use the same deterministic namespaced explicit `store_fact()` idempotency key, built from an unambiguous encoding of the logical unit
- **AND** database-enforced temporal key uniqueness SHALL yield one fact; a pre-insert SELECT alone MUST NOT be treated as concurrency protection
- **AND** replay SHALL return the original fact ID and persisted `occurred_at`, without retiming it, duplicating its score, or reporting a new fact for an existing key
- **AND** a later-arriving earlier row SHALL NOT rewrite the first committed anchor; message-count enrichment MUST NOT silently replace immutable identity/time or add another fact
- **AND** this implementation SHALL use the existing temporal-fact mechanism, not a second general source registry or a change to every memory writer's automatic key

#### Scenario: Day-boundary clock skew

- **WHEN** a source claims 23:59 on one date but the inbox records receipt at 00:01 UTC the next day, and a replay runs in another timezone or on another day
- **THEN** the unit's day and actual timestamp SHALL derive from that persisted 00:01 UTC receipt, with the clock distinction explicit
- **AND** neither the source clock, scheduler timezone, process date nor a channel offset SHALL move that observation to another unit
- **AND** the unchanged observation SHALL return the same fact on replay

#### Scenario: Missing identity and identity collision fail visibly

- **WHEN** a required source/endpoint/chat/entity/receipt anchor is unavailable, a stored logical key has incompatible immutable bindings, or the same persisted observation is attributed inconsistently across units
- **THEN** the writer SHALL refuse that observation, increment the existing `errors` result and preserve prior facts; it MUST NOT manufacture an ID/time or report the failure as healthy empty work
- **AND** a key hit SHALL verify the full stored logical identity and the original anchor binding; a later contributing row is not a replacement anchor or an identity conflict by itself
- **AND** a changed time/source binding for that original anchor or a digest collision SHALL fail rather than silently alias another observation
- **AND** the message-sync checkpoint SHALL NOT advance past refused observations as if processed; cancellation and database failures MUST propagate or be counted through the existing failure boundary
- **AND** diagnostics SHALL contain bounded categories and opaque identifiers, not message text, raw account identifiers or credentials

#### Scenario: Legacy offset history and controlled cutover

- **WHEN** versioned receipt-time facts coexist with existing hour-offset facts
- **THEN** old rows SHALL remain readable with their stored timestamps, direction, metadata and scoring; they MUST NOT be deleted, reinterpreted as actual message times, or automatically rewritten/backfilled
- **AND** the implementation plan SHALL record a durable UTC-day cutover boundary before new writes, preventing one chat/day unit from straddling old and new writers
- **AND** an older or ambiguous legacy unit SHALL NOT mint a new versioned shadow fact merely because its key namespace differs; unresolved overlap SHALL be a visible refusal requiring separately reviewed reconciliation
- **AND** the bounded 30-day scan, calendar checkpoint recovery and legacy/manual read behavior SHALL survive; rollback SHALL pause the new message writer when necessary rather than reactivate synthetic-hour writes over versioned units

#### Scenario: Exact-head adoption precedes implementation

- **WHEN** this proposal is drafted, validated, reviewed or published as a draft PR
- **THEN** none of those actions SHALL adopt it or authorize code, tests, migrations, backfills or runtime activation
- **AND** an independent reviewer SHALL inspect the exact head and the owner SHALL explicitly record adoption naming that reviewed head before merge or implementation
- **AND** actions made through the owner's GitHub account SHALL NOT substitute for that recorded answer
