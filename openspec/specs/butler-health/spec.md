# Health Butler Role

## Purpose
The Health butler (port 41103) is a health tracking companion for measurements, medications, conditions, symptoms, diet, and nutrition.

## Requirements

### Requirement: Health Butler Identity and Runtime
The health butler tracks health data with compound JSONB values and domain-specific analysis tools.

#### Scenario: Identity and port
- **WHEN** the health butler is running
- **THEN** it operates on port 41103 with description "Health tracking assistant for measurements, medications, diet, food preferences, nutrition, meals, and symptoms"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `health` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the health butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), `memory`, and `google_health` (no config keys required)
- **AND** `google_health` declares `dependencies = []` at the `Module` level — it does not declare a module-level dependency on `memory` and instead relies on the butler's module topological-init ordering plus the handler's runtime use of memory-module MCP tools

### Requirement: Health Butler Tool Surface
The health butler provides measurement, medication, condition, symptom, meal, and research tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the health butler
- **THEN** it has access to: `measurement_log`, `measurement_history`, `measurement_latest`, `medication_add`, `medication_list`, `medication_log_dose`, `medication_history`, `condition_add`, `condition_list`, `condition_update`, `symptom_log`, `symptom_history`, `symptom_search`, `meal_log`, `meal_history`, `nutrition_summary`, `research_save`, `research_search`, `health_summary`, `trend_report`, and calendar tools
- **AND** it SHALL additionally have access to: `sleep_latest`, `sleep_history`, `hr_history`, `hrv_history`, `spo2_history`, `breathing_rate_history`, `activity_summary`, `vo2_max_latest`

### Requirement: Health Data Conventions
Health data uses compound JSONB values and standardized severity scales.

#### Scenario: Measurement conventions
- **WHEN** measurements are logged
- **THEN** compound JSONB values are supported (e.g., blood pressure as `{"systolic": 120, "diastolic": 80}`)
- **AND** symptom severity is rated 1-10 (1 = mild, 10 = severe)
- **AND** medication adherence is calculated based on frequency

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

### Requirement: Health Butler Skills
The health butler has check-in and trend interpretation skills.

#### Scenario: Skill inventory
- **WHEN** the health butler operates
- **THEN** it has access to `health-check-in` (guided health check-in workflow covering medication adherence, vitals, symptoms, diet, and summary) and `trend-interpreter` (measurement trend interpretation and anomaly detection for BP, weight, glucose, heart rate), plus shared skills `butler-memory` and `butler-notifications`

### Requirement: Health Memory Taxonomy
The health butler uses a clinical memory taxonomy with permanence based on condition chronicity.

#### Scenario: Memory classification
- **WHEN** the health butler extracts facts
- **THEN** it uses subjects like medication names, condition names, or "user"; predicates like `medication`, `medication_frequency`, `condition_status`, `symptom_pattern`, `dietary_restriction`, `allergy`; permanence `stable` for chronic conditions and allergies, `standard` for current medications and symptoms, `volatile` for acute symptoms

### Requirement: CRUD-to-SPO migration — health domain (bu-ddb.2)
The health butler migrates 6 dedicated CRUD tables (measurements, symptoms, medication_doses, medications, conditions, research) to temporal SPO facts using the memory module's facts table. All facts use `scope='health'` and `entity_id = owner_entity_id`. Full predicate taxonomy and metadata schemas are in `openspec/changes/crud-to-spo-migration/specs/predicate-taxonomy.md`.

#### Scenario: Measurement tools as temporal fact wrappers
- **WHEN** `measurement_log` is called to record a measurement
- **THEN** it MUST internally call `store_fact` with `predicate='measurement_{type}'`, `valid_at=measured_at`, `entity_id=owner_entity_id`, `scope='health'`, and `metadata={value, unit, notes}`
- **AND** `content` MUST be a human-readable summary (e.g. `"Weight: 72.5 kg"`)
- **AND** when `measurement_history` or `measurement_latest` is called
- **THEN** they MUST query facts with predicate matching `measurement_{type}`, ordered by `valid_at DESC`

