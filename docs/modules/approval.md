# Approval Module: Permanent Definition

Status: Normative (Product Contract)
Last updated: 2026-02-13
Primary owner: Platform/Core

## 1. Module
The Approval module is a reusable execution-control module that butlers load locally.

It is responsible for:
- Centrally intercepting configured high-impact tool invocations before execution.
- Parking unapproved invocations as durable pending actions.
- Supporting manual approve/reject/expire workflows through MCP tools.
- Auto-approving matching invocations through standing approval rules.
- Executing approved actions through a shared executor with audit logging.

This document is the authoritative module contract for approval-gated actions (for example, outbound user messaging and email sends to external parties).

## 2. Design Goals
- Keep approval policy centralized and enforceable without per-tool bespoke logic.
- Preserve safety for high-impact outputs through explicit human approval gates.
- Support repeatable operator workflows with standing rules and bounded expiry.
- Keep execution/audit semantics deterministic and queryable.
- Allow module adoption per butler while preserving database isolation.

## 3. Applicability and Boundaries
### In scope
- Gated-tool interception and pending approval queue behavior.
- Standing rule CRUD, matching, and lifecycle.
- Approval action state transitions and executor behavior.
- Module config contract under `[modules.approvals]`.
- Approval MCP tool surfaces and audit query behavior.

### Out of scope
- Channel-specific UX for presenting approval prompts.
- Cross-butler shared approval storage.
- Policy engines external to butler runtime.

## 4. Runtime Architecture Contract
### 4.1 Local components (per hosting butler)
- `Gate wrapper`: wraps configured MCP tools and intercepts calls.
- `Approvals module tools`: queue management and standing-rule management tools.
- `Executor`: shared post-approval execution path (`execute_approved_action`).
- `Storage`: `pending_actions` and `approval_rules` tables in the hosting butler DB.

### 4.2 Mandatory runtime flows
1. `Startup gate application`
- Daemon parses `[modules.approvals]`, merges identity-aware default-gated user outputs, and wraps gated tools.
- Wrapped originals are handed to `ApprovalsModule.set_tool_executor(...)` for manual approvals.
2. `Gated invocation`
- Wrapper serializes invocation into a pending action payload.
- If a standing rule matches, action is auto-approved and executed immediately through the shared executor.
- If no rule matches, action is persisted as `pending` and the tool returns:
  - `{"status":"pending_approval","action_id":"...","message":"..."}`
3. `Manual decision`
- `approve_action` transitions `pending -> approved`, executes via shared executor, and records execution output.
- `reject_action` transitions `pending -> rejected`.
- `expire_stale_actions` transitions `pending -> expired` when `expires_at < now`.
4. `Audit review`
- `list_executed_actions` returns executed rows with optional tool/rule/time filters.

### 4.3 Determinism and isolation
- Approvals data is local to each butler DB; no cross-butler DB access.
- Status transitions are explicit and validated.
- Rule checks use deterministic ordering within the fetched rule set (first match in gate path).

### 4.4 Reliability
- Unknown configured gated tools are skipped during wrapping with warning logs.
- Execution exceptions are captured and persisted as failed execution results while action status still advances to `executed`.
- Approvals disable cleanly when config is absent or `enabled=false`.

## 5. Data Model Contract
### 5.1 `pending_actions`
Purpose: durable queue and audit log for approval-gated tool invocations.

Required fields:
- `id`, `tool_name`, `tool_args`, `status`, `requested_at`

Optional/audit fields:
- `agent_summary`, `session_id`, `expires_at`, `decided_by`, `decided_at`, `execution_result`, `approval_rule_id`

Allowed statuses:
- `pending`, `approved`, `rejected`, `expired`, `executed`

### 5.2 `approval_rules`
Purpose: standing rules for auto-approval of repeatable safe invocations.

Required fields:
- `id`, `tool_name`, `arg_constraints`, `description`, `created_at`, `active`

Optional control/audit fields:
- `created_from`, `expires_at`, `max_uses`, `use_count`

