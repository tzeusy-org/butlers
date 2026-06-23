## Why

The `/health` surface is six disconnected data-entry pages with **no landing** — the
bare `/health` URL has nowhere to go. This contradicts the health manifesto's core
promise that health is "a story told over weeks, months, and years… pieces only reveal
their meaning when held together." Today nothing holds them together: no place the butler
*announces* "here's how you've been," no continuity, no surfaced patterns. A 2026-06-20
maturity audit confirmed the surface is genuinely real (full CRUD over the SPO fact store),
so the redesign problem is **information architecture + orphaned-but-built backend capability
+ insight surfacing**, not fake data. This change gives the health butler a **Voice surface**:
a `/health` Overview that speaks the story, with the six pages reframed from "data entry" to
"trajectory," and realizes the manifesto's "Insight" pillar via a cost-safe, non-diagnostic
correlation engine.

Source: `docs/redesigns/2026-06-20-health-brief.md` (Phase D verdict `proceed-with-amendments`,
overall GREEN, ~$1/month/owner recommended design). Section 0 of that brief is binding intent.

## What Changes

- **New `/health` Overview** (landing, absent today): two-column editorial (`1.4fr / 1fr`) —
  left = serif Voice briefing + 4-cell KPI strip (latest weight, blood_pressure, heart_rate,
  blood_sugar); right = quiet attention index reading insight candidates. Every model-written
  line carries the honesty status pill (`llm · cached` / `templated`).
- **Six sub-pages reframed to Dispatch** (surfaces-not-cards, Display-500 not bold, rule-lists,
  severity glyphs, state color only when health demands it). Measurements lead with the trend
  rule-list, not the input box.
- **Chart-tab honesty fix**: the three dead `MeasurementChart` type tabs `glucose`/`sleep`/`oxygen`
  (which the create form can never produce → perpetual "No data") are replaced with the real
  predicates `blood_sugar`, `spo2`, `steps`; the working tabs (`weight`, `blood_pressure`,
  `heart_rate`, `temperature`) are unchanged.
- **Dashboard dose-logging affordance**: a write path so the owner can log a dose from the
  dashboard (today only a butler MCP tool writes `took_dose`).
- **Auto-refresh carve-out** (**BREAKING** to the existing universal 30s rule): deterministic
  CRUD/KPI/trend hooks keep 30s `refetchInterval`; the **LLM briefing and the insight feed are
  EXCLUDED from auto-refresh** — they use a 5-minute TTL cache with manual refresh via the status
  pill. This binds the new cost invariant against the pre-existing "every health hook refreshes
  every 30s" requirement.
- **New backend routes** (no new tables or columns — all over existing `health.facts`):
  - `POST /api/health/medications/{id}/doses` — dashboard dose-logging.
  - `GET /api/health/medications/{id}/adherence` — frequency-expected adherence (not the
    client's naive taken/total ratio).
  - `GET /api/health/nutrition/summary` — calorie/macro rollup over a range.
  - `GET /api/health/briefing` — LLM Voice composer mirroring `GET /api/dashboard/briefing`;
    templated-only by default, LLM elaboration behind a cost flag, per-owner 5-min TTL cache,
    never raises (templated fallback), with a **non-diagnostic voice-lint** as acceptance criteria.
- **Insight reader** on the Switchboard: `GET /api/switchboard/insights?butler=health&status=pending` — the
  Switchboard role already holds SELECT on `public.insight_candidates`, so no grant migration and
  schema isolation is preserved.
- **Insight-scan job extended**: emits **cross-signal correlation candidates** (Home Assistant
  environment ↔ sleep/symptoms; adherence dip → symptom flare; slow measurement drift) reached via
  **cross-butler MCP only** (never a direct `home.*` DB read). Cadence moves from monthly
  (`0 7 15 * *`) to **weekly** for Insight-pillar freshness (cost stays GREEN). Correlation runs in
  the scheduled job, **never live-on-GET** (the only RED design, de-scoped in Phase D).

## Capabilities

### New Capabilities

_None._ The change extends existing capabilities; no new `specs/<name>/` capability is introduced.
(The `/health` Overview is a new requirement *within* the existing `dashboard-domain-pages`
capability, consistent with how the global dashboard overview and memory overview already live
there.)

### Modified Capabilities

- `dashboard-domain-pages`: ADD the `/health` Overview landing requirement; reframe the six health
  sub-page requirements to the Dispatch language (surfaces-not-cards, rule-lists, severity glyphs);
  fix the measurement chart tabs to real predicates; add the dashboard dose-logging affordance; and
  carve the LLM briefing + insight feed out of the universal 30s auto-refresh rule (two MODIFIED
  refresh requirements).
- `butler-health`: ADD four routes (`POST .../doses`, `GET .../adherence`, `GET /nutrition/summary`,
  `GET /briefing`); MODIFY the "Health Insight Scan Job" to also emit cross-signal correlation
  candidates via cross-butler MCP; MODIFY the schedule cron from monthly to weekly.
- `proactive-insight-engine`: ADD a Switchboard insight **reader** endpoint
  (`GET /api/switchboard/insights?butler=health`) over the existing `public.insight_candidates` table.

## Impact

- **Frontend** (`frontend/src/`): new `pages/HealthOverviewPage.tsx` + `hooks/use-health-briefing.ts`;
  re-skin of the six health page components to Dispatch primitives; one new `/health` index route in
  `router-config.tsx`; nav repoint `/health/measurements` → `/health`. No new npm deps, no token
  additions (all Dispatch tokens present in `index.css:1-290`).
- **Backend** (health butler API `roster/health/api/`, `src/butlers/.../health`): four new routes;
  extension of `health_jobs.py` insight-scan to fan out HA reads via MCP. Lift
  `_frequency_to_doses_per_day` (`health_jobs.py:83`) into a shared helper so the route and job agree
  on the adherence denominator.
- **Switchboard**: one new read route over `public.insight_candidates`.
- **Schema**: **no new tables, no new columns, no DDL.** All reads/writes target existing
  `health.facts` predicates (`measurement_*`, `meal_*`, `medication`, `took_dose`, `symptom`,
  `condition`) and `public.insight_candidates` (`core_010`).
- **Cost**: recommended design ≈ $1/month/owner (cached Haiku briefing + correlation in the weekly
  job) vs ≈ $21 naive. The LLM briefing (#6) and correlation (#7) are individually deferrable behind
  the deterministic Overview (#1–#5).
- **Doctrine/manifesto**: no manifesto update required (chronicler + memory contacts are read-only).
  The non-diagnostic voice-lint is a hard spec acceptance criterion, not a nicety.
