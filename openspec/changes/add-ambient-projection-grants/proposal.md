## Why

Every `/api/*` route needs the owner key or the owner cookie, so a wall display, an e-ink panel
or a Home Assistant sensor can show the system's state only by holding full owner authority. The
north star says a user may never send a message and still get value, and that staleness never
wears current-data authority: a glance in the house needs its own narrow credential and a payload
that visibly expires (bu-q7vx1q.11, JARVIS pursuit run 15).

Owner decision bu-xfhsrz (option B, 2026-10-04) put the grants in a `public` table rather than
the `dashboard_auth` enclave. Owner decision 2026-11-01 (option A) binds the first grant slice to
`GET /api/projections/{id}/glance`, because `GET /api/calendar/subscribe.ics` was removed by owner
decision bu-kf3n4i (option A) and stays removed.

## What changes

- New capability `ambient-projections`.
- `public.ambient_projections` (core_291) stores one grant per surface: label, surface kind,
  audience class (`household` or `owner_private`), route scope (`glance`), the SHA-256 digest of
  the token, the expected fetch interval, and the last fetch. Only the dashboard API login writes
  it; runtime roles read it.
- Owner-session API: `GET /api/projections`, `POST /api/projections` (the token is returned once),
  `DELETE /api/projections/{id}`.
- `OwnerAuthMiddleware` gains one exact-route branch: a token on
  `GET /api/projections/{id}/glance` (header `X-Projection-Token`, or `?token=`) unlocks that one
  route of that one projection and nothing else. Unknown, revoked or mismatched grants answer 404.
- `GET /api/projections/{id}/glance` returns a deterministic, audience-redacted payload classified
  by the briefing's own `classify`, with no model call: `state_class`, a line of at most 48
  characters, a needs-you count, a top item, `as_of`, `valid_until` and source coverage.
- Consumer liveness (grants slice S2): an active grant whose surface has not fetched for twice
  its expected interval adds one low-severity `projection` item to the briefing's attention
  model, derived on read from the table, so no write, schedule or UPDATE policy is needed.

## Deferred

None remaining in this change: Settings > Projections UI (`/settings/projections`, last-fetch
column, revoke, confirm-gated rotate) and one-verb rotate shipped with grants slice S3.

- Text and 1-bit PNG formats, the per-grant response cache, and the Home Assistant package
  example under `docs/` (glance slices S2 and S3).

## Impact

- New: `alembic/versions/core/core_291_ambient_projections.py`,
  `src/butlers/api/projection_grants.py`, `src/butlers/api/briefing/glance.py`,
  `src/butlers/api/routers/projections.py`.
- Changed: `src/butlers/api/owner_auth/http.py` (one branch), `src/butlers/api/app.py`
  (store slot, tracing exclusion), `src/butlers/api/router_registry.py`,
  `src/butlers/api/routers/dashboard_briefing.py` (sixth attention source); spec
  `dashboard-briefing` (Attention Item Sources).
