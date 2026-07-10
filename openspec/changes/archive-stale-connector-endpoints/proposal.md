## Why

Dead connector endpoint identities were cluttering fleet status and dragging
health rollups down. Four identities found in the 2026-07-05 ingestion history
audit (bu-33dm2) sit permanently `offline` yet cannot be removed because
ingestion history still references them:

- `google_health:degraded` — placeholder identity, offline since 2026-06-07
- `google_health:user:uniquosity@gmail.com:<uuid>` — never heartbeated
- `owntracks:unknown` — offline since 2026-05-13, superseded by per-device ids
- `home_assistant:homeassistant.parrot-hen.ts.net:443` — offline since
  2026-05-24, superseded by the `v-on-shenton` host

They inflate the offline count on every fleet-health rollup and add permanent
noise to the connectors roster. Disconnect (`deleted_at`) is the wrong tool — it
hides a row entirely, and these identities must stay reachable for history.

## What Changes

- **New soft-archive state** on `connector_registry`: an `archived_at`
  `TIMESTAMPTZ NULL` column (switchboard migration `sw_022`), distinct from the
  `deleted_at` disconnect soft-delete. Archived = still listed (collapsed
  "archived" section, reachable for history) but excluded from fleet-health
  rollups and alerting. Nothing is deleted.
- **Idempotent data-seed** in `sw_022` archives the four dead identities (the
  UUID-suffixed google_health identity matched by its stable owner prefix).
- **`GET /api/ingestion/connectors/summaries`** returns each connector with
  `archived` / `archived_at`; archived rows are still in the payload so the
  dashboard can group them, but the frontend separates them from the active
  roster (no attention/KPI contribution).
- **Fleet-health rollups exclude archived**: `GET
  /api/ingestion/connectors/cross-summary` and `GET
  /api/switchboard/connectors/summary` add `archived_at IS NULL` to their
  online/stale/offline counts. Archived `!=` degraded — archiving never masks a
  genuinely-failing *live* connector, which stays in the active roster.
- **Reusable mechanism**: audit-only (no Approvals gate)
  `POST …/{type}/{identity}/archive` and `…/unarchive` endpoints, mirroring
  `pause`. The four seeded archives use the migration; future archival uses the
  endpoint.
- **Dashboard**: a collapsed "archived · superseded" section below the roster,
  each row linking to connector detail so history stays reachable.

## Auto-archive policy (bu-33dm2 part 3) — deferred, proposed not implemented

The bead asks to *consider* an auto-archive policy for endpoints offline >30d
that share a `connector_type` with a newer online identity. This is deliberately
NOT auto-applied here: an automated archiver risks silencing a connector that is
merely quiet, which conflicts with the "archiving must never mask a failing live
connector" invariant. Proposed design for a follow-up: a daily maintenance pass
that flags (does not auto-archive) candidates where (a) `last_heartbeat_at` is
>30d old AND (b) a sibling identity of the same `connector_type` is currently
`online`, surfacing them as a review queue; archival stays a human/endpoint
action until the heuristic is proven safe against false positives.