#### Scenario: Symptom tools as temporal fact wrappers
- **WHEN** `symptom_log` is called
- **THEN** it MUST internally call `store_fact` with `predicate='symptom'`, `valid_at=occurred_at`, `entity_id=owner_entity_id`, `scope='health'`, and `metadata={severity, condition_id, notes}`
- **AND** `content` MUST be the symptom name
- **AND** `symptom_history` and `symptom_search` MUST query facts with `predicate='symptom'`

#### Scenario: Medication dose tools as temporal fact wrappers
- **WHEN** `medication_log_dose` is called
- **THEN** it MUST internally call `store_fact` with `predicate='took_dose'`, `valid_at=taken_at`, `entity_id=owner_entity_id`, `scope='health'`, and `metadata={medication_id, skipped, notes}`
- **AND** `medication_history` MUST query facts with `predicate='took_dose'`

#### Scenario: Medication property fact wrappers
- **WHEN** `medication_add` is called
- **THEN** it MUST internally call `store_fact` with `predicate='medication'`, `valid_at=NULL` (property fact), `entity_id=owner_entity_id`, `scope='health'`, and `metadata={name, dosage, frequency, schedule, active, notes}`
- **AND** `content` MUST be `"{name} {dosage} {frequency}"`
- **AND** `medication_list` MUST query facts with `predicate='medication'` and `validity='active'`
- **AND** multiple active medications (different names) MUST coexist because content differentiates them

#### Scenario: Condition property fact wrappers
- **WHEN** `condition_add` or `condition_update` is called
- **THEN** it MUST internally call `store_fact` with `predicate='condition'`, `valid_at=NULL`, `entity_id=owner_entity_id`, `scope='health'`, and `metadata={name, status, diagnosed_at, notes}`
- **AND** `content` MUST be `"{name}: {status}"`
- **AND** `condition_list` MUST query facts with `predicate='condition'` and `validity='active'`

#### Scenario: Research property fact wrappers
- **WHEN** `research_save` is called
- **THEN** it MUST internally call `store_fact` with `predicate='research'`, `valid_at=NULL`, `entity_id=owner_entity_id`, `scope='health'`, `content=research_content`, and `metadata={title, tags, source_url, condition_id}`
- **AND** `research_search` MUST use `memory_search` with `predicate='research'` and `scope='health'`

#### Scenario: health_summary and trend_report query facts
- **WHEN** `health_summary` is called
- **THEN** it MUST aggregate across facts with `scope='health'` and `entity_id=owner_entity_id` for all active medications, conditions, recent measurements, and recent symptoms
- **AND** when `trend_report` is called for a measurement type
- **THEN** it MUST query facts with `predicate='measurement_{type}'` and `valid_at` in the requested date range, returning the same response shape as before (data_points, trend, min, max, avg)

### Requirement: Meal tracking as bitemporal facts
The health butler stores meal observations using the memory module's meal-specific temporal predicates and nutrition metadata, enabling historical meal querying and pattern analysis.

#### Scenario: Meal predicates and temporal facts
- **WHEN** the health butler logs a meal via `meal_log`
- **THEN** the meal data MUST be stored as temporal facts using predicates: `meal_breakfast`, `meal_lunch`, `meal_dinner`, or `meal_snack` (depending on meal type)
- **AND** each meal fact MUST have `valid_at` set to the meal's timestamp (when the meal was consumed)
- **AND** multiple meals of the same type on different days represent separate temporal facts with different `valid_at` values and MUST NOT supersede each other
- **AND** the subject MUST be "user" or the entity ID of the person
- **AND** the `scope` MUST be "health" to isolate meal facts

#### Scenario: Meal nutrition metadata
- **WHEN** a meal fact is stored
- **THEN** the meal `content` field MUST contain a human-readable description of the meal (e.g., "Grilled chicken salad with olive oil dressing")
- **AND** the fact's `metadata` JSONB MUST contain: `estimated_calories` (NUMBER), `macros` (OBJECT with `protein_g`, `carbs_g`, `fat_g`), `logged_at` (ISO 8601 timestamp), `meal_items` (ARRAY of food items with optional allergen tags)
- **AND** metadata MUST support optional fields: `mood_before` (1-10), `satisfaction` (1-10), `symptom_notes` (TEXT), `tags` (ARRAY of dietary markers like "low-carb", "vegetarian", "spicy")

