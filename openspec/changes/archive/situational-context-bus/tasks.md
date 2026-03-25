## 1. Database Migration

- [ ] 1.1 Create core-chain Alembic migration for `shared.user_context` table with all columns, UNIQUE constraint, CHECK constraint, and partial index
- [ ] 1.2 Run migration against dev database and verify table exists with correct schema

## 2. Context Bus Python Module

- [ ] 2.1 Create `src/butlers/context_bus.py` with `ContextSignal` enum (10 signal types)
- [ ] 2.2 Implement `ContextEntry` dataclass for query results
- [ ] 2.3 Implement write permission mapping (signal_type -> authorized butler names) and `_check_write_permission()` validator
- [ ] 2.4 Implement default TTL and max TTL tables per signal type, with `_clamp_ttl()` helper
- [ ] 2.5 Implement `set_context()` with permission check, TTL clamping, and upsert query
- [ ] 2.6 Implement `clear_context()` with soft delete via `superseded_at`
- [ ] 2.7 Implement `get_active_context()` returning list of ContextEntry ordered by confidence desc, set_at desc
- [ ] 2.8 Implement `is_user_in_context()` with `min_confidence` parameter (default 0.5)
- [ ] 2.9 Implement `format_context_preamble()` with confidence labels (explicit, high, medium, low)

## 3. Tests

- [ ] 3.1 Write unit tests for `ContextSignal` enum completeness and string values
- [ ] 3.2 Write unit tests for `_check_write_permission()` — authorized writers, unauthorized rejection, general butler broad access
- [ ] 3.3 Write unit tests for `_clamp_ttl()` — default applied when omitted, clamping to max, within-range passthrough
- [ ] 3.4 Write integration tests for `set_context()` — new signal creation, upsert update, re-activation of superseded signal
- [ ] 3.5 Write integration tests for `clear_context()` — active signal cleared, other butler's signal not affected, non-existent signal no-op
- [ ] 3.6 Write integration tests for `get_active_context()` — active returned, expired excluded, superseded excluded, ordering by confidence
- [ ] 3.7 Write integration tests for `is_user_in_context()` — active match, no match, low confidence filtered, custom min_confidence
- [ ] 3.8 Write unit tests for `format_context_preamble()` — single signal, multiple signals, no value, no signals returns empty string

## 4. Spawner Integration

- [ ] 4.1 Add context preamble injection to spawner's system prompt composition: call `get_active_context()` and `format_context_preamble()`, insert after identity preamble
- [ ] 4.2 Add fail-open error handling: catch exceptions from context query, log at WARNING, proceed without preamble
- [ ] 4.3 Write tests for spawner context preamble injection — signals present, no signals, query failure

## 5. Validation

- [ ] 5.1 Run full test suite and verify no regressions
- [ ] 5.2 Run linter and formatter, fix any issues
- [ ] 5.3 Verify migration is reversible (downgrade drops table cleanly)
