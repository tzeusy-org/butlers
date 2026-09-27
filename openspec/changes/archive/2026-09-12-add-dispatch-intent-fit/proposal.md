## Why

Model resolution proves a catalog entry is *allowed* (enabled, verified, breaker
closed, quota headroom) but never that it *can do the job*. It also narrows to the
winning tier's top-priority set before anything else, so an unusable top-priority
entry takes the whole tier down with it.

That is not hypothetical. The seeded `api-haiku-cheap` entry sits at priority 30 —
the top of the `cheap` tier — while `ApiAdapter.invoke` raises for any non-empty
`mcp_servers`, which the spawner supplies for every trigger source except `healing`
and `qa`. A cheap-tier butler session can therefore resolve onto a model that cannot
run it, and the failure only surfaces after the session exists.

Fit is a precondition, not a preference, so it has to be decided before ranking.

## What Changes

- Define a deterministic, prompt-free `DispatchIntent` derived from the trigger
  source and complexity tier: what the dispatch requires (capabilities, context
  floor, deadline, per-call budget) and how consequential it is.
- Give runtime adapters a declared capability baseline and `public.model_catalog`
  a validated per-entry capability/context envelope layered over it. An envelope
  the descriptor layer cannot parse, and a required capability that is unknown at
  anything above observe-only consequence, fail closed.
- Filter hard fit across every candidate in every candidate tier *before* choosing
  the winning tier, before priority narrowing, and before the tie-break.
- Record a prompt-free resolution receipt: requested vs effective intent, every
  candidate with its outcome and fit findings, evidence age, and the winner reason.

Ranking is deliberately unchanged. Preferred features are recorded and never
re-rank, because preferring (for example) resume-capable models for interactive
triggers is an owner cost/quality decision, not an inference-contract one.

## Capabilities

### New Capabilities

- `dispatch-intent`: the deterministic requirement envelope for one dispatch, the
  hard-fit rules evaluated against a candidate, and the resolution receipt.

### Modified Capabilities

None. `model-catalog` gains additive requirements only (the capability envelope
columns and the fit-before-ranking resolution step); no existing requirement changes.

## Impact

- New: `src/butlers/core/model_capabilities.py`, `src/butlers/core/dispatch_intent.py`.
- Migration `core_204` adds `capabilities`, `max_context_tokens`, `max_output_tokens`
  to `public.model_catalog` — additive, idempotent, no backfill.
- `src/butlers/core/model_routing.py` gains `resolve_dispatch` and an `intent=`
  parameter on `resolve_model_with_effective_tier`; the existing resolution path is
  untouched when no intent is supplied.
- `src/butlers/core/runtimes/*` declare capability baselines.
- `src/butlers/core/spawner.py` derives an intent per spawn and passes it through.

## Deferred

- Persisting the receipt and exposing it as a session dossier door.
- Amending the Switchboard infrastructure contract for fleet inference policy.
- Any ranking influence from preferred features.
