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
   sensitivity, resource and safety arguments, approval policy, idempotency,
   owner authority and, for any tool that can deliver to or invite a party,
   `recipient_params`; undeclared tools are not grantable (D4).
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
10. Engineering executors, tool-less model functions and dashboard fast answers
    are the only model use outside goals (D18, owner decision `bu-u5euwb.21`).
11. Two owner rules bind every goal regardless of origin or exposure:
    communication with anyone other than the owner needs per-event approval or
    an owner-created standing rule, and `transact` is disabled (D4, owner
    decisions 8 and 9).
12. The dashboard presents one mind with specialized regions: goals are the
    unit of activity and domain pages become regions (D19). Its experience
    contract is a separate `dashboard-*` change signed off before it is built.
13. After the switch and the archive of this change, the RFC corpus is
    rewritten and reorganized around the goal architecture as separate work
    (owner decision 10).

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

## Cross-change collisions

Other active changes edit capabilities that this change modifies or removes.
Each one is archived first, re-scoped to the goal runtime, folded into this
change, or withdrawn by owner decision before task 4.2 applies the deltas.
`define-dashboard-fast-answer-runtime` is settled first, because it adds a
model path outside goals; owner decision `bu-u5euwb.21` names it as the D18
dashboard fast-answer kind, and the change is re-scoped to that kind
(`bu-u5euwb.28`). The census, the disposition and
owner for each change, and the requirement blocks both deltas edit are listed
in [collision-dispositions.md](collision-dispositions.md).

## Spec format note

The two new capability specs keep `## Purpose` and `## Source References`
headings, which `spec-trace-check.py --authoring` reports as non-delta H2s.
That matches this repository's convention for new capabilities in active
changes (for example `specify-improvement-proposal-spine` and
`add-connector-oauth-scope-surface`); `openspec validate --strict` accepts
them, and archiving carries them into the baseline spec.

The checker's other reports on this change are inherited, not introduced:
MODIFIED blocks copy baseline requirements that have no `ID:` line or that
use multi-WHEN scenarios, a MODIFIED block repeats its baseline ID, and
REMOVED blocks carry `Reason`/`Migration` rather than `ID`/`Source`/`Scope`.
Backfilling IDs across the baseline is left to the post-switch RFC and spec
reorganization.

## Shared contract authority and source composition (2026-10-24)

The proposed RFC0038 S0–S8 section is the single field/state/API home. New
independently named ADDED blocks reference it rather than replacing existing
Model Resolution, goal008, adapter, origin or sandbox clauses. The complete
old Model Resolution paragraph and Source line remain byte-identical. The
closed source baseline and protected-span evidence are recorded in
[shared-contract-coverage.md](shared-contract-coverage.md).

PR4872's unmerged D20 source is not copied, repaired or adopted. Its existing
replacement spans and insertion gaps remain unchanged. .19's fresh requirement
IDs do not reuse its research identity or current017/018 owner contracts.
Independent structural additions preserve existing roles/grants/eligibility;
owner-fast is a profile, not a new role or fallback. Any future union must keep
all adopted research bytes and its separate enqueue reservation. Its
pre-existing identity collision remains foreign custody, not a .19 permission
or a new global gate.

The source ledger is actual switchboard/sw_047 → sw_050 → sw_051, including
immutable attempt/source-to-canonical decision bindings and terminal
recorded_without_goal dispositions; held sw_048/sw_049 are not delivered. Migration/producers/replay/
shadow readers/enforcement are serialized as RFC S2/S3, with sole mutation
owners and no permanent parallel writer or unsafe rollback. The coverage table
keeps selected first-switch source and M6 gates distinct from M7/later owners;
.19 cannot close on unbuilt behavior or unrecorded adoption.

## Accepted interface-review corrections (R1–R4)

RFC S2.1 discriminates real owner-reply, non-reply and condition-fire inputs in
the one inbox; kind-specific nullability/composite FKs and the one-time
consumed_at/epoch tuple retain historical consumed rows without invented epochs.
S2.2 rearms only a new generation of the unchanged approved envelope with a
cumulative count/deadline and durable prior-fire cursor. S3.1 fixes the initial
kind/alias/eight-mode wire; S3.2 retains Dots confirmation/late/history-preserving
retraction and page-independent reader disposition. S7 separates exact
authenticated internal peers from restrictive external gateway egress.
These complete existing boundaries; all original task states, ownership, held
choices, adoption/activation/M6 and current-source invariants remain unchanged.
