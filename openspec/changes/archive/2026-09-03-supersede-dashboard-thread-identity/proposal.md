# Supersede dashboard thread identity

## Why

The public ingestion envelope now separates stable conversation identity from
per-message reply targeting. The dashboard requirement must retire the legacy
`external_thread_id` contract with explicit lifecycle provenance.

## What Changes

- Replace the dashboard ingestion-envelope requirement with a stable-identity successor.
- Preserve all routing-pin, discretion, retry, and page-context guarantees.

## Impact

- Affected spec: `dashboard-conversations`
- Affected code: dashboard ingest envelope construction and shared ingest contracts
