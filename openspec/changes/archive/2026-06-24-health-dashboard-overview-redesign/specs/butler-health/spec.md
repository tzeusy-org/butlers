# Health Butler Role — Delta for Health Overview Redesign

## ADDED Requirements

### Requirement: [TARGET-STATE] Dashboard dose-logging route

The health butler's dashboard API SHALL expose `POST /api/health/medications/{id}/doses` so the
owner can log a medication dose from the dashboard. The route writes the same `took_dose` temporal
fact that the `medication_log_dose` MCP tool writes — no new table and no new column.

#### Scenario: Logging a dose writes a took_dose fact

- **WHEN** the owner calls `POST /api/health/medications/{id}/doses` with an optional `taken_at`,
  `skipped` (default `false`), and `notes`
- **THEN** the route MUST store a `took_dose` fact with `valid_at = taken_at` (or now), `scope =
  'health'`, `entity_id = owner_entity_id`, and `metadata = {medication_id, skipped, notes}`
- **AND** it MUST return the created dose with HTTP 201
- **AND** it MUST invalidate the per-owner briefing cache so the next briefing reflects the dose

#### Scenario: No new schema for dose logging

- **WHEN** the dose-logging route runs
- **THEN** it MUST write to the existing `health.facts` store using the `took_dose` predicate
- **AND** it MUST NOT require any new table, column, or DDL

### Requirement: [TARGET-STATE] Frequency-expected adherence route

The health butler's dashboard API SHALL expose `GET /api/health/medications/{id}/adherence` that
returns adherence computed against the medication's prescribed frequency (expected doses), not a
naive taken/total ratio. The expected-dose denominator MUST be derived from the same
frequency-to-doses-per-day helper used by the insight-scan job, lifted into a shared module so the
route and the job agree on the denominator.

#### Scenario: Adherence response shape

- **WHEN** the owner calls `GET /api/health/medications/{id}/adherence?window_days=30`
- **THEN** the response MUST include `expected_doses`, `taken_doses`, `skipped_doses`, and
  `adherence_rate`
- **AND** `expected_doses` MUST be computed from the prescribed frequency over the window, not from
  the count of logged doses

#### Scenario: Shared denominator with the insight job

- **WHEN** the adherence route and the insight-scan job both compute expected doses for the same
  medication and window
- **THEN** they MUST use the same shared frequency-to-doses-per-day helper
- **AND** they MUST produce the same expected-dose denominator

### Requirement: [TARGET-STATE] Nutrition summary route

The health butler's dashboard API SHALL expose `GET /api/health/nutrition/summary` that aggregates
calories and macros across meal facts in a date range, exposing over HTTP the rollup the
`nutrition_summary` MCP tool already computes. No new table or column.

#### Scenario: Nutrition summary aggregation

- **WHEN** the owner calls `GET /api/health/nutrition/summary?start=&end=`
- **THEN** the response MUST include `total_calories`, total macros, a `daily_avg`, `meal_count`, and
  `days`
- **AND** the figures MUST be aggregated from existing meal facts (`meal_*` predicates) over the
  range, with no new schema

### Requirement: [TARGET-STATE] Health Voice briefing route

The health butler's dashboard API SHALL expose `GET /api/health/briefing`, an owner-only LLM Voice
composer that mirrors `GET /api/dashboard/briefing` (see the `dashboard-briefing` spec). It returns a
`Briefing` object (`greet`, `headline`, `elaboration`, `source`, `state_class`, `generated_at`). It
SHALL be **templated-only by default**, with LLM elaboration enabled only behind a cost flag; it
SHALL cache per owner for 5 minutes; and it SHALL never raise — any LLM, lint, or timeout failure
falls through to the deterministic templated paragraph. The `source` field is exactly one of
`"llm"` (a model-written elaboration) or `"fallback"` (the deterministic templated paragraph); the
dashboard BriefingStatus pill renders `source = "llm"` as `llm · cached` and `source = "fallback"`
as `templated`.

The briefing copy MUST pass a **non-diagnostic voice-lint** that extends the global
`voice_lint_passes`; on failure the elaboration MUST fall through to the templated fallback (never
raise). The lint MUST reject: (1) diagnosis/advice tokens — `diagnos*`, "you (may|might|could)
have", "risk of", "symptom of", "consistent with", "indicates", "should see a doctor", or any
treatment advice; (2) celebration/judgment — exclamation marks, first-person pronouns, praise tokens,
green-check/streak language; (3) future-tense markers ("will", "going to") — prediction is
diagnosis-adjacent; (4) clinical verdict adjectives ("elevated", "dangerously high") where a
measurement should instead be paired with the owner's own stored reference range.

#### Scenario: Templated-only by default

- **WHEN** the cost flag for LLM elaboration is off
- **THEN** `GET /api/health/briefing` MUST return a deterministic templated briefing with `source =
  "fallback"`
- **AND** it MUST NOT invoke an LLM

#### Scenario: Cached LLM elaboration when the flag is on

- **WHEN** the cost flag is on and an owner calls the endpoint within 5 minutes of a prior successful
  call
- **THEN** the response MUST be served from the per-owner 5-minute TTL cache
- **AND** `generated_at` MUST reflect the original cached generation time

#### Scenario: Voice-lint rejects a diagnostic line

- **WHEN** an LLM elaboration contains a diagnosis/advice token (e.g. "risk of" or "you may have")
- **THEN** the elaboration MUST be rejected and replaced with the templated fallback
- **AND** `source` MUST be `"fallback"`
- **AND** the endpoint MUST NOT raise

