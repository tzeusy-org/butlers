## ADDED Requirements

### Requirement: Calendar Event Revisions Are Writer-And-Chronicler Only

`connectors.calendar_event_revisions` SHALL grant `connector_writer` only `SELECT` and `INSERT` and
`butler_chronicler_rw` only `SELECT`; no other role, `PUBLIC` included, SHALL hold any privilege on
it, because it keeps verbatim titles, locations and attendees. The connectors schema's default
privileges SHALL be revoked for it at creation and after every `scripts/init-db.sql` bootstrap
replay, and a trigger SHALL refuse `UPDATE`, `DELETE` and `TRUNCATE`.

ID: REQ-database-security-017
Source: bu-uhbhzq.1 (run 24 move 1), slice S2; owner decision 2026-11-22 (run24-calendar-journal = D)
Scope: v1-mandatory

#### Scenario: Only the writer and Chronicler can touch the journal

- **WHEN** the table's ACL is read after migration or after a bootstrap replay
- **THEN** it shows `connector_writer` with SELECT and INSERT, `butler_chronicler_rw` with SELECT, and no other grantee
