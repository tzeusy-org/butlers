# Relationship Turn Status

## ADDED Requirements

### Requirement: Turn status is a derived, unstored, entity-keyed read

The Relationship butler SHALL expose `whose_turn` for a canonical person as a deterministic read
projection computed from approved interaction evidence at read time. Its value SHALL be exactly one of
`owner`, `other` or `unknown`. The canonical person SHALL be a non-merged `public.entities` row. Every
result SHALL carry a `basis` that names, for each of the inbound and outbound directions, an
availability state (see the taxonomy requirement), the timestamp of the newest qualifying evidence
when there is any, and the source channel kind of that evidence. A result SHALL NOT be written to a
table, cache, fact or ledger. This contract SHALL NOT be satisfied by a stored turn-state table; any
durable materialization would be a separate owner decision with its own change.

ID: REQ-relationship-turn-status-001
Source: bu-h40h2b.14 Slice C (turn-state projection, not a free-standing source of truth); bu-b1ybef closed 2026-10-17 as an engineering call (derive on demand, no new collection)
Scope: v1-reserved

#### Scenario: Computed at read time

- **WHEN** a caller reads the turn status for a canonical person
- **THEN** the result is derived from current evidence at that moment
- **AND** nothing is persisted for it

#### Scenario: Explainable result

- **WHEN** `whose_turn` is `owner`, `other` or `unknown`
- **THEN** the result names the basis: each direction's availability state, the newest qualifying evidence timestamp and the source channel kind

#### Scenario: Merged entity is not a unit

- **WHEN** a status is requested for an entity tombstoned by a merge
- **THEN** no status is produced for it
- **AND** the surviving entity's status includes the evidence that followed the merge

### Requirement: Eligible inbound and outbound evidence

Qualifying evidence SHALL be limited to what the system already holds from user-to-person channels:
inbound messages recorded in `switchboard.message_inbox` on `email`, `telegram_user_client` and
`whatsapp_user_client`, and the owner's own messages on `telegram_user_client` and
`whatsapp_user_client` that those connectors report through the batch envelope's `owner_sender_id`.
Interaction facts that `interaction_sync` wrote with a server attestation SHALL be an equivalent,
day-resolution form of the same evidence. The following SHALL NOT qualify: messages with
`interaction_eligible` false, chats over 20 participants, Telegram bot, Discord and calendar-derived
interactions, automated replies and bounces (`control.automated_kind` set), legacy or unattested
interaction rows, messages whose sender is the owner's own identity on the inbound side, and rows
whose identity does not resolve to a canonical person. Gmail outbound evidence SHALL come only from an
on-demand owner-Gmail read where that capability exists and is granted; until then email outbound
SHALL be `not_tracked`. The Gmail `in:sent` Message-ID set and `connectors.owner_outbound_events`
SHALL NOT be evidence.

ID: REQ-relationship-turn-status-002
Source: bu-h40h2b.14 Slice A (eligible sources, group/thread treatment, self/auto-reply exclusion); passive-interaction-sync "Message-based interaction detection"; [Observed] roster/relationship/jobs/relationship_jobs.py `_INTERACTION_SYNC_CHANNEL_MAP`; src/butlers/connectors/gmail.py `_fetch_sent_message_ids`; src/butlers/connectors/owner_outbound_events.py
Scope: v1-reserved

#### Scenario: Direct message on a user-client channel qualifies

- **WHEN** a WhatsApp direct chat contains a message from a resolved person and a later owner message reported through `owner_sender_id`
- **THEN** both are qualifying evidence

#### Scenario: Large group is excluded

- **WHEN** a chat has more than 20 participants or is marked `interaction_eligible` false
- **THEN** none of its messages is qualifying evidence

#### Scenario: Automated reply is excluded

- **WHEN** an inbound email carries `control.automated_kind`
- **THEN** it is not qualifying evidence

#### Scenario: Sent-message identifiers and outbound point events are not evidence

- **WHEN** the only outbound signal is the in-memory Gmail sent Message-ID set or a `connectors.owner_outbound_events` row
- **THEN** it does not qualify
- **AND** email outbound reads `not_tracked`

### Requirement: Availability taxonomy per direction

