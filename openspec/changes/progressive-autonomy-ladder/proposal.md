## Why

Users currently manage standing approval rules manually -- they must notice repeated approval patterns themselves and explicitly create rules. This is friction-heavy: a user who approves "send Telegram to Mom" five times in a row should be offered auto-approval for that exact action, not forced to remember the rule-creation workflow. Progressive autonomy reduces approval fatigue while preserving user control, by surfacing data-driven promotion suggestions that the user confirms or dismisses.

## What Changes

- **Approval history tracker**: Record every approval decision with a composite key of `(tool_name, arg_key, arg_value)` tuples, enabling frequency analysis per exact argument combination.
- **Promotion suggestion engine**: When the same `(tool_name, arg_key, arg_value)` combination has been manually approved N times (default threshold: 5), generate a promotion suggestion proposing a standing rule with `match_type: "exact"` constraints pinned to those specific argument values.
- **4-stage autonomy ladder**: Define progression stages `inform -> suggest+wait -> act+notify -> act_silently`, where each tool+args combination tracks its own stage independently.
- **Cardinality-safe grouping**: Approving "send Telegram to Mom" 5 times suggests auto-approve ONLY "send Telegram to Mom" -- never "all Telegram messages" or "all messages to Mom". Grouping is always by the full set of `(tool_name, arg_key, arg_value)` tuples.
- **User-confirmed promotions**: Suggestions are presented for explicit user confirmation, showing exactly what scope the proposed rule covers. The system never auto-promotes.
- **Manual scope widening**: Users may manually widen a suggested rule's scope (e.g., from exact to pattern) but the system never auto-widens.
- **Suggestion cooldown**: Dismissed suggestions enter a 30-day cooldown before being re-suggested.
- **Approval velocity tracking**: Monitor whether approvals are getting faster (possible annoyance signal), feeding into suggestion timing.
- **Demotion path**: If an auto-approved action fails (execution error), the system can suggest revoking or narrowing the standing rule.
- **Audit trail**: All promotions, dismissals, and demotions are recorded as immutable audit events.

## Capabilities

### New Capabilities
- `autonomy-tracker`: Tracks approval history by exact `(tool_name, arg_key, arg_value)` tuples, computes approval frequency, detects promotion-ready combinations, manages suggestion lifecycle (cooldowns, dismissals), and tracks approval velocity.
- `autonomy-suggestions`: Generates promotion suggestions from tracker data, presents suggestions to users with exact scope descriptions, handles user confirmation or dismissal, creates standing rules from confirmed suggestions with `match_type: "exact"`, and manages the demotion path when auto-approved actions fail.

### Modified Capabilities
- `module-approvals`: Extended to emit approval events consumable by the autonomy tracker, and to check for execution failures on auto-approved actions that may trigger demotion suggestions.
- `dashboard-approvals`: Extended to display promotion suggestions in the approvals UI, show autonomy stage per tool+args combination, and provide confirmation/dismissal controls.

## Impact

- **Database**: New tables for approval history tracking (`autonomy_approval_history`), promotion suggestions (`autonomy_suggestions`), and suggestion dismissals. New columns or metadata on existing `approval_events` table for velocity tracking.
- **Approvals module** (`src/butlers/modules/approvals/`): Hook into `action_approved` and `action_execution_failed` event paths to feed the tracker. New MCP tools for suggestion management.
- **Dashboard API** (`roster/*/api/`): New endpoints for listing/confirming/dismissing promotion suggestions, viewing autonomy stage per action pattern.
- **Dashboard frontend**: New UI components for suggestion cards, confirmation dialogs with scope preview, and autonomy stage indicators.
- **Configuration**: New config keys under `[modules.approvals]` for promotion threshold, cooldown duration, velocity tracking window, and demotion policy.