#### Scenario: Meal tools as fact-query wrappers
- **WHEN** `meal_log` is called to record a meal
- **THEN** it MUST internally call `memory_store_fact` with the appropriate meal predicate, `valid_at`, and nutrition metadata
- **AND** when `meal_history` is called to retrieve meal observations
- **THEN** it SHOULD prefer `memory_search` or `memory_recall` with `scope='health'` and predicate filters for `meal_breakfast`, `meal_lunch`, `meal_dinner`, `meal_snack` for open-ended semantic searches
- **BUT** when the query requires predicate filtering combined with a date-range constraint (e.g., "meals between 2024-01-01 and 2024-01-07"), `meal_history` MAY use direct asyncpg SQL against the health butler's fact tables instead of `memory_search`/`memory_recall`, because those memory APIs do not expose structured date-range predicates
- **AND** `nutrition_summary` MUST aggregate nutrition metadata across multiple meal facts in a date range by sum of calories and macros; it MAY use direct SQL for this aggregation since it requires date-bounded GROUP BY queries that `memory_search`/`memory_recall` do not support

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
- **THEN** it SHALL register an `insight-scan` job handler that is invokable by the scheduler's `job` dispatch mode

#### Scenario: Candidate submission via Switchboard MCP
- **WHEN** the `insight-scan` job generates a candidate
- **THEN** it SHALL submit the candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool
- **AND** if the tool returns `{"status": "filtered"}`, the butler SHALL skip remaining candidates (verbosity is off)
- **AND** if the tool returns `{"status": "error"}`, the butler SHALL log the error and continue with remaining candidates

#### Scenario: Measurement gap insights
- **WHEN** the insight-scan job evaluates measurement gaps
- **THEN** it SHALL generate candidates for measurement types where the time since last measurement exceeds 2x the user's typical cadence for that measurement type
- **AND** the typical cadence SHALL be computed as the median interval between the last 10 measurements of that type
- **AND** gaps exceeding 3x the typical cadence SHALL have priority 75
- **AND** gaps exceeding 2x the typical cadence SHALL have priority 55
- **AND** the `dedup_key` SHALL be `health:measurement-gap:{measurement-type}`
- **AND** `expires_at` SHALL be 3 days from generation
- **AND** measurement types with fewer than 3 historical entries SHALL be excluded (insufficient data for cadence computation)

#### Scenario: Medication refill timing insights
- **WHEN** the insight-scan job evaluates active medications
- **THEN** it SHALL generate candidates for medications where dose logging frequency suggests the supply will run out within 14 days (based on prescribed frequency vs logged doses)
- **AND** estimated depletion within 3 days SHALL have priority 90 (time-critical)
- **AND** estimated depletion within 7 days SHALL have priority 75
- **AND** estimated depletion within 14 days SHALL have priority 60
- **AND** the `dedup_key` SHALL be `health:medication-refill:{medication-id}`
- **AND** `expires_at` SHALL be the estimated depletion date
- **AND** medications with `active=false` SHALL be excluded

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

### Requirement: Wellness Memory Taxonomy

The Health butler SHALL store Google-Health-derived facts using the SPO temporal-fact taxonomy established by `crud-to-spo-migration`, under `scope='health'` and `entity_id=owner_entity_id`. Measurement-shaped records SHALL use the canonical `measurement_{type}` predicate pattern; genuinely non-measurement records (sleep sessions) SHALL use their own predicates. Every new predicate SHALL be registered in the memory module's `predicate_registry`.

#### Scenario: Predicate taxonomy

