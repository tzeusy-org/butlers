## ADDED Requirements

### Requirement: Domain time claim projection

ID: REQ-module-calendar-061
Source: bu-5ii4gj.13 (run 26 move 13), slice S2
Scope: v1-mandatory

`CalendarModule` SHALL expose an in-process `reconcile_domain_claims(source_butler, claims,
complete)` that the owning butler's own job calls inside its own daemon. For each claim with presence
`block` or `reminder` it SHALL upsert one `lane="butler"` row in that butler's own
`calendar_events`, keyed `(source_id, origin_ref = "claim:" + claim_key)`, carrying the claim's
`record_version` in its metadata; a claim with presence `overlay` SHALL produce no row. A changed
`record_version` SHALL update the same row in place. When `complete` is true, a `claim:` row of that
butler whose claim is absent or whose status is `settled` or `cancelled` SHALL be retracted. It SHALL
write only the calling butler's own rows: no cross-schema write, no new MCP tool, and no write to the
owner's primary calendar.

#### Scenario: Reconcile is idempotent

- **WHEN** the same complete claim set is reconciled twice
- **THEN** the second run writes nothing

### Requirement: A retracted claim row is cancelled, not deleted

ID: REQ-module-calendar-062
Source: bu-5ii4gj.13 (run 26 move 13), slice S2
Scope: v1-mandatory

Retracting a claim row SHALL set its status to cancelled, so the existing outbound push of cancelled
butler-lane rows removes the provider's copy and the history stays (the push behaviour is unchanged;
the reconcile is tested at that seam). A claim that returns (the record becomes active again) SHALL revive the
same row, not create a second one. A butler's reconcile SHALL touch only rows whose `origin_ref`
starts with `claim:`.

#### Scenario: A returning claim revives its row

- **WHEN** a retracted claim's record becomes active again
- **THEN** the same row is un-cancelled and the provider receives an update
