# Health Butler Role

## Purpose
The Health butler (port 41103) is a health tracking companion for measurements, medications, conditions, symptoms, diet, and nutrition.

## ADDED Requirements

### Requirement: Health Butler Identity and Runtime
The health butler tracks health data with compound JSONB values and domain-specific analysis tools.

#### Scenario: Identity and port
- **WHEN** the health butler is running
- **THEN** it operates on port 41103 with description "Health tracking assistant for measurements, medications, diet, food preferences, nutrition, meals, and symptoms"
- **AND** it uses the `codex` runtime adapter with a maximum of 3 concurrent sessions
- **AND** its database schema is `health` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the health butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), and `memory`

### Requirement: Health Butler Tool Surface
The health butler provides measurement, medication, condition, symptom, meal, and research tools.

#### Scenario: Tool inventory
- **WHEN** a runtime instance is spawned for the health butler
- **THEN** it has access to: `measurement_log`, `measurement_history`, `measurement_latest`, `medication_add`, `medication_list`, `medication_log_dose`, `medication_history`, `condition_add`, `condition_list`, `condition_update`, `symptom_log`, `symptom_history`, `symptom_search`, `meal_log`, `meal_history`, `nutrition_summary`, `research_save`, `research_search`, `health_summary`, `trend_report`, and calendar tools

### Requirement: Health Data Conventions
Health data uses compound JSONB values and standardized severity scales.

#### Scenario: Measurement conventions
- **WHEN** measurements are logged
- **THEN** compound JSONB values are supported (e.g., blood pressure as `{"systolic": 120, "diastolic": 80}`)
- **AND** symptom severity is rated 1-10 (1 = mild, 10 = severe)
- **AND** medication adherence is calculated based on frequency

### Requirement: Health Butler Schedules
The health butler runs a weekly summary and memory maintenance jobs.

#### Scenario: Scheduled task inventory
- **WHEN** the health butler daemon is running
- **THEN** it executes: `weekly-health-summary` (0 9 * * 0, prompt-based: generate comprehensive weekly summary of weight trends, medication adherence, symptom patterns, and notable changes), `memory-consolidation` (0 */6 * * *, job), and `memory-episode-cleanup` (0 4 * * *, job)

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
