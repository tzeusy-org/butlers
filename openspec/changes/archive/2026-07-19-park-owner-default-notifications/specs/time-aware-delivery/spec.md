# Time-Aware Delivery — Delta

## MODIFIED Requirements

### Requirement: Deferred Notification Storage

Deferred notifications SHALL be stored in the originating butler's
`deferred_notifications` table with `id`, `butler_name`, `channel`, `message`,
`priority`, `envelope` (the full `notify.v1` envelope), `deferred_at`,
`deliver_at`, `status`, and `delivered_at`. `status` SHALL remain one of
`pending`, `delivered`, `expired`, or `cancelled`.

The storage-and-flush mechanism SHALL serve three existing admission sources:
per-butler delivery-preferences batching, retry envelopes for a genuine
transport failure, and eligible owner-default approvals-policy/context holds.
The stored row SHALL not need a source-specific schema field. Each admission
source supplies its own authoritative `deliver_at`; the scheduler later treats
all rows uniformly.

Retry callers that re-derive the same recurring failed transition SHALL keep
their existing bounded retry-envelope deduplication. Owner-default policy or
context holds are separate direct notify calls and SHALL retain one row per
successful call rather than inventing a generic content deduplication key.

#### Scenario: Retry envelopes remain superseded across a persistent outage

- **WHEN** a recurring scan re-derives the same failed transition on each tick
  during a multi-tick transport outage
- **THEN** each tick cancels the prior pending retry envelope before enqueueing
  the latest one
- **AND** recovery yields one delivery for that transition

#### Scenario: Delivery-preferences notification is persisted

- **WHEN** a medium-priority notification is deferred by per-butler delivery
  preferences
- **THEN** a pending row stores its full envelope and the preferences-derived
  batch `deliver_at`

#### Scenario: Owner-default policy hold is persisted without a new schema

- **WHEN** the direct owner-default notify gate selects approvals-policy quiet
  hours
- **THEN** a pending row stores the full resolved envelope and the
  policy-derived UTC `deliver_at`
- **AND** the row uses no new column or public content store

#### Scenario: Owner-default context hold uses the supplied wake anchor

- **WHEN** the direct owner-default notify gate selects an active suppressing
  context
- **THEN** a pending row stores the full resolved envelope with the latest
  active suppressor expiry as `deliver_at`

#### Scenario: Daemon restart preserves every deferred admission source

- **WHEN** a daemon restarts after any deferred-notification row was stored
- **THEN** the row remains in the originating schema and is eligible at its
  stored `deliver_at`
