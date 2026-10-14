## MODIFIED Requirements

### Requirement: Health Butler Identity and Runtime

The health butler SHALL track health data with compound JSONB values and domain-specific analysis tools. The health daemon SHALL NOT spawn reasoning sessions; reasoning over health data SHALL run as invocations of admitted goals that call Health capabilities within their grants.

Source: RFC 0038 D2, D16
Scope: v1-mandatory

#### Scenario: Identity and port
- **WHEN** the health butler is running
- **THEN** it operates on port 41103 with description "Health tracking assistant for measurements, medications, diet, food preferences, nutrition, meals, and symptoms"
- **AND** it spawns no reasoning session and has no per-butler runtime adapter or session concurrency cap; models are chosen per goal invocation
- **AND** its database schema is `health` within the consolidated `butlers` database

#### Scenario: Module profile
- **WHEN** the health butler starts
- **THEN** it loads modules: `calendar` (Google provider, suggest conflicts policy), `contacts` (Google provider, sync enabled, 15-minute interval, 6-day full sync), `memory`, and `google_health` (no config keys required)
- **AND** `google_health` declares `dependencies = []` at the `Module` level — it does not declare a module-level dependency on `memory` and instead relies on the butler's module topological-init ordering plus the handler's runtime use of memory-module MCP tools

### Requirement: Health Butler Tool Surface

The health butler SHALL provide measurement, medication, condition, symptom, meal, and research tools as capabilities registered in the broker catalog. A goal invocation SHALL reach a Health capability only when its grant contains it.

Source: RFC 0038 D2, D4, D7
Scope: v1-mandatory

#### Scenario: Tool inventory
- **WHEN** the health butler registers its capabilities in the catalog
- **THEN** its catalog includes: `measurement_log`, `measurement_history`, `measurement_latest`, `medication_add`, `medication_list`, `medication_log_dose`, `medication_history`, `condition_add`, `condition_list`, `condition_update`, `symptom_log`, `symptom_history`, `symptom_search`, `meal_log`, `meal_history`, `nutrition_summary`, `research_save`, `research_search`, `health_summary`, `trend_report`, and calendar tools
- **AND** it SHALL additionally include: `health_sleep_latest`, `health_sleep_history`, `health_hr_history`, `health_hrv_history`, `health_spo2_history`, `health_breathing_rate_history`, `health_activity_summary`, `health_vo2_max_latest`

#### Scenario: Medication snapshot service tool for Travel
- **WHEN** the health butler registers its tools
- **THEN** it also registers a non-LLM-presentable medication snapshot service tool returning the same `health.medication-travel.v1` envelope as `medication_travel_snapshot`, reachable only through the Switchboard service-plane relay from the authenticated Travel daemon
- **AND** the service tool is never listed in the broker catalog or granted to a goal invocation

### Requirement: Wellness Envelope Ingestion Path

The Health butler SHALL receive accepted `wellness/google_health` envelopes through its deterministic `wellness_ingest_envelope` service-plane ingest, which is not LLM-presentable and is not on the session listener. Delivery SHALL NOT use `route.execute`, model classification, or a reasoning session. The butler's module code SHALL translate each envelope into predicate-keyed memory facts.

Source: RFC 0038 D1, D2, D7
Scope: v1-mandatory

#### Scenario: Route-execute entry

- **WHEN** Switchboard accepts a `wellness/google_health` envelope for the Health butler
- **THEN** the envelope SHALL be delivered to Health's `wellness_ingest_envelope` service-plane ingest without a `route.execute` call
- **AND** no reasoning session SHALL be started to translate it, and the translation SHALL write with `system` content authority

#### Scenario: Envelope to fact translation

- **WHEN** the Health butler processes a wellness envelope
- **THEN** it SHALL extract `payload.raw` (the full Google Health API response dict)
- **AND** SHALL derive the appropriate predicate from the envelope's resource hint
- **AND** SHALL call the memory module's fact-store write tool (tool name confirmed against `src/butlers/modules/memory/tools/__init__.py` — `memory_store_fact`) with the derived predicate, `valid_at`, `entity_id = owner_entity_id`, `scope = 'health'`, and `metadata` matching the predicate taxonomy
- **AND** SHALL stamp a canonical `source` metadata key (`"google_health"`) on the fact so the reading is attributable by `GET /api/health/measurements/sources`
- **AND** SHALL be safe under replay (duplicate envelopes with the same `control.idempotency_key` SHALL NOT produce duplicate facts)

#### Scenario: Canonical source-attribution key

- **WHEN** the Health butler writes any wellness measurement fact (from either the `google_health` or `home_assistant` arm)
- **THEN** the fact's `metadata` SHALL carry a single canonical `source` key (`"google_health"` or `"home_assistant"` respectively) — the key `GET /api/health/measurements/sources` reads first
- **AND** the source endpoint SHALL resolve the source name as `COALESCE(metadata->>'source', metadata->>'provider')` so facts written before this convention (carrying only the legacy `provider` key) remain attributable without a backfill

#### Scenario: Scope revocation during in-flight envelope

- **WHEN** the translation runs but the Google Health module reports that scopes are no longer granted
- **THEN** the Health butler SHALL still store the fact (the envelope pre-dates the revocation and is legitimate data)
- **AND** SHALL NOT treat the ingest as an error

#### Scenario: Malformed payload

- **WHEN** a wellness envelope's `payload.raw` is missing expected fields for the derived predicate
- **THEN** the Health butler SHALL log a warning with the offending record identifier
- **AND** SHALL NOT crash the butler
- **AND** SHALL skip the fact without advancing any butler-side state