#### Scenario: Endpoint never raises

- **WHEN** the LLM transport is unreachable or times out
- **THEN** the response MUST be HTTP 200 with the templated fallback paragraph
- **AND** `source` MUST be `"fallback"`

#### Scenario: Owner-only access

- **WHEN** a non-owner session calls `GET /api/health/briefing`
- **THEN** the response MUST be HTTP 403 and no cache entry is read or written

## MODIFIED Requirements

### Requirement: Health Butler Schedules
The health butler runs health checks, memory jobs, and insight scans. The `insight-scan` job SHALL
run on a **weekly** cadence (changed from the prior monthly `0 7 15 * * *`) so cross-signal
correlation freshness meets the Insight pillar; Phase D confirms weekly stays cost-GREEN because the
job is per-deployment, not per-pageview.

#### Scenario: Scheduled task inventory
- **WHEN** the health butler daemon is running
- **THEN** it executes: `memory-consolidation` (`0 */6 * * *`, job), `memory-episode-cleanup`
  (`0 4 * * *`, job), and `insight-scan` (`0 7 * * 1`, job: evaluate health domain data and generate
  insight candidates, including cross-signal correlation candidates)
- **AND** the `insight-scan` cron MUST be weekly, not the prior monthly `0 7 15 * * *`

### Requirement: Health Insight Scan Job
The health butler's `insight-scan` job SHALL evaluate health domain data and produce insight
candidates covering measurement gaps, medication refill timing, symptom trend alerts, health streaks,
**and cross-signal correlations**. All candidates are submitted via the Switchboard's
`propose_insight_candidate()` MCP tool — the butler does not write to `public.insight_candidates`
directly. Cross-signal correlation runs **only** inside this scheduled job; it MUST NOT run
live-on-GET (the Phase D RED design). Correlation that requires data owned by another butler (e.g.
Home Assistant environmental sensors in the `home` butler) MUST reach that data via cross-butler
MCP/Switchboard, never a direct cross-schema DB read.

#### Scenario: Insight-scan job handler registration
- **WHEN** the health butler starts
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job`
  dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()`
  MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue

#### Scenario: Measurement gap insights
- **WHEN** the insight-scan job evaluates measurement gaps
- **THEN** it SHALL generate candidates for measurement types where the time since last measurement
  exceeds 2x the user's typical cadence for that measurement type
- **AND** the typical cadence SHALL be the median interval between the last 10 measurements of that
  type
- **AND** gaps exceeding 3x the typical cadence SHALL have priority 75, and gaps exceeding 2x SHALL
  have priority 55
- **AND** the `dedup_key` SHALL be `health:measurement-gap:{measurement-type}`, `expires_at` 3 days
  from generation, and types with fewer than 3 historical entries SHALL be excluded

#### Scenario: Medication refill timing insights
- **WHEN** the insight-scan job evaluates active medications
- **THEN** it SHALL generate candidates for medications where dose-logging frequency suggests supply
  depletion within 14 days
- **AND** depletion within 3/7/14 days SHALL have priority 90/75/60 respectively
- **AND** the `dedup_key` SHALL be `health:medication-refill:{medication-id}`, `expires_at` the
  estimated depletion date, and `active=false` medications SHALL be excluded

#### Scenario: Symptom trend alerts
- **WHEN** the insight-scan job evaluates recent symptom logs
- **THEN** it SHALL generate candidates when the same symptom is logged 3+ times in the past 7 days
  with severity >= 3 on the 1-10 severity scale (per the Health Data Conventions; the symptoms page
  bands severity at 2/5/8 on the same 1-10 scale)
- **AND** priority SHALL be 70, `dedup_key` SHALL be
  `health:symptom-trend:{symptom-name}:{year-week}`, `expires_at` 3 days, and the message SHALL
  include count and average severity

#### Scenario: Health streak recognition
- **WHEN** the insight-scan job detects a positive health streak
- **THEN** it SHALL generate candidates for notable streaks (e.g. "7 consecutive days of logging
  meals")
- **AND** priority SHALL be 25, `dedup_key` `health:streak:{measurement-type}:{streak-milestone}`,
  `cooldown_days` 30, `expires_at` 7 days, milestones at 7/30/60/90/180/365 days
- **AND** streaks MUST be stated as observations, never framed as a reward or achievement

#### Scenario: Cross-signal correlation candidates via cross-butler MCP
- **WHEN** the insight-scan job evaluates cross-signal correlations
- **THEN** it SHALL generate candidates for co-occurrence patterns such as Home Assistant
  environment (bedroom temperature, air quality) ↔ sleep/symptom signals, adherence dips preceding
  symptom flares, and slow measurement drift
- **AND** it SHALL obtain any data owned by another butler (e.g. Home Assistant data in the `home`
  butler) via cross-butler MCP/Switchboard, never a direct cross-schema DB read
- **AND** the candidate message SHALL use **co-occurrence framing only**, never a causal claim and
  never a clinical interpretation (e.g. "on nights the bedroom ran warm, sleep ran shorter"), so it
  passes the non-diagnostic voice-lint
- **AND** correlation SHALL run only in this scheduled job and SHALL NOT be computed live-on-GET

#### Scenario: Correlation candidates surface through the insight reader
- **WHEN** a cross-signal correlation candidate is accepted into `public.insight_candidates`
- **THEN** the `/health` Overview attention index SHALL surface it by reading
  `GET /api/switchboard/insights?butler=health&status=pending` (it MUST NOT recompute the correlation on read)
