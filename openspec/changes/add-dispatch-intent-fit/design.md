## Context

Resolution today answers "is this entry allowed?" — enabled, verified, breaker closed,
quota headroom — and then ranks. Nothing asks whether the entry can do the job. The
gap is not theoretical: `api-haiku-cheap` is seeded at priority 30 (top of `cheap`)
and its adapter raises on any MCP tool wiring, which the spawner supplies for every
trigger source except `healing` and `qa`.

## Decisions

### Two layers of capability truth, not one

A capability is answered by layering a catalog row's `capabilities` envelope over the
`declared_capabilities` its runtime adapter class declares.

The alternative — one column, populated per row — would have required backfilling
every existing entry in the same migration to avoid bricking the fleet, and would have
put a fact the adapter already knows (`ApiAdapter` cannot accept tools) into operator
data where it can rot. With the baseline layer, an empty envelope (the default for
every existing row) excludes nobody and no backfill is needed, while an operator can
still contradict the baseline for a specific model.

### Support is three-valued

`unknown` is distinct from `unsupported`. An unregistered runtime type answers unknown
for everything and therefore fails closed above `observe` consequence; an adapter that
genuinely cannot provide a capability declares `false`, which excludes everywhere. The
asymmetry is deliberate: absence of proof is tolerated exactly where nothing outside
the system is waiting on the result, and nowhere else.

### Fit runs before tier selection, not just before ranking

Filtering only within the already-chosen tier would still let a tier that cannot serve
the intent win and then produce nothing. Fit is applied to every candidate in every
candidate tier first; the winning tier is the first one with a survivor.

### Preferred features do not re-rank

They are recorded on the receipt and ignored by the tie-break. Preferring
resume-capable models for interactive triggers would systematically shift traffic
toward one provider — a cost and quality trade-off that belongs to the owner. This
also gives the change a property worth stating plainly: an intent that requires
nothing selects exactly what the pre-existing resolver selects.

### Deadline and budget exclude only on evidence

A candidate with no latency history or no known price is never excluded. Excluding on
absence would make the fleet unable to try anything new and would disqualify models
that are free, local, or subscription-covered — the same doctrine
`compute_routing_score` already follows for its evidence gate.

### The receipt is built but not yet persisted

`resolve_dispatch` returns the full receipt and `resolve_model_with_effective_tier`
drops it, keeping its 6-tuple signature so the roughly twenty existing call sites and
test patches are unaffected. Persisting the receipt and exposing it as a session
dossier door is left for a follow-up: there is no session-dossier surface in the tree
today, so building the door is a separate, larger piece of work than deciding the
contract.

## Risks

- The deadline rule is dormant in practice today: only the `qa` and `healing` dispatch
  paths pass a `timeout_override`, and both derive intents that require nothing.
- Hard fit can now yield "no candidate" where resolution previously returned an
  unusable one. That routes to the caller's existing static fallback, and the receipt
  plus a warning log say why — a visible fallback rather than a session that fails at
  invoke time.
