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
10. Engineering executors and tool-less model functions are the only model use
    outside goals (D18).
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

Active changes at this change's authoring that touch the same capabilities;
each must archive first, be re-scoped, or be folded in before task 4.2:

- Requirements this change removes or rewrites:
  `durable-dashboard-terminal-action-recovery` (Switchboard chat lanes,
  `staffer-qa`), `specify-telegram-continuity-routed-context-contract`
  (misroute re-dispatch), `recover-ingestion-target-deliveries`
  (`module-pipeline`), `add-runtime-tool-surface-discovery`
  (`runtime-config-api`), `harden-runtime-auth-and-breaker-attention` and
  `generation-fenced-codex-auth-rotation-provenance` (`core-credentials`).
- Routing-dependent additions to re-scope: `ask-once-knowledge-gaps`,
  `durable-precommit-cancellation-admission`,
  `restore-butler-control-plane-liveness`,
  `strict-owner-telegram-wake-recovery`,
  `specify-roster-identity-owner-operations-overlay`,
  `repair-whatsapp-identity-reconciliation`, `account-security-perception`,
  `reconcile-dashboard-conversation-contracts`.
- Same-capability edits to merge: `specify-improvement-proposal-spine`
  (`core-skills`), `education-mind-map-lifecycle-integrity` (education
  capabilities), `relationship-fact-effective-time` (`butler-relationship`),
  `compose-general-capture-with-private-custody` (`butler-general`).
- Any change that adds a model path outside D18, such as
  `define-dashboard-fast-answer-runtime`, is re-scoped before approval.

The `dashboard-*` capabilities that conflict with the goal runtime are left to
the D19 dashboard change.

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
