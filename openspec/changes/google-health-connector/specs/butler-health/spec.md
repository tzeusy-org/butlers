# Butler Health — Google Health Delta

## MODIFIED Requirements

### Requirement: Health Butler Module Profile

The Health butler SHALL load the Google Health module in addition to its existing modules.

#### Scenario: Module profile

- **WHEN** the health butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), `memory`, and `google_health` (no config keys required)
- **AND** `google_health` declares `dependencies = []` at the `Module` level — it does not declare a module-level dependency on `memory` (no existing module spec does) and instead relies on the butler's module topological-init ordering plus the handler's runtime use of memory-module MCP tools

### Requirement: Health Butler Tool Surface

The Health butler SHALL include Google Health query tools in its tool inventory.

#### Scenario: Tool inventory

- **WHEN** a runtime instance is spawned for the health butler
- **THEN** it SHALL have access to the existing tools (`measurement_log`, `measurement_history`, `measurement_latest`, `medication_*`, `condition_*`, `symptom_*`, `meal_*`, `nutrition_summary`, `research_*`, `health_summary`, `trend_report`, and calendar tools)
- **AND** it SHALL additionally have access to: `sleep_latest`, `sleep_history`, `hr_history`, `hrv_history`, `spo2_history`, `breathing_rate_history`, `activity_summary`, `vo2_max_latest`

## ADDED Requirements

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
- **AND** the measurement predicates above SHALL NOT collide with the pre-existing `measurement_heart_rate` predicate — the distinction is that `measurement_resting_hr` is a daily summary derived from continuous monitoring, whereas `measurement_heart_rate` is a point-in-time manual reading

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

The Health butler SHALL receive `wellness/google_health` envelopes from the Switchboard via the standard route-execute pathway (the same mechanism non-interactive channels like `gaming/steam` and `spotify/spotify` already use — not a new handler registry API). The butler's module code SHALL translate each envelope into predicate-keyed memory facts.

#### Scenario: Route-execute entry

- **WHEN** the Switchboard dispatches an accepted `wellness/google_health` envelope to the Health butler
- **THEN** dispatch SHALL use the same pathway used for other non-interactive channels today (no new per-butler ingest-handler registry is introduced by this change)
- **AND** the Health butler's module SHALL expose an entry point that receives the envelope and performs translation (exact entry-point naming to be confirmed against the existing Spotify / Steam butler-side ingestion handlers)

#### Scenario: Envelope to fact translation

- **WHEN** the Health butler processes a wellness envelope
- **THEN** it SHALL extract `payload.raw` (the full Google Health API response dict)
- **AND** SHALL derive the appropriate predicate from the envelope's resource hint (carried in `event.external_event_id` and/or the `:<resource>` suffix on `source.endpoint_identity`)
- **AND** SHALL call the memory module's fact-store write tool (tool name to be confirmed against `src/butlers/modules/memory/tools/__init__.py` — likely `memory_store_fact` — before implementation; the spec does not hard-code an unverified name) with the derived predicate, `valid_at` from the envelope's record timestamp, `entity_id = owner_entity_id`, `scope = 'health'`, `content` matching the normalized summary text, and `metadata` matching the predicate taxonomy
- **AND** SHALL be safe under replay (duplicate envelopes with the same `control.idempotency_key` SHALL NOT produce duplicate facts)

#### Scenario: Non-primary account rejection (single-owner v1 invariant)

- **WHEN** a wellness envelope arrives whose `sender.identity` does NOT match the primary Google account's `google_user_id`
- **THEN** the Health butler SHALL reject the envelope without storing any fact
- **AND** SHALL log a warning naming the mismatched identity
- **AND** this reciprocal invariant (connector only polls primary, butler only accepts primary) keeps v1 strictly single-owner even if a regression causes the connector to poll a non-primary account

#### Scenario: Scope revocation during in-flight envelope

- **WHEN** the translation runs but the Google Health module reports that scopes are no longer granted
- **THEN** the Health butler SHALL still store the fact (the envelope pre-dates the revocation and is legitimate data)
- **AND** SHALL NOT treat the ingest as an error

#### Scenario: Malformed payload

- **WHEN** a wellness envelope's `payload.raw` is missing expected fields for the derived predicate
- **THEN** the Health butler SHALL log a warning with the offending record identifier
- **AND** SHALL NOT crash the butler
- **AND** SHALL skip the fact without advancing any butler-side state
- **AND** the connector's at-least-once delivery guarantee ensures a re-ingest is possible once the payload shape is corrected

## Source References

- `butler-health` (existing spec)
- `module-google-health` (tool surface)
- `connector-google-health` (envelope contract)
- `module-memory` (fact store primitives and `predicate_registry`)
- `crud-to-spo-migration/specs/predicate-taxonomy.md` (predicate naming conventions — blocking prerequisite)
- Non-Negotiable Rule 3 (MCP-only inter-butler communication)
- Non-Negotiable Rule 7 (Transport is connector responsibility — the Health butler never calls `health.googleapis.com` directly)
