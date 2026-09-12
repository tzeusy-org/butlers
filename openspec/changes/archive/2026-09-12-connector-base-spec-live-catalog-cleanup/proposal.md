## Why

`GET /api/ingestion/connectors/available` currently publishes
`supports_backfill`, even though backfill orchestration is an internal
Switchboard MCP protocol rather than a deployable-connector catalog property.
Removing the overclaimed field keeps the discovery response truthful and its
frontend type aligned with the live contract.

## What Changes

- **BREAKING**: remove `supports_backfill` from every entry returned by
  `GET /api/ingestion/connectors/available`.
- Define the available-connector response as exactly `connector_type`,
  `channel`, `provider`, and `display_name` per profile.
- Remove the corresponding backend Pydantic field, frontend TypeScript field,
  and obsolete test fixture values.
- Preserve the separate internal `backfill.poll` / `backfill.progress` MCP
  protocol and connector heartbeat capabilities.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `connector-base-spec`: define the four-field available-connector catalog
  response and explicitly keep it distinct from internal backfill orchestration.

## Impact

- Backend catalog and response model:
  `src/butlers/api/routers/ingestion_connectors.py`.
- Backend response-contract regression test:
  `tests/api/test_ingestion_connectors_available.py`.
- Frontend API type and local catalog fixtures:
  `frontend/src/api/types.ts` and
  `frontend/src/components/ingestion/connectors/ConnectorsRoster.test.tsx`.
- No database schema, dependency, or internal MCP tool changes.
