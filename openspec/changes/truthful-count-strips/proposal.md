## Why

Current connector histories manufacture health zeroes, infer clock labels from sparse array position, and cannot distinguish missing receiver history from positively established complete recording. Existing session strips also discard source hour keys. This produces confident quiet/liveness claims when their sources are unavailable.

## What Changes

- Commit the existing heartbeat history insert and registry upsert atomically, with migration-owned trigger evidence, post-serialization clocks and closed catalog checks under existing runtime roles.
- Return count availability and exact-endpoint listening independently over explicit windows; preserve legacy history as observational with no backfill certification.
- Share keyed count-series and BucketStrip primitives across the existing connector, activity, overview, status-board and management surfaces; enforce scoped zero-fill data flow in real ESLint.
- Preserve existing heartbeat submission failure isolation, Gmail classification admission/ACK ordering, owner timezone, route/read-door and protected trend contracts.

## Capabilities

### New Capabilities

None. The change extends existing connector and dashboard capabilities.

### Modified Capabilities

- `connector-base-spec`: protected durable recording evidence without additional heartbeat DML.
- `dashboard-ingestion-dispatch-console`: independent count/listening source truth.
- `dashboard-design-language`: source-keyed count-bucket grammar and enforcement.
- `dashboard-butler-management`: retained source hour keys and unavailable-state truth.

## Impact

Switchboard sw_041/downsw_040, registered heartbeat writer, ingestion API, existing board projection, dashboard adapters and strips. No new provider calls, roles/grants, per-heartbeat DML, retention policy or additional dashboard endpoint. All seven original outcomes remain mandatory; actual elapsed recorded-history evidence, hosted PostgreSQL and protected delivery are not supplied by synthetic chronology or software checks.
