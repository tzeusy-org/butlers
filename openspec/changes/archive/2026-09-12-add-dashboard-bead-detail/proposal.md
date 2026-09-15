## Why

Decisions and blocker surfaces can name a Bead but cannot safely lead the
owner to its detail. A live tracker bridge would widen the dashboard trust
boundary and a raw snapshot view could leak notes, metadata, identities, or
arbitrary links. The dashboard needs one narrow, truthful, same-origin detail
path over the already-exported read-only snapshot.

## What Changes

- Add a bounded, read-only Bead snapshot reader that projects only an explicit
  safe allowlist before any API serialization.
- Add `GET /api/beads/{id}` and `/beads/:id`, including `export_as_of` and
  honest unavailable/stale semantics.
- Link Decisions and blocker references only to the same-origin Bead detail
  route; render `external_ref` as inert text.
- Add privacy sentinels, API/UI/accessibility coverage, and the matching
  dashboard API/front-end contract documentation.

## Capabilities

### New Capabilities

- `dashboard-bead-detail`: Snapshot-backed, allowlisted, same-origin detail
  reads for a single Bead with explicit freshness and unavailable states.

### Modified Capabilities

- `dashboard-api`: Define the bounded Bead detail endpoint and its 404/503
  response semantics.
- `dashboard-decisions`: Require decision and blocker drill-downs to use only
  the same-origin Bead detail route while keeping external references inert.

## Impact

- Affected backend: dashboard router registration, Bead snapshot reader, typed
  API response models, and focused API tests.
- Affected frontend: route registration, typed API client/hook, Bead detail
  page, Decisions/blocker navigation, and focused Vitest coverage.
- Affected documentation: OpenSpec deltas and the frontend backend API
  contract.
- No database migration, Beads lifecycle mutation, credential, live
  `bd`/Dolt/GitHub bridge, external request, or new dependency.
