# Design

[RFC 0038](../../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
is the single design authority. This file records only what OpenSpec needs to
trace the change.

## Decisions (summary)

1. Switchboard is the control plane: ingress, goal admission, goal ledger,
   goal runner and capability broker, in its own process and container so only
   it holds the signing key (RFC 0038 D1).
2. Domain butlers are capability services and obligation owners; they never
   spawn sessions (D2).
3. Sessions run on a separate session host, sandboxed per invocation, and
   reach only the broker's session listener and their model provider (D3).
4. Every model-reachable tool declares effect class, output trust and
   sensitivity, resource and safety arguments, approval policy, idempotency and
   owner authority; undeclared tools are not grantable (D4).
5. Authority ceilings derive from origin: owner, obligation, third party or
   staffer; caused admissions inherit lineage and exposure under a chain limit
   (D5). Exposure to untrusted output, including tainted stored records, gates
   consequential effects (D6).
6. The broker checks every call transactionally and signs a single-use,
   audience-bound assertion that the owning domain verifies before the handler
   runs (D7).
7. Children are depth 1, created only by `goal.delegate`, with grants that are
   subsets of the lead's minus `administer` (D8, D9).
8. Budgets, state machines, effect ledger, exact-approval execution, Stop and
   epoch-fenced recovery are goal-level (D10 to D14).
9. Model choice is per invocation by role and tier with a finite escalation
   ladder that never creates a new goal (D16).
10. Engineering executors and tool-less model functions are the only model use
    outside goals (D18).

## Cutover

No dual-running. The runtime is built dark on main, proven in dev against the
cutover gate (scenarios, adversarial suite, replay evaluation, stop and
recovery drills), then switched on by one release that also deletes the
classify-and-route path and applies the baseline deltas. Rollback redeploys
the previous release.

## Validation strategy

Evaluate whole-goal success, constraint preservation, useful proactive
actions, missed obligations, owner corrections, latency and total cost
including retries and escalations, against recorded outcomes of the current
runtime. Use zero-child work, parallel read-only children, conflicting
cross-domain constraints, scoped child writes, injected third-party content,
cancellation, recovery and delegation-escape attempts. Model tiers are
assigned from this evaluation, not from vendor benchmarks.
