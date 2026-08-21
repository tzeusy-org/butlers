## Tasks

Implementation is owned by `bu-27dxl.14`. This change is the normative gate it
was parked behind; the tasks below are the shape that work takes, not work
performed by this change.

### 1. Migration: widen the status CHECK to include `draft`

Extend the `education.mind_maps` status CHECK constraint in a new migration
under `roster/education/migrations/` to `('draft', 'active', 'completed',
'abandoned')`. Leave the column default at `'active'` for now — changing it
before the enforcement trigger exists would let a partially-migrated deploy
create drafts that nothing knows how to advance.

Acceptance:
- A row with `status = 'draft'` can be inserted.
- Existing rows are untouched.

### 2. Migration: legacy transition for active zero-node maps

In the same migration sequence, immediately after task 1: set every `active`
mind map with zero `mind_map_nodes` rows to `abandoned`, recording the reason
as a legacy-integrity transition. This resolves the live 34-day
"Systems Programming - SPSC & CPU Pinning" phantom.

Acceptance:
- REQ "Legacy transition for existing active zero-node mind maps" scenarios pass.
- Re-running the migration is a no-op.
- Runs strictly before task 3.

### 3. Migration: enforcement trigger

Install a `BEFORE INSERT OR UPDATE` trigger on `education.mind_maps` that
raises when the resulting row would be `active` with zero nodes. INSERT with
`status = 'active'` is rejected unconditionally (nodes cannot precede their
map row through the foreign key).

Acceptance:
- REQ "Mind map content invariant for active status" database scenarios pass.
- Direct psql attempts to create the phantom fail.

### 4. Migration: flip the column default to `draft`

Only after task 3.

### 5. Mind map tool changes

`roster/education/tools/`: `mind_map_create()` creates `draft` and refuses a
caller-supplied status; `mind_map_update_status()` enforces the transition
table and the node-count guard in-transaction; `mind_map_list()` accepts
`draft`; node deletion of the last node of an `active` map transitions it out
of `active`.

Acceptance:
- `module-education-mind-map` scenarios pass.

### 6. Atomic teaching flow start

`teaching_flow_start()` commits the mind map row and the `flow:{mind_map_id}`
state entry in one transaction. `teaching_flow_advance()` rejects
`planning` → `teaching` unless the mind map is `active`.

Acceptance:
- `module-education-teaching-flows` scenarios pass, including both rollback
  scenarios.

### 7. curriculum_generate activation gate

`curriculum_generate()` becomes the sole activation path and refuses an empty
graph with a curriculum-shaped error.

Acceptance:
- `module-education-curriculum` scenarios pass.

### 8. Staleness sweep rebased on the mind map table

The weekly `stale-flow-check` enumerates `education.mind_maps` rows with
`status IN ('draft','active')`, adds the 24-hour zero-node draft rule, and
handles maps with no flow state through `mind_map_update_status()`. Update the
`stale-flow-cleanup` skill prose to match.

Acceptance:
- `module-education-teaching-flows` and `butler-education` scenarios pass,
  including the map-with-no-flow-state scenario.

### 9. Curriculum-request lock: lease and deterministic release

`roster/education/api/router.py`: add `lease_expires_at` (15 minutes) to the
lock payload; 409 only on a live lease; release the lock from the API layer
when the triggered session terminates, whatever the outcome; demote the
prompt's `state_delete` step to an optional idempotent early release.

Acceptance:
- `dashboard-education-api` curriculum-request scenarios pass, including the
  session-ignores-the-instruction and daemon-restart scenarios.

### 10. Status endpoint 409 path

`PUT /mind-maps/{id}/status` returns 409 with a reason for lifecycle
rejections, 422 for `draft` as a request value.

Acceptance:
- `dashboard-education-api` status endpoint scenarios pass.

### 11. Frontend: age-aware empty-curriculum copy

`frontend/src/components/education/MindMapGraph.tsx`: delete the evergreen
string, implement the tier table keyed on `status` and `created_at` age,
surface the inline Abandon action in the stalled tier, and render the
integrity-fault copy for an `active` zero-node map. Update
`education-error-states.test.tsx`, which currently asserts the evergreen
string is shown.

Acceptance:
- `dashboard-education-ui` "Age-aware empty-curriculum copy" scenarios pass.
- No occurrence of "still building" remains under `frontend/src/`.

### 12. Frontend: per-map review fetch failure surfacing

`frontend/src/components/education/ReviewTimeline.tsx` and the butler-detail
Reviews tab: stop coercing a failed per-map query to `[]`, gate the empty
state on all queries having succeeded, and render `SourceDegradedNote` naming
the failing map.

Acceptance:
- `dashboard-education-ui` "Per-map review fetch failure surfacing" scenarios
  pass.

### 13. Frontend: draft maps in the selector

Selector lists `draft` alongside `active` with a "Setting up" badge;
auto-selection prefers `active`; status badge and Abandon/Re-activate controls
handle `draft` and the empty-abandoned case.

Acceptance:
- `dashboard-education-ui` layout and management-action scenarios pass.

### 14. Post-deploy verification

Query the education schema and confirm no row satisfies
`status = 'active' AND node_count = 0`, and that the named phantom is
`abandoned`.
