# Observability and Operations

Butlers is a long-running, async, multi-process system. Runtime-facing changes
must preserve diagnosability, not just nominal behavior.

## Core Expectations

- Failure paths should emit enough context to reconstruct what happened.
- Background work should be traceable to a request, session, task, or job.
- Health and readiness behavior should stay aligned with operator-facing
  surfaces and docs.
- Recovery logic should record decisions clearly enough to separate operator
  error, policy rejection, timeout, and actual execution failure.

## When Observability Work Is Mandatory

Add or update logs, metrics, traces, or status surfaces when a change touches:

- daemon startup or shutdown
- session spawning or timeout behavior
- routing, classification, or queueing
- connectors or ingestion paths
- scheduler, reminders, or background polling
- recovery and self-healing flows
- operator-triggered actions that may fail asynchronously

## Logging and Telemetry Discipline

- Include stable identifiers when available: request id, session id, butler
  name, action id, schedule id, or sync cursor.
- Do not log secrets, raw tokens, or sensitive message bodies unless a contract
  explicitly requires a redacted form.

## Operability Rules

- If a workflow depends on a health endpoint, status API, or dashboard surface,
  keep that contract coherent with the implementation.
- If a fallback or no-op behavior exists for backward safety, it must be
  intentional, documented, and visible in logs or status.

## Cross-References

- RFC 0005 defines the observability architecture.
- `AGENTS.md` captures repo-specific runtime and migration contracts discovered
  during implementation.

## Goal-Level Evidence

The [goal runtime](../legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md)
must make one goal reconstructable across its lead, children, retries,
escalations and replacements. Record trusted lineage, governing policy
versions, grant scope, admission decisions, aggregate work budgets,
cancellation fences, and durable result/effect references without leaking
private payloads into telemetry.

Verification must distinguish an accepted request from a launched invocation,
a child result from a completed goal, and cancellation from reversal of an
already committed effect. Recovery evidence must establish single active lead
ownership, preserved budgets and no unproven effect replay. Per-session logs
alone do not establish these goal-level guarantees; the goal ledger does.