- **WHEN** the Health butler ingests a `wellness/google_health` envelope
- **THEN** it SHALL store facts using one of the following predicates:
  - `sleep_session` — `valid_at` = session start timestamp, metadata includes `session_id`, `end_time`, `duration_ms`, `efficiency`, `minutes_asleep`, `minutes_awake`, `stages: {deep, light, rem, wake}`
  - `sleep_stage_summary` — `valid_at` = session start, metadata includes `session_id` and full stage breakdown
  - `measurement_resting_hr` — `valid_at` = date 00:00 in the owner's local timezone, metadata includes `value` (bpm) and `heart_rate_zones`
  - `measurement_hrv` — `valid_at` = date 00:00 local, metadata includes `daily_rmssd`, `deep_rmssd`, `coverage`
  - `measurement_spo2` — `valid_at` = date 00:00 local, metadata includes `avg`, `min`, `max`
  - `measurement_breathing_rate` — `valid_at` = date 00:00 local, metadata includes `value` (breaths per minute)
  - `measurement_steps` — `valid_at` = date 00:00 local, metadata includes `value`, `distance_km`, `floors`
  - `measurement_active_minutes` — `valid_at` = date 00:00 local, metadata includes `very_active`, `fairly_active`, `lightly_active`, `sedentary`
  - `measurement_vo2_max` — `valid_at` = date 00:00 local, metadata includes `range_low`, `range_high`, `midpoint`
- **AND** every wellness fact SHALL have `entity_id = owner_entity_id`
- **AND** every wellness fact SHALL have `scope = 'health'`
- **AND** `measurement_resting_hr` SHALL NOT collide with the pre-existing `measurement_heart_rate` predicate — `measurement_resting_hr` is a daily summary derived from continuous monitoring, whereas `measurement_heart_rate` is a point-in-time manual reading

#### Scenario: Predicate registry registration

- **WHEN** the Health butler's migrations run
- **THEN** all nine wellness predicates SHALL be upserted into the memory module's `predicate_registry` with the entity-type and cardinality metadata the registry requires
- **AND** the migration SHALL be idempotent (re-running produces no duplicates)

#### Scenario: Memory classification

- **WHEN** the Health butler classifies wellness facts for memory retention
- **THEN** it SHALL use `permanence = 'standard'` for daily summaries (they are the permanent record of a completed day)
- **AND** SHALL use `permanence = 'standard'` for sleep sessions
- **AND** SHALL NOT use `permanence = 'volatile'` for wellness data (wellness signals are historically valuable for trend analysis)

### Requirement: Wellness Envelope Ingestion Path

The Health butler SHALL receive `wellness/google_health` envelopes from the Switchboard via the standard route-execute pathway. The butler's module code SHALL translate each envelope into predicate-keyed memory facts.

#### Scenario: Route-execute entry

- **WHEN** the Switchboard dispatches an accepted `wellness/google_health` envelope to the Health butler
- **THEN** dispatch SHALL use the same pathway used for other non-interactive channels today (no new per-butler ingest-handler registry is introduced by this change)

#### Scenario: Envelope to fact translation

- **WHEN** the Health butler processes a wellness envelope
- **THEN** it SHALL extract `payload.raw` (the full Google Health API response dict)
- **AND** SHALL derive the appropriate predicate from the envelope's resource hint
- **AND** SHALL call the memory module's fact-store write tool (tool name confirmed against `src/butlers/modules/memory/tools/__init__.py` — `memory_store_fact`) with the derived predicate, `valid_at`, `entity_id = owner_entity_id`, `scope = 'health'`, and `metadata` matching the predicate taxonomy
- **AND** SHALL be safe under replay (duplicate envelopes with the same `control.idempotency_key` SHALL NOT produce duplicate facts)

#### Scenario: Scope revocation during in-flight envelope

- **WHEN** the translation runs but the Google Health module reports that scopes are no longer granted
- **THEN** the Health butler SHALL still store the fact (the envelope pre-dates the revocation and is legitimate data)
- **AND** SHALL NOT treat the ingest as an error

#### Scenario: Malformed payload

- **WHEN** a wellness envelope's `payload.raw` is missing expected fields for the derived predicate
- **THEN** the Health butler SHALL log a warning with the offending record identifier
- **AND** SHALL NOT crash the butler
- **AND** SHALL skip the fact without advancing any butler-side state

### Requirement: Owner identity validation for wellness ingest

The Health butler SHALL accept wellness envelopes whose `sender.identity` resolves to any active Google account in `public.google_accounts` owned by the butler's owner entity, not just the primary account.

#### Scenario: Owner account accepted (any health-scoped account)

