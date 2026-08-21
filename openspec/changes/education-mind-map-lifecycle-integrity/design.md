## Context

Four defects, one root cause. The mind map data model has three statuses —
`active`, `completed`, `abandoned` — with `DEFAULT 'active'`
(`roster/education/migrations/001_education_tables.py:29-30`). There is no way
to represent a map that exists but is not yet a curriculum. So every creation
path is forced to lie: it marks the map `active` at the instant it has the
least content it will ever have.

`module-education-curriculum` already describes a lifecycle that begins at
`creation` and says `curriculum_generate()` "transitions the mind map to
`active`". That transition has always been a no-op, because the row was
already `active` from birth. The curriculum spec describes a state the schema
cannot store.

## Goals / Non-Goals

Goals:

- Make `status = 'active'` mean "this map has concepts in it" — enforced, not
  documented.
- Give the four `bu-27dxl.14` slices a normative home before code exists.
- Resolve the live phantom rather than grandfathering it.

Non-Goals:

- Reworking the curriculum planner, the diagnostic phase, or SM-2.
- Replacing the KV curriculum-request lock with a durable queue.

## Decisions

### Decision 1: add a `draft` status rather than making creation take nodes

The alternative was to make `mind_map_create()` accept the full node set so a
map is never nodeless. That fails against how the planner actually works: an
ephemeral LLM session emits `mind_map_node_create()` calls one at a time over
minutes, and the diagnostic phase runs *before* planning, so a map must exist
and be addressable long before any concept does.

`draft` is the state the curriculum spec already describes as `creation`. It
is named as a noun because it renders in a status badge next to `active`,
`completed`, and `abandoned`.

Cost: a CHECK-constraint widening and a fourth badge in the UI. Benefit: the
invariant becomes expressible, and the previously fictional "transitions to
active" step becomes a real event with a real precondition.

### Decision 2: the enforcement point is `mind_map_update_status()`, backed by a trigger

Application-level checks alone would leave the phantom reachable from a
migration, a psql session, or the next code path someone writes. A plain
`CHECK` constraint cannot express it either — "has at least one node" is a
cross-table predicate.

So the invariant is enforced twice, deliberately:

1. `mind_map_update_status()` counts `mind_map_nodes` for the map inside the
   same transaction as the status write and rejects a transition to `active`
   at zero.
2. A `BEFORE INSERT OR UPDATE` trigger on `education.mind_maps` enforces the
   same predicate at the database. Because `mind_map_nodes.mind_map_id` is a
   foreign key to `mind_maps.id`, nodes cannot exist before the map row — so
   an INSERT with `status = 'active'` is *necessarily* zero-node and is
   rejected unconditionally. Creation is always `draft`, with no special case.

The application check exists so callers get a clear error; the trigger exists
so nothing can route around the application check.

### Decision 3: atomic creation is one database transaction, not a compensating write

Teaching-flow state lives in the butler `state` table
(`src/butlers/core/state.py:65`), the same PostgreSQL pool as
`education.mind_maps`. So "create the map and attach the flow" is a single
transaction, not a saga with a cleanup path. If the flow-state write fails,
the map row was never committed and there is nothing to clean up. This is the
whole of slice 1: the phantom exists because these were two commits.

### Decision 4: legacy phantoms are abandoned, not repaired

The owner allowed either. Repair would mean re-running an LLM decomposition
against a request the owner made 34 days ago and never followed up on, and in
the live case the phantom duplicates the title of an existing 30-node
`abandoned` map — the concepts already exist, in a map the owner walked away
from. Abandonment is the honest record.

Ordering matters: the backfill must run before the enforcement trigger is
installed. Installing the trigger first does not fix existing rows (triggers
fire on write, not on rest), and would then reject the backfill's own updates
if written carelessly.

### Decision 5: the curriculum-request lock releases from the API layer, not the prompt

Today the only release is step 4 of a prose prompt
(`roster/education/api/router.py:659-670`). Everything about that is
conditional on a language model following an instruction after having already
done the interesting part of its job. The `trigger` core tool
(`src/butlers/core_tools/_infra.py:148`) awaits the spawner and returns
`success`/`error`, so the API layer can release the lock when the session
terminates, whatever the outcome — an unconditional release, not a
compensating one.

A bounded lease is added on top for the case the API layer itself dies (daemon
restart mid-session): an expired lock is reclaimed by the next request rather
than answered with 409. The session-side `state_delete` survives as an
idempotent early release, but it is explicitly not a release *path* the system
depends on.

### Decision 6: draft maps stay visible in the UI

A `draft` map that the mind map selector filtered out would be a phantom
again, in a new status — invisible, un-abandonable, quietly rotting. So the
selector lists draft maps alongside active ones. The Reviews fan-out stays
active-only, because a draft map has no nodes and therefore no reviews.

## Risks / Trade-offs

- **A fourth status touches more surface than a fix strictly needs.** Mitigated
  by keeping `draft` out of the write API: the dashboard cannot put a map into
  `draft`, only the creation path can.
- **A draft map that stalls is still bad, just visible.** The weekly sweep
  bounds how long it survives, and the age-aware copy tells the owner the
  truth in the meantime — at 24 hours the page says "stalled", not "still
  building". Detection latency in the sweep is bounded by its weekly cadence;
  owner-visible honesty is not, because the copy is computed at render time
  from `created_at`.
- **Surfacing per-map fetch failures makes the Reviews tab noisier.** That is
  the intended trade: `docs/api_and_protocols/response-conventions.md:78-81`
  requires that an all-clear renderer be gated on the degraded flag rather
  than suppressing it, and a review the owner never sees is worse than a
  banner they do.

## Migration Plan

Owned by `bu-27dxl.14`; specified here so the ordering constraint is
normative:

1. Widen the `mind_maps.status` CHECK to include `draft`. Do not change the
   column default yet.
2. Backfill: every `active` map with zero nodes becomes `abandoned`, with the
   reason recorded. This resolves the live 34-day phantom.
3. Install the enforcement trigger.
4. Change the column default from `'active'` to `'draft'`.

Steps 2 and 3 must not be reordered.

## Open Questions

None. The owner decision of 2026-07-25 settles invest-vs-cut and supplies the
acceptance invariant; the remaining choices above are engineering ones taken
inside it.
