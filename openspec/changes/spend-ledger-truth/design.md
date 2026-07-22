## Context

`public.token_usage_ledger` is an append-only public-schema record of the
catalog entry that actually consumed tokens. It includes the executing
`catalog_entry_id`, butler identity, optional session ID, timestamp, purpose,
and all four token buckets. The current Spend API instead fans out to each
butler's `sessions` table for daily, summary, butler, and model prices. A
session's `model` is the requested model, not necessarily the resolved or
executed catalog entry, so using it for money can produce a convincing but
false model attribution. It also multiplies a month-level ledger price across
daily session fragments.

The existing monthly ceiling already obtains its numeric subtotal from the
ledger through `price_mtd_from_ledger`, but the helper turns a missing price
into zero. Live catalog entries for the gpt-5.6 Codex family have ledger use
but no price entry, so the page and gate are falsely calm.

This change spans pricing configuration, the core gate, the privileged API,
and the Spend dashboard. It intentionally uses existing schema: no migration
is needed, and avoiding one also avoids the active `core_177` migration on a
concurrent branch.

## Goals / Non-Goals

**Goals:**

- Preserve the three distinct states: priced, explicitly zero-marginal-cost,
  and unpriced.
- Make ledger aggregates authoritative for the dashboard's dollars and token
  actuals: summary, daily series, butler/model/purpose breakdowns, and
  forecast.
- Give every affected response a structured unpriced envelope instead of
  encoding unknown spend as a zero.
- Make the monthly-ceiling's partial visibility explicit and surface material
  sessions-versus-ledger drift before it can be mistaken for reconciliation.
- Label pre-2026-07-10 ranges that retain requested-model session labels.

**Non-Goals:**

- Changing the monthly-ceiling policy to block all dispatches merely because a
  model is unpriced. The known-priced subtotal remains the existing gate input,
  while its blind spot becomes explicit.
- Backfilling or mutating historical session rows. The API label and ledger
  attribution are safer and reversible.
- Reworking schedule/top-session evidence or routing-rule enforcement. Those
  paths retain their existing metadata semantics; this change only replaces
  named aggregate dollar paths.
- Introducing model prices into `public.model_catalog`; `pricing.toml` remains
  the established runtime pricing configuration.

## Decisions

### 1. Pricing returns `None` for unknown and carries an explicit billing class

`PricingConfig.estimate_cost()` already has a truthful `float | None` contract.
`estimate_session_cost()` will align with it rather than converting `None` to
`0.0`. Flat and tiered price objects gain an optional billing class, with
`subscription` and `local` being explicit known-zero classes; gpt-5.6 entries
will be declared `subscription` with zero marginal rates.

This distinguishes an absent price from an intentionally zero marginal price
without changing the established price-file ownership. Adding a nullable price
column to the catalog was rejected: it needs a migration, duplicates the
existing configuration, and would still require migration/backfill choices.

### 2. One reusable ledger aggregation produces priced subtotal plus omissions

The core routing module will expose a small structured result for a ledger
aggregation: known `cost_usd` and a list of unpriced model usages (model ID,
call count, and token buckets). The aggregate joins the ledger to
`model_catalog` so it uses the executed model ID. All callers invoke the same
pricing routine, including `price_mtd_from_ledger` and `check_monthly_ceiling`.

The alternative—returning a number plus a log warning—was rejected because a
log cannot prevent a UI or API consumer from presenting an incomplete subtotal
as total spend. Returning zero for unknown was rejected for the same reason.

### 3. The Spend API uses ledger GROUP BYs for money and actual tokens

The spend router will issue range-bounded ledger queries grouped by date,
butler, model, and purpose. Its summaries, daily chart rows, model/butler/
purpose breakdowns, and forecast use the same structured pricing conversion.
Session fan-out may still provide non-monetary session doors or diagnostics,
but it is never used to choose a model price or total a named aggregate.

Every implicit Spend "today" uses the UTC clock. That keeps preset summaries,
the seven-day daily default, MTD breakdowns, forecast elapsed-day math, and the
monthly-ceiling ledger helper on the same calendar month at a local-timezone
rollover.