For each direction the projection SHALL report exactly one state: `observed` (qualifying evidence
exists), `not_tracked` (no enabled eligible source exists for that direction for any of the person's
resolved identities), `no_signal` (an eligible source is enabled and readable but holds no qualifying
evidence in the evidence window), or `unavailable` (an eligible source or the read failed or cannot be
proven live). `unavailable` SHALL take precedence over `no_signal` and `not_tracked` for the same
direction when any eligible source for it could not be read. These states describe the turn evidence
only. They SHALL NOT rename, relax or replace the existing `unmeasurable` classification of
stale-contact cadence (REQ-butler-relationship-001), which SHALL continue to apply unchanged.

ID: REQ-relationship-turn-status-003
Source: bu-h40h2b.14 Slice A (three-way measurement taxonomy); REQ-butler-relationship-001 (stale-contact claims require one authoritative producer)
Scope: v1-reserved

#### Scenario: No eligible source

- **WHEN** a person has only a Telegram bot identity
- **THEN** both directions read `not_tracked`

#### Scenario: Source present, nothing in window

- **WHEN** a person has a WhatsApp identity and the connector is live but no qualifying message falls in the window
- **THEN** both directions read `no_signal`, not `not_tracked`

#### Scenario: Read failure is not silence

- **WHEN** the message store cannot be read
- **THEN** the affected directions read `unavailable`
- **AND** the status is never presented as `no_signal`

#### Scenario: Cadence classification is untouched

- **WHEN** a contact is `unmeasurable` for stale-contact cadence
- **THEN** it stays `unmeasurable` regardless of the turn taxonomy

### Requirement: Turn value rules, ordering and the evidence window

`whose_turn` SHALL be `owner` when the newest qualifying event is inbound and `other` when it is
outbound, only if all of these hold: both directions are `observed` or `no_signal` on the event's
source channel (neither is `not_tracked` or `unavailable` there); no newer evidence exists on a channel
where a direction is `not_tracked` or `unavailable`; and the order of the newest inbound and outbound
events is established. Otherwise it SHALL be `unknown` with the reason in the basis. Order SHALL be
established by `occurred_at` when its basis is `source_stated`, and otherwise by the day of receipt.
Interaction facts carry only a day plus a synthetic hour offset, so same-day inbound and outbound
evidence with no `source_stated` time SHALL give `unknown`; the synthetic offset SHALL NOT be read as
an order. Evidence older than the retention of its source SHALL NOT be assumed absent: a turn that
would depend on it SHALL be `unknown`.

