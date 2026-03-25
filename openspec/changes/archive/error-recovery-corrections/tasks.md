## 1. Database Schema

- [ ] 1.1 Create Alembic migration adding `corrections` table to each butler schema with columns: `id` (UUID PK), `correction_type` (TEXT NOT NULL), `target_session_id` (UUID NOT NULL FK to sessions), `correcting_session_id` (UUID NOT NULL FK to sessions), `description` (TEXT NOT NULL), `status` (TEXT NOT NULL), `summary` (TEXT NOT NULL), `original_data_snapshot` (JSONB), `correction_details` (JSONB), `created_at` (TIMESTAMPTZ NOT NULL DEFAULT now())
- [ ] 1.2 Add CHECK constraint on `correction_type` for valid values: `data_correction`, `misroute`, `memory_deletion`, `action_reversal`
- [ ] 1.3 Add CHECK constraint on `status` for valid values: `applied`, `partially_applied`, `failed`
- [ ] 1.4 Add index on `target_session_id` for correction audit queries
- [ ] 1.5 Add index on `correcting_session_id` for reverse lookups

## 2. Core Corrections Module

- [ ] 2.1 Create `src/butlers/core/corrections.py` with correction type enum, precondition validation, and correction record insertion functions
- [ ] 2.2 Implement `create_correction()` — insert-only function that writes to the corrections table and enforces append-only semantics
- [ ] 2.3 Implement `corrections_by_session()` and `corrections_for_session()` query functions
- [ ] 2.4 Implement precondition checkers for each correction type: `check_data_correction_preconditions()`, `check_misroute_preconditions()`, `check_memory_deletion_preconditions()`, `check_action_reversal_preconditions()`

## 3. Core `correct` MCP Tool

- [ ] 3.1 Add `correct` to `CORE_TOOL_NAMES` in the daemon module
- [ ] 3.2 Implement `correct` tool handler with type discrimination routing to type-specific handlers
- [ ] 3.3 Implement `data_correction` handler: validate preconditions, snapshot original state value, update state via `state_set`, record correction
- [ ] 3.4 Implement `memory_deletion` handler: validate preconditions, snapshot original memory content, call `memory_forget`, update memory metadata with correction provenance, record correction
- [ ] 3.5 Implement `misroute` handler: validate preconditions, snapshot original routing, call Switchboard `correct_route`, record correction. On success, return `new_session_id` of the re-dispatched session in `correction_details` for traceability
- [ ] 3.6 Implement `action_reversal` handler: validate preconditions, inspect session tool calls for reversible actions, attempt reversal, record correction with partial/full/failed status
- [ ] 3.7 Register the canonical tool description text verbatim from the spec (all four correction types, "NOT for" exclusion list, required/optional parameters including `target_butler`). The description is the primary LLM interface — treat it as a contract
- [ ] 3.8 Implement the failure message dictionary: each precondition failure MUST produce the exact error message template from the spec (with placeholder substitution). Messages must include both the failure reason and a remediation hint (which tool to call, what to ask the user)
- [ ] 3.9 Implement the correction type decision tree as a code-level helper with inline documentation, so maintainers can verify decision coverage matches the spec
- [ ] 3.10 Implement cross-schema correction resolution: accept optional `target_butler` parameter. If provided, query that butler's schema for `target_session_id`. Write correction record to the CURRENT butler's `corrections` table with `target_butler` in `correction_details`
- [ ] 3.11 Implement rate limiting: max 10 corrections per source session per rolling hour. Return actionable error message on limit exceeded. Counter is per-session, resets naturally as corrections age out of the 1-hour window

## 4. Switchboard Extension

- [ ] 4.1 Add `correct_route` MCP tool to the Switchboard butler
- [ ] 4.2 Implement ingestion event lookup by `request_id` for re-dispatch context
- [ ] 4.3 Implement re-dispatch to correct butler preserving original request context and adding `correction_id` metadata
- [ ] 4.4 Handle expired ingestion event case (message past 1-month retention) with clear failure message
- [ ] 4.5 Update original `message_inbox` lifecycle record to reflect correction (mark as `corrected`, record new routing)

## 5. Memory Module Extension

- [ ] 5.1 Add correction provenance to `memory_forget` — accept optional `correction_id` and `correction_reason` parameters
- [ ] 5.2 When correction provenance is provided, update the memory's metadata with `correction_id` and `correction_reason` alongside the retraction
- [ ] 5.3 Add `memory_events` row with correction-driven retraction event type when memory is retracted via correction
- [ ] 5.4 Guard against correcting already-retracted or superseded memories with clear error messages

## 6. Session Integration

- [ ] 6.1 Add `correction_count` to `sessions_get` response (count of corrections targeting that session)
- [ ] 6.2 Wire corrections query functions into session detail API endpoint

## 7. Tests

- [ ] 7.1 Unit tests for correction record insertion and append-only enforcement
- [ ] 7.2 Unit tests for precondition validation per correction type (valid and invalid cases)
- [ ] 7.3 Unit tests for `data_correction` handler: state update, snapshot, correction record
- [ ] 7.4 Unit tests for `memory_deletion` handler: memory retraction, provenance metadata, already-retracted guard
- [ ] 7.5 Unit tests for `misroute` handler: re-dispatch success, expired event failure, unregistered butler failure. Verify `new_session_id` is present in successful correction result
- [ ] 7.6 Unit tests for `action_reversal` handler: full reversal, partial reversal, failed reversal
- [ ] 7.7 Unit tests for correction audit queries (by target session, by correcting session)
- [ ] 7.8 Integration test: end-to-end data correction flow (create session, store bad data, correct it, verify audit trail)
- [ ] 7.9 Test that `correct` tool description contains: all four correction type names, the "NOT for" exclusion list, all required parameter names (`correction_type`, `target_session_id`, `description`), and all optional parameter names (`target_butler`, `correct_butler`, `state_key`, `corrected_value`, `memory_type`, `memory_id`, `action_description`)
- [ ] 7.10 Failure message dictionary tests: for EACH entry in the failure message dictionary, verify the error message matches the template with correct placeholder substitution. Covers: session not found, state key not found, memory already retracted, memory superseded, butler not registered, ingestion event expired, action not reversible, unknown correction type, missing required parameter, session has no ingestion event, memory not found, switchboard unreachable
- [ ] 7.11 Decision tree coverage tests: at least one test per branch of the correction type decision tree, including the "none of the above" fallthrough that recommends alternative tools
- [ ] 7.12 Cross-schema resolution tests: correction within own schema (no `target_butler`), correction targeting another butler's schema (valid `target_butler`), correction targeting non-existent butler (error with available butler list)
- [ ] 7.13 Rate limiting tests: corrections within limit proceed normally, 11th correction in same hour is rejected with actionable message, rate limit is per-session (different sessions have independent counters), corrections older than 1 hour no longer count
