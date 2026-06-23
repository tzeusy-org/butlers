## Why

RFC-0020 (Calendar Cross-Domain Overlay Read Exception, **Accepted** 2026-06-21)
settled the doctrine for the calendar fusion roadmap (epic `bu-1ajgg9`, under
`bu-l3k0zg`): the per-open / on-demand / LLM-synthesis design is **rejected**
under RFC 0010 reuse criteria #2 (deterministic / no LLM) and #3 (batch / not
real-time). The owner adopted the **no-LLM structured variant** — deterministic
contribution jobs precompute per-day overlay entries into each specialist's
`state` store; a migration-tracked read-only UNION view aggregates them; the
calendar reads the cached view at zero LLM cost.

This is the **missing capability-contract layer** for that work. RFC-0020 is the
doctrine (why the design is permitted); this change is the behavior contract
(what it produces and how it behaves) — and it deliberately MIRRORS the existing,
proven cross-butler briefing pattern (`general.v_briefing_contributions` /
`core_063` / the `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` contribution jobs) rather
than inventing a parallel mechanism.

Without this contract, the foundation bead (`bu-xcd1cp`: contribution jobs +
cached view) and the downstream overlay-render / prep-rail / day-briefing beads
would each invent their own envelope fields, API shapes, and empty-state handling
independently — drifting the surface before it exists. This change therefore
serves as the **spec gate** the `bu-l3k0zg` planning contract requires before the
foundation split begins.

## What Changes

### New capability: `calendar-overlay-aggregation`

The precompute-and-cache foundation, mirroring the briefing aggregation +
contribution capabilities:

- **Cross-Schema Overlay View.** A migration-tracked read-only SQL view
  `calendar.v_overlay_contributions` UNIONs `butler`, `key`, and `value` from
  each contributing specialist's `state` table, filtered to
  `key LIKE 'calendar/overlay/%'`, with a hardcoded `butler` source literal per
  UNION term — the exact shape of `general.v_briefing_contributions` (core_063).
  The view is empty-when-none and read-only by construction.

- **Overlay View Migration.** An Alembic migration creates the view and grants
  SELECT on each contributing specialist's `state` table to the calendar reader
  role; downgrade drops the view and revokes the grants (reversible, auditable).
  It reuses the `to_regclass`/optional-schema guard contract from `core_063`.

- **Overlay Contribution Schema + State Key Convention.** Each specialist writes
  a structured per-day envelope (typed `entries`, no generated prose) under the
  key `calendar/overlay/<YYYY-MM-DD>` — the overlay analogue of
  `briefing/daily/<YYYY-MM-DD>`.

- **Per-Butler Overlay Contribution Job.** Finance, travel, relationship, and
  health each register a `calendar_overlay_contribution` deterministic
  (`dispatch_mode="job"`, zero-LLM) job in the **existing**
  `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`, querying their domain tables and
  writing the per-day envelopes.

- **Contribution Job Scheduling.** Each contributing specialist's `butler.toml`
  gains a `calendar_overlay_contribution` schedule entry with
  `dispatch_mode="job"` on a fixed cron, registered the same way the briefing
  contribution jobs are.

### Modified capability: `dashboard-api` (the read surface)

The dashboard API gains the **read** surface that projects the cached view —
this is where the overlay/prep-rail/briefing UI reads from, at zero per-open LLM:

- **Calendar Overlay Projection.** `GET /api/calendar/workspace?view=overlays`
  projects the cached view into `UnifiedCalendarEntry` rows tagged with a new
  `source_type` value `"overlay_contribution"`; fail-open empty on missing
  view/query failure.
- **Meeting-Prep Rail Read.** A read endpoint for a selected event's prep context
  (attendees / notes / last-met), sourced from precomputed contributions — never
  a direct cross-butler read or a per-open LLM synthesis.
- **Day-Briefing Card Read.** A structured "tomorrow at a glance" day-card read
  with an honest empty-state and NO per-open LLM call.

## Capabilities

### New Capabilities

- `calendar-overlay-aggregation`: the overlay contribution envelope schema and
  state-key convention, the cross-schema read-only overlay view, the view+grants
  migration, the per-butler deterministic contribution jobs, and their
  scheduling.

### Modified Capabilities

- `dashboard-api`: adds the calendar overlay projection (`view=overlays`,
  `"overlay_contribution"` source type), the meeting-prep rail read endpoint, and
  the day-briefing card read endpoint. The underlying calendar provider
  architecture, sync model, and existing `view=user | butler` behavior are
  unchanged.

## Impact

- **New Alembic migration** (next in core chain after `core_136`): creates
  `calendar.v_overlay_contributions` and grants SELECT on each contributing
  specialist's `state` table to the calendar reader database role. Mirrors
  `core_063_v_briefing_contributions.py` and reuses its optional-schema guard.
- **Contribution jobs** registered in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY`
  (`src/butlers/scheduled_jobs.py`) for finance/travel/relationship/health, with
  implementations alongside the briefing jobs (`src/butlers/jobs/`). Each
  contributing specialist's `butler.toml` gains a `calendar_overlay_contribution`
  schedule entry.
- **Workspace API** (`src/butlers/api/routers/calendar_workspace.py`,
  `src/butlers/api/models/calendar_workspace.py`): widen the `view` query
  parameter to accept `overlays`; add `"overlay_contribution"` to
  `UnifiedCalendarSourceType`; add the prep-rail and day-briefing read endpoints.
- **No change to provider writes.** The overlay pipeline is read-only from each
  specialist's perspective and never touches Google Calendar or any butler's
  external provider. There is no LLM session anywhere in the read path.

## Sequencing

This change is the **spec gate** for the overlay foundation:

- **It must land (merge + `openspec validate --strict` green) before**
  `bu-xcd1cp` (the contribution-jobs + cached-view foundation implementation) and
  the downstream overlay-render / prep-rail / day-briefing beads begin.
- **The prep-rail read is contract-only here.** Its data source — co-attended
  edges (`bu-xgz7g.1`) and contact-link coverage (`bu-mcz0o9`) — is not built;
  this change specifies the read shape and the honest empty-state it returns
  until that coverage lands. It does NOT authorize a direct cross-butler read.
- **Additive to existing calendar surfaces.** The overlays/prep/briefing reads
  add new endpoints and one new `source_type` value; they do not modify
  `view=user | butler | proposals` behavior and can land alongside the other
  in-flight calendar changes without conflict.

## Out of Scope

- **Batched pre-rendered LLM summary.** RFC-0020 §Decision defers the optional
  narrative layer (batch LLM → pre-rendered `summary` text) as `bu-jdrkbj` (P4).
  No `summary` field appears in the v1 overlay envelope; adding it later is an
  additive delta.
- **FE ribbon/pill/day-card rendering.** The frontend overlay UI is a separate FE
  bead under epic `bu-l3k0zg`; this change defines the read contract it consumes.
- **Cross-butler write coupling / shared `public.calendar_overlays` table.**
  Contributing specialists write exclusively to their own schema's `state` store;
  the shared-public-table alternative was explicitly rejected in RFC-0020
  §Alternatives.
- **Lifestyle, Home, and Education overlay contributions.** These have no
  date-keyed calendar events in v1 and are excluded from the contributing set.
- **Live (intra-day) overlay currency.** Overlays are batch-precomputed daily;
  the per-open / on-demand path was rejected by RFC-0020 and is not in scope.
