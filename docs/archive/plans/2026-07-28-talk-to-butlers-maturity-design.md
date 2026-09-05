> **ARCHIVED** — This implementation/design plan is historical. Archived on 2026-09-06.
> **Reason:** Reliability-first front-door guarantees (message-scoped Stop, receipts, durable route reservation) landed in the conversations spine.
> **Successor:** `openspec/specs/dashboard-conversations/spec.md`.
>
> The doctrine on archival lives in `openspec/specs/docs-information-architecture/spec.md`.

# Talk to Butlers maturity design

**Status:** proposed direction, awaiting owner approval before Bead authoring or
implementation dispatch.

## The design problem

Talk to Butlers works as an available dashboard entry point, but it is not yet a
reliable control surface. A user-visible route, QA report, dead-letter capture,
reply, or Stop can cross different crash boundaries. The design must preserve the
product's specialist-roster identity while making each consequential claim
durable and inspectable.

## Considered approaches

| Approach | Benefits | Cost / risk | Decision |
| --- | --- | --- | --- |
| Reliability-first specialist front door | Makes the existing product truthful: message-scoped Stop, receipt-backed terminal actions, route ambiguity, crash recovery, owner controls. | Requires migrations, effect-specific receiver contracts, and a staged rollout. | **Recommended** |
| Add a generic question lane now | Feels broadly useful and may reduce dead letters. | Invents product authority and can turn ambiguity into silent General routing before reliability is proven. | Rejected for this changeset |
| Merge Stop only, then declare maturity | Delivers a valuable immediate control improvement. | Leaves post-reservation terminal effects and route ambiguity without durable recovery. | Insufficient |

## Recommended behavior

1. The first dashboard classification action reserves exactly one lane:
   `route_pending`, `bug_report`, or `dead_letter`.
2. A route becomes immutable only after an `accepted` acknowledgement.
   It may become a dead letter only with fenced proof that no route dispatch had
   a side effect. Unknown route outcomes become owner-visible ambiguity.
3. A terminal action has one immutable parent plus independently recoverable
   child effects. QA report, dead-letter capture, and owner acknowledgement are
   not collapsed into one success flag. Dashboard QA mode is authorized from a
   validated Switchboard-router MCP service principal, never a caller-supplied
   source, and its receipt-backed discovery inbox survives QA restart into the
   ordinary patrol/triage path.
4. Stop is message-scoped and server-linearized. The UI may say `Cancelled by
   owner` only after a durable cancelled outcome; pending or ambiguous Stop
   results refetch the same message read model. A pending Stop survives reload
   as its own durable state rather than being rendered as ordinary submission.
5. A targetless ingress cannot spin forever: after its durable 60-second claim
   fence, the owner may recover that exact immutable message through the same
   claim boundary. The system never silently sends it again or creates a second
   user message.
6. Reconciliation starts in persisted owner-controlled `observe` mode. It can
   inspect receipts and expose bounded ambiguity but cannot issue an automatic
   second external effect. Promotion to `active` follows a kill/restart canary
   and metric review.
7. The outcome-only message-scoped Stop contract replaces the repository-owned
   conversation-scoped endpoint and boolean response in one implementation
   change. The dashboard client, both chat surfaces, tests, and API inventory
   migrate before the aliases are deleted; no indefinite compatibility surface
   remains without a verified consumer, accountable owner, and dated sunset.

## Deliberately deferred product choices

The owner must approve a narrow operator-ingress exception for this dashboard
surface: it is a direct owner-only `dashboard` / `internal` ingress through the
standard Switchboard spine, not generic chat. The current reliability slice also
ends at truthful ingress/route acknowledgement and terminal bug/dead-letter
effects; durable downstream routed-session/reply outcome is a separate approved
change.

The current contract has no generic question lane. The owner must decide whether
an otherwise ambiguous question should:

- remain an explicit rephrase/dead-letter outcome (recommended);
- enter a bounded domain-clarification lane; or
- receive constrained General residual authority.

This choice is separate from the reliability work and must not be inferred by
classification prompts or fallback code.

## Delivery gate

No new Bead graph is created by this design. Existing `bu-s3qvp` is live and
must not be treated as HOLD-gated merely because this document exists. Before
the owner may approve the product choices and OpenSpec changesets, #3624 must
land at an independently verified current-base head and the recovery packet must
rebase and reconcile its full Stop/SSE replacement against that landing; then
#3618 must be rebased and independently reconciled, or its truthful dispatch receipt,
routed-butler accountability, and non-destructive read-recovery guarantees must
each be retained in the surviving packet or explicitly owner-rejected before it
is closed as superseded. Only then create a new HOLD-first graph for the
documentation reconciliation and bounded recovery implementation leaves.
