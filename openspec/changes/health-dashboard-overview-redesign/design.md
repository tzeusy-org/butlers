## Context

The health surface (`frontend/src/pages/Health*`, six pages) is a real, full-CRUD UI over the
health butler's SPO fact store (`health.facts`), but it has no landing page and surfaces no
continuity or patterns. The 2026-06-20 integration brief
(`docs/redesigns/2026-06-20-health-brief.md`) originated a redesign from the canonical **Dispatch**
design language (`pr/overview/DESIGN_LANGUAGE.md`) and the current live implementation. A Phase D
feasibility pass returned `proceed-with-amendments` (overall GREEN), with the recommended design
costed at ≈ $1/month/owner.

Three backend reads are **wire-orphaned** (built, zero FE consumer): `/measurements/trend`,
`/measurements/latest`, `/measurements/sources`. The deterministic Overview and every reframed
sub-page are reads over endpoints that already exist. Only the dashboard dose-logging write and the
insight feed + LLM briefing/correlation engine need genuinely new backend. **No new table or column
is required for anything** — all reads/writes target existing `health.facts` predicates and
`public.insight_candidates` (`core_010`).

Binding constraints: Section 0 of the brief (design intent), the Dispatch language, the health
manifesto's "companion not a doctor" rule, and the project's schema-isolation security doctrine
(butlers reach other butlers' data only via MCP/Switchboard, never cross-schema SQL).

## Goals / Non-Goals

**Goals:**

- A `/health` Overview that lets the owner know, within ~5s, the single most important thing about
  their health right now, in one sentence, and reach any concerning signal in one click.
- Reframe the six sub-pages from "data entry" to "trajectory" using Dispatch primitives.
- Realize the manifesto's "Insight" pillar as a first-class, **non-diagnostic**, cost-safe
  correlation engine, surfaced in the Voice with an honesty pill.
- Keep the deterministic Overview (#1–#5 of the backend epic) fully functional with zero LLM, so the
  LLM briefing (#6) and correlation (#7) are deferrable.

**Non-Goals:**

- Not a diagnosis engine: no risk scores, no "you may have X", no medical advice.
- No celebration / gamification (no streak confetti, no green-check dopamine).
- No real-time / wearable-live dashboard (continuity is weeks-and-months; google_health stays a
  background collector).
- No new tables, columns, predicates, or DDL. No new npm dependencies, no new CSS tokens.
- No separate Home Assistant sensor panel in v1.

## Decisions

### D1 — Insight reader hosted on the Switchboard, not a new health route + grant migration

The Overview's attention index reads `public.insight_candidates`. Per `core_010_insight_tables.py`,
the insight broker (`butler_switchboard_rw`) holds full DML — and therefore SELECT — on
`public.insight_candidates`, while every other butler role (including `butler_health_rw`) holds
**INSERT only** and has **no SELECT** on that table. There is no blanket public-SELECT rule for butler
roles: `database-security` grants butler roles SELECT only on public tables *outside* the
write-authorization matrix, and `public.insight_candidates` is *inside* it. Hosting the reader as
`GET /api/switchboard/insights?butler=health` on the **Switchboard** (the role that already has the read)
therefore needs **no grant migration** and preserves schema isolation. _Alternative considered:_
`GET /api/health/insights` on the health role plus an idempotent grant-only migration — rejected
because the health role has no SELECT today (it would need that new grant), widening grants for no
benefit when the Switchboard already has the read.

### D2 — Briefing: templated-only by default, LLM behind a cost flag, 5-min TTL cache, never raises

`GET /api/health/briefing` mirrors `GET /api/dashboard/briefing` (see `dashboard-briefing` spec):
deterministic templated greeting + headline always; LLM elaboration only when a cost flag is on;
per-owner 5-minute TTL cache; the endpoint never raises (templated fallback on any LLM/lint/timeout
failure). Phase D cost table: cached Haiku briefing ≈ $0.77/mo (GREEN); templated-only = $0 (GREEN);
uncached Sonnet ≈ $2.30/mo (YELLOW → keep cache). _Alternative considered:_ live-on-load LLM without
cache — rejected (no stable cache, cost risk).

### D3 — Correlation in the scheduled job, never live-on-GET

Cross-signal correlation (HA env ↔ sleep/symptoms; adherence dip → flare; measurement drift) runs in
the scheduled `insight-scan` job and emits candidates; the Overview merely *reads* them through the
D1 reader. Phase D found live-on-GET fan-out is the **only RED** design (Sonnet $0.57/day/owner >
the $0.50 red line; no stable cache; violates Section 0's "no real-time dashboard"; adds cross-butler
MCP latency to a ~5s page). The scheduled-job design is ~10× cheaper daily and removes the RED. The
spec MUST NOT re-introduce live-on-GET correlation.

### D4 — Cross-butler data via MCP only (schema isolation)

Correlation needs Home Assistant sensor data, which lives in the **`home` butler**, not
`health.facts`. The composer reaches HA data via **MCP/Switchboard**, never a direct `home.*` DB
read. This makes correlation an orchestrated LLM+MCP step, not a SQL join, consistent with the
security doctrine.

### D5 — Correlation cadence: monthly → weekly

The current `insight_scan` cron is `0 7 15 * *` (monthly, the 15th;
`roster/health/butler.toml:58`). For Insight-pillar freshness the cadence moves to **weekly**
(`0 7 * * 1`, Mondays 07:00). Phase D confirms weekly stays GREEN on cost (the job is per-deployment,
not per-pageview; correlation ≈ $0.57/run amortizes to cents/month). _Alternative considered:_ keep
monthly — rejected as too stale for a first-class insight surface; daily — unnecessary for
weeks-and-months continuity.

### D6 — Auto-refresh carve-out binds the universal 30s rule

The `dashboard-domain-pages` spec mandates 30s `refetchInterval` on **all** health hooks
(§"Health data hooks with auto-refresh", §"Auto-refresh intervals by domain"). The LLM briefing and
the insight feed are deliberately **excluded** from auto-refresh: they use the D2 5-minute TTL cache
and a **manual** refresh via the status pill. This is a permanent cost guard — an auto-refreshing LLM
endpoint would multiply spawn cost. The carve-out is encoded as MODIFIED requirements on both
refresh requirements so the new invariant names and overrides the 30s rule it changes (rather than
silently leaving two contradictory rules).

### D7 — Non-diagnostic voice-lint is acceptance criteria, not a nicety

The briefing and insight copy MUST pass a health-specific voice-lint extending the global
`voice_lint_passes`; on failure → templated fallback, never raise. Rules (brief §4): diagnosis/advice
blocklist (`diagnos*`, "you (may|might|could) have", "risk of", "symptom of", "consistent with",
"indicates", "should see a doctor", treatment advice); causation guard (reject causal connectives —
co-occurrence framing only); no celebration/judgment (`!`, first person, praise, streak language);
tense (past for events, present for state, reject future — prediction is diagnosis-adjacent);
reference-range-not-verdict (pair a measurement with the owner's own stored range; reject clinical
adjectives like "elevated"/"dangerously high"); prefer "the" over "your" where it reads. These enter
the spec as scenarios on the briefing and correlation requirements.

### D8 — KPI strip + chart-tab honesty fix

The 4 KPI cells are latest **weight, blood_pressure, heart_rate, blood_sugar** (dropping the
glucose/sleep/oxygen mismatch). `MeasurementChart` type tabs are corrected from the dead
`glucose`/`sleep`/`oxygen` (the create form produces `blood_sugar`; wellness produces `spo2`/`steps`)
to the real predicates `blood_sugar`, `spo2`, `steps`, ending the perpetual "No data" state. The
hardcoded chart hex (`#3b82f6`/`#f43f5e`) is bridged to `--category-4` via a token→hex read of the
computed CSS var (recharts needs literal colors).

## Risks / Trade-offs

- **[LLM briefing voice quality may force a Sonnet tier-bump → cost YELLOW]** → keep the 5-min TTL
  cache and templated-only default; never enable per-load LLM elaboration without the cache (Phase D
  gate, not kill).
- **[Cross-butler MCP fan-out adds latency/failure modes to the insight job]** → correlation lives in
  the scheduled job, not the request path; a missing/slow `home` MCP degrades to fewer candidates,
  never a failed page load.
- **[`MedicationTracker` is the highest-churn component (card grid + nested DoseLog → Dispatch rows)]**
  → preserve the Active/All filter and adherence semantics; move dose history to a detail/expand
  affordance; keep the existing create/edit Dialog/Form.
- **[Existing `HealthPages.view-only.test.tsx` asserts current behavior]** → update/extend assertions
  during the refactor; do not silently delete them.
- **[New insight reader could leak non-health candidates]** → the reader MUST filter
  `origin_butler='health'` (the `butler=health` query param) and default to `status=pending`.

## Migration Plan

No schema migration. Deploy order follows the backend epic dependency graph:

1. Wire the three orphaned reads (`/measurements/trend`, `/latest`, `/sources`) — FE-only, no BE
   change.
2. `POST /medications/{id}/doses` (dashboard dose-logging; invalidate briefing cache).
3. `GET /nutrition/summary`.
4. `GET /medications/{id}/adherence` (shared frequency helper).
5. Insight reader on Switchboard (`GET /api/switchboard/insights?butler=health`) — powers the deterministic
   attention list. Steps 1–5 deliver a fully deterministic, zero-LLM Overview.
6. `GET /api/health/briefing` (LLM composer; depends on #5). Deferrable.
7. Cross-signal correlation in the `insight-scan` job + cron → weekly (depends on #5 + `home` MCP).
   Deferrable; highest cost/risk.

Rollback: each route is additive and independently revertible; the nav repoint and new `/health`
index route can be reverted without touching the six unchanged sub-paths (bookmarks keep working).

## Open Questions

All Phase 5 open questions from the brief are **resolved** and encoded:

- Insight reader host → Switchboard (D1). Briefing default → templated-only + cost flag + 5-min cache
  (D2). Correlation → scheduled job, weekly cadence (D3, D5). KPI strip + chart tabs → fixed (D8).
  Auto-refresh reconciliation → carve-out (D6). Voice-lint → acceptance criteria (D7).
- Remaining implementation-level questions (nav grouping style; recharts token→hex bridge mechanics;
  `view-only.test.tsx` update strategy) are component-level and deferred to the implementation beads,
  not spec-level.
