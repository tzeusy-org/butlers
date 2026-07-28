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
| Availability | components reachable; configured browser/API path unproven | The local API is `ok`, the local frontend returns HTTP 200, and `/api/butlers` reported 12/12 `ok` on 2026-07-28; the configured widget API path and remote Tailscale ingress are not independently proven from this host. |
| Specialist routing | functional in the backplane | In the last 24 hours, 39 successful Switchboard classification sessions each called `route_to_butler`; a fresh widget send-to-reply trace is not yet live-proven. |
| Truthful dispatch / Stop | in flight | PR #3624 has durable message-scoped Stop and route handoff work. Its current head is green but based behind `main`; it needs independent current-base evidence, merge, and recovery-spec rebase before the next contract is signed off. |
| Terminal bug/dead-letter effects | weak | A crash after reservation can leave `external_action_in_progress` with no durable per-effect proof or recovery owner; existing P1 `bu-s3qvp` names this gap. |
| Owner-visible recovery | weak | No durable read/UI contract yet exposes a route-only ambiguous outcome or a partially completed terminal action. |
| Operator-flow evidence | weak | The panel renders locally, but no fresh dashboard send → Switchboard route → reply → Stop trace was created for this audit; the only persisted widget conversation is stale and lacks per-message/session linkage. |
| Generic questions | intentionally absent | The current lane taxonomy has no approved question lane. That is a product decision, not a defect to paper over with General. |

The local components are reachable, not a configured-browser or production
end-to-end proof: no new owner message was sent for this audit, and the active
dev checkout is not an exact deployment of either review branch. A headless
local browser reached the panel, but bare Vite issued its intended Tailscale-mounted
`/butlers-dev-api/api/...` request and received 404; direct dashboard API and
Vite `/api/...` proxy requests were 200. The host's self-request to the stated
Tailscale URL also returned 404 despite advertised Serve paths, but it resolves
back to the host itself. That is **[Unknown]** remote-tailnet behavior, not proof
that the owner's remote browser is broken. A remote-tailnet smoke must establish
the intended path before this surface is called externally verified.

## What is true today

- `POST /api/butlers/{name}/conversation-turns/{message_id}/cancel` is the
  canonical message-scoped Stop endpoint on PR #3624; the legacy
  conversation-scoped endpoint remains a compatibility path.
- The current response remains boolean-shaped. The proposed recovery contract
  adds a durable, additive outcome discriminator rather than forcing the UI to
  infer recovery state from `cancelled` / `already_finished`.
- PR #3618 (`make-dashboard-chat-truthful`) is an open draft based on an older
  base. Its dispatch-receipt/UI work must be rebased and revalidated after
  #3624, or its truthful routed-versus-targetless receipt, routed-butler
  accountability, and non-destructive read recovery must each be retained in the
  surviving packet or explicitly owner-rejected before it is closed as
  superseded; it cannot be silently folded in.
- A route acknowledgement, a QA report, a dead-letter capture, and an
  in-thread reply are distinct visible effects. One turn row alone cannot prove
  their independent crash boundaries.
- Existing conversation and inbox rows do not carry enough per-message/session
  linkage for support-grade causal tracing; this is a distinct observability gap,
  not evidence that the recent backplane routes failed.

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

Both strict OpenSpec validations pass. The whole-tree authoring trace check still
fails on 2,589 repository-wide errors, so it is recorded as a legacy evidence
limitation rather than a pass gate for this packet. Planned-work test-citation
warnings are expected; they are not evidence that the proposed behavior has
shipped.

## Reconciliation record

The live inventory plus three independent artifact reconciliations converged the
plan before this report:

1. Workflow/implementation inventory established the actual two-lane product,
   the stale #3618/#3624 ordering, and the absence of a generic question lane.
2. First independent recovery review required a singular parent action with
   individual QA, dead-letter, and reply receipts; bounded receiver-proof
   recovery; and Stop linearization before an irreversible call.
3. Second independent contract review required a durable QA inbox → fenced
   claim → acknowledged-finding lifecycle, a representable immutable
   `owner_resolution` overlay, and reciprocal Stop/effect fences.
4. Final independent replacement review restored every modified canonical
   requirement's baseline guarantees, serialized the two RFC 0003 amendments,
   and confirmed strict validation and diff hygiene.

The resulting plan refuses both duplicated delivery and fabricated calm:
unknown route or effect state becomes visible ambiguity, never an automatic
second send or a success-shaped toast.

## Ordered work, once approved

