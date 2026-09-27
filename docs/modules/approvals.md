# Approvals Module

> **Purpose:** Human-in-the-loop approval mechanism that intercepts high-impact tool invocations, parks them for review, and supports standing rules for auto-approval.
> **Audience:** Contributors and module developers.
> **Prerequisites:** [Module System](module-system.md).

## Overview

![Approval Flow](./approval-flow.svg)

The Approvals module is an execution-control module that butlers load locally. It intercepts configured high-impact tool invocations before execution, parks unapproved invocations as durable pending actions, and supports manual approve/reject/expire workflows plus standing approval rules for auto-approval of repeatable safe patterns.

This is how the system ensures that butlers cannot send emails to strangers, post messages to non-owner contacts, or execute other sensitive operations without explicit human authorization.

Source: `src/butlers/modules/approvals/` -- `module.py` (tools), `gate.py` (interception), `executor.py` (execution), `rules.py` (matching), `events.py` (audit), `redaction.py`, `retention.py`, `sensitivity.py`.

## Configuration

Enable in `butler.toml`:

```toml
[modules.approvals]
enabled = true
default_expiry_hours = 48
default_risk_tier = "medium"    # low | medium | high | critical

[modules.approvals.gated_tools]
email_send_message = {}
email_reply_to_thread = { expiry_hours = 24, risk_tier = "high" }
telegram_send_message = {}
telegram_reply_to_message = {}
```

Only tools listed in `gated_tools` are intercepted. Tools are gated strictly by config -- there are no implicit defaults based on tool name patterns.

## Two-Layer Gating

The approval gate operates at two independent layers, both enforcing gating:

**Layer 1 -- MCP tool wrapping** (`gate.py`): Intercepts gated tool calls at the MCP boundary. Primary gate for direct LLM tool invocations.

**Layer 2 -- `route.execute` inline gate** (`daemon.py`): The Messenger butler's `route.execute` handler calls channel module methods directly, bypassing MCP tool wrappers. An inline gate re-enforces role-based gating at this layer.

### Role-Based Auto-Approval

1. **Owner-targeted**: Actions targeting the owner contact are auto-approved immediately (owners are pre-trusted).
2. **Known non-owner**: Standing rules are checked. Match -> auto-approve and execute. No match -> park as `pending`.
3. **Unresolvable target**: Parked as `pending` (conservative default).

## Tools Provided

Tools are registered in `ApprovalsModule.register_tools` (`src/butlers/modules/approvals/module.py`);
read it for the current names and signatures. The families:

- **Queue** -- list, show, count, approve, reject and expire pending actions, and query executed
  actions for audit. Approval executes immediately only when an owning executor is available;
  otherwise `dispatch_approved_action` later runs the approved action through the owning daemon's
  original tool handler.
- **Standing rules** -- create (directly or from a pending action with suggested constraints),
  list, show and revoke the rules described below.
- **Autonomy suggestions** -- list, confirm or dismiss suggested promotions and demotions of a
  tool's approval posture.

## Standing Rules

Standing rules auto-approve matching invocations. A rule matches when all of these hold:

- Tool name matches.
- Rule is active and not expired.
- `use_count < max_uses` when bounded.
- Argument constraints match (typed: `exact`, `pattern`, `any`; legacy: `"*"` wildcard).

Matching precedence is deterministic: constraint specificity (descending) -> bounded scope before unbounded -> newer rule before older -> lexical rule ID tiebreaker.

### Constraint Suggestions

`suggest_rule_constraints` and `create_rule_from_action` use sensitivity classification:

1. Module-declared tool metadata (`ToolMeta.arg_sensitivities`) -- checked first.
2. Heuristic sensitive argument names (`to`, `recipient`, `email`, `url`, `amount`, etc.).
3. Default: non-sensitive.

Sensitive args get `{"type": "exact", "value": ...}`; non-sensitive args get `{"type": "any"}`.

## Action Lifecycle

Valid status transitions:

```
pending -> approved | rejected | expired
approved -> executed | abandoned
rejected, expired, executed, abandoned -> (terminal)
```

All actual execution — auto-approved actions and manually approved actions dispatched by their owning butler — uses the shared `execute_approved_action()` path. It holds a database row lock from its approved/null eligibility check through handler invocation and the successful terminal write, so an Abandon request cannot win after a side effect starts. A successful action becomes `executed` only after its result and immutable audit event are persisted. If a handler is unavailable or fails, the action remains `approved` with no execution result so the operator can retry, reject, or dashboard-abandon it; an already-executed replay returns the stored result without re-running the tool.

`abandoned` is a terminal, dashboard-only recovery outcome for an action that was
previously approved but intentionally left unexecuted. It requires a non-blank
reason and appends an immutable `action_abandoned` event in the same transaction.
There is no MCP, Telegram callback, automatic, bulk, or scheduled abandonment
path.

## Risk Tiers

Tools and actions are classified into risk tiers: `low`, `medium`, `high`, `critical`. Higher tiers (`high`, `critical`) require narrower constraints (at least one `exact` or `pattern`) and bounded scope (`expires_at` or `max_uses`) for standing rules.

## Database Tables

The module owns tables in the hosting butler's schema (Alembic branch: `approvals`):

- `pending_actions` -- durable queue and audit log for gated invocations. Ordinary terminal actions are retained for 90 days; rejected or abandoned ordered `memory_entity_merge` / legacy `entity_merge` actions remain as durable owner decisions so curation cannot reopen the same pair.
- `approval_rules` -- standing rules for auto-approval. Its optional `created_from` action ID is historical provenance, so a retained rule does not block deletion of its terminal source action after that action's 90-day window.
- `approval_events` -- append-only immutable audit log. Its `action_id` and `rule_id` are historical provenance, so deleting a terminal action after its 90-day window or an inactive rule after its 180-day window neither mutates nor deletes the event; events retain their separate 365-day audit window. New non-null action and rule references are still validated when an event is written.

## Dependencies

None. The approvals module is a leaf module. Other modules interact with it indirectly through the daemon's gate-wiring mechanism.

## Related Pages

- [Module System](module-system.md)
- [Calendar Module](calendar.md) -- uses approval integration for overlap overrides
- [Email Module](email.md) -- tools gated by approvals
- [Telegram Module](telegram.md) -- tools gated by approvals
