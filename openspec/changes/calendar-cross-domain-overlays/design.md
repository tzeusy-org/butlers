# Design — Calendar cross-domain overlays

## Context

RFC-0020 (accepted 2026-06-21) permits the calendar workspace to read specialist
domain state through a migration-tracked read-only UNION view, following the
same five-guardrail pattern RFC 0010 established for `general.v_briefing_contributions`
(Alembic migration `core_063_v_briefing_contributions.py`). The pattern is:

1. Each specialist writes deterministic, no-LLM overlay contributions into its
   own `state` store under a filtered key prefix.
2. An Alembic migration creates a read-only UNION view in the calendar reader's
   schema that aggregates those contributions.
3. The workspace API reads the cached view at render — zero LLM, zero cross-schema
   fan-out at request time.

RFC-0020 §Decision selected the **no-LLM structured variant**: drop synthesis
entirely, write structured overlay entries (not prose), render them as ribbons /
pills / lists. This design implements that path.

## Decisions

### D1 — Key prefix: `calendar/overlay/<YYYY-MM-DD>` mirrors the briefing pattern

The briefing contribution key convention is `briefing/daily/<YYYY-MM-DD>` (per the
`cross-butler-briefing-contribution` spec). The overlay key convention mirrors it:
`calendar/overlay/<YYYY-MM-DD>`.

**Why this prefix?**

- `calendar/` namespace scopes overlay keys away from briefing keys in the same
  `state` table — they never collide and can be queried or pruned independently.
- `overlay/` distinguishes this access pattern from a possible future
  `calendar/prep-rail/<event-id>` pattern for the meeting-prep rail.
- `<YYYY-MM-DD>` is the date in SGT (UTC+8, the operator timezone) at job
  execution time, matching the briefing convention and keeping cross-feature key
  interpretation consistent.

The view key filter is `key LIKE 'calendar/overlay/%'` — it is deliberately
narrow (Guardrail #3 of RFC 0010: bounded key access, not whole-table).

### D2 — Contribution envelope: structured `entries` array, no `summary`

The briefing contribution envelope has a `summary` (pre-rendered prose). The
overlay envelope does NOT — RFC-0020 §Decision explicitly defers the narrative
layer to `bu-jdrkbj` (P4). The v1 envelope is:

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

**Fields:**
- `butler` (TEXT): string literal matching the source schema. The aggregation
  layer validates `value->>'butler' == butler` column (same as briefing
  aggregation) — a mismatch treats the entry as malformed.
- `date` (TEXT, ISO YYYY-MM-DD): the target calendar date these entries appear on.
- `has_entries` (boolean): quick "did this specialist have anything today" signal
  for the empty-state card without parsing `entries`.
- `entries` (array): ordered by priority descending (high → medium → low).
- `entries[].kind` (TEXT): domain-specific type string; see D4 for the canonical
  kind set per specialist.
- `entries[].label` (TEXT): human-readable short description suitable for a
  calendar pill. Never generated prose — always deterministically formatted from
  domain data.
- `entries[].priority` ("high" | "medium" | "low"): visual weight.
- `entries[].meta` (JSONB, optional): kind-specific structured data for FE
  rendering. Content is specialist-owned; the overlay layer does not interpret it.

**No `summary` field.** If `bu-jdrkbj` is ever promoted, it will add `summary`
as an ADDITIVE field to the envelope — backward compatible since the v1 consumer
only reads `has_entries` + `entries`.

### D3 — View lives in `calendar` schema (the reader), not `public`

`general.v_briefing_contributions` is in the `general` schema (the reader). The
overlay view is `calendar.v_overlay_contributions` — in the calendar schema (the
reader).

**Why not `public`?**

RFC-0020 §Alternatives explicitly rejects the shared-`public`-table approach:
"it introduces cross-butler write coupling to the `public` schema (read-only for
most butlers under RFC 0006) and sets precedent for arbitrary shared tables."
The same reasoning applies to the view. The view is in the reader's schema;
specialists only need SELECT grants on their own `state` tables — no `public`
write.

The SELECT grants are issued to the database role used by the calendar butler
(e.g. `butler_general_rw` pattern → `butler_calendar_rw`). If that role does not
exist at migration time, the migration creates it best-effort (matching the
`_ensure_role_exists` pattern in `core_063`).

### D4 — Contributing butler set: finance, travel, relationship, health

RFC-0020 §Design lists four specialist types that produce date-keyed calendar
events: finance (bills/renewals), travel (departures/check-ins), relationship
(birthdays/follow-ups), health (appointments).

