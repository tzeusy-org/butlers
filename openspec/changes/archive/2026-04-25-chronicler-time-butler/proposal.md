# Chronicler — Retrospective Time Butler

## Why

The Butlers ecosystem already captures time-bearing signals across many
surfaces (LLM sessions, Google Calendar events, Spotify listening sessions,
Steam play history, OwnTracks location points, Home Assistant state history)
but has no coherent retrospective view. When the user asks "what did I do
yesterday?" or "how much time did I spend listening to music last week?",
each butler today would have to re-derive its own partial view against its
own schema, violating Rule 3 (MCP-only cross-butler communication) or
re-computing expensive projections per query.

Chronicler centralizes retrospective reconstruction into one domain butler
that reads from approved migration-tracked source surfaces, writes to its
own schema, preserves source provenance + precision + uncertainty on every
row, supports user corrections without losing originals, and never runs an
LLM per ingestion event. RFC 0014 defines the full contract.

## What Changes

- **New domain butler `chronicler`** (`roster/chronicler/`): `butler.toml`,
  `MANIFESTO.md`, `CLAUDE.md`/`AGENTS.md`, migrations, dashboard API routes.
  Domain butler (not staffer). No connector ownership, no ingress routing.
- **Schema bootstrap** (`scripts/init-db.sql`): add `chronicler` schema and
  `butler_chronicler_rw` runtime role with grants.
- **Storage primitives** (`roster/chronicler/migrations/001_chronicler_tables.py`):
  `point_events`, `episodes`, `episode_event_links`, `overrides`,
  `projection_checkpoints`, `source_adapter_state`, `idempotency_keys`,
  and corrected views.
- **Source compatibility contracts**: declarations for `core.sessions`
  (supported), `google_calendar.completed` (supported),
  `spotify.session_summary` (deferred pending durable evidence surface),
  `google_health.*` (deferred), with a lint check for future timestamped
  specs missing `chronicler_compatibility`.
- **Projection adapters**: `core.sessions` (butler/agent session records
  → lifecycle events + work episodes) and `google_calendar.completed`
  (completed non-cancelled instances → scheduled-block episodes). Spotify
  adapter is gated on a durable summary evidence surface and shipped as
  a follow-up bead if no such surface exists.
- **Dashboard API** (`roster/chronicler/api/`): `/api/chronicler/events`,
  `/api/chronicler/episodes`, `/api/chronicler/episodes/{id}`,
  `/api/chronicler/episodes/{id}/events`,
  `/api/chronicler/episodes/{id}/corrections` (GET + POST). The existing
  `/api/timeline` route is preserved — it is the operational cross-butler
  session/notification stream and remains distinct from Chronicler.
- **Switchboard routing**: guidance text updated so explicit retrospective
  time-review requests route to Chronicler while passive timestamped
  events and domain-next-action questions continue routing to their
  owning butlers.
- **Sparse interpretation guardrails**: Tier 2 LLM entry points for
  day-close summary, drilldown, ambiguity resolution, correction
  assistance, with token-bounded input assertions. Projection adapters
  MUST NOT call LLMs; guardrail tests enforce the invariant.
- **Heart-and-Soul update** (`about/heart-and-soul/v1.md`): Chronicler
  added as the ninth domain butler.

## Capabilities

### New Capabilities

- `butler-chronicler`: Retrospective time reconstruction butler with point
  events, overlapping episodes, correction overlay, source adapters, and
  `/api/chronicler/*` API surface.

### Modified Capabilities

- `butler-switchboard`: Routing guidance recognizes Chronicler for explicit
  retrospective time-review requests. Passive timestamped events continue
  routing to owning domain butlers.

## Impact

- New `chronicler` schema and `butler_chronicler_rw` role in `scripts/init-db.sql`.
- New roster directory `roster/chronicler/`.
- New Alembic chain `roster/chronicler/migrations/` (branch label `chronicler`).
- New FastAPI router `roster/chronicler/api/router.py` auto-discovered
  by `src/butlers/api/router_discovery.py`.
- Switchboard classification guidance text updated in
  `roster/switchboard/tools/routing/classify.py`.
- New source compatibility lint in `tests/` that fails when a future
  timestamped OpenSpec source spec omits `chronicler_compatibility`.
- RFC 0014 added at `about/legends-and-lore/rfcs/0014-chronicler-time-butler.md`.

## Deferred

- **Spotify fine-grained track timeline**: explicitly out of scope. Only
  durable session summaries feed Chronicler if/when the durable evidence
  surface exists.
- **Google Health**: deferred per RFC 0014.
- **Steam / OwnTracks / Home Assistant projection adapters**: declared as
  `planned` but not implemented in this change.
- **Automatic episode merging / reconciliation**: out of scope; overlap
  is the expected case.
