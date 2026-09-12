## Context

The Timeline combines persisted events with cross-butler session lineage. Its
current nullable cost is overloaded: an absent model price and a failed runtime
that emitted no tokens both become "unpriced." The same surface uses a
background-query flag as a full-ledger loading signal, and has two replay REST
paths with inconsistent email-safety checks.

The change must preserve the existing live event invalidation path, Gmail's
non-idempotency protection, raw-error privacy, and the current persistent
schema. It also starts from `origin/main` after the Codex credential-sync
hardening, but does not itself mutate credentials or model catalog data.

## Goals / Non-Goals

**Goals:**

- Make cost coverage truthful at list, event-rollup, and window-rollup levels.
- Make no-usage sessions explicit without exposing runtime failure details.
- Keep passive event-stream and polling refreshes visually stable.
- Resolve replay safety once from authoritative source and connector state,
  then enforce it before every status transition.

**Non-Goals:**

- Reauthorize Codex, alter a provider credential, or change active model IDs.
- Enable Gmail replay or make any outbound delivery.
- Change connector replay drain mechanics or introduce a new database table.
- Throttle, debounce, or remove live event invalidation.

## Decisions

### Read-time cost evidence classification

Classify each session after the existing pricing/stored-cost calculation:

- `priced`: a numeric known cost exists, including `0.0`.
- `unpriced`: one or more token buckets exist but no price resolves.
- `no_usage`: neither token buckets nor a stored/estimated cost exists.

The core rollup helpers carry separate additive `unpriced_session_count` and
`no_usage_session_count` values rather than infer either from a nullable
subtotal. Raw runtime errors remain server-only; the ledger reports only the
safe fact that a session recorded no token usage.

This is chosen over a frontend-only status heuristic because window and
per-butler rollups do not contain enough lineage information to classify cost
coverage accurately.

### Semantic stale-data cue

Keep `placeholderData` and `FetchingDim`, but activate the dim only while
TanStack Query reports placeholder data for a changed query key. Same-key
background refetches caused by the stream or interval retain the ledger at full
opacity. This preserves an honest cue for stale filter results without using
network activity as a user-visible interruption.

This is chosen over debouncing invalidations because throttling changes
freshness semantics and still cannot distinguish passive refresh from a filter
transition.

### Server-authoritative replay policy

Create a shared policy resolver over both event stores and the active connector
registry. It derives a safe boolean and a non-sensitive reason from channel,
provider/channel connector candidates, endpoint identity, and `replay_safe`.
An accepted event's provider and channel are both candidates because registry
types can be connector-specific (for example, `telegram_bot`) while its
provider is generic (`telegram`). Filtered events similarly use their native
connector type and source channel, because older persisted rows can retain a
generic provider-like type. Exactly one active registry row must match:

- email is always denied;
- a resolved `replay_safe=false` connector is denied;
- absent or ambiguous policy is denied;
- resolver infrastructure failure prevents mutation.

Both REST actions use this resolver for preflight and display evidence. Every
status transition then includes the exact-one-active-safe-policy predicate and
a shared lock on its resolved registry row in the same SQL statement. The list
enrichment exposes the result so the UI can hide or disable unsafe actions and
selection. The frontend also treats missing policy evidence as unsafe, while
retaining a 409 fallback for stale data.

This is chosen over a UI-only email exception because direct POSTs must not
bypass safety, and over generic `TRUE` fallbacks because unknown source policy
is not evidence of idempotency.

## Risks / Trade-offs

- [Some historic non-email rows cannot resolve a connector policy] → They are
  intentionally non-actionable until source policy is explicit; the UI gives a
  non-sensitive reason rather than silently attempting a replay.
- [No-usage evidence does not identify a root cause] → The ledger avoids
  exposing raw runtime errors; operators can investigate failures through the
  existing session diagnostics.
- [A registry policy changes during a bulk request] → The request preflights
  the batch, and each replay transition atomically locks and predicates on the
  exact active safe policy, so an unsafe transition is never accepted.
- [Cost evidence increases response fields] → The fields are additive and
  default safely for older/missing frontend data.

## Migration Plan

1. Add focused failing Python and Vitest regressions.
2. Implement the additive API/model and UI behavior without a schema migration.
3. Validate focused backend/frontend suites, OpenSpec, lint, typecheck, and
   build on the exact branch head.
4. Deploy through the normal merged Compose workflow. Rollback is a normal
   code rollback; no persisted data has changed.

## Open Questions

None. The active catalog and credential corrections are operator actions and
remain outside this code change.
