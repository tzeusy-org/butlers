# RFC 0028: Home Physical Actuation Contract

**Status:** Accepted

**Date:** 2026-09-03

## Context

Home Assistant is the one module whose generic MCP escape hatch can move
physical matter. Prompt instructions are not an authority boundary, and an HA
HTTP success only proves transport acceptance. The Home butler therefore needs
a deterministic boundary between an ephemeral LLM request and the physical
world.

## Decision

Every `(domain, service)` pair passes an allowlisted risk map:

- `safe`: no material physical consequence, such as refreshing entity state;
- `reversible`: a bounded action with an explicit inverse or restore hint;
- `consequential`: an action whose effects require owner review;
- `protected`: access, security, unknown, or otherwise fail-closed actions.

Unknown pairs are `protected`. Consequential and protected requests are parked
through the approvals module before any HA request. Approved replay receives
its action, session, and actor lineage only from the shared executor's ambient
context. That authorization is bound to the exact executor task, tool name, and
canonical argument digest: caller arguments cannot assert it, a different call
cannot reuse it, and an asynchronous child inheriting ContextVar storage has no
authority after or during its parent's approved call.

The module claims a unique `attempting` row in `home.ha_command_log` before the
request leaves the process. If that write fails, no physical request is sent.
Each execution or retry is a new attempt; no idempotency key may suppress a
physical retry while pretending it happened. The receipt settles to exactly
one of:

- `succeeded`: a live read-back matches the declared post-condition;
- `failed`: connection establishment definitively failed or HA rejected the request;
- `unverified`: HA may have accepted the request but the response timed out,
  reset, or could not be parsed; no deterministic check exists; or the observed
  world does not match.

`unverified` requires operator attention and is never success. Reversible
attempts carry a rollback hint. A post-dispatch uncertainty still triggers live
read-back, but remains unverified even if that state matches because the command
response itself is incomplete. The same approval cannot retry an `unverified`
attempt or a receipt left `attempting` by process death or settlement failure;
the owner must reconcile the physical state and authorize a new action. A
durable `succeeded` receipt may finalize approval bookkeeping without repeating
the physical request.
The receipt keeps requested and observed home state in the Home schema.

After settlement, Home publishes a minimized `home.actuation_executed` domain
event containing only receipt identity, service classification, outcome, and
the attention flag. Shared event delivery is observability, not outcome
authority: an event failure cannot rewrite the physical receipt, and an event
success cannot upgrade a failed or unverified attempt.

## Security and failure properties

- The LLM cannot bypass approval with a tool argument.
- An unavailable approvals runtime refuses consequential/protected calls.
- An unavailable receipt store refuses all physical calls.
- HTTP 2xx is insufficient for `succeeded`; live state read-back is required.
- Area/device targets and services without a declared verifier settle
  `unverified` rather than guessing.
- Receipt actor and session fields are server-derived.

## Reuse boundary

Other physical integrations MAY reuse this pattern only with their own
owner-reviewed risk map, post-condition vocabulary, schema-owned receipt, and
approval replay tests. They MUST NOT reuse Home's service classifications as a
generic automation policy.

## Alternatives rejected

- Prompt-only confirmation: bypassable by an LLM.
- Gate every HA call uniformly: hides meaningful safe/reversible distinctions
  and makes standing approval policy dangerously broad.
- Treat HA HTTP success as outcome: confuses transport with physical state.
- Deduplicate by requested command: a prior attempt does not prove a later
  physical request occurred.
