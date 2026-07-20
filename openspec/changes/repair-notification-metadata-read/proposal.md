## Why

The merged Switchboard writer correction in #3458 prevents new JSONB metadata
from being double-encoded, but it deliberately leaves historical
`switchboard.notifications.metadata` string scalars in place. The notification
API currently turns those rows into `null`, which hides recoverable provenance
from an object-or-null response contract. A read-compatible response is needed
before historical data can be repaired, and a repair is unsafe until the writer
actually serving every active notification path is proven to include #3458 or a
descendant.

## What Changes

- Define one-layer legacy metadata normalization for the global notification
  list, butler-scoped list, and mark-read response: mappings remain mappings,
  encoded JSON objects become objects, and malformed or non-object encoded
  strings are retained as `{"_raw": <original string>}`.
- Preserve `null` for absent metadata and ordinary non-string, non-object JSONB
  values, without recursive decoding or inferred provenance.
- Update the notification API documentation to describe the precise
  object-or-null compatibility contract.
- Define a deployment-evidence gate that proves every actual serving
  notification writer has the #3458 correction, observes a bounded clean
  post-deploy window, and blocks historical repair on any incomplete or stale
  evidence.
- Define a single transactional, Switchboard-only historical-repair migration
  for pre-cutoff JSONB string rows, including an exception-safe one-layer
  parser for malformed inner JSON, an absent-relation no-op guard,
  aggregate-only evidence, and an intentional no-op downgrade.
- Define focused API, migration, writer-regression, documentation, and
  operational-evidence verification work without executing a repair in this
  planning change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `core-notify`: notification metadata read responses gain bounded legacy
  normalization, and historical metadata repair gains an explicit safe-rollout
  contract.

## Impact

- Affected future implementation areas: `src/butlers/api/routers/notifications.py`,
  `docs/frontend/backend-api-contract.md`, the Switchboard migration chain, the
  production writer regression, and focused API/migration/operations tests.
- The completed `fix-notification-jsonb-metadata-write` change remains
  unchanged; this change neither amends it nor reopens its write-side scope.
- No delivery, retry, status, timeline, session/trace propagation, frontend
  metadata display, runtime restart, manual SQL, or live data repair is in
  scope.
