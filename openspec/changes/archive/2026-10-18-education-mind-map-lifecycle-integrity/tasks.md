## Tasks

Implementation was delivered by `bu-27dxl.14` (PR #4421, fc2621c11) and verified by `bu-27dxl.17`. This change is the normative gate it
was parked behind; the tasks below are the shape that work takes, not work
performed by this change. Every box is therefore unchecked until `bu-27dxl.14`
lands the corresponding piece.

### 1. Migration: widen the status CHECK to include `draft`

- [x] 1.1 Extend the `education.mind_maps` status CHECK constraint in a new migration
      under `roster/education/migrations/` to `('draft', 'active', 'completed',
      'abandoned')`. Leave the column default at `'active'` for now — changing it
      before the enforcement trigger exists would let a partially-migrated deploy
      create drafts that nothing knows how to advance.

Acceptance:
- A row with `status = 'draft'` can be inserted.
- Existing rows are untouched.

Evidence: `roster/education/migrations/006_mind_map_lifecycle.py:30` widens the CHECK to draft/active/completed/abandoned. Dev DB constraint reads `status = ANY (draft, active, completed, abandoned)`.

### 2. Migration: legacy transition for active zero-node maps

- [x] 2.1 In the same migration sequence, immediately after task 1: set every `active`
      mind map with zero `mind_map_nodes` rows to `abandoned`, recording the reason
      as a legacy-integrity transition. This resolves the live 34-day
      "Systems Programming - SPSC & CPU Pinning" phantom.

Acceptance:
- REQ "Legacy transition for existing active zero-node mind maps" scenarios pass.
- Re-running the migration is a no-op.
- Runs strictly before task 3.

Evidence: `006_mind_map_lifecycle.py:36-45` abandons zero-node active maps and writes the audit row `education.mind_map.legacy_empty_abandoned`; `tests/test_education_mind_map_staleness.py::test_lifecycle_migration_orders_backfill_audit_and_durable_guards`. Dev: the named phantom rows are abandoned (task 14.1).

### 3. Migration: enforcement triggers (both tables)

- [x] 3.1 Install a `BEFORE INSERT OR UPDATE` trigger on `education.mind_maps` that
      raises when the resulting row would be `active` with zero nodes. INSERT with
      `status = 'active'` is rejected unconditionally (nodes cannot precede their
      map row through the foreign key).
- [x] 3.2 Install an `AFTER DELETE` trigger on `education.mind_map_nodes` that raises
      when the deletion leaves an `active` mind map with zero nodes, and that takes
      no action when the mind map row itself no longer exists (cascade delete).
      Without this second trigger the invariant is only a transition guard: the
      node-delete direction re-creates the phantom with no enforcement point firing.

Acceptance:
- REQ "Mind map content invariant for active status" database scenarios pass,
  including the four node-deletion scenarios.
- Direct psql attempts to create the phantom fail from both directions.
- A test covers the cascade exemption explicitly; trigger/cascade ordering is
  not assumed.

Evidence: `006_mind_map_lifecycle.py:59-101` installs `mind_maps_active_content`, `mind_map_nodes_removal_lock` and `mind_map_nodes_active_content`; `tests/test_education_mind_map_staleness.py::test_content_guards_serialize_activation_removal_and_allow_cascade` covers the cascade exemption. All three triggers are present on the dev DB.

### 4. Migration: flip the column default to `draft`

- [x] 4.1 Flip the `education.mind_maps` status column default to `draft`. Only after
      task 3.

Evidence: `006_mind_map_lifecycle.py:104` flips the default after the triggers; dev DB column default is `'draft'::text`.

### 5. Mind map tool changes

- [x] 5.1 `roster/education/tools/`: `mind_map_create()` creates `draft` and refuses a
      caller-supplied status; `mind_map_update_status()` enforces the transition
      table and the node-count guard in-transaction; `mind_map_list()` accepts
      `draft`; node deletion of the last node of an `active` map is refused and
      leaves both the node and the map status unchanged.

Acceptance:
- `module-education-mind-map` scenarios pass.

Evidence: `roster/education/tools/mind_maps.py` (`mind_map_create` inserts draft at line 30, transition table at line 127, `mind_map_update_status` at line 134); `roster/education/tests/test_mind_maps.py::test_sql_inserts_draft_status` and `test_lifecycle_transition_matrix_preserves_refused_rows`.

### 6. Atomic teaching flow start

- [x] 6.1 `teaching_flow_start()` commits the mind map row and the `flow:{mind_map_id}`
      state entry in one transaction. `teaching_flow_advance()` rejects
      `planning` → `teaching` unless the mind map is `active`.

Acceptance:
- `module-education-teaching-flows` scenarios pass, including both rollback
  scenarios.

Evidence: `roster/education/tools/teaching_flows.py:262` commits the map and flow state in one `_transaction`; `teaching_flow_advance` rejects non-active maps at line 383; `roster/education/tests/test_teaching_flows.py::test_planning_requires_an_active_map_before_frontier_or_state_write` and `tests/test_education_mind_map_staleness.py::test_atomic_flow_and_populated_curriculum_on_migrated_runtime`.

### 7. curriculum_generate activation gate

- [x] 7.1 `curriculum_generate()` activates a populated draft exclusively through
      `mind_map_update_status()`, the single activation invariant owner, and refuses
      an empty graph with a curriculum-shaped error. Independently specified tool
      and API transitions remain available for populated eligible maps.

Acceptance:
- `module-education-curriculum` scenarios pass.

Evidence: `roster/education/tools/curriculum.py:510-511` and `:650` activate only through `mind_map_update_status` and refuse an empty graph; covered by `tests/test_education_mind_map_staleness.py::test_atomic_flow_and_populated_curriculum_on_migrated_runtime` and the `roster/education/tests/test_curriculum.py` changes in #4421.

### 8. Staleness sweep rebased on the mind map table

- [x] 8.1 The weekly `stale-flow-check` enumerates `education.mind_maps` rows with
      `status IN ('draft','active')`, adds the 24-hour zero-node draft rule, and
      handles maps with no flow state through `mind_map_update_status()`. Update the
      `stale-flow-cleanup` skill prose to match.

Acceptance:
- `module-education-teaching-flows` and `butler-education` scenarios pass,
  including the map-with-no-flow-state scenario.

Evidence: `roster/education/tools/teaching_flows.py:755-800` enumerates `education.mind_maps` with `status IN ('draft','active',...)`; `roster/education/butler.toml:57-66` prompt; `.agents/skills/stale-flow-cleanup/SKILL.md`; `tests/test_education_mind_map_staleness.py::test_empty_draft_map_uses_creation_timestamp` and `test_registered_job_handler_transitions_stale_maps`.

### 9. Preserve receipt-backed curriculum submission

- [x] 9.1 Keep curriculum request submission on the PR #3757 contract while
      implementing this change: persist `education.curriculum_requests` before
      detached work, use `uq_curriculum_requests_one_open` as the single active
      request guard, settle terminal outcomes idempotently, and retain the bounded
      abandoned-receipt sweep. Do not add a second admission or release mechanism.

Acceptance:
- The current `dashboard-education-api` submission and receipt-lifecycle scenarios
  remain unchanged and pass.
- A duplicate `accepted` or `running` receipt still returns 409.
- A stale non-terminal receipt is still settled to `failed` with
  `failure_reason = "timed_out"` before admission is retried.

Evidence: #4421 did not touch the receipt migrations or the receipt-backed submission path (`roster/education/api/router.py` 409 guard near line 1262 is unchanged by the lifecycle work); the change's diff stat lists no `curriculum_requests` migration.

### 10. Status endpoint 409 path

- [x] 10.1 `PUT /mind-maps/{id}/status` returns 409 with a reason for lifecycle
      rejections, 422 for `draft` as a request value.

Acceptance:
- `dashboard-education-api` status endpoint scenarios pass.

Evidence: `roster/education/api/router.py:663-673` returns 422 for an invalid or `draft` request status and 409 for `MindMapLifecycleError`; `roster/education/tests/test_api.py` additions in #4421.

### 11. Frontend: age-aware empty-curriculum copy

- [x] 11.1 `frontend/src/components/education/MindMapGraph.tsx`: delete the evergreen
      string, implement the tier table keyed on `status` and `created_at` age,
      surface the inline Abandon action in the stalled tier, and render the
      integrity-fault copy for an `active` zero-node map. Update
      `education-error-states.test.tsx`, which currently asserts the evergreen
      string is shown.

Acceptance:
- `dashboard-education-ui` "Age-aware empty-curriculum copy" scenarios pass.
- No occurrence of "still building" remains under `frontend/src/`.

Evidence: `frontend/src/components/education/MindMapGraph.tsx:32-34` age-aware copy; `frontend/src/components/education/education-error-states.test.tsx:128-142` now asserts the evergreen 'still building' text is absent, and no production source under `frontend/src/` contains it.

### 12. Frontend: per-map review fetch failure surfacing

- [x] 12.1 `frontend/src/components/education/ReviewTimeline.tsx` and the butler-detail
      Reviews tab: stop coercing a failed per-map query to `[]`, gate the empty
      state on all queries having succeeded, and render `SourceDegradedNote` naming
      the failing map.

Acceptance:
- `dashboard-education-ui` "Per-map review fetch failure surfacing" scenarios
  pass.

Evidence: `frontend/src/components/education/ReviewTimeline.tsx` and `ButlerEducationReviewsTab.tsx` stop coercing failures to `[]` and render `SourceDegradedNote`; tests `ReviewTimeline.test.tsx` and `ButlerEducationReviewsTab.test.tsx`.

### 13. Frontend: draft maps in the selector

- [x] 13.1 Selector lists `draft` alongside `active` with a "Setting up" badge;
      auto-selection prefers `active`; status badge and Abandon/Re-activate controls
      handle `draft` and the empty-abandoned case.

Acceptance:
- `dashboard-education-ui` layout and management-action scenarios pass.

Evidence: `frontend/src/pages/EducationPage.tsx:198` lists drafts with the 'Setting up' suffix; `frontend/src/pages/EducationPage.selection.test.tsx`.

### 14. Post-deploy verification

- [x] 14.1 Query the education schema and confirm no row satisfies
      `status = 'active' AND node_count = 0`, and that the named phantom is
      `abandoned`.

Evidence (bu-27dxl.17, 2026-10-18, dev namespace `butlers-dev` only, read-only
`SET default_transaction_read_only = on`, aggregate output only): the deployed
image `butlers-app:36f0e0eb080c` (commit 36f0e0eb0, 2026-10-15) descends from
fc2621c11 (#4421). `education.mind_maps` holds 5 rows, all `abandoned`; rows with
`status = 'active'` and zero nodes: 0; the two rows carrying the named phantom's
title are both `abandoned`; the three lifecycle triggers are present and the
status default is `draft`. Production was not queried.


### Implementation verification record (bu-27dxl.14)

The source implements tasks 1–13. The migration also adds `mind_maps.metadata`
(JSONB, non-null, default `{}`) and nullable integer `mind_map_nodes.sequence`,
which existing goal/curriculum writers and the governing planning scenarios
already require. It does not alter root-node planning or receipt migrations.
Software-only roster and frontend checks have run; real-Postgres migration,
role/concurrency/rollback checks, independent review and protected hosted CI are
still pending. Boxes remain unchecked until their complete corresponding
acceptance evidence is available. Task 14 remains separately mandatory and
UNRUN: this PR author has no live migration/deployment/private-content authority.

### Verification record (bu-27dxl.17)

PR #4421 merged as fc2621c11 with protected CI green (guards, check, frontend,
frontend-e2e, check-unit-1..5 and check-integration-1..5 all succeeded). Tasks
1 to 13 are ticked with file and test evidence above; task 14.1 is ticked with the
dev-database check recorded under it.
