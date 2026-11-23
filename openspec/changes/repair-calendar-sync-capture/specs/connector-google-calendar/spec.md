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
as an initial baseline. A later full sync that yields a token SHALL clear the degraded state.

ID: REQ-connector-google-calendar-011
Source: bu-uhbhzq.1 (run 24 move 1), slice S1
Scope: v1-mandatory

#### Scenario: Absence of a token is reported, not hidden

- **WHEN** the full sync's last page has no `nextSyncToken`
- **THEN** no cursor is saved, no "syncToken saved" line is logged, health is degraded with a reason naming the missing sync token, and the next attempt waits a poll interval
