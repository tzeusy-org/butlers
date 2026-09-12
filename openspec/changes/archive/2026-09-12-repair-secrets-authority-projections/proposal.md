## Why

The Secrets passport currently lets compatibility projections obscure the
authoritative state that the system has already persisted. A successful Codex
CLI test can leave the passport stale because same-key per-butler mirrors are
aggregated as independent health votes and the completed test does not refresh
the inventory query. The connector-owned Spotify projection has the inverse
problem: it is synthesized as generic `warn` even though its actual authority
is the closed connector-status response and its only valid recovery actions are
Connect or Re-authorize.

These are projection defects, not credential-storage defects. The contract
must name which existing source is authoritative at each surface so the repair
does not create a second credential, probe, or lifecycle path.

## What Changes

- Make a canonical shared `cli[]` row authoritative for its CLI credential key
  in passport state and CLI-family KPI counts; same-key per-butler `cli-auth`
  system rows remain raw compatibility evidence but cannot override or
  duplicate that authority.
- Preserve legacy display behavior when a canonical CLI row is absent by
  allowing per-butler mirrors to supply a most-severe fallback.
- Refresh both Secrets inventory and CLI-provider status after any CLI Test
  request that completes with HTTP success, including a persisted failed test.
- Derive the presentation-only `u:spotify` spine state from the existing closed
  Spotify connector-status response, with explicit loading, healthy,
  not-set, authorization-needed, and failed states.
- Preserve Spotify's connector-owned lifecycle, the generic Secrets probe
  prohibition, and all existing content-blind response boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-api`: define canonical CLI authority precedence for inventory
  projection and aggregate counts.
- `butler-secrets`: define CLI Test query refresh and connector-status-derived
  Spotify passport presentation.

## Impact

- `src/butlers/api/routers/secrets_v2.py`: conceptual CLI-family aggregation
  and KPI precedence only; raw source rows and credential data remain intact.
- `frontend/src/components/secrets/passport/`: matching inventory adaptation,
  CLI test query invalidation, and Spotify spine-state projection.
- Existing interfaces remain in place: `GET /api/secrets/inventory`,
  `POST /api/cli-auth/{provider}/test`, and
  `GET /api/connectors/spotify/status`.
- No migration, credential rewrite, provider payload expansion, generic
  Spotify probe, runtime-data mutation, or credential operation is introduced.
