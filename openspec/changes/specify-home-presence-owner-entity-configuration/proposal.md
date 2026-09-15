# Specify an owner-gated Home presence entity-configuration contract

## Why

`bu-8cdl1.11` (PR #3997) and its `in_space` extension (PR #4022) introduced the
`home` producer that publishes `at_home` / `in_space` from
`home:presence:owner_entities`, a state-store key holding the owner's HA
presence entity ids. No dashboard route was ever added to set it. Today the
key can be populated only through the pre-existing generic per-butler state
surface (`PUT /api/butlers/home/state/{key}`, MCP-proxied) or the
`state_set`/`state_get` MCP tools -- both are unauthenticated by any
owner-specific control, unvalidated (no entity-id shape, no reference, no
duplicate check), and unguarded by any concurrency token, so
`at_home`/`in_space` remain `unconfigured` unless someone bypasses the
product boundary (`bu-bdlr3`).

`bu-bdlr3`'s shaping pass (evidence packet
`coordinator-evidence/bu-bdlr3-shaping-20260906`) found no approved
specification authorizes an HTTP surface that discloses and mutates these
private HA entity ids, and that the implementation bead must stay blocked
behind exactly that spec plus `bu-pb6oy` (the browser-auth prerequisite this
draft does not resolve). This change defines that contract only. It performs
no read, write, or runtime effect.

## What Changes

- Add two future dashboard routes,
  `GET /api/home/settings/presence/owner-entities` and
  `PUT /api/home/settings/presence/owner-entities`, that read and
  whole-list-replace the single JSON array stored at
  `home:presence:owner_entities`, using the table's existing integer
  `version` column for optimistic concurrency.
- Require the existing fail-closed `require_dashboard_owner_control` boundary
  (or an owner-approved successor from `bu-pb6oy`) before either route reads
  or buffers a body, acquires a database pool, or observes protected state.
  `authenticated_principal()` is used only after authentication to derive the
  audit actor.
- Preserve both `at_home` and `in_space` consumer semantics: the accepted
  entity-id shape covers the non-person room-sensor case `in_space` already
  reads from the same list, not just `person.*`/`device_tracker.*`.
- Define exact validation (bounded, deduplicated, syntactically valid,
  locally-observed-in-`ha_entity_snapshot` entity ids), version-conflict
  semantics (`expected_version` CAS keyed off the state row's real version,
  including the absent-row `version=0` case), idempotent no-op retries, and
  fixed content-blind failure categories.
- Exempt the exact PUT route from `DashboardAuditMiddleware`'s generic body
  capture and define an explicit, aggregate-only audit/log/metric/trace
  allowlist that never carries a raw entity id.
- Honestly enumerate the *existing* generic disclosure and lost-update
  surfaces this new route does **not** close: the generic
  `GET/PUT/DELETE /api/butlers/{name}/state[/{key}]` routes and the
  `state_get`/`state_set`/`state_list`/`state_delete` MCP tools (enabled by
  default -- Home's `butler.toml` sets no `core_groups` restriction) can
  still read or blind-overwrite this exact key with no owner gate, no
  validation, and no CAS. The new route's guarantees hold only among callers
  of the new route; this draft does not claim to close those pre-existing
  paths, and does not implement a fix for them.
- Require future real-Postgres, API, and producer-compatibility tests before
  implementation is considered complete, including forced concurrent CAS
  interleavings and a proof that `PUT` itself performs no producer/context
  effect.

## Capabilities

### New Capabilities

- `home-presence-configuration`: owner-authenticated, dashboard-only read and
  whole-list replacement of the Home butler's configured HA presence entity
  ids consumed by the `at_home` / `in_space` context producer.

### Modified Capabilities

None. This draft adds no requirement to `core-state` or `context-bus`; it
specifies a new consumer contract layered on their existing behavior.

## Impact

- Affected future API surface: `GET`/`PUT`
  `/api/home/settings/presence/owner-entities`.
- Affected future storage seam: the existing `home:presence:owner_entities`
  row in the Home schema's `state` table (no migration -- the row already
  carries JSONB `value` and integer `version`).
- Affected future audit seam: a narrow `DashboardAuditMiddleware` body-read
  exemption for the exact PUT route, plus one explicit content-blind audit
  event.
- This draft changes no runtime behavior, schema, frontend, state data,
  credentials, deployment, or environment.
- `bu-bdlr3` remains blocked on independent exact-head security/privacy
  review, separate owner approval of the exact reviewed artifact, and
  resolution of `bu-pb6oy`. Merge, queue, deployment, and any real HA
  identifier submission are separate acts this draft does not authorize.
