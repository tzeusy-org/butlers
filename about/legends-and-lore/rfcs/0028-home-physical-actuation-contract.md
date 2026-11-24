# RFC 0028: Home Physical Actuation Contract

**Status:** Accepted

**Date:** 2026-09-03

Governing doctrine: [security.md, Goal and Delegation Authority](../../heart-and-soul/security.md#goal-and-delegation-authority) (owner approval for consequential and protected actuation is exposure-conditioned; physical-actuation contracts continue to bind) and [security.md, Approval Gates](../../heart-and-soul/security.md#approval-gates) (the gate is enforced outside the prompt).

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

## Amendment 1: Critical entities and approved safety mitigations

**Status:** Adopted 2026-11-21 (owner, questionnaire item home-critical-entities-amendment). **Date:** 2026-11-15.

1. The owner MAY mark explicit Home Assistant entities critical in a Home-owned registry. Only the
   authenticated owner, through an owner-only dashboard route, adds or removes an entry. No model
   session, tool argument, goal, capability grant, standing rule, Home Assistant label, area,
   attribute or name adds, removes or implies an entry.
2. The `reversible` class runs without approval only for a call whose target is an explicit list
   of entities, none of which is critical. A call naming a critical entity is at least
   `consequential` and parks, whatever its base class. While any entity is critical, a call also
   counts as naming one when its target is not an explicit entity list; when it is an indirect
   actuator (`scene.turn_on`, `script.turn_on`, any `script.<name>` service, `automation.trigger`,
   any `homeassistant` service that is not `safe`, or any service pair this map does not list);
   when a target is absent from Home's entity cache; or when a target is a group or helper whose
   member list, followed through nested groups, contains a critical entity or cannot be read. No
   standing rule, decision memory, suggestion or capability grant approves a critical call; each
   needs its own owner approval of the stored call. `safe` services are never promoted.
2a. This covers only calls that Butlers sends. Home Assistant automations that fire on their own
   triggers, and changes made outside Butlers, do not pass through this gate and are not covered.
   A helper that wraps an entity without exposing its membership is covered only when the owner
   marks the helper itself critical.
3. If the registry cannot be read, every call is treated as naming a critical entity.
4. Before an approved call naming a critical entity, or an approved safety mitigation, leaves the
   process, Home reads every target entity live from Home Assistant. If a read fails, the entity is
   missing, `unavailable` or `unknown`, the read set differs from the stored target, or the read is
   more than 10 seconds old at the receipt claim, no request is sent and the approval stays unrun.
   The pre-call state is kept on the receipt.
5. If the pre-call state already meets the call's declared post-condition, no request is sent and
   the receipt settles `already_satisfied`. `already_satisfied` is not `succeeded` and claims no
   effect. A service with no declared post-condition is never `already_satisfied`. It is terminal
   for its approval: a later replay returns it without a request and raises no attention.
6. An approved safety mitigation is an owner-declared exact call for one explicit entity, parked
   when a deterministic safety condition opens, and replayed only while that condition is still
   open and the owner's declaration still digests to the stored call. There is no all, area,
   device, label, wildcard or multi-entity mitigation and no action that secures several items.
   Mitigations never run without an owner approval.
7. The risk map, unknown-is-protected, receipt fencing, and the rule that `unverified` is never
   success are unchanged. The receipt now settles to exactly one of `succeeded`, `failed`,
   `unverified` or `already_satisfied`.
