## ADDED Requirements

### Requirement: Private email correspondence evidence ownership

Messenger SHALL own the email correspondence ledger, its provider-Sent
confirmation, and a deterministic, zero-LLM maintenance job that confirms
recorded message IDs, expires overdue `accepted` rows to `unknown`, and purges
rows older than 180 days. The maintenance job SHALL NOT initiate delivery. No
Messenger MCP tool SHALL expose raw ledger rows.

#### Scenario: Approved email egress records a private intent

- **WHEN** Messenger sends an approved email
- **THEN** it records the intent in the ledger before egress

#### Scenario: Maintenance cannot initiate delivery

- **WHEN** the maintenance job runs
- **THEN** it performs only exact-ID lookups, state expiry, and purge
- **AND** it sends no message
