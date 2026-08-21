## Why

The education butler can create a mind map that is `active` and empty, and
nothing in the system ever notices.

Live evidence from the dev database (education schema, probed 2026-07-25):

| title | status | nodes | created | age |
| --- | --- | --- | --- | --- |
| Systems Programming - SPSC & CPU Pinning | `active` | 0 | 2026-06-21 | 34 days |
| `__probe_not_used__` | `abandoned` | 0 | 2026-06-17 | — |
| Systems Programming - SPSC & CPU Pinning | `abandoned` | 30 | 2026-06-28 | — |
| Systems Programming: SPSC & CPU Pinning | `abandoned` | 0 | 2026-04-01 | — |

The first row is the phantom. It is the *only* `active` curriculum the owner
has, it has never held a single concept, and it is invisible to both cleanup
paths: the stale-flow sweep keys off teaching-flow state that was never
written for it, and the mind-map staleness sweep keys off the newest
`updated_at` across the map's nodes, which for a zero-node map is NULL. The
owner's Education page renders it with the evergreen line "This curriculum has
no concepts yet — the butler is still building it", which has now been true
for 34 days and false for 34 days.

The current specs do not merely fail to forbid this state — they **require**
it. `mind_map_create()` is specified to insert `status = 'active'` with no
nodes, and `teaching_flow_start()` is specified to insert an `active` map row
and then, as a separate write, the flow state.

The owner decided INVEST on 2026-07-25 with a binding acceptance addendum:

> after slice (1), it must be impossible to end up with a `status=active`
> zero-node map. The 34-day phantom must be resolved (repaired or
> transitioned out of `active`) as part of this work, not left as legacy data.

This change reconciles the specs with that decision. Where existing spec text
permits — or mandates — an active zero-node map or evergreen "still building"
copy, that text is **superseded and rewritten**, not layered over. A spec that
both permits and forbids the same state is worse than either.

## What Changes

### Modified Capabilities

- `module-education-mind-map`: mind maps are created `draft`, not `active`.
  Adds the lifecycle invariant and names its single enforcement point. Adds
  the one-time legacy transition for existing active zero-node maps. Extends
  the staleness sweep to reach draft maps and orphans.
- `module-education-curriculum`: `curriculum_generate()` becomes the sole path
  that activates a mind map, and it must refuse to activate an empty graph.
  The lifecycle's previously unrepresentable `creation` phase becomes the
  concrete `draft` status. This resolves a standing contradiction as a side
  effect rather than as separate work: the spec has always declared a
  pre-active `creation` state that the `status` CHECK constraint at
  `roster/education/migrations/001_education_tables.py:29-30` could not store,
  so every map jumped straight to `active`. Adding `draft` makes the declared
  lifecycle representable; no follow-up item is filed for it.
- `module-education-teaching-flows`: `teaching_flow_start()` creates the mind
  map row and the flow state in one transaction — both or neither. The
  staleness sweep enumerates `mind_maps` rows rather than flow-state keys, so
  a map whose flow state was never written is still reachable.
- `dashboard-education-api`: the status endpoint refuses to activate an empty
  map. The curriculum-request lock gains a deterministic release path, a
  bounded lease, and a per-acquisition request token, so LLM obedience is no
  longer the only thing standing between the owner and a permanent 409 — and a
  late release from a superseded acquisition cannot free somebody else's lock.
- `dashboard-education-ui`: the evergreen empty-state string is removed
  outright and replaced with age-aware copy keyed to the map's status and
  `created_at`. Per-map review fetch failures are surfaced instead of folding
  into the calm "No reviews scheduled" state.
- `butler-education`: the weekly stale-flow scheduled task and the
  `stale-flow-cleanup` skill are restated to cover stalled draft maps and
  flow-less orphans.

## In Scope

- The lifecycle invariant (`active` implies at least one node) and its
  enforcement point.
- The `draft` mind map status and the transitions into and out of it.
- The one-time legacy transition for existing `active` zero-node maps,
  including the named live phantom.
- Deterministic release, bounded lease, and token-scoped compare-and-delete
  for `pending_curriculum_request`.
- Age-aware empty-curriculum copy.
- Per-map review fetch-failure surfacing on both the `/education` Reviews tab
  and the education butler-detail Reviews tab.

## Out of Scope

- Implementation, migrations, and frontend code. Those are `bu-27dxl.14`.
- Any change to *what* the curriculum planner decomposes, how many nodes it
  produces, or how it sequences them.
- Diagnostic, mastery, spaced-repetition, and analytics behaviour. Those specs
  were checked and contain no text that permits an active zero-node map;
  `module-education-mastery`'s "Map summary for empty mind map" scenario
  remains correct because a zero-node map is still legal in `draft` and
  `abandoned`.
- Retiring the `pending_curriculum_request` KV lock in favour of a real queue.

## Impact

- Affected specs: `module-education-mind-map`,
  `module-education-curriculum`, `module-education-teaching-flows`,
  `dashboard-education-api`, `dashboard-education-ui`, `butler-education`.
- Affected code (for `bu-27dxl.14`, not this change):
  `roster/education/migrations/` (status CHECK widening, enforcement trigger,
  legacy backfill), `roster/education/tools/` (mind map and teaching flow
  helpers), `roster/education/api/router.py`,
  `frontend/src/components/education/MindMapGraph.tsx`,
  `frontend/src/components/education/ReviewTimeline.tsx`.
- Data migration required: yes. The legacy backfill must run **before** the
  enforcement trigger is installed, or existing rows stay illegal forever.
