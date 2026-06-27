## Why

The calendar workspace read-model silently collapses cross-source duplicate
events: the same Google event synced into multiple butler schemas, and
cross-calendar copies that Google re-stamps with a fresh `origin_ref`, are
merged to a single entry by a two-pass dedup (origin-ref identity, then
title/start collapse). This is invisible — the user cannot see what was
collapsed, cannot tell a true duplicate from two genuinely-distinct events that
happen to share a title+time, and cannot steer how aggressive the collapse is.

Bead `bu-tjo2m1` (epic `bu-l3k0zg`) turns that hidden behavior into a reviewable
surface: expose the collapsed clusters, let the user keep a cluster separate, and
let the user pick the match strategy and the noisy-cluster reporting threshold.
No LLM, no provider write.

This is a contract-touching change (new dashboard API routes + a migration), so
it is specified here before/with implementation per the spec-driven repo's
planning contract.

## What Changes

- **NEW `GET /api/calendar/workspace/duplicates`** — re-runs the same two-pass
  dedup over the (un-collapsed) workspace rows for a date range and returns every
  cluster of >1 members it would collapse: the kept survivor plus the
  collapsed-away duplicates, the match pass (`origin_ref`/`title`) that grouped
  them, and whether the cluster is pinned keep-separate. Clusters with fewer
  members than the active `noisy_threshold` are filtered out. Fail-open: any read
  failure yields an `available=false` envelope with an empty cluster list, never
  an HTTP 500.
- **NEW `PATCH /api/calendar/workspace/dedup-rules`** — persists the workspace-
  global match strategy (`exact` | `balanced` | `aggressive`) and the
  `noisy_threshold`. The live workspace read honors the persisted rules, so
  changing the strategy changes what the read collapses.
- **NEW `POST /api/calendar/workspace/duplicates/keep-separate`** — pins (or
  unpins) a cluster so the dedup no longer collapses it; pinned clusters show all
  members in the workspace read but are still reported on the review surface
  (flagged).
- **NEW `public.calendar_dedup_rules` + `public.calendar_dedup_overrides`**
  (migration `core_144`) — workspace-global singleton rules row and one
  keep-separate override row per pinned cluster. They live in `public` because
  the dedup operates on the cross-schema merge of the read; CRUD is granted to
  every butler runtime role (mirroring `public.owner_scheduling_preferences`).
- **Out of scope (follow-up)** — the full frontend duplicate-review panel
  (cluster list + keep-separate toggles + strategy/threshold control) is a
  pure-FE surface over these contracts; it is spec-exempt under the single-owner
  craft-and-care override and tracked as a discovered follow-up.

## Impact

- Affected specs: `dashboard-api` (three new requirements).
- Affected code: `src/butlers/api/routers/calendar_workspace.py` (new endpoints +
  reusable `_dedup_workspace_rows` cluster builder),
  `src/butlers/api/read_models/calendar_workspace_v1.py` (dedup rules/override
  store), `src/butlers/api/models/calendar_workspace.py` (request/response
  models), `alembic/versions/core/core_144_calendar_dedup_overrides.py`.
- One database migration (two `public` tables). No new MCP tool, no provider
  write, no LLM session.