This keeps all four dollar-bearing aggregate surfaces on one source. The
previous fan-out cannot be retained as a fallback: a fallback that changes the
source of truth would revive the requested-versus-executed attribution bug.
When the ledger is unavailable, the response is degraded rather than
substituting session-derived dollars.

### 4. API contracts expose omissions, divergence, and historical context

Spend response data includes `unpriced_models`; the forecast additionally
states that the ceiling is blind to their count. A date/butler deadman compares
non-price token totals from the ledger against session totals and reports a
divergence when the relative difference exceeds five percent. It does not
invent reconciliation from rows that lack matching evidence. Ranges beginning
before 2026-07-10 receive a fixed attribution note stating that session model
labels from that period may be requested rather than executed; ledger costs
remain executed-model costs.

Persisting a separate divergence table/job was rejected for this slice. The
existing endpoint request already has the query window and can expose fresh
evidence without a migration or a stale scheduled-job lifecycle. The detector
is structured so a future scheduled monitor can reuse it.

### 5. The UI renders an absence, not a numeric zero

Model breakdown rows with unknown price render `—/unpriced` and show their
observed call count. Explicit subscription/local entries remain numeric `$0`
with their billing class. Summary/forecast/chart areas use a source-degraded
note to name unpriced coverage, ceiling blindness, material divergence, and
the pre-fix attribution window instead of attaching a deceptive zero to any
of those cases.

That rule also applies to compact and synthesized summary consumers: Butler
Spend and Overview, desktop/mobile Sidebar, SpendPage movers, and spend
verdicts suppress partial dollar totals and calm pace/mover language whenever
their relevant `unpriced_models` envelope is non-empty. A numeric `$0.00`
remains valid only when the relevant coverage is complete.

### 6. Compatibility envelopes and session-cost coverage carry their unknown state

Spend endpoints retain their existing 200 compatibility envelopes when the
ledger source fails, but `source_error` is authoritative over every placeholder
total, map, or empty daily series in the response. Every summary or daily
consumer therefore renders unavailable/degraded evidence instead of applying a
normal zero fallback. This remains distinct from a successful known zero, which
has no source error.

For ingestion-event costs, a numeric value is a known-priced subtotal and an
`unpriced_session_count` carries the omitted session coverage. All-unpriced
sessions return a null subtotal; mixed known/unpriced sessions return the known
subtotal plus the count; explicitly zero-priced sessions return `0.0` with a
zero count. The list joins live session evidence whenever it exists, and lazy
write-back occurs only when every session cost is known (including known zero).
This additive contract is reused by request and window rollups so UI consumers
can label partial evidence rather than treating it as a total.

## Risks / Trade-offs

- [The priced subtotal can still be read as a global total] → Label it as
  excluding unpriced models wherever `unpriced_models` is non-empty, including
  the ceiling state.
- [The ledger omits invocations that yielded no token usage] → Spend is defined
  by consumed tokens; session counts and session doors remain diagnostic data,
  not money evidence.
- [A live deadman adds per-butler reads] → Bound it to the requested range,
  compare token totals only, and return degraded evidence rather than blocking
  the dashboard.
- [Existing date semantics are UTC] → Preserve the API's current UTC daily
  boundaries in this slice instead of silently introducing owner-timezone
  behavior.
- [Unpriced model IDs might be noisy] → Group by executed catalog model ID and
  include token counts/calls so operators can add the missing pricing entry.

## Migration Plan

1. Deploy explicit gpt-5.6 pricing classifications with the structured
   unknown-price result and its focused unit tests.
2. Deploy ledger aggregates and response fields together with the frontend
   rendering; an older frontend safely ignores additive fields, while an older
   backend is never asked to interpret a new request parameter.
3. Watch the divergence and unpriced notices in the Spend dashboard. Add
   pricing entries or investigate drift rather than treating either notice as
   an automatic data repair.
4. Roll back code/config together if needed. No data migration or backfill is
   required, so rollback has no persistent data reversal step.

## Open Questions

- None for this bounded slice. A future change can decide whether the
  divergence detector should be persisted and scheduled after operators have
  observed real thresholds.
