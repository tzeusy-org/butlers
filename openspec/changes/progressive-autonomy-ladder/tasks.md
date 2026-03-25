## 1. Database Schema

- [ ] 1.1 Create Alembic migration adding `autonomy_approval_history` table with columns: `id` (UUID PK), `pattern_fingerprint` (VARCHAR(64)), `tool_name` (TEXT), `tool_args` (JSONB), `action_id` (UUID FK to pending_actions), `approved_at` (TIMESTAMPTZ), `time_to_decision_seconds` (FLOAT); indexes on `pattern_fingerprint` and `(pattern_fingerprint, approved_at)`
- [ ] 1.2 Create Alembic migration adding `autonomy_suggestions` table with columns: `id` (UUID PK), `suggestion_type` (VARCHAR default "promotion"), `pattern_fingerprint` (VARCHAR(64)), `tool_name` (TEXT), `representative_args` (JSONB), `status` (VARCHAR), `approval_count_at_creation` (INTEGER), `created_at` (TIMESTAMPTZ), `decided_at` (TIMESTAMPTZ nullable), `decided_by` (TEXT nullable), `resulting_rule_id` (UUID nullable FK to approval_rules), `cooldown_until` (TIMESTAMPTZ nullable), `dismissal_reason` (TEXT nullable); indexes on `pattern_fingerprint` and `(status, created_at)`

## 2. Pattern Fingerprint

- [ ] 2.1 Implement `compute_fingerprint(tool_name, tool_args)` function in new file `src/butlers/modules/approvals/autonomy_tracker.py` -- SHA-256 hash of canonical JSON with sorted keys
- [ ] 2.2 Write unit tests for fingerprint computation: determinism, different args produce different hashes, key order independence, tool_name is part of hash

## 3. Approval History Tracker

- [ ] 3.1 Implement `record_approval(pool, action)` in `autonomy_tracker.py` -- inserts into `autonomy_approval_history` with computed fingerprint and `time_to_decision_seconds`
- [ ] 3.2 Implement `get_approval_count(pool, pattern_fingerprint)` -- counts history rows for a fingerprint
- [ ] 3.3 Implement `check_promotion_threshold(pool, pattern_fingerprint, tool_name, tool_args, config)` -- checks count against threshold, checks for existing rules/suggestions/cooldowns, creates suggestion if threshold met
- [ ] 3.4 Write unit tests for history recording (only manual approvals recorded, not auto-approved/rejected/expired)
- [ ] 3.5 Write unit tests for threshold detection (creates suggestion at threshold, no duplicate, respects cooldown, respects existing rules)

## 4. Approval Velocity Tracking

- [ ] 4.1 Implement `update_velocity(pool, state_pool, pattern_fingerprint, config)` in `autonomy_tracker.py` -- computes rolling average `time_to_decision_seconds`, stores in state store under `autonomy:velocity:{fingerprint}`
- [ ] 4.2 Implement `get_velocity(state_pool, pattern_fingerprint)` -- reads velocity from state store, returns dict with `avg_seconds`, `sample_count`, `fast_approval`, `updated_at`
- [ ] 4.3 Write unit tests for velocity computation and fast_approval flag (< 5 seconds threshold)

## 5. Promotion Suggestion Engine

- [ ] 5.1 Implement `create_promotion_suggestion(pool, pattern_fingerprint, tool_name, representative_args, approval_count)` in new file `src/butlers/modules/approvals/autonomy_suggestions.py` -- creates suggestion row and records `promotion_suggested` audit event
- [ ] 5.2 Implement `generate_scope_description(tool_name, representative_args)` -- produces human-readable string listing all exact arg matches
- [ ] 5.3 Implement `confirm_suggestion(pool, suggestion_id, actor)` -- creates standing rule with exact constraints from `representative_args`, transitions suggestion to `confirmed`, records `promotion_confirmed` audit event; for demotion suggestions, revokes the referenced rule and records `demotion_confirmed`
- [ ] 5.4 Implement `dismiss_suggestion(pool, suggestion_id, actor, reason, cooldown_days)` -- transitions to `dismissed`, sets `cooldown_until`, records audit event
- [ ] 5.5 Implement `list_suggestions(pool, status, suggestion_type, limit, offset)` -- returns suggestions with scope descriptions
- [ ] 5.6 Implement `supersede_matching_suggestions(pool, tool_name, arg_constraints)` -- finds and transitions pending suggestions that would be covered by a new rule
- [ ] 5.7 Write unit tests for suggestion creation, confirmation (creates exact rule), dismissal (sets cooldown), listing, and superseding

