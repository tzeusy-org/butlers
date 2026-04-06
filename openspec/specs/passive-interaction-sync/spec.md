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
exponential-decay scoring over interaction facts (`predicate='interaction'`,
`scope='relationship'`). Contacts with zero interaction facts are hard-assigned
to tier 1500 regardless of actual communication frequency. This creates a
systematic blind spot: the user's most-contacted people (partner, family, close
friends) can sit at the lowest tier because no pipeline logs their conversations
as interaction facts.

## ADDED Requirements

### Requirement: Message-based interaction detection

The relationship butler SHALL run a scheduled job (`interaction_sync`) that scans
`switchboard.message_inbox` for recent messages on user-to-person channels and
creates interaction facts for resolved contacts.

#### Scenario: Detect Telegram user client conversations

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL query `switchboard.message_inbox` for messages where
  `request_context->>'source_channel'` is `'telegram_user_client'` and
  `received_at` is within the scan window (since last successful run, or last
  24 hours on first run)
- **AND** it SHALL group messages by `(source_sender_identity, DATE(received_at))`
  to produce one interaction per contact per day
- **AND** for each group, it SHALL resolve `source_sender_identity` to a
  `contact_id` via `public.contact_info` reverse-lookup

#### Scenario: Detect WhatsApp user client conversations

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL apply the same scan-and-resolve logic for messages where
  `request_context->>'source_channel'` is `'whatsapp_user_client'`

#### Scenario: Detect email conversations

- **WHEN** `interaction_sync` runs
- **THEN** it SHALL apply the same scan-and-resolve logic for messages where
  `request_context->>'source_channel'` is `'email'`
- **AND** sender resolution SHALL match the sender email address against
  `public.contact_info` entries of type `'email'`

#### Scenario: Interaction fact creation

- **WHEN** a (contact_id, date, channel) group is resolved
- **THEN** the job SHALL call `interaction_log()` with:
  - `contact_id` = the resolved contact UUID
  - `type` = the source channel name (e.g., `'telegram_user_client'`)
  - `summary` = `"{message_count} messages on {channel}"` (e.g., "12 messages on telegram_user_client")
  - `occurred_at` = the date of the message group (midday UTC to ensure date-based dedup works)
  - `direction` = `'mutual'`
  - `metadata` = `{"source": "interaction_sync", "message_count": N}`
- **AND** the date-based deduplication in `interaction_log()` SHALL prevent
  duplicate facts if the job runs multiple times per day

#### Scenario: Unresolved senders are skipped

- **WHEN** `source_sender_identity` does not match any row in
  `public.contact_info` for the expected type
- **THEN** the job SHALL skip that sender without error
- **AND** it SHALL increment an `unresolved_senders` counter in the return stats

#### Scenario: Owner messages are excluded

- **WHEN** the resolved contact has role `'owner'` in `public.contacts.roles`
- **THEN** the job SHALL skip that contact (the user's own messages should not
  create self-interactions)

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
- **THEN** for each attendee email, the job SHALL attempt to resolve it to a
  `contact_id` via `public.contact_info` where `type = 'email'` and
  `value = attendee_email` (case-insensitive exact match)
- **AND** attendees who are the owner (organizer or matching owner contact email)
  SHALL be excluded

#### Scenario: Calendar interaction fact creation

- **WHEN** an attendee email resolves to a contact_id
- **THEN** the job SHALL call `interaction_log()` with:
  - `contact_id` = the resolved contact UUID
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
  `0 */4 * * *` (every 4 hours) and `dispatch_mode = "job"`

### Requirement: Job return stats

#### Scenario: Return value

- **WHEN** the job completes
- **THEN** it SHALL return a dict containing:
  - `messages_scanned` (int)
  - `calendar_events_scanned` (int)
  - `interactions_created` (int)
  - `interactions_deduplicated` (int)
  - `unresolved_senders` (int)
  - `contacts_updated` (int) -- distinct contacts that received new interactions
  - `scan_window_start` (ISO8601 string)
  - `scan_window_end` (ISO8601 string)

## Design Notes

### Why a background job instead of ingestion-time extraction?

1. **Decoupled from ingestion hot path** -- the Switchboard ingestion pipeline is
   latency-sensitive. Adding per-message contact resolution and fact creation
   would slow down all message processing.
2. **Batch efficiency** -- grouping messages by (sender, date) produces one fact
   per day per contact instead of one per message.
3. **Idempotent** -- `interaction_log()` deduplicates by (contact_id, type, date),
   so the job is safe to re-run.
4. **Channel-agnostic** -- new channels (Slack, Discord) can be added by
   extending the `SYNC_CHANNELS` list without touching the ingestion pipeline.

### Identity resolution strategy

Message channels store sender identifiers differently:
- `telegram_user_client`: numeric user ID (stored as `type='telegram_user_id'` in contact_info)
- `whatsapp_user_client`: phone number (stored as `type='phone'` in contact_info)
- `email`: email address (stored as `type='email'` in contact_info)

The job maps `source_channel` to the expected `contact_info.type` for resolution.
Exact match is used (not ILIKE partial match) to avoid false positives.

### Calendar attendee resolution

Calendar events store attendees as email addresses in the `metadata` JSONB field.
Resolution uses exact case-insensitive match against `contact_info.type = 'email'`.
This means contacts must have an email in `contact_info` to be detected from
calendar events -- phone-only contacts will not match.
