## ADDED Requirements

### Requirement: Sync Requests Share One Filter Set

Every `events.list` sync request the connector makes, whether the full sync or an incremental
poll, SHALL send the same filter parameters (`singleEvents=true`, `showDeleted=true`) and SHALL NOT
send `orderBy`, because Google issues a `nextSyncToken` only to a request without `orderBy` and
requires a sync-token request to repeat the initial request's filters.

A full sync, initial or after a 410, SHALL skip events whose `status` is `cancelled`: it lists the
calendar as it stands, and cancellations are processed only from incremental sync. Detecting
deletions across a 410 gap is left to the revision journal diff (a later slice).

ID: REQ-connector-google-calendar-010
Source: bu-uhbhzq.1 (run 24 move 1), slice S1
Scope: v1-mandatory

#### Scenario: The full sync yields a token the next poll can use

- **WHEN** a connector with no persisted cursor completes its full sync
- **THEN** the request carried no `orderBy`, a `nextSyncToken` is persisted through `cursor_store`, and the next poll calls `events.list` with `syncToken` and the same `singleEvents` and `showDeleted` values

### Requirement: A Full Sync Without a Token Is Not Healthy

When the last page of a full sync carries no `nextSyncToken`, the connector SHALL save no cursor,
SHALL NOT log that a sync token was saved, SHALL report degraded health with a reason naming the
missing token, and SHALL wait one poll interval before trying again rather than repeating the full
sync back to back. This holds when a retry keeps the old cursor after a failed ingest, and after a
410 the expired token SHALL be cleared in memory and in the stored cursor when the resync yields no
token. The blanked cursor SHALL be a durable recovery marker, distinct from an absent cursor, so the
next full sync (in the same process or after a restart) ingests its events instead of treating them
as an initial baseline. A cursor that cannot be read SHALL skip the cycle and degrade health rather than run a baseline.
Blanking the cursor refreshes the checkpoint timestamp (the cursor store has no touch-free write).
A later full sync that yields a token SHALL clear the degraded state.

ID: REQ-connector-google-calendar-011
Source: bu-uhbhzq.1 (run 24 move 1), slice S1
Scope: v1-mandatory

#### Scenario: Absence of a token is reported, not hidden

- **WHEN** the full sync's last page has no `nextSyncToken`
- **THEN** no cursor is saved, no "syncToken saved" line is logged, health is degraded with a reason naming the missing sync token, and the next attempt waits a poll interval

### Requirement: Every Observed Event Version Is Journaled Append-Only

The connector SHALL append every provider event version it observes (the baseline, each change,
each cancellation, each attendee response and each 410 recovery) to
`connectors.calendar_event_revisions`, keyed on `(endpoint_identity, calendar_id, event_id, etag)`,
as the commit point: before it submits an event to Switchboard and before it saves the sync token.
A failed journal write SHALL submit nothing, hold the sync token, report degraded health and be
retried on the next cycle, and SHALL log the failure class only. Replaying a page or re-running a
full sync over unchanged etags SHALL add no rows. No journal row SHALL ever be updated or deleted.
Titles, times and attendees SHALL be kept verbatim, and locations too apart from join secrets;
`extendedProperties` SHALL NOT be journaled; descriptions and locations SHALL be scrubbed of
links and passcodes (including scheme-less meeting hosts) and conference data reduced to the
solution name and key type, and attachment, source and gadget links dropped; an event blocked by
source policy SHALL keep structure and times only, with no raw payload, and its fallback version key
SHALL derive only from that structure. A policy evaluation error that leaves the answer open SHALL
hold the batch (nothing journaled or routed, token held, health degraded); the global deny SHALL be
evaluated independently of connector-local policy. The kind of change SHALL be derived at read time
from consecutive revisions and SHALL NOT be stored.

ID: REQ-connector-google-calendar-012
Source: bu-uhbhzq.1 (run 24 move 1), slice S2; owner decision 2026-11-22 (run24-calendar-journal = D)
Scope: v1-mandatory

#### Scenario: A moved meeting and an RSVP each append one revision

- **WHEN** an incremental page carries one event with a new etag and start and one with a new etag and a changed attendee response
- **THEN** exactly two `change` rows are appended, and replaying the same page adds none

#### Scenario: A failed write holds the token

- **WHEN** the journal insert fails for a fetched batch
- **THEN** nothing is submitted, the saved sync token is unchanged, health is degraded, and the next cycle journals the same delta before the token advances
