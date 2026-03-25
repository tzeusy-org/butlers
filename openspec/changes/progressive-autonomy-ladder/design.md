## Context

The Butlers approvals module currently provides a reactive approval workflow: tools are gated, actions queue for human approval, and users can manually create standing rules to auto-approve recurring patterns. However, the system lacks proactive intelligence -- it never notices that the user has approved the same exact action 5 times in a row and offers to reduce that friction.

The existing infrastructure provides strong foundations:
- `approval_events` table records every approval decision immutably
- `approval_rules` with typed `arg_constraints` (exact/pattern/any) already supports the precision needed
- `sensitivity.py` classifies args and suggests constraints
- `rules.py` handles matching with specificity-based precedence

The progressive autonomy ladder adds a data-driven promotion layer on top, analyzing approval history to suggest standing rules -- always scoped to exact argument combinations, always requiring user confirmation.

## Goals / Non-Goals

**Goals:**
- Reduce approval fatigue by detecting repeatedly-approved action patterns and suggesting standing rules
- Preserve user control: suggestions require explicit confirmation, never auto-promote
- Maintain cardinality safety: grouping is always by exact `(tool_name, arg_key, arg_value)` tuples -- never generalize
- Provide a demotion path when auto-approved actions fail
- Track approval velocity as a signal for suggestion timing
- Full audit trail for all promotion lifecycle events

**Non-Goals:**
- Automatic promotion without user confirmation (explicitly excluded)
- Pattern-based or wildcard suggestions (system only suggests `match_type: "exact"`)
- Cross-butler autonomy sharing (each butler tracks independently)
- ML-based pattern detection (simple frequency counting is sufficient)
- The 4-stage ladder (inform/suggest+wait/act+notify/act_silently) as enforced runtime stages -- for v1 we implement the suggest+wait stage only; the remaining stages are future work documented but not built

## Decisions

### D1: Grouping key is the full sorted tuple set of (tool_name, arg_key, arg_value)

**Decision**: Each approval is tracked by hashing the combination of `tool_name` plus all `(arg_key, arg_value)` pairs (sorted by key for determinism). This hash becomes the `pattern_fingerprint`.

**Rationale**: This ensures "send Telegram to Mom" and "send Telegram to Dad" are tracked as entirely separate patterns. Any other grouping strategy (e.g., grouping by tool_name alone, or by subsets of args) risks over-generalizing and violating the cardinality requirement.

**Alternative considered**: Grouping by tool_name + sensitive args only. Rejected because it would collapse patterns like "send Telegram to Mom with message X" and "send Telegram to Mom with message Y" which may have different risk profiles, and it contradicts the requirement that suggestions use `match_type: "exact"` for all args.

### D2: Promotion suggestions stored as first-class entities, not derived on-the-fly

**Decision**: When a pattern crosses the promotion threshold, a `promotion_suggestion` row is created and persists until confirmed or dismissed. Suggestions are not re-computed on every query.

**Rationale**: This allows tracking suggestion state (pending, confirmed, dismissed), cooldown periods, and audit trails. It also avoids expensive history scans on every dashboard load.

**Alternative considered**: Computing suggestions dynamically from approval history. Rejected because it makes cooldowns, dismissals, and audit trails harder to implement, and would require repeated aggregation queries.

### D3: Tracker runs as a post-approval hook, not a scheduled job

**Decision**: The approval history tracker is invoked synchronously after each manual approval event (in the `approve_action` code path). When the approval count for a pattern crosses the threshold, a suggestion is created immediately.

**Rationale**: Real-time detection provides the best UX -- the user sees a suggestion right after their 5th approval of the same pattern. A scheduled job would add latency and complexity (cron config, catch-up logic).

**Alternative considered**: Periodic batch analysis via the butler scheduler. Rejected for the latency and complexity reasons above.

### D4: Suggestions create standard standing rules on confirmation

