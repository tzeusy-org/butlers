## 1. Behavior-executing regression

- [ ] 1.1 After exact owner adoption, add a real-Postgres API test using the migrated core, memory, and relationship chains; stub only the embedding engine and Chronicler MCP transport, then drive the existing entity note, interaction, and gift POST routes followed by the canonical activity GET and assert the same persisted IDs, kinds, timestamps, stores, and exact summaries.
- [ ] 1.2 In the same real-persistence lane, create an identity triple through the canonical Relationship writer, preserve a legacy narrative row, and prove both remain visible with source-qualified identities while retracted and superseded rows remain absent.
- [ ] 1.3 Exercise existing gift and loan successor semantics without editing their writers: the active successor appears once and the superseded row stays absent. Refresh and serialize this assertion with `bu-2jtfw.12` before touching its test ownership.

## 2. Bounded activity read implementation

- [ ] 2.1 Extend the activity DTO additively with normalized `summary` and nullable `store`, preserving explicit nulls and documenting `(src, store, id)` as the stable row identity.
- [ ] 2.2 Add separate active-row fetches for relationship-scoped narrative facts and current subject/object identity facts; normalize their exact content, store, kind, and timestamps, then merge them in memory without a SQL join, data rewrite, restore, or heuristic deduplication.
- [ ] 2.3 Preserve the existing participant-filtered Chronicler MCP call, owner gate, content-blind degraded discriminator, deterministic sort, post-merge pagination, and same-candidate-set daily bins and bins-only response.

## 3. Contract and boundary coverage

- [ ] 3.1 Update `tests/api/test_relationship_entities_activity.py` for both local sources, meaningful summaries, source-qualified collisions, ordering, totals, pagination, bins, healthy empty, and every Chronicler failure shape; remove the false single-store assertion.
- [ ] 3.2 Reconcile the duplicate mocked activity section in `roster/relationship/tests/test_entities_api.py` so one seam owns each invariant and no fake `entity_facts`-only row is accepted as POST-to-read proof.
- [ ] 3.3 Keep `roster/relationship/tests/test_chronicler_boundary.py` fail-closed for Chronicler SQL/imports and add a guard against joining the two Relationship stores; run `tests/contracts/test_relationship_facts_scope.py` for the narrative query and add a response-contract gate for the additive DTO fields.
- [ ] 3.4 Run the affected entity tab, SPO lifecycle, loan entity-id, merge/forget, and archive/delete roster/API tests plus `tests/ roster/ --collect-only -q -n 0`; use `make test-plan BASE=origin/main` from the dirty implementation worktree and honor any broader real-Postgres or contract escalation.

## 4. Verification and handoff

- [ ] 4.1 Run Ruff check and format verification for touched Python/tests, the source-specific OpenSpec trace checks, strict OpenSpec validation, overwrite and countable-task guards, and `make check-guards` without re-freezing any baseline.
- [ ] 4.2 Report the exact test delta and targeted commands, push the exact implementation head, obtain fresh independent semantic/privacy review with zero unresolved threads, and use terminal hosted CI plus the merge queue as broad evidence.
- [ ] 4.3 Keep frontend fixture, cache invalidation, transport, and direct-mutation corrections in `bu-bbwur` / PR 4057; do not duplicate them in the backend change.
