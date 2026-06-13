## Why

The doctrine in `about/heart-and-soul/vision.md` Non-Negotiable Rule 1 states: the user owns the instance, the data, the credentials, and the agents. The dashboard currently shows no surface where that ownership is made concrete. A SaaS dashboard hides its plumbing because the user does not own it; this user does. Owner sovereignty gets its own page (`/system`), as settled in `about/heart-and-soul/design-language.md` Settled Direction #3.

## What Changes

- **New Capability**: `system-overview-page` -- the dashboard SHALL expose a `/system` route that surfaces instance-level ownership facts in one place: software version, uptime, database size and growth, backup recency, data egress catalog ("your data has been seen by these external endpoints"), and per-butler last-touch heartbeats.
- **Modified Capability**: `butler-base-spec` -- the internal interface by which butler daemons report instance facts (last-touch, active session count) SHALL be documented as an explicit contract, since the System page aggregates across all butlers. DB connection pool stats are explicitly deferred to a future extension.
- The `/system` route is added to the router and nav-config under the Telemetry section.
- New dashboard API endpoints under `/api/system/*` return the ownership facts.
- The egress catalog component surfaces "data has been seen by [list]" -- this is privacy-sensitive and requires an explicit access contract (owner-only in v1).

## Capabilities

### New Capabilities

- `system-overview-page`: The `/system` page, its route, data model, API surface, and privacy contract. Covers the five ownership-fact domains: instance identity, database state, backup state, data egress catalog, and per-butler heartbeats.

### Modified Capabilities

- `butler-base-spec`: Document the internal interface for instance-level facts that butler daemons already track (last-touch, active session count) so the System page aggregator has a normative contract to consume. DB connection pool stats are out of scope for v1 (they require in-process access the dashboard API layer does not have).

## Impact

- **New route**: `/system` registered in the React Router config and `nav-config.ts` under Telemetry.
- **New API router**: `src/butlers/api/routers/system.py` with endpoints under `/api/system/`.
- **New frontend page**: `frontend/src/pages/SystemPage.tsx` with tile components.
- **No database schema changes**: all facts are derived at query time from existing tables (`{schema}.sessions`, `switchboard.dashboard_audit_log`, `public.ingestion_events`, and `pg_database`/`pg_stat_user_tables`). No asyncpg pool stats in v1.
- **Privacy surface**: the egress catalog reveals which external actors have received data from this instance. Access is gated to the owner contact in v1.
- **Specs touched**: new `system-overview-page`, delta to `butler-base-spec`.
