# RFC 0014: Chronicler Retrospective Time Butler

**Status:** Accepted
**Date:** 2026-04-24

## Summary

Chronicler is a retrospective "time butler" that reconstructs lived past time
from derived events and episodes produced by existing butlers, modules, and
connectors. It projects timestamped evidence from approved source surfaces
(canonical `sessions`, completed calendar instances, durable Spotify session
summaries, etc.) into two Chronicler-owned storage shapes: **point events**
(things that happened at an instant) and **episodes** (things that took a
span of time). Episodes MAY overlap. Every Chronicler record preserves source
provenance, boundary precision, and privacy/retention metadata; none of it
is computed on the fly for interactive queries.

Chronicler is **retrospective-only**. It does not plan, schedule, nudge, or
dispatch anything. It is a domain butler (not a staffer), has no ingress
routing authority, owns no connectors, and never ingests raw input directly
from an external service. It reads from other butlers' approved read surfaces
via migration-tracked views and its own projection checkpoints.

Chronicler does **not** replace the operational `/api/timeline` route that
aggregates current sessions and notifications across butlers. It adds a new
namespace `/api/chronicler/*` for reconstructed time.

## Motivation

The Butlers ecosystem already captures time-bearing signals across many
surfaces: LLM session records, Google Calendar events, Spotify listening
sessions, Steam play history, OwnTracks location points, Home Assistant
state history. These live in butler-specific or connector-specific tables
and are optimized for each owner's operational needs (e.g. `sessions` is
append-only, some connector tables have short retention).

When the user wants to answer "what did I do yesterday?" or "how much time
did I spend listening to music last week?" there is currently no coherent
retrospective view. Each butler could re-derive a partial view against its
own tables, but that would:

1. Cross schema boundaries repeatedly (violating RFC 0006).
2. Produce inconsistent overlap/precision/provenance semantics.
3. Re-compute expensive projections per query.
4. Miss the fact that real retrospective views naturally overlap: a commute
   (OwnTracks) happens while listening to music (Spotify) while no work
   session is active (sessions). Overlap is the common case, not an error.

Chronicler centralizes retrospective reconstruction into one butler that:

- Owns one schema (`chronicler`) with one role (`butler_chronicler_rw`).
- Projects each approved source surface into point events and/or episodes
  using deterministic, idempotent, checkpointed adapters.
- Preserves source refs, precision, and uncertainty in every row.
- Supports user corrections without discarding the original projection.
- Never invokes an LLM per ingestion event (see "Sparse Interpretation").

## Design

### D1: Data Model Overview

Chronicler owns the `chronicler` schema. Core tables are:

- `point_events` — things that happened at an instant (e.g. "session started",
  "arrived at home"). One timestamp, no duration.
- `episodes` — things that took a span of time (e.g. "listening session",
  "scheduled meeting attended", "work session"). Start + optional end;
  open-ended episodes are allowed (end NULL) when the source is live or
  the end has not yet been observed.
- `episode_event_links` — many-to-many between episodes and the point events
  that support them. A single episode MAY be derived from or accompanied by
  multiple evidence events (e.g. a work episode may have "session_started"
  and "session_completed" point events as boundary evidence).
- `overrides` — user-supplied corrections that supersede the canonical
  projection for a specific record. Corrections NEVER delete the original
  row; they layer on top via a corrected view.
- `projection_checkpoints` — per-source-adapter cursor state (last watermark,
  last run time, last error).
- `source_adapter_state` — per-source adapter registration, schema version,
  enabled/disabled, current health.
- `idempotency_keys` — stable keys for every projected row keyed on
  `(source_name, source_ref)` so replays never duplicate.

Every row on `point_events`, `episodes`, and `overrides` carries:

- `source_name` (text) — name of the adapter that produced this row.
- `source_ref` (text) — opaque stable reference back to the source record.
- `precision` (enum: `exact`, `minute`, `hour`, `day`, `unknown`) — boundary
  precision declared by the source.
- `privacy` (enum: `normal`, `sensitive`, `restricted`) — retention and
  visibility class inherited from the source declaration.
