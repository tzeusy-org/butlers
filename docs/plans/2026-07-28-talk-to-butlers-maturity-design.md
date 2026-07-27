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
2. A route becomes immutable only after an `accepted` or `ok` acknowledgement.
   It may become a dead letter only with fenced proof that no route dispatch had
   a side effect. Unknown route outcomes become owner-visible ambiguity.
3. A terminal action has one immutable parent plus independently recoverable
   child effects. QA report, dead-letter capture, and owner acknowledgement are
   not collapsed into one success flag.
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

## Deliberately deferred product choice

The current contract has no generic question lane. The owner must decide whether
an otherwise ambiguous question should:

- remain an explicit rephrase/dead-letter outcome (recommended);
- enter a bounded domain-clarification lane; or
- receive constrained General residual authority.

This choice is separate from the reliability work and must not be inferred by
classification prompts or fallback code.

## Delivery gate

No new Bead graph is created by this design. Existing `bu-s3qvp` is live and
must not be treated as HOLD-gated merely because this document exists. After the
owner approves the OpenSpec changesets, create a new HOLD-first graph that
serializes #3624, the #3618 rebase-or-close decision, the documentation
reconciliation, and bounded recovery implementation leaves.
