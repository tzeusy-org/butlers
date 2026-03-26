## ADDED Requirements

### Requirement: Preference predicate namespace convention

Preference predicates SHALL use the naming format `preferences:<domain>_<name>` where `<domain>` is the butler domain (e.g., `travel`, `health`, `finance`, `relationship`, `home`, `general`) and `<name>` is the specific preference identifier in snake_case. Preference facts SHALL be stored in the existing `facts` table with no schema changes.

#### Scenario: Preference predicate naming format

- **WHEN** a preference is stored via `set_preference`
- **THEN** the fact's `predicate` field MUST match the pattern `preferences:<domain>_<name>` where `<domain>` is a recognized butler domain and `<name>` is a non-empty snake_case identifier
- **AND** the `subject` field MUST be the owner's canonical name (human-readable label)
- **AND** the `entity_id` MUST be the owner entity UUID resolved from `public.contacts WHERE roles @> '["owner"]'`

#### Scenario: Preference fact uses domain-aligned scope

- **WHEN** a preference is stored with a domain prefix (e.g., `preferences:travel_flight_seat`)
- **THEN** the fact's `scope` field MUST match the domain segment of the predicate (e.g., `travel`)
- **AND** if the predicate uses `preferences:general_*`, the scope MUST be `global`

---

### Requirement: Standard preference predicates seeded in predicate_registry

The system SHALL seed standard preference predicates into the `predicate_registry` table via a memory module migration. Each registered preference predicate SHALL include `expected_subject_type='person'`, `is_edge=false`, and a descriptive `description` field.

#### Scenario: Travel domain preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:travel_flight_seat` (aisle/window/middle preference), `preferences:travel_flight_class` (economy/business/first), `preferences:travel_hotel_type` (hotel style preference), `preferences:travel_airline` (preferred airline), `preferences:travel_meal` (in-flight meal preference)
- **AND** each predicate MUST have `expected_subject_type='person'` and `is_edge=false`

#### Scenario: Health domain preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:health_dietary_restriction` (foods to avoid), `preferences:health_dietary_preference` (food preferences), `preferences:health_exercise_preference` (preferred exercise types), `preferences:health_measurement_unit` (metric vs imperial for health metrics)

#### Scenario: Finance domain preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:finance_currency` (preferred display currency), `preferences:finance_budget_period` (weekly/monthly/yearly budget cycle), `preferences:finance_rounding` (rounding preference for amounts)

#### Scenario: Relationship domain preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:relationship_communication_style` (formal/casual/etc.), `preferences:relationship_contact_frequency` (how often to reach out), `preferences:relationship_birthday_reminder_days` (days before birthday to remind)

#### Scenario: Home domain preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:home_temperature_unit` (celsius/fahrenheit), `preferences:home_comfort_temperature` (preferred indoor temperature), `preferences:home_wake_time` (usual wake time), `preferences:home_sleep_time` (usual bedtime)

#### Scenario: General preference predicates registered

- **WHEN** the preference predicate seed migration is applied
- **THEN** the `predicate_registry` MUST contain at minimum: `preferences:general_communication_style` (formal/casual/concise/detailed), `preferences:general_language` (preferred language), `preferences:general_timezone` (preferred timezone), `preferences:general_name` (preferred name/nickname to be addressed by)

#### Scenario: Migration is idempotent

- **WHEN** the seed migration is applied and preference predicates already exist in `predicate_registry`
- **THEN** the migration MUST use `ON CONFLICT (name) DO NOTHING` and complete without error

---

### Requirement: set_preference MCP tool

The memory module SHALL expose a `set_preference` MCP tool that stores a user preference as a fact with preference-appropriate defaults. The tool SHALL auto-resolve the owner entity and apply high permanence and importance defaults.

#### Scenario: Basic preference storage

- **WHEN** `set_preference` is called with `predicate="preferences:travel_flight_seat"` and `value="window"`
- **THEN** a fact MUST be stored with `subject` = owner's canonical name, `predicate` = `"preferences:travel_flight_seat"`, `content` = `"window"`, `entity_id` = owner entity UUID, `scope` = `"travel"`, `permanence` = `"stable"`, `importance` = `8.0`

#### Scenario: Scope derived from predicate domain segment

- **WHEN** `set_preference` is called with `predicate="preferences:health_dietary_restriction"` and `value="no shellfish"`
- **THEN** the stored fact's `scope` MUST be `"health"` (derived from the predicate's domain segment)

#### Scenario: General preferences use global scope

- **WHEN** `set_preference` is called with `predicate="preferences:general_language"` and `value="English"`
- **THEN** the stored fact's `scope` MUST be `"global"`

#### Scenario: Permanence override allowed

- **WHEN** `set_preference` is called with `permanence="permanent"`
- **THEN** the stored fact MUST use `permanence="permanent"` (decay_rate=0.0) instead of the default `"stable"`