- `retention_days` (int or NULL) — retention policy, NULL means inherit
  Chronicler default.
- `tombstone_at` (timestamptz, nullable) — soft-delete marker; rows past
  tombstone are excluded from default views.

### D2: Source Compatibility Contracts

Each source adapter MUST declare a **compatibility record** before projection
runs against it. The declaration is stored via `source_adapter_state` and
includes:

- `source_name` — unique string (e.g. `core.sessions`, `google_calendar.completed`,
  `spotify.session_summary`).
- `chronicler_compatibility` — one of:
  - `supported` — adapter is live and projecting.
  - `deferred` — intentionally out of scope for the current release.
  - `not_time_bearing` — the source explicitly carries no retrospectively
    meaningful time data (e.g. static catalog rows).
  - `planned` — future support, not yet implemented.
- `read_surface` — schema-qualified view or table Chronicler reads from
  (migration-tracked, read-only).
- `boundary_semantics` — how to interpret start/end/timestamp in the source.
- `optional_schema` — if the source's schema is optional (module not
  installed on this deployment), the adapter MUST degrade gracefully.

Initial declarations (v1 of Chronicler):

| Source | Status | Read surface |
|---|---|---|
| `core.sessions` | supported | `public.sessions` (cross-butler view) |
| `google_calendar.completed` | supported | calendar module completed-instance view |
| `spotify.session_summary` | deferred pending durable evidence surface | — |
| `steam.play_history` | planned | — |
| `owntracks.points` | planned | — |
| `home_assistant.history` | planned | — |
| `google_health.*` | deferred | — |
| TTL diagnostic process logs | not_time_bearing | — |

A lint/check MUST ensure any new OpenSpec source spec with timestamp fields
declares either `chronicler_compatibility` or `not_time_bearing=true`.

### D3: Adapter Contract

Each Chronicler adapter:

- Reads from its declared `read_surface` only.
- Produces `point_events` and/or `episodes` with stable `source_ref` values
  so replays are idempotent.
- Writes to `projection_checkpoints` on every run (success or failure)
  with watermark + error detail.
- Never invokes an LLM per event.
- Degrades gracefully if its optional schema is missing (marks its state
  `inactive`, records the reason, exits cleanly).
- Runs under cron dispatch (`dispatch_mode=job`) from the Chronicler
  butler's schedule, not as a connector.

### D4: Corrections Model

User corrections are layered via `overrides`:

- Each override row references the canonical row and supplies updated
  fields (start, end, title, privacy, tombstone, or free-form notes).
- The canonical row is never updated. Replays re-project canonical values;
  overrides are re-applied on read.
- A corrected view (`v_episodes_corrected`, `v_point_events_corrected`)
  exposes the effective rows. Default API reads use the corrected view.
- Correction history is exposed via `GET /api/chronicler/episodes/{id}/corrections`.

### D5: Sparse Interpretation Guardrails

Chronicler MAY invoke an LLM for bounded Tier 2 interpretation paths:

- **Day-close summary** — one LLM call per day-close cron tick, input is
  a token-bounded episode/event bundle for the closing day.
- **Explicit drilldown** — one LLM call when the user asks "what was that
  meeting about?" with an episode ID and the bundle for that episode.
- **Ambiguity resolution** — one LLM call when two overlapping episodes
  conflict irreconcilably.
- **Correction assistance** — one LLM call to format a correction response
  when the user submits a natural-language correction.

The guardrails:

- Projection adapters MUST NOT call LLMs under any path.
- Tier 2 paths MUST assert `len(input_bundle) <= MAX_TIER_2_INPUT_BYTES`
  at call time and fail fast otherwise.
- Tier 2 paths MUST preserve provenance in output (cite source refs).
- Tests MUST exercise the no-LLM invariant for every adapter.

### D6: Switchboard Routing Boundary

Switchboard classification metadata is updated so:

- **Explicit retrospective time-review** requests route to Chronicler:
  - "what did I do yesterday / last week"
  - "how much time did I spend on X"
  - "when did I last do Y" (retrospective-only)
  - "fix the start time of my 3pm meeting yesterday" (correction intent)
