## Context

`approved` with `execution_result = NULL` is deliberately the durable stalled
predicate and the only state that the recovery dispatcher can replay. The owner
has now authorized a distinct terminal outcome for an action they originally
approved but deliberately stop recovering. This crosses the approvals state
machine, immutable event log, retention, dashboard API, and dashboard UI.

## Goals / Non-Goals

**Goals:**

- Persist one honest terminal `abandoned` outcome with a required accountable
  reason and an immutable audit event.
- Make the persisted compare-and-set operation the sole arbiter between Retry
  and Abandon.
- Keep stale counts and recovery affordances derived from the current durable
  state, not client optimistic state.

**Non-Goals:**

- Rewriting or replaying malformed historic commands.
- Reusing `rejected`, treating execution failure as abandonable, or exposing an
  MCP, Telegram callback, scheduled, automatic, or bulk abandonment path.
- Deleting action data or changing approval-request delivery behavior.

## Decisions

### D1 — Store abandonment as a new terminal status

The migration extends the `pending_actions.status` check constraint with
`abandoned`; only `approved -> abandoned` is valid and only while
`execution_result IS NULL`. Reusing `rejected` was rejected because it
misstates the original owner decision. A status rather than an execution-result
flag keeps the stalled predicate, history filtering, retention, and retry
eligibility explicit and queryable.

### D2 — Hold durable row ownership through retry execution

Retry locks the eligible row with `SELECT ... FOR UPDATE` before invoking its
handler and keeps that transaction open through the successful `executed` state
and audit append. Abandon uses its exact approved/null compare-and-set update,
which waits for that row lock. Thus Abandon either wins before a handler is
allowed to start, or loses after Retry atomically records execution; there is
no transient status and no window for an unowned side effect. A failed handler
releases the lock while leaving the row approved/null, then records its
non-terminal failure audit; a later explicit Abandon remains valid.

### D3 — The dashboard is the sole invocation boundary

The router accepts a strict non-blank reason and calls a module operation with
an authenticated dashboard actor. No MCP tool is registered, no callback token
is minted, and no batch endpoint exists. This keeps the owner decision a
deliberate, attributable console action.

### D4 — Event, retention, and projections evolve together

`action_abandoned` is added to the canonical event enum and inserted in the
same transaction as the terminal state. Abandoned records join the terminal
action retention set; they remain visible until that policy permits cleanup.
All API and frontend predicates use `status == approved &&
execution_result == null` for stalled/Retry, so an abandoned action immediately
leaves both without secondary counters or flags.

## Risks / Trade-offs

- **[Retry can have external side effects]** → The executor holds a database
  row lock before handler invocation; once it owns the row, Abandon cannot
  report success.
- **[A handler fails after Retry begins]** → The executor commits no terminal
  state, leaving the approved/null predicate intact for a later explicit owner
  recovery choice.
- **[Historic schemas reject the new enum value]** → The migration rewrites
  the existing status check constraint atomically before code can write
  `abandoned`; migration tests cover upgraded and fresh schemas.
- **[The UI sends a blank reason]** → Request validation and the module
  operation both reject blank/whitespace-only input before mutation.

## Migration Plan

1. Add the `abandoned` check-constraint value and preserve all existing rows.
2. Deploy module/API/frontend code that recognizes the status and event.
3. Roll back code before the migration only after confirming no action has been
   abandoned; otherwise retain the migration and roll forward because older code
   cannot safely classify the new durable status.

## Open Questions

None. The closed owner decision fixes the source state, reason, event,
dashboard-only boundary, and prohibited automation.