**Decision**: When a user confirms a promotion suggestion, the system creates a standard `approval_rules` row using `create_rule_from_action`-style logic with all constraints set to `exact`. No new rule type is introduced.

**Rationale**: Reuses the existing rule matching engine, specificity logic, and audit trail. The suggestion is just a UX layer that feeds into the existing rule system.

**Alternative considered**: A new "auto-rule" type with different matching semantics. Rejected because it would duplicate rule matching logic and create confusion about which rules apply.

### D5: Approval velocity tracked as a rolling window metric, stored in state store

**Decision**: For each pattern fingerprint, track the average time between the approval request and the user's approval decision over the last N approvals (configurable, default 10). Store this as a KV entry in the butler's state store using key prefix `autonomy:velocity:`.

**Rationale**: Velocity is a heuristic signal, not a critical data structure. The state store is the right place for per-butler KV data that doesn't need relational queries. A rolling window avoids unbounded growth.

**Alternative considered**: A dedicated `approval_velocity` table. Rejected as over-engineering for what is essentially a cached metric.

### D6: Demotion suggestions are advisory, not automatic

**Decision**: When an auto-approved action (matched by a standing rule) fails execution, the system creates a demotion suggestion advising the user to review/revoke the rule. The rule is NOT automatically revoked.

**Rationale**: Consistent with the "user confirms all autonomy changes" principle. An execution failure might be transient (network error) -- automatic revocation would cause unnecessary churn.

### D7: New tables live in the butler's own schema alongside existing approvals tables

**Decision**: `autonomy_approval_history` and `autonomy_suggestions` tables are created via Alembic migration in the butler's schema, co-located with `pending_actions`, `approval_rules`, and `approval_events`.

**Rationale**: These tables are tightly coupled to the approvals domain and share the same lifecycle. No cross-butler access is needed.

## Risks / Trade-offs

- **[Risk] Pattern fingerprint collision** -- Two meaningfully different invocations could theoretically produce the same hash. Mitigation: Use SHA-256 on a canonical JSON representation; collision probability is negligible for practical volumes.

- **[Risk] Suggestion spam if threshold is too low** -- Users could be overwhelmed by suggestions. Mitigation: Default threshold of 5 is conservative; 30-day cooldown on dismissed suggestions prevents re-nagging; configurable per butler.

- **[Risk] Stale suggestions after user changes standing rules manually** -- A user might create a manual standing rule that covers the pattern a suggestion is about to propose. Mitigation: Before presenting a suggestion, check if a matching standing rule already exists; if so, auto-close the suggestion.

- **[Trade-off] Exact-only suggestions are conservative** -- Users who want broader rules must manually widen scope. This is intentional -- the system errs on the side of safety. Power users can create pattern-based rules through the existing manual workflow.

- **[Trade-off] Velocity tracking adds per-approval overhead** -- Computing and storing velocity metrics on every approval adds a small amount of work. Mitigation: State store writes are fast (single KV upsert); this is negligible compared to the approval workflow itself.

## Migration Plan

1. **Database migration**: Add `autonomy_approval_history` and `autonomy_suggestions` tables via Alembic migration. No existing tables are altered (additive only).
2. **Module code**: Add tracker and suggestion logic as new files in `src/butlers/modules/approvals/`. Hook into existing `approve_action` and `execute_approved_action` code paths.
3. **MCP tools**: Add new tools for listing/confirming/dismissing suggestions. These are additive to the existing 13-tool surface.
4. **Dashboard**: Add suggestion UI components. Existing dashboard functionality is unchanged.
5. **Rollback**: Drop the new tables and remove the hook calls. No existing data is affected.

## Open Questions

- Should the velocity metric influence the promotion threshold (e.g., suggest sooner if the user is approving faster)? Deferred to v2 -- for now velocity is tracked but only used as a dashboard indicator.
- Should there be a global cap on the number of active standing rules per butler? Not part of this change but worth considering for future governance.