- **Passive timestamped events** (Spotify now-playing, Steam game started,
  OwnTracks point, Home Assistant state change, Google Health reading) MUST
  continue routing to their owning domain butlers. Chronicler's projection
  runs asynchronously on schedule.
- **Domain-next-action questions** stay with the owning butler:
  - "recommend me music" → Lifestyle, not Chronicler.
  - "schedule lunch with X" → whoever owns calendar intent (Lifestyle for
    taste-linked, Relationship for social, etc.).
- **Lifestyle domain overlap** (e.g. "what was that song I was listening to
  during my run") stays with Lifestyle for taste/preference angle;
  Chronicler handles the chronological slice if the question is
  unambiguously retrospective.

### D7: API Surface

All Chronicler API routes live under `/api/chronicler/*`. The existing
`/api/timeline` route (cross-butler sessions + notifications) is preserved
untouched.

- `GET /api/chronicler/events` — list point events with filters.
- `GET /api/chronicler/episodes` — list episodes with filters.
- `GET /api/chronicler/episodes/{id}` — single episode detail (corrected).
- `GET /api/chronicler/episodes/{id}/events` — supporting events for an
  episode via `episode_event_links`.
- `GET /api/chronicler/episodes/{id}/corrections` — correction history.
- `POST /api/chronicler/episodes/{id}/corrections` — submit a correction.

All responses include provenance fields (source_name, source_ref,
precision, privacy). All list endpoints support cursor pagination.

### D8: Heart-and-Soul Position

Chronicler is added to `about/heart-and-soul/v1.md` under Butlers as the
ninth domain butler. The vision update is minimal: "Chronicler reconstructs
lived past time from other butlers' timestamped evidence; it does not plan,
ingest, or notify."

## Non-Goals

- Chronicler does NOT ingest raw external data. Adapters read from
  migration-tracked read surfaces that other butlers/connectors own.
- Chronicler does NOT own any connector.
- Chronicler does NOT route user messages (it has no Switchboard authority).
- Chronicler does NOT plan or schedule anything.
- Chronicler does NOT store raw source payloads; only projected rows with
  source refs.
- Chronicler does NOT replace `/api/timeline` — it adds a separate
  namespace.
- Chronicler does NOT project every time-bearing source in v1. The initial
  set is intentionally small (sessions + completed calendar); further
  sources are declared as `planned` or `deferred`.

## Migration and Rollout

1. Bootstrap schema and role (`scripts/init-db.sql` updated, migration tests
   added).
2. Register Chronicler in `roster/` with `butler.toml` and manifesto.
3. Apply migrations (Chronicler chain at `roster/chronicler/migrations/`).
4. Enable adapters one at a time, starting with `core.sessions`.
5. Add API routes (read + correction).
6. Update Switchboard routing guidance and classification prompt.
7. Add guardrail tests.

## Open Questions

- **Does Chronicler expose MCP tools?** v1 says yes, minimal — primarily
  read-side helpers (`chronicler_list_episodes`, `chronicler_get_episode`,
  `chronicler_submit_correction`). MCP tool surface is intentionally small
  because Chronicler's primary interaction surface is the dashboard API.
- **Correction conflict resolution?** Later overrides win. Multi-party
  correction is not a v1 concern (single-user system).
- **Episode merging?** Out of scope for v1. Overlap is allowed and not
  reconciled automatically.

## References

- RFC 0001 (daemon lifecycle) — Chronicler uses standard butler lifecycle.
- RFC 0002 (modules) — Chronicler's adapters are NOT modules; they are
  internal projection jobs dispatched by the scheduler.
- RFC 0003 (switchboard) — routing boundary rules (D6).
- RFC 0006 (database isolation) — Chronicler owns its schema; read
  surfaces from other schemas are migration-tracked views.
- RFC 0009 (context bus) — Chronicler MAY read context but does not write.
- RFC 0010 (cross-butler briefing) — precedent for Chronicler's
  cross-schema read surface pattern.
