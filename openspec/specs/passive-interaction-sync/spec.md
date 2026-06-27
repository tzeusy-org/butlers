# Passive Interaction Sync

## Purpose

Automatically detect and log interactions from communication channels and
calendar events so the Dunbar tier system has accurate data. Today, interaction
facts are only created when the user explicitly narrates them ("I had coffee with
Chloe"). This spec defines a background job that passively creates interaction
facts from data already in the system: messages in `switchboard.message_inbox`
and events in `public.calendar_events`.

## Context

The Dunbar tier engine (`roster/relationship/tools/dunbar.py`) ranks contacts by
exponential-decay scoring over interaction facts (`predicate LIKE 'interaction_%'`,
`scope='relationship'`). Contacts with zero interaction facts are hard-assigned
to tier 1500 regardless of actual communication frequency. This creates a
systematic blind spot: the user's most-contacted people (partner, family, close
friends) can sit at the lowest tier because no pipeline logs their conversations
as interaction facts.

## ADDED Requirements

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
- **WHEN** `source_sender_identity` does not resolve to an active `relationship.entity_facts` triple for the expected predicate
- **THEN** the job SHALL skip that sender without error
- **AND** it SHALL increment a `skipped_unresolved` counter in the return stats

#### Scenario: Owner messages are excluded from contact resolution
- **WHEN** the resolved entity has role `'owner'` in `public.entities.roles`
- **THEN** the job SHALL skip that entity for contact resolution (no self-interaction)
- **AND** the owner's presence as a sender SHALL be used solely to determine direction for other participants

### Requirement: Calendar-based interaction detection

The `interaction_sync` job SHALL also scan past calendar events for social
gatherings and log interactions with attendees who are known contacts.

#### Scenario: Detect past calendar events with attendees

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `public.calendar_events` for events where:
  - `starts_at` is within the scan window
  - `status` = `'confirmed'`
  - The event has attendees in `metadata->'attendees'` (JSONB array)

#### Scenario: Resolve attendees to contacts

- **WHEN** a calendar event has attendees
- **THEN** for each attendee email, the job SHALL attempt to resolve it to an
  `entity_id` via `relationship.entity_facts` where `predicate = 'has-email'` and
  `LOWER(object) = LOWER(attendee_email)` (case-insensitive exact match)
- **AND** attendees who are the owner (identified via `public.entities.roles` containing `'owner'`)
  SHALL be excluded

#### Scenario: Calendar interaction fact creation

- **WHEN** an attendee email resolves to an entity_id
- **THEN** the job SHALL call `interaction_log()` with:
  - `entity_id` = the resolved entity UUID
  - `type` = `'calendar_event'`
  - `summary` = the event title (e.g., "Dinner at Mario's")
  - `occurred_at` = the event's `starts_at` timestamp
  - `direction` = `'mutual'`
  - `metadata` = `{"source": "interaction_sync", "event_id": "<uuid>", "event_title": "<title>"}`

#### Scenario: Declined events are excluded

- **WHEN** the owner's RSVP status on the event is `'declined'`
- **THEN** the job SHALL skip that event entirely

#### Scenario: Cancelled events are excluded

- **WHEN** a calendar event has `status = 'cancelled'`
- **THEN** the job SHALL skip that event

### Requirement: Scan window and checkpoint

The job SHALL maintain a durable checkpoint to avoid re-scanning the full history
on every run.

#### Scenario: Checkpoint persistence

- **WHEN** the job completes successfully
- **THEN** it SHALL store the scan window end time in the butler's state store
  under key `interaction_sync.last_scan_at`
- **AND** the next run SHALL use this as the scan window start time

#### Scenario: First run without checkpoint

- **WHEN** the job runs for the first time (no checkpoint exists)
- **THEN** it SHALL scan the last 30 days of messages and calendar events
  as a backfill window

#### Scenario: Scan window cap

- **WHEN** the checkpoint is older than 30 days (e.g., after a long outage)
- **THEN** the scan window start SHALL be capped at 30 days ago to prevent
  unbounded backfill

### Requirement: Schedule configuration

#### Scenario: Default schedule

- **WHEN** the relationship butler starts
- **THEN** the `interaction_sync` job SHALL be registered with cron
  `30 6 * * *` (daily at 06:30 UTC) and `dispatch_mode = "job"`

### Requirement: Job return stats

#### Scenario: Return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict containing:
  - `scan_window_start` (ISO8601 string)
  - `scan_window_end` (ISO8601 string)
  - `processed` (int) — total sender-channel-date combinations examined
  - `logged` (int) — interaction facts created (incoming + outgoing combined)
  - `skipped_unresolved` (int) — senders or attendees not found in `relationship.entity_facts`
  - `skipped_owner` (int) — owner's own sender or attendee entries excluded
  - `skipped_ineligible` (int) — messages excluded by `interaction_eligible = 'false'` flag
  - `skipped_group_too_large` (int) — chat groups exceeding the 20-participant gate
  - `calendar_events_scanned` (int)
  - `errors` (int) — checkpoint I/O failures and other non-fatal errors
  - `co_attended_edges_minted` (int), count of co-attendance relationship edges created from shared calendar events
  - `knows_edges_minted` (int), count of "knows" relationship edges minted during the sync

## Design Notes

### Why a background job instead of ingestion-time extraction?

1. **Decoupled from ingestion hot path** -- the Switchboard ingestion pipeline is
   latency-sensitive. Adding per-message contact resolution and fact creation
   would slow down all message processing.
2. **Batch efficiency** -- grouping messages by (sender, date) produces one fact
   per day per contact instead of one per message.
3. **Idempotent** -- `interaction_log()` deduplicates by (entity_id, predicate, valid_at),
   so the job is safe to re-run.
4. **Channel-agnostic** -- new channels (Slack, Discord) can be added by
   extending the `SYNC_CHANNELS` list without touching the ingestion pipeline.

### Identity resolution strategy

Message channels store sender identifiers differently.  The job maps
`source_channel` to the expected `relationship.entity_facts` predicate and
lookup key for resolution (`public.contact_info` was dropped in migration
core_115 / bead bu-e2ja9):

| `source_channel`       | Lookup key type  | `entity_facts` predicate |
|------------------------|------------------|--------------------------|
| `telegram_user_client` | `telegram_chat_id` | `has-handle`           |
| `whatsapp_user_client` | `whatsapp_jid`   | `has-handle`             |
| `email`                | `email`          | `has-email`              |

Sender identities are resolved by matching `entity_facts.object = sender_identity`
(exact match, case-sensitive for handles; `LOWER()` equality for emails) to find
the entity UUID. Unresolved senders are skipped without error.

### Calendar attendee resolution

Calendar events store attendees as email addresses in the `metadata` JSONB field.
Resolution uses case-insensitive exact match against `relationship.entity_facts`
rows where `predicate = 'has-email'` and `object_kind = 'literal'`.
(`public.contact_info` was dropped in migration core_115 / bead bu-e2ja9.)
This means contacts must have a `has-email` triple in `entity_facts` to be
detected from calendar events — contacts without an email fact will not match.
