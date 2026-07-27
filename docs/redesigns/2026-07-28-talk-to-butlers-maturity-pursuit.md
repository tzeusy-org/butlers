# Talk to Butlers Maturity Pursuit — 2026-07-28

A focused JARVIS-style maturity pursuit for the **Talk to Butlers** front door,
not a whole-ecosystem page audit. It reconciles the live dashboard path, the
current implementation, active pull requests, persistent turn state, and the
next trustworthy delivery boundary.

**Data:** [structured audit data](2026-07-28-talk-to-butlers-maturity-pursuit-data.json).

## North star

Talk to Butlers is a specialist-roster front door: an owner statement reaches a
clear, appropriate domain butler or a truthful system workflow; the owner can
see what happened, stop work honestly, and recover from process loss without
being told a report was filed, a route completed, or a Stop succeeded when the
system cannot prove it. It is not a generic chat wrapper and it must not silently
route uncertainty to General.

## Current maturity verdict

**Functional, not mature.** The dev stack is available and the entry path is a
real vertical slice, but its most consequential control and recovery guarantees
remain in flight.

| Dimension | Verdict | Evidence / gap |
| --- | --- | --- |
| Availability | functional | Local API health is `ok`, the frontend returns HTTP 200, and `/api/butlers` reported 12/12 `ok` on 2026-07-28. |
| Specialist routing | functional | Dashboard ingress uses `dashboard` / `internal`, data/correction and bug/system lanes, and refuses a silent General fallback. |
| Truthful dispatch / Stop | in flight | PR #3624 has durable message-scoped Stop and route handoff work, but it is open and its fresh hosted checks are still running. |
| Terminal bug/dead-letter effects | weak | A crash after reservation can leave `external_action_in_progress` with no durable per-effect proof or recovery owner; existing P1 `bu-s3qvp` names this gap. |
| Owner-visible recovery | weak | No durable read/UI contract yet exposes a route-only ambiguous outcome or a partially completed terminal action. |
| Generic questions | intentionally absent | The current lane taxonomy has no approved question lane. That is a product decision, not a defect to paper over with General. |

The local stack shows product availability, not a production end-to-end proof: no
new owner message was sent for this audit, and the active dev checkout is not an
exact deployment of either review branch.

## What is true today

- `POST /api/butlers/{name}/conversation-turns/{message_id}/cancel` is the
  canonical message-scoped Stop endpoint on PR #3624; the legacy
  conversation-scoped endpoint remains a compatibility path.
- The current response remains boolean-shaped. The proposed recovery contract
  adds a durable, additive outcome discriminator rather than forcing the UI to
  infer recovery state from `cancelled` / `already_finished`.
- PR #3618 (`make-dashboard-chat-truthful`) is an open draft based on an older
  base. Its dispatch-receipt/UI work must be rebased and revalidated after
  #3624, or explicitly closed as superseded; it cannot be silently folded in.
- A route acknowledgement, a QA report, a dead-letter capture, and an
  in-thread reply are distinct visible effects. One turn row alone cannot prove
  their independent crash boundaries.

## Changeset direction

Two intentionally narrow OpenSpec changes carry the proposed work:

1. [`reconcile-dashboard-conversation-contracts`](../../openspec/changes/reconcile-dashboard-conversation-contracts/)
   corrects RFC 0003 provenance, reconciles the anchor/resume status record, and
   limits any future roadmap edit to a content-only correction of `bu-27dxl.9`.
   It changes no runtime behavior.
2. [`durable-dashboard-terminal-action-recovery`](../../openspec/changes/durable-dashboard-terminal-action-recovery/)
   defines one lane reservation, parent/child effect receipts, receipt-before-
   retry reconciliation, truthful Stop outcomes that survive reload, bounded
   exact-message ingress recovery, durable route ambiguity, partial-effect
   language, and an observe-first rollout.

Both strict OpenSpec validations pass. The repository-wide traceability checker
has its existing 2,579 baseline errors; these two authored changes add no new
traceability errors. Missing test citations are expected implementation warnings,
not evidence that the planned behavior has shipped.

## Reconciliation record

Four independent passes converged the plan before this report:

1. Workflow/implementation inventory established the actual two-lane product,
   the stale #3618/#3624 ordering, and the absence of a generic question lane.
2. Recovery review required a singular parent action with individual QA,
   dead-letter, and reply receipts; bounded receiver-proof recovery; and Stop
   linearization before an irreversible call.
3. API/UI review required the canonical message endpoint, additive outcome
   compatibility mapping, durable effect-level language, and a concrete QA MCP
   wire contract.
4. Direction review required first-lane fencing, safe `route_pending →
   dead_letter` transition rules, a durable route-ambiguity read path,
   `accepted|ok` acknowledgement vocabulary, a normative observe/active mode,
   and exact Bead-safety wording.

The resulting plan refuses both duplicated delivery and fabricated calm:
unknown route or effect state becomes visible ambiguity, never an automatic
second send or a success-shaped toast.

## Ordered work, once approved

1. Land PR #3624 only after an independently reviewed, green exact head.
2. On that merged base, explicitly rebase-and-review PR #3618 or close it as
   superseded.
3. Apply the documentation-only reconciliation change.
4. Implement the `bu-s3qvp` recovery contract behind an owner-held observe
   mode; promote to active only after a compose kill/restart canary and metric
   review.
5. Consider first-token streaming, unified read surfaces, or a question lane
   only as separate decisions after the reliability spine is real.

## Bead safety and release state

No Beads were created or mutated during this planning run. `bu-s3qvp` is already
an open live P1 and `bu-27dxl.9` is also live; this report does **not** make
either safe to dispatch. Before any implementation starts, the owner must approve
the changesets and create a new explicit HOLD-gated execution graph that names
the leaves, ownership, dependencies, and the #3624/#3618 ordering gate.

The proposed graph is deliberately only a preview:

```text
[HOLD: owner approves changesets and question-lane stance]
  ├─ exact-head #3624 gate
  ├─ #3618 rebase-or-close decision gate
  ├─ reconciliation documentation change
  └─ bu-s3qvp recovery leaves
       ├─ receipt/idempotency boundaries
       ├─ action journal + lane fencing
       ├─ reconciler modes + owner APIs
       └─ crash/restart + UI canary
```

## Owner decision still required

The reliability direction above does not require a new generic question lane.
Before any such work is created, choose one explicit stance for an ambiguous
owner question: keep truthful dead-letter/rephrase behavior (recommended), add a
bounded domain-clarification lane, or give General a deliberately constrained
residual question authority. The current system must not choose that policy by
accident.

## Conclusion

**Real direction**: make Talk to Butlers a durable specialist-routing control
surface whose receipt, Stop, route, and recovery claims are independently
provable.

**Work on next**: resolve #3624 at a green exact head, decide #3618 on that
base, then release the owner-approved recovery packet under a HOLD gate.

**Stop pretending**: that a reachable chat widget is mature, that a boolean Stop
response proves every terminal side effect, or that an unapproved generic
question lane can be safely inferred from ambiguity.