### 5.3 State-transition contract
Valid transitions:
- `pending -> approved|rejected|expired`
- `approved -> executed`
- terminal: `rejected|expired|executed`

Invalid transitions must be rejected (`InvalidTransitionError` path in module logic).

## 6. Gate and Rule-Matching Contract
### 6.1 Config-driven gating
- Only tools in `gated_tools` are intercepted.
- Effective expiry is per-tool `expiry_hours` override or module `default_expiry_hours`.
- Identity-aware defaults are merged before wrapping:
  - user outputs with `approval_default="always"` are default-gated.
  - user `send/reply` name safety-net tools are default-gated even without metadata.
  - bot outputs are not default-gated unless explicitly configured.

### 6.2 Standing rule checks (gate path)
Rule must satisfy all:
- tool name matches
- rule is active
- not expired (`expires_at` unset or in future)
- `use_count < max_uses` when bounded
- argument constraints match

Current gate matcher behavior:
- empty constraints `{}` match any invocation of the tool
- exact key/value matching
- legacy wildcard `"*"` supported per key

### 6.3 Constraint suggestions
- `suggest_rule_constraints` and `create_rule_from_action` use sensitivity classification.
- Sensitivity resolution order:
  1. Module-declared tool metadata (`ToolMeta.arg_sensitivities`)
  2. Heuristic sensitive arg names (`to`, `recipient`, `email`, `url`, `amount`, etc.)
  3. Default non-sensitive
- Suggested constraints:
  - sensitive args -> `{ "type": "exact", "value": ... }`
  - non-sensitive args -> `{ "type": "any" }`

## 7. Execution and Audit Contract
### 7.1 Shared executor path
Both auto-approved and manually approved actions execute through `execute_approved_action(...)` when a tool executor is wired.

Executor guarantees:
- runs sync or async tool handlers
- normalizes non-dict return values to `{ "value": ... }`
- persists `execution_result` with `success` and `executed_at` (and `result` or `error`)
- updates action status to `executed`
- increments `approval_rules.use_count` for auto-approved executions

### 7.2 Manual approval fallback
If no tool executor is wired, manual `approve_action` still advances to `executed` with `execution_result = null`.

### 7.3 Query surfaces
- `list_executed_actions` supports optional filters:
  - `tool_name`
  - `rule_id`
  - `since`
  - `limit`

## 8. Module Configuration Contract
Module config is declared under `[modules.approvals]` in `butler.toml`.

Supported settings:
- `enabled` (bool)
- `default_expiry_hours` (int, default `48`)
- `[modules.approvals.gated_tools]` mapping:
  - `<tool_name> = {}`
  - or `<tool_name> = { expiry_hours = <int> }`

Validation:
- Unknown gated tool names fail validation against registered tools.
- Missing config behaves as approvals disabled.

## 9. MCP Tool Surface Contract
When enabled, the module registers 13 stable tools:

- Queue:
  - `list_pending_actions`
  - `show_pending_action`
  - `approve_action`
  - `reject_action`
  - `pending_action_count`
  - `expire_stale_actions`
  - `list_executed_actions`
- Rules:
  - `create_approval_rule`
  - `create_rule_from_action`
  - `list_approval_rules`
  - `show_approval_rule`
  - `revoke_approval_rule`
  - `suggest_rule_constraints`

## 10. Non-Goals
- Replacing module/business authorization logic.
- Defining channel-specific approval UI/notification workflows.
- Sharing approval queues across butler databases.

## 11. Authorization and Actor Contract
Approvals is a single-user, federated control surface: each butler instance is owned by one human operator.

Authorization requirements:
- Decision-bearing actions (`approve_action`, `reject_action`, `create_approval_rule`, `create_rule_from_action`, `revoke_approval_rule`) must require authenticated human identity.
- LLM/runtime/tool actors must not directly invoke decision-bearing approval actions.
- Multi-approver workflows are out of scope for this product; exactly one human approver is the model.

