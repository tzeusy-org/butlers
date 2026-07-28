## Context

The approval executor deliberately invokes an owning daemon's original MCP
handler after human approval. That boundary is sound only when a producer has
stored the exact registered tool name and argument shape that the daemon can
accept. The current direct producers for connector lifecycle and relationship
curation bypass the normal gate wrapper, so their queue rows were never
checked against that surface. The resulting failure appears only after an
owner has approved the action.

The inventory has three entries:

| Producer | Owning daemon | Outcome |
| --- | --- | --- |
| `connector_disconnect` | Switchboard | Add a registered, gated native handler. |
| `memory_reclassify` | Relationship | Add a registered, gated memory handler. |
| `connector_rotate_token` | Switchboard | Reject before parking: the request carries no safe credential reference or replayable rotation command. |

## Goals / Non-Goals

**Goals:**

- Make every newly parked direct non-Messenger action either executable from
  its durable stored command or explicitly rejectable before it enters the
  queue.
- Keep execution inside the owning daemon and its existing shared approvals
  executor.
- Preserve secret masking and avoid credential recovery guesses.
- Detect a missing registered handler during daemon startup and via focused
  regression tests.

**Non-Goals:**

- Rewriting, auto-repairing, or re-executing historical pending actions.
- Adding a generic cross-butler dispatcher, a new pending-actions schema, or a
  credential escrow mechanism.
- Making a token-rotation request succeed without a durable, authorized
  reference to the replacement credential.

## Decisions

### D1 — Declare direct commands at the producer boundary

A small approvals command-contract module will own the inventoried producer
definitions: owner, registered tool name, and an exact argument-key set. A
producer builds its persisted `tool_name` and `tool_args` through that
declaration; extra, missing, or non-object arguments fail before parking.

The daemon validates contracts belonging to its own roster name against the
actual FastMCP registry after tool registration. It rejects a missing handler,
a variadic handler, or a handler with a mismatched parameter set at startup.

Alternative: infer aliases or reshape arguments during retry. Rejected because
it can turn an owner-approved action into a different command and makes audit
provenance unreliable.

### D2 — Native handlers live with the schemas they mutate

Switchboard owns `connector_registry`, so `connector_disconnect` becomes a
Switchboard MCP tool that performs an idempotent soft delete. Relationship owns
the curation queue and its memory schema, so `memory_reclassify` becomes a
Relationship-only memory MCP tool which updates an active fact to `volatile`
permanence and its matching decay rate. Its memory type, fact ID, and target
permanence are all safety-critical approval-rule arguments, so a standing rule
must pin the complete command. Both names are approval-gated in their rosters;
the executor uses the preserved original handler after approval.

Alternative: run the dashboard HTTP handler from dispatch. Rejected because
the handler is a submission boundary and would park a new action rather than
execute the approved one.

### D3 — Reject unrepresentable token rotations before queue insertion

The existing rotate-token endpoint deliberately omits credentials from
`tool_args`, but supplies neither a credential reference nor a deterministic
provider operation that can obtain a replacement value. It will append a safe
error audit entry and return a client-visible failure before calling the park
helper. It will not create a pending row.

Alternative: look up a credential by connector type at dispatch. Rejected
because account/provider selection would be guesswork and could rotate the
wrong credential after owner approval.

### D4 — Historic rows remain evidence, not input to repair

The existing executor eligibility rules remain unchanged. A malformed historic
row reaches the owning executor only if an operator explicitly retries it; a
missing or incompatible handler produces the existing execution-failed audit
event and leaves `status='approved'` with a null result. No stored name,
arguments, or provenance is rewritten.

## Risks / Trade-offs

- [A daemon has stale roster configuration] → startup validation fails loudly
  before it can accept new incompatible work.
- [A connector was already removed by another path] → the disconnect handler
  returns an explicit idempotent result or a truthful missing-resource failure.
- [A fact is no longer active by approval time] → reclassification fails and
  remains an approved, unexecuted action for explicit operator resolution.
- [Rotation becomes supported later] → add a credential-reference contract and
  native handler in a dedicated change; do not weaken the fail-closed path.

## Migration Plan

1. Deploy the new command declarations, native handlers, and owner roster
   configuration together.
2. New disconnect and reclassification rows are replayable immediately.
3. New token-rotation requests fail before parking with a durable audit signal.
4. Existing rows remain unchanged; operators can inspect their truthful
   dispatch failure and decide whether to recreate an action through a future
   supported flow.
5. Roll back by reverting code and roster changes. No schema or data rollback
   is required.

## Open Questions

None. A replayable token rotation requires a separately designed secret
reference protocol and is intentionally not inferred here.
