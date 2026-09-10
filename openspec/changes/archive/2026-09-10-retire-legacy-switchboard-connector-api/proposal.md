## Why

The Timeline still reads the obsolete Switchboard connector registry directly.
That path treats storage checkpoints and retired identities as running
connectors, producing false operator attention despite the role-aware ingestion
surface already being available. Detail, statistics, and settings callers also
keep the dashboard tied to a second API namespace.

## What Changes

- **BREAKING**: remove the complete `/api/switchboard/connectors` route family.
- Make `/api/ingestion/connectors` the only dashboard connector API namespace.
  Its role-aware `summaries` response becomes the single list source.
- Add canonical ingestion detail, statistics, and settings routes for the three
  currently live dashboard use cases that lack replacements.
- Migrate every same-repo frontend caller, test, and live documentation surface
  in the same delivery; delete unused legacy summary, delete, cursor, and
  fanout behavior rather than re-homing it.
- Remove the duplicate canonical-router mount so generated OpenAPI has one
  operation per connector route.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-ingestion-dispatch-console`: the roster, Timeline attention, and
  detail experience use one canonical role-aware connector API surface.
- `connector-base-spec`: the dashboard connector response and settings API
  contracts move from the obsolete Switchboard namespace to the ingestion
  namespace, and retired fanout/card-delete behavior is no longer promised.

## Impact

- Backend: `ingestion_connectors` becomes the sole owner of dashboard connector
  reads/settings; the Switchboard router loses the retired route family.
- Frontend: client, query hooks, Timeline, System topology, and connector detail
  all call the ingestion namespace.
- API: consumers of the old Tailnet-visible paths receive 404 after migration;
  the owner explicitly requested removal and no tracked external consumer is
  verified, so no compatibility alias is introduced.
- Verification/docs: update OpenAPI/client, API, frontend, and E2E tests plus
  current specs and API documentation. Archived specs and redesign artifacts
  remain historical evidence.
