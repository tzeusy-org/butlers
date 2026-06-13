# Design

## Scope

This change adds availability failover for model catalog entries after the runtime has
selected a candidate. It does not change the butler's task semantics, route inbox state
machine, or scheduler. The central constraint is side-effect safety: a fallback attempt
must not replay an event after the original attempt may have acted on it.

Existing initial resolution behavior is preserved. If a requested tier has no eligible
model, `resolve_model()` may still fall through to the next canonical tier according to
the current model-catalog routing contract. Once a candidate has been selected from a
specific effective tier, every subsequent failover attempt for that logical session is
restricted to that same effective tier.

## Candidate Resolution

Add a resolver function that can return the ordered candidate set, or the next candidate
excluding already-attempted IDs. It must reuse the existing model-catalog semantics:

- join `public.model_catalog` with `public.butler_model_overrides`;
- apply `COALESCE` for `enabled`, `priority`, and `complexity_tier`;
- include only effective `enabled = true`;
- include only models whose state is verified or untested where state exists;
- sort by effective priority descending, then `created_at ASC`, then `id ASC`;
- restrict to one exact effective tier for failover;
- exclude catalog entry IDs that have already been attempted in the logical session.

Round-robin remains valid for initial selection among tied top-priority candidates. For
fallback ordering, deterministic ordering is preferable: after the first candidate fails,
the next candidate should be predictably the next highest-priority model, not another
rotation that could skip a lower candidate or repeat a tied one.

## Failure Classification

Failover is eligible only for systemic failures where the selected runtime failed as
infrastructure before it could perform domain work. Initial classifier inputs should be
exception class, adapter type, error message, process metadata, captured tool calls, and
whether a session row was created.

Eligible examples:

- runtime binary missing or unregistered runtime type;
- CLI exits before emitting a valid result due to auth, provider outage, malformed CLI
  config, model unavailable, or provider-side rate limiting;
- adapter timeout before any tool call or side-effect-capable output;
- MCP tool discovery failure before any tool was executed;
- quota exhaustion before invocation.

Ineligible examples:

- any captured MCP tool call, even if the final session failed;
- guardrail terminations such as `degenerate_tool_loop`, `tool_call_budget_exceeded`,
  or `token_budget_exceeded`;
- business/tool validation errors;
- route inbox processing errors after a butler tool has mutated state;
- agent answer quality failures or empty-but-valid model output.

The classifier should default to no failover for unknown errors. Operators can still use
the failure tail to disable or demote a model.

## Spawner Flow

The spawner should treat a trigger as one logical session with a bounded sequence of
model attempts. Pseudocode:

1. Resolve the initial candidate as today, including existing initial tier fallthrough.
2. Save the effective tier that produced the candidate.
3. Before invoking, check quota for the candidate.
4. If quota is exhausted, record a skipped attempt and ask for the next eligible
   candidate in the same effective tier.
5. Invoke the adapter.
6. On success, complete the session using the model that succeeded and record attempt
   provenance.
7. On failure, gather captured tool calls and process metadata.
8. If the classifier says failover is eligible and captured tool calls are empty, record
   the failed attempt and try the next same-tier candidate.
9. Otherwise complete the logical session as failed with the original error.
10. If every same-tier candidate is exhausted, complete the session with the last error
    and include a failover-exhausted provenance marker.

Use a small hard cap on attempts as a defensive backstop, no greater than the candidate
count and configurable only if needed later. Healing sessions should not get special
treatment unless they already bypass a normal tool surface; if they do, they still must
respect the same side-effect gate.

## Provenance And Observability

Operators need to know why the final model differs from the top-priority row. The
implementation should persist enough detail to answer:

- which catalog entries were attempted;
- which were skipped due to quota;
- which failed with which error code/message;
- which attempt succeeded;
- whether failover was suppressed because side effects were detected.

This can be implemented by extending `public.dispatch_failures` plus session process
logs, or by adding a purpose-built `public.model_dispatch_attempts` table. If a new
public table is added, update the RFC 0006 public-schema write authorization matrix and
the database-security spec. The existing `GET /api/settings/models/{id}/failures`
surface can remain the primary operator view if it includes the new attempt provenance.

Metrics should be minimal and high signal:

- `butler_model_failover_attempts_total{butler,from_model,to_model,reason}`;
- `butler_model_failover_suppressed_total{butler,reason}`;
- `butler_model_failover_exhausted_total{butler,tier}`.

## Testing Strategy

Unit tests should cover the classifier first because it is the safety boundary. Model
routing tests should prove exact-tier next-candidate behavior, override application,
attempt exclusion, state filtering, and deterministic same-tier ordering.

Spawner tests should cover:

- quota exhaustion skips primary and invokes the next same-tier candidate;
- quota exhaustion returns the existing block when no same-tier candidate exists;
- CLI systemic failure before tool calls retries next same-tier candidate;
- runtime failure with captured tool calls does not retry;
- guardrail errors do not retry;
- failover exhaustion records all attempts and returns the final failure.

At least one adapter-level test should simulate Codex CLI failure before any MCP tool
execution, because Codex is the motivating operational example. Integration testing
should use fakes for side-effect safety; live provider tests are not required.