Actor model requirements:
- The module is primarily for LLM-driven actions that can cause external side effects.
- Auto-approval via standing rules is treated as pre-approval by the human who created/revoked the rule lifecycle.
- Human-driven high-impact actions should still pass through approval semantics when initiated by autonomous flows on the user's behalf.

## 12. Idempotency and Concurrency Contract
Approval decisions and execution must be race-safe and retry-safe.

Required behaviors:
- Decision operations must be compare-and-set on current state (pending-only writes).
- Concurrent decisions for the same action must converge to one terminal outcome.
- Replayed/manual retries of the same decision request must be idempotent and return the final action state.
- Action execution must run at most once per action ID under normal operation.
- If exactly-once execution cannot be proven after infrastructure failure, action state must capture ambiguous execution status for manual reconciliation.

## 13. Immutable Audit Event Contract
The approvals subsystem requires immutable auditability in addition to mutable queue/rule snapshots.

Required audit surface:
- Append-only approval event log (`approval_events`) with immutable records.
- Event types should cover at least: action queued, auto-approved, approved, rejected, expired, execution succeeded, execution failed, rule created, rule revoked.
- Event envelope should include: `event_id`, `action_id`/`rule_id`, `event_type`, `actor`, `timestamp`, `reason`, and payload metadata.

Integrity requirements:
- Existing events must never be updated or deleted in-place.
- Retention/archival flows must preserve event immutability semantics.

## 14. Data Protection and Retention Contract
Approvals data can include sensitive user and external-party information.

Protection requirements:
- `tool_args` and `agent_summary` must be scrubbed for secrets/tokens before persistence and logging.
- Sensitive fields (recipient identifiers, credentials, account/payment fields, URLs with tokens) must support redaction in operator-visible summaries.
- Error payloads and execution results must avoid leaking secret material.

Retention requirements:
- Retention windows for pending actions, decided actions, rules, and immutable events must be explicit and configurable.
- Expired/revoked artifacts may be hidden from default views but remain audit-addressable for retention duration.

## 15. Risk Tier and Policy Precedence Contract
Approval policy must remain predictable as rule count and tool surface grow.

Risk model:
- Tools/actions should be classified into explicit risk tiers (for example `low`, `medium`, `high`, `critical`) by policy metadata.
- Single-approver semantics apply to all tiers in this product.
- Standing rules are allowed for all tiers, but higher tiers should require narrower constraints (exact/pattern) and bounded scope (`expires_at`/`max_uses`).

Policy precedence:
- Deny/force-gate policy beats permissive defaults.
- Explicit matching standing rule beats default pending behavior.
- Absent rule match, high-impact LLM-driven actions must remain pending approval.

## 16. Friction-Minimizing Operator UX Contract
The approval workflow should reduce cognitive load while preserving safety.

Required decision context:
- Every pending action view should include action intent, normalized arguments, risk tier, and expiry timing.
- The system should explain why an action is pending and why a rule matched (or did not match).

Recommended operator actions:
- Approve once.
- Reject with reason.
- Approve and create constrained standing rule in one flow.
- Preview suggested constraints before rule creation.

Batch ergonomics:
- Support bulk approval/rejection for homogeneous low-risk items when explicit user intent is clear.
- Show estimated blast radius before creating broad rules.

## 17. Single-Pane Frontend Integration Contract
The approvals module should be visible as a first-class operator workflow in the frontend single pane (`docs/frontend/*`).

Target frontend surfaces:
- Approval queue view (pending + filters + expiry visibility).
- Approval action detail view (full context, decision actions, execution outcome).
- Standing rule management view (list/detail/create/revoke + use_count visibility).
- Approval audit timeline/metrics view (latency, auto-approval rate, rejection/failure trends).

Target backend API alignment:
- `docs/frontend/backend-api-contract.md` should include an approvals domain contract matching MCP approval operations.
- Frontend feature inventory should track approvals status explicitly (implemented vs planned) to avoid drift.
