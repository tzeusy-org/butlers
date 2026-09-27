## Why

Messenger exposes a delivery-tracking, retry, dead-letter, and health stack that
has no production caller. Its database-backed health endpoints consequently
report confident empty values while live delivery uses approved channel adapters
through the Switchboard routing and attention paths.

## What Changes

- **BREAKING** Remove the unwired Messenger tracking/reliability MCP module,
  fabricated health REST API, dashboard client/cache surface, and Conversations tab.
- **BREAKING** Retire the four unused Messenger delivery tables with a migration
  that stops before DDL if any legacy table contains rows.
- Preserve direct approved channel-adapter egress, the `notify` routing contract,
  deferred notifications, approval gates, and Switchboard attention outcomes.
- Document that downgrade recreates only an empty compatibility schema and cannot
  recover retired data.

## Capabilities

### New Capabilities

- `messenger-tracking-retirement`: Safe removal contract for the unwired Messenger
  tracking and fabricated-health subsystem.

### Modified Capabilities

- `butler-messenger`: Remove obsolete tracking, retry, dead-letter, and health
  promises while retaining Messenger's delivery execution-plane contract.
- `dashboard-butler-management`: Remove the Messenger Conversations bespoke tab.

## Impact

Affected surfaces are the Messenger roster module, migration chain, API router,
frontend Messenger client/tab registration, the Messenger role spec and docs.
The live Switchboard-to-channel-adapter delivery path is intentionally unchanged.