ID: REQ-relationship-turn-status-004
Source: bu-h40h2b.14 Slice A (event-time versus ingest-time) and Slice C (unknown); [Observed] roster/relationship/jobs/relationship_jobs.py gives outgoing facts a +12 hour offset so they coexist with incoming facts, roster/relationship/tools/dunbar.py reads `metadata->>'direction'`; connector-event-clock REQ-connector-event-clock-001 (specify-connector-event-clock-and-coverage, #4686)
Scope: v1-reserved

#### Scenario: Reply is the latest event

- **WHEN** a person writes on Monday and the owner replies on Tuesday, both on WhatsApp
- **THEN** `whose_turn` is `other`

#### Scenario: Unanswered inbound

- **WHEN** a person's WhatsApp message is the newest event and no later owner message exists
- **THEN** `whose_turn` is `owner`

#### Scenario: Same day without a time

- **WHEN** the newest inbound and outbound evidence fall on the same day and neither has a `source_stated` time
- **THEN** `whose_turn` is `unknown`

#### Scenario: Email-only person

- **WHEN** a person's evidence is inbound email and email outbound is `not_tracked`
- **THEN** `whose_turn` is `unknown`, because the owner may have replied unseen

#### Scenario: Newer untracked channel

- **WHEN** a WhatsApp reply is the newest tracked event and a newer inbound email exists on a channel with `not_tracked` outbound
- **THEN** `whose_turn` is `unknown`

### Requirement: Identity merge, unresolved identity and retraction

Evidence SHALL be counted once per canonical person. An event reached through more than one identity of
the same person SHALL contribute once, keyed by its source channel, endpoint, conversation and message
or day. Evidence from a sender whose identity does not resolve to a canonical person SHALL NOT be
attributed to anyone, and a person with unresolved or merge-pending identity evidence SHALL read
`unknown` with basis `identity_unresolved` rather than a turn. Because nothing is stored, a merge, a
retracted interaction fact (`validity = 'retracted'`) or a removed evidence row SHALL take effect at the
next read with no repair step, and a retracted fact SHALL NOT be counted.

ID: REQ-relationship-turn-status-005
Source: bu-h40h2b.14 Slice A (merge remapping, correction/retraction, unresolved identity) and Slice D; [Observed] roster/relationship/tools/entity_merge.py repoints active `entity_facts` with lifecycle intact; relationship_assert_fact.py retraction sets `validity = 'retracted'`
Scope: v1-reserved

#### Scenario: Three identities, one person

- **WHEN** one person has an email, a WhatsApp JID and a Telegram identity
- **THEN** their evidence contributes to a single status for that person
- **AND** the same event is not counted twice

#### Scenario: Merge changes the answer without repair

- **WHEN** two entities are merged
- **THEN** the next read for the survivor includes the merged evidence
- **AND** no stored state needs correcting

#### Scenario: Retracted fact is ignored

- **WHEN** an interaction fact is retracted
- **THEN** the next read does not count it

#### Scenario: Unresolved identity

- **WHEN** the newest evidence comes from a sender that does not resolve to a canonical person
- **THEN** it is attributed to nobody
- **AND** a person with a merge-pending identity reads `unknown` with basis `identity_unresolved`

### Requirement: A turn status grants no outreach authority

`whose_turn` is information. A value of `owner` SHALL NOT create, schedule or approve a nudge, task,
draft, send, commitment or obligation, and SHALL NOT make a contact `overdue` or lift an `unmeasurable`
classification. Any consumer that acts on it SHALL pass its own authorization gate. Obligations remain
with the Promise radar (bu-jv1moa.11), and prepared actions and occasion candidates remain with
bu-h40h2b.13. No surface SHALL present a turn status as a send affordance.

ID: REQ-relationship-turn-status-006
Source: bu-h40h2b.14 Slice C ("whose_turn ... never an obligation or automatic send trigger") and design Status; REQ-butler-relationship-001
Scope: v1-mandatory

#### Scenario: Owner's turn creates nothing

- **WHEN** `whose_turn` is `owner`
- **THEN** no task, nudge, draft or send is created as a result

#### Scenario: Status does not change cadence

- **WHEN** a contact is within its cadence window and `whose_turn` is `owner`
- **THEN** the contact is not reported overdue because of the turn status

### Requirement: Privacy and retention of the projection

A turn-status response SHALL contain only the canonical entity identifier and display name,
`whose_turn`, the per-direction availability states, the source channel kinds, and evidence timestamps
at day resolution or coarser unless a `source_stated` time is used, plus counts. It SHALL NOT contain a
message body, snippet or subject, a recipient or sender address, a message or thread identifier, a draft
text, or a raw connector payload. The projection adds no stored data, so no new retention applies;
the evidence window is bounded by the retention of the existing sources it reads. Logs and metrics SHALL
identify a person by canonical entity identifier only.

ID: REQ-relationship-turn-status-007
Source: bu-h40h2b.14 API/UI and privacy; Slice C ("never retain raw message body, participant addresses, or draft text")
Scope: v1-mandatory

#### Scenario: Content-blind response

- **WHEN** a turn status is returned
- **THEN** it contains no body, snippet, subject, address, message identifier or draft text

#### Scenario: No new retention

- **WHEN** the projection runs repeatedly
- **THEN** no new table or row is written for it

### Requirement: Out of scope: Gmail SENT ingestion and a stored turn-state table

This contract SHALL NOT be implemented by ingesting Gmail SENT messages, changing an OAuth scope,
backfilling history, adding a provider call to the Gmail connector, or creating a turn-state table. Gmail
SENT ingestion remains absent until a separate owner-approved connector contract names account scope,
forward or backfill policy, exact retained fields, body and snippet exclusion, deduplication and live
operation boundaries. The Gmail connector's `in:sent` listing SHALL remain a metadata read of the
`Message-ID` header for reply-policy tiering only.

ID: REQ-relationship-turn-status-008
Source: bu-h40h2b.14 acceptance 2; bu-b1ybef closed 2026-10-17 (a SENT collection store would be an owner privacy decision)
Scope: v1-mandatory

#### Scenario: No SENT collection

- **WHEN** this contract is implemented
- **THEN** the Gmail connector's provider calls, OAuth scopes and retained fields are unchanged

#### Scenario: No turn-state table

- **WHEN** this contract is implemented
- **THEN** no `relationship.turn_state` table or equivalent exists

## Source References

- Non-Negotiable Rule 4 (the daemon is deterministic infrastructure)
- RFC 0011 and RFC 0013 (stale-contact claims; Dunbar interaction scoring), RFC 0029 (expected signals and honest absence)
- `passive-interaction-sync`, `dunbar-tier-scoring` and `butler-relationship` capabilities