## 6. Demotion Suggestions

- [ ] 6.1 Implement `create_demotion_suggestion(pool, action, rule_id, error_details)` in `autonomy_suggestions.py` -- creates demotion suggestion row and records `demotion_suggested` audit event
- [ ] 6.2 Write unit tests for demotion creation, confirmation (revokes rule), and dismissal (keeps rule)

## 7. Approvals Module Integration Hooks

- [ ] 7.1 Add post-approval tracker hook in `operations.py` `approve_action` -- after successful approval, call `record_approval` and `check_promotion_threshold`; wrap in try/except so tracker failure doesn't block approval
- [ ] 7.2 Add post-execution demotion hook in `executor.py` `execute_approved_action` -- after execution failure on auto-approved action, call `create_demotion_suggestion`
- [ ] 7.3 Add rule-creation supersede hook in `operations.py` `create_approval_rule` and `create_rule_from_action` -- after rule creation, call `supersede_matching_suggestions`
- [ ] 7.4 Write integration tests verifying the end-to-end flow: 5 manual approvals trigger suggestion, confirmation creates exact rule, execution failure triggers demotion

## 8. MCP Tool Registration

- [ ] 8.1 Register `list_promotion_suggestions` MCP tool in `module.py` -- delegates to `autonomy_suggestions.list_suggestions`
- [ ] 8.2 Register `confirm_promotion_suggestion` MCP tool in `module.py` -- requires human actor auth, delegates to `autonomy_suggestions.confirm_suggestion`
- [ ] 8.3 Register `dismiss_promotion_suggestion` MCP tool in `module.py` -- requires human actor auth, delegates to `autonomy_suggestions.dismiss_suggestion`
- [ ] 8.4 Write tests verifying all 3 new tools are registered and the total tool count is 16

## 9. Audit Events

- [ ] 9.1 Add new event types to `ApprovalEventType` enum in `events.py`: `PROMOTION_SUGGESTED`, `PROMOTION_CONFIRMED`, `PROMOTION_DISMISSED`, `PROMOTION_SUPERSEDED`, `DEMOTION_SUGGESTED`, `DEMOTION_CONFIRMED`, `DEMOTION_DISMISSED`
- [ ] 9.2 Write tests verifying all promotion/demotion lifecycle transitions emit the correct audit event type

## 10. Configuration

- [ ] 10.1 Add `promotion_threshold`, `velocity_window`, and `suggestion_cooldown_days` config keys to the approvals module config parser in `module.py` with defaults (5, 10, 30)
- [ ] 10.2 Write tests verifying config parsing with custom values and defaults

## 11. Dashboard API

- [ ] 11.1 Add `GET /api/approvals/suggestions` endpoint -- accepts `status`, `suggestion_type`, `limit`, `offset` params; returns `PaginatedResponse<AutonomySuggestion>` with `scope_description` and `velocity` data
- [ ] 11.2 Add `POST /api/approvals/suggestions/{suggestionId}/confirm` endpoint -- requires auth, invokes `confirm_suggestion`
- [ ] 11.3 Add `POST /api/approvals/suggestions/{suggestionId}/dismiss` endpoint -- requires auth, optional `reason` in body, invokes `dismiss_suggestion`
- [ ] 11.4 Write API tests for all 3 endpoints (success, empty results, auth required, invalid suggestion ID)

## 12. Dashboard Frontend

- [ ] 12.1 Create autonomy suggestions banner component -- renders promotion/demotion suggestion cards with scope description, approval count, velocity indicator, confirm/dismiss buttons
- [ ] 12.2 Add demotion card variant with warning style, error summary, "Revoke Rule"/"Keep Rule" buttons
- [ ] 12.3 Integrate suggestions banner into `/approvals` page above metrics cards, conditionally rendered when pending suggestions exist
- [ ] 12.4 Add "Active Suggestions" count badge to metrics section
- [ ] 12.5 Wire confirm/dismiss button handlers to API endpoints with success toast notifications and card removal on success