**Education, Home, Lifestyle are excluded** because:
- Education contributions (review counts, streaks) are not date-bound in a
  calendar-day sense — they are relevant every day, not on specific future dates.
- Home contributions (sensor outliers, device alerts) are real-time, not
  future-date calendar events.
- Lifestyle contributions (consumption facts, taste preferences) are retrospective
  log entries, not calendar-forward events.

**Canonical `kind` values per specialist:**

| Specialist   | kind values |
|--------------|-------------|
| `finance`    | `bill_due`, `subscription_renewal` |
| `travel`     | `departure`, `arrival`, `check_in`, `check_out` |
| `relationship` | `birthday`, `important_date`, `follow_up` |
| `health`     | `appointment`, `medication_reminder` |

The `kind` is a discriminator for FE icon/color selection; the implementation is
NOT required to enumerate these values — additional kinds can be added additively
without a spec change, as long as `label` remains human-readable.

### D5 — Contribution job: one entry per future date in a rolling window

Each specialist job runs on a fixed cron (`50 6 * * *` = 06:50 UTC, before the
briefing pipeline at 06:55 UTC) and writes entries for today through
`+OVERLAY_LOOKAHEAD_DAYS` (proposed: 30 days). This means:

- The view always has contributions for the next ~30 days, refreshed daily.
- A new appointment booked mid-day may not appear until the next job run. This
  is acceptable for v1 (structured overlay, no live sync), matching the
  briefing pattern.
- Each job run **overwrites** the existing entry for each date (upsert semantics
  via `state_set`).
- Entries for dates more than 30 days past are pruned in the same job run to
  bound state store growth.

Cron `50 6 * * *` is chosen to precede the briefing contribution job at `55 6 * * *`
so that the combined briefing card (`briefing/combined/*`) can optionally
incorporate overlay data from the same day's run in the future.

### D6 — Honest empty-state: `has_domain_context` in the workspace response

`GET /api/calendar/workspace?view=overlays` must signal the difference between:

1. **"No events today"** — job ran, specialist found nothing, `has_entries=false`.
2. **"Job hasn't run yet or view missing"** — view absent / query failed / no
   contribution key for this date.

Both return `entries: []` in the unified projection. The difference surfaces in a
new response-level field `has_domain_context: bool`:

- `true`: the view was reachable and at least one specialist had a contribution
  for this date (even if all had `has_entries=false`).
- `false`: either the view was unreachable OR no specialist had a contribution
  for the requested date. In the FE, `false` renders "No domain context for this
  day" rather than silently omitting the section.

This field belongs on the workspace response envelope, not on individual
`UnifiedCalendarEntry` rows, because it is per-date metadata, not per-entry.

## Degraded-mode behavior

The `view=overlays` endpoint reads `calendar.v_overlay_contributions`. This view
does not call Prometheus, so it is NOT in the aggregate-metrics degraded-envelope
family (`aggregates_available`).

- **Read is fail-open.** If the view is absent (pre-migration), the contributing
  specialist schemas don't have state tables yet, or the query fails, the endpoint
  returns `entries: []` with `has_domain_context: false` — never HTTP 500.
- **Per-specialist failures are independent.** The UNION view's PostgreSQL
  execution does not fail the whole query if one UNION term's source table is
  missing (handled by `to_regclass`-style guards in the migration, matching
  `core_063`'s `_state_table_exists` pattern).
- **No write path.** Overlay contributions are written by each specialist to its
  own state store via `state_set`; no overlay write goes through the calendar
  schema. There is no calendar-side write degradation mode.

## Risks / Trade-offs

- **Contribution lag.** Overlays are batch-precomputed daily; an appointment
  booked at 08:00 SGT won't appear on the overlay until 06:50 UTC the next day.
  Acceptable for v1 structured overlays. If live currency is needed, the path is
  Switchboard fan-out at render time — explicitly the RFC-0020-rejected naive
  design — so it remains out of scope.
- **Lookahead window tuning.** 30 days is proposed; if specialist butlers generate
  many entries per day (e.g. a travel-heavy user), the state store could grow.
  Mitigated by the pruning step; the window can be narrowed to 14 days if needed.
- **New `source_type` value.** Adding `"overlay_contribution"` to the literal
  touches the workspace model and any exhaustive `source_type` switch, exactly as
  `"proposed_event"` did for `calendar-event-proposals`. Mitigated by it only
  being emitted on the `overlays` view.
- **Migration order.** The view references specialist state tables that may not
  exist at migration time (calendar module enabled before finance, etc.). Mitigated
  by the same `_state_table_exists` guard pattern from `core_063` — unavailable
  specialists emit a NULL-returning stub UNION term, and grants silently no-op.