#### Scenario: Importance override allowed

- **WHEN** `set_preference` is called with `importance=9.5`
- **THEN** the stored fact MUST use `importance=9.5` instead of the default `8.0`

#### Scenario: Predicate validation

- **WHEN** `set_preference` is called with a predicate that does not start with `preferences:`
- **THEN** the tool MUST return an error with a message indicating the predicate must use the `preferences:` namespace and suggesting the correct format

#### Scenario: Supersession on update

- **WHEN** `set_preference` is called with a predicate that matches an existing active preference fact for the same owner entity and scope
- **THEN** the existing preference fact MUST be superseded (validity set to `'superseded'`) and the new fact MUST have `supersedes_id` referencing the old fact
- **AND** the tool response MUST indicate the preference was updated (not created for the first time)

#### Scenario: Owner entity resolution failure

- **WHEN** `set_preference` is called but no owner contact with an `entity_id` exists in `public.contacts`
- **THEN** the tool MUST return an error with a message indicating the owner entity could not be resolved and suggesting running butler startup or creating the owner contact

#### Scenario: Optional metadata

- **WHEN** `set_preference` is called with additional `metadata` (e.g., `{"source": "user_explicit", "confidence_note": "stated directly"}`)
- **THEN** the stored fact's `metadata` field MUST include the provided metadata merged with any defaults

---

### Requirement: get_preferences MCP tool

The memory module SHALL expose a `get_preferences` MCP tool that retrieves all active user preferences, returning a simplified list format optimized for LLM consumption.

#### Scenario: Retrieve all preferences

- **WHEN** `get_preferences` is called with no filters
- **THEN** the tool MUST return all facts where `predicate LIKE 'preferences:%'` AND `validity = 'active'` AND `entity_id` = owner entity UUID
- **AND** each result MUST include: `predicate`, `value` (from `content`), `scope`, `importance`, `permanence`, `updated_at` (from `created_at`)

#### Scenario: Filter by scope

- **WHEN** `get_preferences` is called with `scope="travel"`
- **THEN** the tool MUST return only preference facts matching `scope = 'travel'`

#### Scenario: Filter by predicate pattern

- **WHEN** `get_preferences` is called with `predicate_pattern="preferences:health_%"`
- **THEN** the tool MUST return only preference facts matching `predicate LIKE 'preferences:health_%'`

#### Scenario: Empty result

- **WHEN** `get_preferences` is called and no active preference facts exist for the owner
- **THEN** the tool MUST return an empty list (not an error)

#### Scenario: Results ordered by predicate

- **WHEN** `get_preferences` returns multiple preferences
- **THEN** the results MUST be ordered by `predicate ASC` for deterministic output

#### Scenario: Effective confidence included

- **WHEN** `get_preferences` returns preference facts
- **THEN** each result MUST include an `effective_confidence` field computed using the standard decay formula (based on `confidence`, `decay_rate`, and `last_confirmed_at`)

---

### Requirement: Preferences surface in memory_context Profile Facts

Preference facts SHALL be included in the `memory_context` tool's "Profile Facts" section via the existing owner-entity fact query, with no changes to the memory_context code path. Preferences surface naturally due to their high importance (8.0) and owner entity anchoring.

#### Scenario: Preferences appear in Profile Facts

- **WHEN** `memory_context` is called for a butler and the owner has active preference facts
- **THEN** the preference facts MUST appear in the "Profile Facts" section, ranked by `importance DESC` alongside other owner-entity facts
- **AND** the preference facts MUST be formatted using the standard fact line format: `- [<subject>] [<predicate>]: <content> (confidence: <effective_confidence>)`

#### Scenario: High-importance preferences rank above standard facts

- **WHEN** the owner has both preference facts (importance=8.0) and standard consolidated facts (importance=5.0)
- **THEN** the preference facts MUST appear before the standard facts in the Profile Facts section (since Profile Facts sorts by importance DESC)

---

### Requirement: Preference retention and decay behavior

Preference facts SHALL use `stable` permanence by default (decay_rate=0.002) to ensure near-permanent retention. The `permanent` permanence level (decay_rate=0.0) SHALL be available for preferences that must never decay.

#### Scenario: Default preference decay rate

- **WHEN** a preference is stored via `set_preference` with default permanence
- **THEN** the fact MUST have `permanence='stable'` and `decay_rate=0.002`

#### Scenario: Permanent preference never decays

- **WHEN** a preference is stored via `set_preference` with `permanence="permanent"`
- **THEN** the fact MUST have `permanence='permanent'` and `decay_rate=0.0`
- **AND** the fact's effective confidence MUST remain at its initial value regardless of time elapsed

#### Scenario: Preference retention class

- **WHEN** a preference is stored via `set_preference`
- **THEN** the fact's `retention_class` MUST be `"operational"` (the default for facts, ensuring inclusion in standard retention policy)
