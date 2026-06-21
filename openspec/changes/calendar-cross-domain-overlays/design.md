# Design — Calendar cross-domain overlays

## Context

RFC-0020 (Accepted 2026-06-21) permits the calendar workspace to read specialist
domain state through a migration-tracked read-only UNION view, reusing the
five-guardrail pattern RFC 0010 established for `general.v_briefing_contributions`
(Alembic migration `core_063_v_briefing_contributions.py`). The owner adopted the
**no-LLM structured variant**: drop synthesis entirely, write structured overlay
entries (not prose), render them as ribbons / pills / lists with zero LLM at
render.

The proven briefing pattern this change mirrors has three moving parts:

1. **Per-butler deterministic contribution jobs** (`daily_briefing_contribution`)
   registered in `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` with `dispatch_mode="job"`
   that write structured envelopes into each butler's own `state` store under a
   filtered key prefix (`briefing/daily/<date>`), at zero LLM cost.
2. **A read-only cross-schema UNION view** (`general.v_briefing_contributions`,
   created by `core_063`) in the reader's schema, with a hardcoded `butler`
   source literal per UNION term, a key filter, and migration-tracked reversible
   SELECT grants.
3. **A read of the cached view** with no LLM in the path.

This design applies all three to the calendar overlay.

## Decisions

### D1 — REUSE, do not rebuild

This is the load-bearing decision. The overlay foundation is the briefing
foundation pointed at a new key prefix and reader schema:

- **Contribution jobs register in the EXISTING registry.** Each contributing
  specialist's `calendar_overlay_contribution` handler is added to the same
  `_DETERMINISTIC_SCHEDULE_JOB_REGISTRY` (`src/butlers/scheduled_jobs.py`) that
  already holds `daily_briefing_contribution`, under the specialist's butler
  name, with `dispatch_mode="job"`. No new dispatch system, no new scheduler
  path, no new job-registry mechanism is introduced.
- **The view MIRRORS `general.v_briefing_contributions` (core_063).** Same
  shape: `SELECT '<butler>' AS butler, key, value FROM <schema>.state WHERE key
  LIKE '<prefix>/%'`, UNION ALL across the contributing schemas, with the
  `butler` value a hardcoded string literal per term (not from the payload).
- **The migration reuses `core_063`'s optional-schema guard contract** (AGENTS.md
  "Core migration optional-schema guard"): `_state_table_exists` via
  `to_regclass`, `_ensure_role_exists` best-effort role creation, and a
  NULL-returning stub UNION term (`SELECT NULL::text AS butler ... WHERE FALSE`)
  for any specialist whose `state` table is absent at migration time. This is why
  the view is safe on fresh/core-only databases and in tests.

The only intentional differences from briefing are: the reader schema is
`calendar` (not `general`), the key prefix is `calendar/overlay/` (not
`briefing/daily/`), the contributing set is four specialists (not seven), and the
envelope carries typed `entries` instead of `highlights` + `summary` (no prose;
see D3).

### D2 — The SPLIT: one view+grants unit + one contribution job per butler

The foundation decomposes into independently-shippable, parallelizable units that
map 1:1 onto split beads:

| Unit | Scope |
|------|-------|
| **view** | The `calendar.v_overlay_contributions` UNION view + reversible SELECT grants, in one Alembic migration (mirrors `core_063`). |
| **job-finance** | `finance.calendar_overlay_contribution` deterministic job (bills / subscription renewals). |
| **job-travel** | `travel.calendar_overlay_contribution` deterministic job (departures / arrivals / check-ins / check-outs). |
| **job-relationship** | `relationship.calendar_overlay_contribution` deterministic job (birthdays / important dates / follow-ups). |
| **job-health** | `health.calendar_overlay_contribution` deterministic job (appointments / medication reminders). |
| **render** | `view=overlays` workspace projection → overlay ribbons/pills from the cached view. |
| **prep-rail** | Meeting-prep rail read (attendee / notes / last-met), contribution-sourced. |
| **briefing** | Day-briefing card read (structured "tomorrow at a glance"). |

The view unit has no dependency on any job (it returns zero rows until jobs run —
the empty-when-none contract). Each job unit depends only on the key convention
(D4), not on the view or on other jobs. The render/prep-rail/briefing reads depend
on the view existing but degrade fail-open before it does. This is what lets the
beads be worked in parallel after this gate lands.

### D3 — Option A: no LLM at render; any narrative is batch pre-rendered (deferred)

Per RFC-0020 §Decision, the v1 path is the structured variant — **no LLM anywhere
in the read path**:

- Contribution jobs are pure deterministic SQL/Python (the same class of work as
  the briefing contribution jobs). They produce structured `entries`, not prose.
- The `view=overlays`, prep-rail, and day-briefing reads are pure DB projections
  of the cached view. They construct entries/cards from already-computed fields;
  they do not call an LLM, and they do not fan out to sibling schemas at request
  time.

The v1 envelope therefore has **no `summary` field**. The briefing envelope's
pre-rendered `summary` exists because the EOD briefing has a single daily LLM
session that consumes it; the overlay render has none. If batched pre-rendered
prose is ever wanted, RFC-0020 step 4 / `bu-jdrkbj` (P4) specifies it as **one
scheduled session** that writes a pre-rendered `summary` into the contribution —
an additive envelope field, displayed verbatim, never a per-open session. That
layer is **deferred** and out of scope here.