1. Before claiming configured or intended-host availability, have the owner or
   an explicitly owner-authorized separate tailnet client passively smoke the
   configured `/butlers-dev/` panel and the actual widget request `GET
   /butlers-dev-api/api/butlers/switchboard/conversations?limit=1`, recording
   only route, status, and timestamp. That list can reveal conversation titles
   and routed-butler metadata; if its read is not authorized, use the narrower
   `/butlers-dev-api/api/butlers` proxy check instead. An end-to-end
   send/reply/Stop canary needs separately authorized test content and must
   record only safe request/message/session evidence.
2. Independently review #3624, recheck its current head/base, and merge only
   with current-base exact-head or validated merge-result evidence. Rebase the
   recovery packet on that landing and preserve or explicitly supersede every
   Stop/SSE clause before its signoff.
3. On that merged base, explicitly rebase-and-review PR #3618, or record an
   owner-approved disposition of each of its distinct receipt, accountability,
   and read-recovery guarantees; transplant retained guarantees into the
   surviving packet before closing #3618 as superseded.
4. Apply the documentation-only reconciliation change.
5. Implement the `bu-s3qvp` recovery contract behind an owner-held observe
   mode; promote to active only after a compose kill/restart canary and metric
   review.
6. Consider per-message/session traceability, first-token streaming, unified
   read surfaces, or a question lane
   only as separate decisions after the reliability spine is real.

## Bead safety and release state

No Beads were created or mutated during this planning run. `bu-s3qvp` is already
an open live P1 and `bu-27dxl.9` is also live; this report does **not** make
either safe to dispatch. Before any implementation starts, the owner must approve
the changesets and create a new explicit HOLD-gated execution graph that names
the leaves, ownership, dependencies, and the #3624/#3618 ordering gate.

The proposed graph is deliberately only a preview:

```text
[HOLD: owner decides product boundary and approves changesets]
  ├─ current-base #3624 and recovery-SSE-rebase gate
  ├─ #3618 rebase-or-per-guarantee-disposition gate
  ├─ reconciliation documentation change
  └─ bu-s3qvp recovery leaves
       ├─ receipt/idempotency boundaries
       ├─ action journal + lane fencing
       ├─ reconciler modes + owner APIs
       └─ crash/restart + UI canary
```

## Owner decisions still required

No implementation Beads may be created until these are decided and the two
changesets are approved.

| Decision | Choices | Recommendation |
| --- | --- | --- |
| Dashboard product boundary | Document a narrow owner-only operator-ingress exception; rework the surface back to read-only; or treat it as a general chat surface | Document the narrow exception: direct `dashboard` / `internal` ingress through the standard Switchboard spine, not a public/general chat system. |
| What “mature” includes now | Stop at truthful ingress/route acknowledgement plus terminal bug/dead-letter effects; or add durable downstream routed-session/reply outcome now | Stop at the current reliability slice. A downstream session/reply durability contract is valuable but must be a separately approved change. |
| Ambiguous generic questions | Truthful dead-letter/rephrase; bounded domain clarification; or deliberately constrained General authority | Keep truthful dead-letter/rephrase behavior. The current system must not invent General residual authority. |
| Intended-host evidence | Owner-run or explicitly authorized remote-tailnet passive smoke of the actual widget request; or authorize an anonymized send/reply/Stop canary after passive success | Require the privacy-bounded passive smoke before any configured/external availability claim; authorize a content-safe canary only if end-to-end proof is needed before the recovery release. |
| #3618 truthful-UI disposition | Rebase and independently reconcile #3618; or, after per-guarantee review, retain its routed-versus-targetless receipt, routed-butler accountability, and read-recovery behavior in the surviving packet; or explicitly reject one or more guarantees | Preserve all three guarantees, through a rebase or explicit transplant after #3618 no longer actively modifies the same requirements. |
| Direction-packet approval | Approve both narrow changesets after the above choices and, after #3624 lands, resolve #3618 through rebase-and-reconcile or owner-approved per-guarantee retention/rejection before closing it; or revise their scope | Approve only after the product boundary and maturity definition are explicit and the #3618 HOLD is resolved without silently discarding a truthful UI behavior. |

## Conclusion

**Real direction**: make Talk to Butlers a durable specialist-routing control
surface whose receipt, Stop, route, and recovery claims are independently
provable.

**Work on next**: establish remote-tailnet availability evidence, resolve #3624
with current-base exact-head evidence and rebase the recovery SSE replacement,
decide #3618 on that base while preserving or explicitly rejecting each distinct
truthful UI guarantee, then release the owner-approved recovery packet under a
HOLD gate.

**Stop pretending**: that locally reachable components prove the configured
browser path or intended host works, that a boolean Stop response proves every
terminal side effect, or that an unapproved generic question lane can be safely
inferred from ambiguity.