- **WHEN** a wellness envelope arrives whose `sender.identity` matches the `google_user_id` (canonically the email today) of ANY active `public.google_accounts` row whose `entity_id` equals the owner entity AND whose `granted_scopes` contains all three Google Health scopes
- **THEN** the Health butler SHALL accept and translate the envelope as normal
- **AND** the resulting fact's `entity_id` SHALL be the owner entity (single owner; one fact graph, regardless of which Google account ingested the data)

#### Scenario: Foreign-identity rejection

- **WHEN** a wellness envelope arrives whose `sender.identity` does NOT match any active health-scoped `google_accounts` row for the owner entity
- **THEN** the Health butler SHALL reject the envelope without storing any fact
- **AND** SHALL log a warning naming the mismatched identity and listing the recognised owner identities

### Requirement: Wellness envelope translation dispatches on source provider

The Health butler's wellness ingest SHALL dispatch translation on
`source.provider`. Envelopes with `provider = "google_health"` SHALL be
translated exactly as before this change (resource-segment parsing of
`external_event_id`, the existing resource→predicate table, and owner-account
sender validation per the `connector-google-health-multi-account` delta).
Envelopes with `provider = "home_assistant"` SHALL be translated from the
normalized `payload.raw.wellness_measurement` object. Envelopes with any other
provider SHALL be rejected with a labeled rejection metric and no fact written.

#### Scenario: Home Assistant measurement translated to a fact

- **WHEN** a wellness envelope arrives with `source.provider =
  "home_assistant"` and a well-formed `wellness_measurement` payload
  (`metric`, numeric `value`, `unit`, `valid_at`, `source_entity_id`)
- **THEN** the Health butler SHALL write exactly one fact with predicate
  `measurement_{metric}`, `scope = "health"`, `valid_at` from the payload,
  `entity_id` = the owner entity, and `metadata` containing at least
  `provider`, `source_entity_id`, `unit`, and the numeric `value`
- **AND** SHALL NOT spawn an LLM session to do so

#### Scenario: Malformed Home Assistant payload rejected

- **WHEN** a wellness envelope arrives with `source.provider =
  "home_assistant"` but `wellness_measurement` is missing, non-numeric, or
  lacks `metric`/`valid_at`
- **THEN** the Health butler SHALL reject the envelope without storing any fact
- **AND** SHALL increment a rejection metric labeled with the failure reason

#### Scenario: Sender validation is provider-appropriate

- **WHEN** a `home_assistant` wellness envelope arrives with
  `sender.identity` set to the source HA entity_id (a device identifier, not
  an owner Google account)
- **THEN** the Health butler SHALL NOT apply Google-account sender validation
  to it
- **AND** SHALL accept it on the basis of `source.provider =
  "home_assistant"` plus a well-formed `wellness_measurement` payload
- **AND** Google Health envelopes SHALL continue to validate against the
  owner's health-scoped Google accounts unchanged

### Requirement: Cross-provider measurement idempotency

Facts translated from `home_assistant` wellness envelopes SHALL carry an
explicit provider-agnostic idempotency key derived from
`(owner_entity_id, scope, predicate, valid_at)` — excluding provider and
source episode — so that the same physical reading delivered through multiple
providers or replays at the same `valid_at` stores exactly one fact
(first-writer-wins via the existing `(tenant_id, idempotency_key)` no-op
check).

#### Scenario: Duplicate delivery stores one fact

- **WHEN** the same `home_assistant` wellness envelope is delivered twice
  (e.g. connector replay after checkpoint overlap)
- **THEN** exactly one fact SHALL exist for that
  `(predicate, valid_at)`
- **AND** the second write SHALL be a no-op returning the existing fact id

#### Scenario: Same reading from two providers stores one fact

- **WHEN** a reading with identical `predicate` and `valid_at` has already
  been stored from another wellness provider using the same provider-agnostic
  key
- **THEN** the `home_assistant` translation SHALL be a no-op
- **AND** the surviving fact SHALL be the first writer's

#### Scenario: Distinct readings are not collapsed

- **WHEN** two readings share a predicate but differ in `valid_at`
- **THEN** both SHALL be stored as separate facts

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
