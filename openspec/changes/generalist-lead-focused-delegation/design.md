# Target shape and transition constraints

## Context

The [amendment](../../../about/legends-and-lore/decisions/2026-10-14-doctrine-focused-delegation.md)
and proposed architecture doctrine own the reasoning model. RFC 0027 owns the
current searchable per-butler presentation surface. These are different scopes.

## Decisions

1. A goal has one active lead ownership slot. The lead retains the full user
   objective, resolves competing constraints, and is accountable for the result.
   Independent goals can execute concurrently.
2. Children are depth 1 and have dedicated task prompts, relevant context,
   inherited governing guidance and explicitly bounded execution grants.
   A child can request more investigation from its lead, never delegate itself.
3. Domain services own durable facts, invariant checks and effect receipts.
   Both direct lead calls and child calls use the existing MCP control-plane
   boundary; neither requires another LLM merely to access a domain capability.
4. Schedules and obligations outlive agent sessions. Proactive events can
   initiate a lead, including a domain-focused lead that needs no children.
5. Search scope and execution scope are related but distinct. A narrower prompt
   cannot establish a narrower execution grant. Runtime admission must prove
   that all available effect paths respect the grant.
6. Depth, total work, concurrency, deadline and cancellation are separately
   enforced. Retries and replacements remain in the same goal accounting.
7. Child results carry evidence, uncertainty and effect status. The lead cannot
   convert partial work or unknown effects into a successful whole-goal claim.

## Tradeoffs and alternatives

Mandatory specialist routing preserves clear focus but can fragment a goal and
force extra reasoning for simple access. An unrestricted universal agent retains
the goal but weakens least privilege and proactive ownership. The chosen target
retains one coherent planner while separating capability ownership and scoped
execution from reasoning organization.

Depth one keeps responsibility legible and avoids recursive agent trees, but
requires the lead to coordinate work that a deeper team might delegate locally.
Serial sibling rounds remain possible and must be bounded. Whether that limit
needs revision is an outcome question, not settled by current benchmark results.

## Transition and unresolved implementation design

This is a design reservation, not an implementation-ready wire contract.
Before implementation, resolve the lead's host and identity; actor/grant
issuance and revocation; protocol binding and resource scope; trusted lineage;
child admission, return, stop and replacement state machines; atomic budget
accounting; conflict handling and durable effect reconciliation; and exact
rollback/activation gates. Preserve existing private-source and approval rules.

Explicit MODIFIED baseline deltas and RFC amendments must accompany that design.
Do not call current cross-butler delegation a depth-one child mechanism, treat
RFC 0027 as call authorization, or expand General/Switchboard by renaming them.
Existing pending tool-discovery, fast-answer and platform-consolidation changes
are independent and must be reconciled at their actual landed versions.

## Validation strategy

Evaluate whole-goal success, constraint preservation, useful proactive actions,
missed obligations, owner corrections, latency and total cost including retries.
Use simple zero-child work, parallel read-only work, conflicting cross-domain
constraints, scoped child writes, cancellation, recovery and adversarial
delegation attempts. Compare architectures with equivalent tool/context access
and disclosed compute budgets; do not substitute an agent count or tool-search
hit rate for outcome evidence. No such runtime evaluation is claimed here.
