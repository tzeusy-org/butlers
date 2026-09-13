## Why

The archived connector-retirement change still records a generic settings
clause that says every setting waits for a connector restart. That conflicts
with the supported `flush_interval_s` live-reload behavior and prevents the
archive ledger from distinguishing the retired contract from its replacement.

## What Changes

- Remove the obsolete `Connector Settings API` requirement whose lifecycle
  wording was tied to the retired endpoint family.
- Add the canonical ingestion settings requirement, including the exact route,
  shallow-merge behavior, content-blind response, and per-setting reload
  boundary.
- Record the batch-settings card's canonical route in its capability spec.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-base-spec`: replace the obsolete generic settings requirement with
  the canonical ingestion settings contract.
- `dashboard-connector-batch-settings`: bind the batch-settings UI to the
  canonical ingestion settings route.

## Impact

This is a specification and documentation reconciliation only. The implemented
route, client, and live reload behavior remain unchanged; archiving the change
makes the historical retirement ledger explicitly recognize the replacement.
