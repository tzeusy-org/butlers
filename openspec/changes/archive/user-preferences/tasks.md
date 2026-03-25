## 1. Predicate Registry Migration

- [ ] 1.1 Create memory module migration file `seed_preference_predicates.py` that inserts all standard preference predicates into `predicate_registry` using `ON CONFLICT (name) DO NOTHING`
- [ ] 1.2 Seed travel domain predicates: `preferences:travel_flight_seat`, `preferences:travel_flight_class`, `preferences:travel_hotel_type`, `preferences:travel_airline`, `preferences:travel_meal`
- [ ] 1.3 Seed health domain predicates: `preferences:health_dietary_restriction`, `preferences:health_dietary_preference`, `preferences:health_exercise_preference`, `preferences:health_measurement_unit`
- [ ] 1.4 Seed finance domain predicates: `preferences:finance_currency`, `preferences:finance_budget_period`, `preferences:finance_rounding`
- [ ] 1.5 Seed relationship domain predicates: `preferences:relationship_communication_style`, `preferences:relationship_contact_frequency`, `preferences:relationship_birthday_reminder_days`
- [ ] 1.6 Seed home domain predicates: `preferences:home_temperature_unit`, `preferences:home_comfort_temperature`, `preferences:home_wake_time`, `preferences:home_sleep_time`
- [ ] 1.7 Seed general domain predicates: `preferences:general_communication_style`, `preferences:general_language`, `preferences:general_timezone`, `preferences:general_name`

## 2. set_preference MCP Tool

- [ ] 2.1 Add `set_preference` function in `src/butlers/modules/memory/tools/` (new file `preferences.py` or extend existing tools) that wraps `store_fact` with preference defaults: `permanence="stable"`, `importance=8.0`, `retention_class="operational"`
- [ ] 2.2 Implement owner entity auto-resolution (query `shared.contacts WHERE roles @> '["owner"]'` and cache the entity_id)
- [ ] 2.3 Implement scope derivation from predicate domain segment (`preferences:travel_*` -> `scope="travel"`, `preferences:general_*` -> `scope="global"`)
- [ ] 2.4 Implement predicate validation — reject predicates not starting with `preferences:` with actionable error message
- [ ] 2.5 Support optional overrides for `permanence`, `importance`, and `metadata` parameters
- [ ] 2.6 Return response indicating whether the preference was created or updated (superseded an existing one)
- [ ] 2.7 Register `set_preference` as an MCP tool in the memory module's `register_tools()`

## 3. get_preferences MCP Tool

- [ ] 3.1 Add `get_preferences` function that queries `facts WHERE predicate LIKE 'preferences:%' AND validity = 'active' AND entity_id = owner_entity_id`
- [ ] 3.2 Implement optional `scope` filter parameter
- [ ] 3.3 Implement optional `predicate_pattern` filter parameter (SQL LIKE pattern)
- [ ] 3.4 Return simplified result format: `[{predicate, value, scope, importance, permanence, effective_confidence, updated_at}]` ordered by `predicate ASC`
- [ ] 3.5 Compute `effective_confidence` using the standard decay formula from `context.py`'s `_effective_confidence`
- [ ] 3.6 Register `get_preferences` as an MCP tool in the memory module's `register_tools()`

## 4. Tests

- [ ] 4.1 Test `set_preference` stores fact with correct defaults (permanence=stable, importance=8.0, scope derived from predicate)
- [ ] 4.2 Test `set_preference` predicate validation rejects non-`preferences:` predicates
- [ ] 4.3 Test `set_preference` supersedes existing preference on re-set
- [ ] 4.4 Test `set_preference` with permanence and importance overrides
- [ ] 4.5 Test `set_preference` error when owner entity not found
- [ ] 4.6 Test `get_preferences` returns all active preferences for owner
- [ ] 4.7 Test `get_preferences` with scope filter
- [ ] 4.8 Test `get_preferences` with predicate_pattern filter
- [ ] 4.9 Test `get_preferences` returns empty list when no preferences exist
- [ ] 4.10 Test `get_preferences` excludes superseded/retracted/expired preferences
- [ ] 4.11 Test preference facts appear in `memory_context` Profile Facts output
- [ ] 4.12 Test preference predicates seeded by migration exist in `predicate_registry`

## 5. Integration Verification

- [ ] 5.1 Verify `memory_context` Profile Facts includes preference facts ranked by importance (no code changes needed — just confirm existing behavior with preference facts)
- [ ] 5.2 Verify supersession chain works correctly when updating a preference multiple times
- [ ] 5.3 Run full test suite to confirm no regressions
