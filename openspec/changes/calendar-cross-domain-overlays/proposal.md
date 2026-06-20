## Why

RFC-0020 (Calendar Cross-Domain Overlay Read Exception, accepted 2026-06-21)
settled the doctrine for the calendar fusion roadmap (`bu-1ajgg9`, epic
`bu-l3k0zg`): the per-open / on-demand / LLM-synthesis design is rejected under
RFC 0010 reuse criteria #2 (deterministic) and #3 (batch), and the owner adopted
the **no-LLM structured variant** — deterministic contribution jobs precompute
per-day overlay entries into each specialist's state store; a migration-tracked
read-only view aggregates them; the calendar reads the cached view at zero LLM
cost.

Before any implementation bead (`bu-xcd1cp`: contribution jobs + cached view
foundation) can safely build on this, the **behavior contract** must exist as a
validated OpenSpec change. RFC-0020 is doctrine (why the design is permitted);
this change is the contract (what it produces and how it behaves). Without it,
each implementation bead would invent its own API shapes, envelope fields, and
empty-state handling independently — drifting the surface before it exists.

This change therefore serves as the **spec gate** that `bu-1ajgg9.1` requires
before the cf1 foundation split (`bu-xcd1cp`) and downstream overlay-render /
prep-rail / briefing-card beads can begin.

## What Changes

### New Capability: `calendar-cross-domain-overlays`

A new capability covering the full precompute-and-cache pipeline:

- **Per-day contribution envelope.** Each specialist butler writes structured
  overlay entries into its state store under the key
  `calendar/overlay/<YYYY-MM-DD>`. The envelope contains a typed `entries` array
  (kind, label, priority, kind-specific `meta`) with no generated prose — the
  no-LLM structured variant accepted in RFC-0020 §Decision.

- **Cross-schema read-only view.** An Alembic migration creates
  `calendar.v_overlay_contributions` that UNIONs `butler`, `key`, and `value`
  columns from each specialist's `state` table, filtered to
  `key LIKE 'calendar/overlay/%'`. Each UNION term hardcodes the source butler
  as a string literal (RFC 0010 Guardrail #2). PostgreSQL forbids writes through
  a UNION view (Guardrail #1). SELECT grants are migration-tracked and reversible
  on downgrade (Guardrail #5).

- **Contributing butler set.** The four specialists with date-keyed domain
  events that belong on a calendar: `finance` (bills/renewals), `travel`
  (departures/check-ins), `relationship` (birthdays/follow-ups), `health`
  (appointments). Education, Home, and Lifestyle do not produce calendar-date
  events and are excluded from the overlay set.

- **Deterministic contribution jobs.** Each contributing specialist registers a
  `calendar_overlay_contribution` job (`dispatch_mode="job"`) that queries its
  domain tables, writes the per-day envelope, and prunes entries older than 30
  days to bound state store growth.

- **Overlays workspace projection.** `GET /api/calendar/workspace?view=overlays`
  reads the aggregated view and projects each entry into a `UnifiedCalendarEntry`
  tagged `source_type="overlay_contribution"`. Entries are non-editable in place
  (`editable=false`). The endpoint is fail-open: a missing view or query failure
  returns an empty entries list, never HTTP 500.

- **Briefing day-card read-model.** The per-day `view=overlays` response is the
  data source for the "tomorrow at a glance" day-briefing card (`bu-1ajgg9`).
  Empty-state is explicit: when no contributions exist (pre-job-run or specialist
  disabled) the response has `entries: []` and `has_domain_context: false` so the
  FE can render "No domain context for this day" rather than silently omitting the
  card section.

### Modified Capability: `module-calendar`

The `module-calendar` spec is extended to document:

- `view=overlays` as a valid `view` parameter on
  `GET /api/calendar/workspace`, alongside the existing `user | butler | proposals`
  values.
- `"overlay_contribution"` added to the `UnifiedCalendarSourceType` literal.
- The `view=overlays` fail-open semantics (consistent with `view=proposals`).

## Capabilities

### New Capabilities

- `calendar-cross-domain-overlays`: the per-day contribution envelope schema,
  contribution state key convention, cross-schema overlay view contract, the
  contributing butler set and their job contracts, the overlays workspace
  projection, and the briefing day-card read-model with honest empty-state.

### Modified Capabilities

- `module-calendar`: widened `view` parameter to accept `overlays`; added
  `"overlay_contribution"` to `UnifiedCalendarSourceType`. The underlying MCP
  tools, provider architecture, sync model, and event CRUD tools are unchanged.

## Impact

- **New Alembic migration** (next in core chain after `core_134`): creates
  `calendar.v_overlay_contributions` view and grants SELECT on each contributing
  specialist's `state` table to the calendar reader database role.
- **Contribution job schedule entries**: each contributing specialist butler's
  `butler.toml` gains a `calendar_overlay_contribution` entry with
  `dispatch_mode="job"`. Cron timing runs before the morning briefing pipeline
  (proposed: `50 6 * * *`, 06:50 UTC = 14:50 SGT).
- **Workspace API** (`src/butlers/api/routers/calendar_workspace.py`,
  `src/butlers/api/models/calendar_workspace.py`): widen the `view` query
  parameter to accept `overlays`; add `"overlay_contribution"` to
  `UnifiedCalendarSourceType`.
- **No change to provider writes.** The overlay pipeline is read-only from each
  specialist's perspective and does not touch Google Calendar or any butler's
  external provider.

## Sequencing

This change is the **spec gate** for the overlay foundation:

- **This change must land (merge + `openspec validate --strict` green) before**
  `bu-xcd1cp` (contribution jobs + cached view foundation implementation) can
  start. `bu-xcd1cp` is the cf1 implementation bead that builds the actual jobs
  and migration; it is currently blocked behind this gate.

- **Does not depend on any other in-flight calendar change.** The overlays
  surface is additive: it adds a new `view=overlays` endpoint and a new
  `source_type` value and does not modify existing `user | butler | proposals`
  behavior. It can land alongside or after the other calendar changes without
  conflict.

## Out of Scope

- **Meeting-prep rail.** The RFC-0020 prep-rail feature (attendees + recent
  Gmail threads + relationship notes for a selected event) depends on
  contact-link coverage (`bu-mcz0o9`) and co-attended edges (`bu-xgz7g.1`) that
  are not yet built. It is explicitly NOT part of this contract.
- **Batched pre-rendered LLM summary.** RFC-0020 §Decision defers the optional
  narrative-summary layer (batch LLM → pre-rendered text) as `bu-jdrkbj` (P4).
  No `summary` field appears in the v1 envelope; adding it is a future delta.
- **FE ribbon/pill rendering.** The frontend overlay UI (ribbons, pills, day
  columns) is a separate FE bead under epic `bu-l3k0zg`; this change defines the
  contract it consumes.
- **Cross-butler write coupling.** Contributing specialists write exclusively to
  their own schema's `state` store. There is no `public.calendar_overlays` shared
  table — that alternative was explicitly rejected in RFC-0020 §Alternatives.
- **Lifestyle, Home, and Education overlay contributions.** These three specialists
  have no date-keyed events that are meaningfully calendar-overlayable in v1 and
  are excluded from the contributing butler set.