### D4 — Contribution key convention: `calendar/overlay/<date>`

The briefing key convention is `briefing/daily/<YYYY-MM-DD>`. The overlay
convention mirrors it exactly: `calendar/overlay/<YYYY-MM-DD>`, where `<date>` is
the target calendar date in SGT (UTC+8) at job execution time.

- The `calendar/` namespace scopes overlay keys away from `briefing/daily/` keys
  in the same `state` table — they never collide and prune independently.
- `overlay/` distinguishes this from a possible future `calendar/prep/<event-id>`
  prefix for the prep-rail's own precomputed cache.
- The view key filter is `key LIKE 'calendar/overlay/%'` — deliberately narrow
  (RFC 0010 Guardrail #3: bounded key access, not whole-table).

A single job run MAY write multiple keys (one per date in a rolling lookahead
window), overwriting via `state_set` (upsert), and prunes keys whose date suffix
is older than the retention window — exactly the briefing contribution cleanup
pattern.

### D5 — Contribution envelope: structured `entries`, no prose

```json
{
  "butler":      "finance",
  "date":        "2026-06-21",
  "has_entries": true,
  "entries": [
    {
      "kind":     "bill_due",
      "label":    "Credit card bill",
      "priority": "high",
      "meta":     { "amount": 450.00, "currency": "SGD" }
    }
  ]
}
```

- `butler` (TEXT): string literal matching the source schema; the projection
  validates `value->>'butler'` equals the view's `butler` source column (the
  briefing aggregation does the same) — a mismatch marks the entry malformed.
- `date` (TEXT, ISO YYYY-MM-DD): the target calendar date.
- `has_entries` (boolean): "did this specialist have anything for this date" —
  lets the empty-state card distinguish "ran, found nothing" from "hasn't run".
- `entries[]`: ordered priority-descending; each has `kind` (FE
  icon/color discriminator), `label` (deterministically formatted, never prose),
  `priority` (`high|medium|low`), and an optional kind-specific `meta` object the
  overlay layer does not interpret.

### D6 — Honest empty-state: `has_domain_context` on the read responses

The `view=overlays` and day-briefing reads must distinguish:

1. **"Nothing today"** — jobs ran, specialists found nothing (`has_entries=false`).
2. **"Context unavailable"** — view absent / query failed / no contribution key
   for the date yet.

Both yield `entries: []`. The difference surfaces in a response-level
`has_domain_context: bool` (`true` only when the view was reachable AND at least
one specialist had a contribution for the date). The FE renders `false` as
"No domain context for this day" rather than silently dropping the card section.

### D7 — Prep-rail is contribution-sourced, never a direct cross-butler read

The meeting-prep rail (attendees + relationship notes + last-met for a selected
event) is exactly the kind of cross-domain data RFC-0020 forbids reading
on-demand at calendar-open time. Its read endpoint therefore reads only
**precomputed contribution data** through the same cached-view discipline (a
`calendar/prep/<event-id>` contribution prefix, populated by a future
deterministic job once co-attended edges `bu-xgz7g.1` and contact-link coverage
`bu-mcz0o9` exist). Until that coverage lands, the prep-rail read returns its
honest empty-state. It MUST NOT issue a live `SELECT ... FROM relationship.*` at
request time and MUST NOT spawn an LLM session — both are the RFC-0020-rejected
naive paths.

## Degraded-mode behavior

The overlay reads project `calendar.v_overlay_contributions`. The view does not
call Prometheus, so these endpoints are NOT in the aggregate-metrics
degraded-envelope family (`aggregates_available`).

- **Reads are fail-open.** If the view is absent (pre-migration), a contributing
  specialist's `state` table is missing, or the query fails, the endpoint returns
  `entries: []` with `has_domain_context: false` — never HTTP 500.
- **Per-specialist failures are independent.** The migration's
  `to_regclass`/optional-schema guard emits a NULL-returning stub term for any
  missing specialist, so one absent schema never fails the whole view query.
- **No write path.** Overlay contributions are written by each specialist to its
  own `state` store via `state_set`; nothing is written through the calendar
  schema, and the view is a UNION (not updatable) — there is no calendar-side
  write degradation mode.

## Risks / Trade-offs

- **Contribution lag.** Overlays are batch-precomputed daily; an appointment
  booked mid-day won't appear until the next job run. Acceptable for v1 (the
  briefing has the same property); the live alternative is the RFC-0020-rejected
  naive design.
- **Lookahead window growth.** A travel-heavy day could write many entries per
  date; bounded by the per-run prune step and a tunable lookahead window.
- **New `source_type` value.** `"overlay_contribution"` touches the workspace
  model and any exhaustive `source_type` switch, exactly as `"proposed_event"`
  did for `calendar-event-proposals`; mitigated by it only being emitted on the
  `overlays` view.
- **Migration order.** The view references specialist `state` tables that may not
  exist when the calendar migration runs; mitigated entirely by the reused
  `core_063` optional-schema guard (stub UNION term + best-effort grants).
