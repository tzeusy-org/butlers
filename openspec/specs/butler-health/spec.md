# Health Butler Role

## Purpose
The Health butler (port 40103) is a health tracking companion for measurements, medications, conditions, symptoms, diet, and nutrition.

## ADDED Requirements

### Requirement: Health Butler Identity and Runtime
The health butler tracks health data with compound JSONB values and domain-specific analysis tools.

#### Scenario: Identity and port
- **WHEN** the health butler is running
- **THEN** it operates on port 40103 with description "Health tracking assistant for measurements, medications, diet, food preferences, nutrition, meals, and symptoms"
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
- **THEN** it MUST internally call `memory_search` or `memory_recall` with `scope='health'` and predicate filters for `meal_breakfast`, `meal_lunch`, `meal_dinner`, `meal_snack`
- **AND** `nutrition_summary` MUST aggregate nutrition metadata across multiple meal facts in a date range by sum of calories and macros
