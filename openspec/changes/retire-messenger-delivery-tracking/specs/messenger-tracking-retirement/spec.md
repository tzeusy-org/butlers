## ADDED Requirements

### Requirement: Safe retirement of unwired Messenger tracking data
The Messenger migration chain SHALL retire its unused delivery tracking tables
only when all four legacy tables are empty, and SHALL refuse the migration
before schema mutation when any legacy table contains data.

#### Scenario: Clean Messenger schema advances to msg_003
- **WHEN** the Messenger schema has applied `msg_002` and all legacy delivery
  tracking tables are empty
- **THEN** `msg_003` drops `delivery_dead_letter`, `delivery_receipts`,
  `delivery_attempts`, and `delivery_requests` in dependency-safe order

#### Scenario: Legacy data prevents retirement
- **WHEN** any legacy tracking table contains at least one row
- **THEN** `msg_003` raises before executing destructive DDL and identifies that
  retained data requires an explicit migration decision

#### Scenario: A concurrent writer races retirement
- **WHEN** a transaction has written a legacy tracking row but has not committed
- **THEN** `msg_003` locks every existing legacy table before checking rows
- **AND** after that writer commits, the migration raises without dropping any
  legacy table or losing the committed row

#### Scenario: Downgrade does not claim data recovery
- **WHEN** `msg_003` is downgraded after successful retirement
- **THEN** it recreates only an empty compatibility schema
- **AND** it does not claim to restore retired delivery rows
