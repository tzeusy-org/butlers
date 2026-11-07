## 0. Owner gate

- [x] 0.1 Closed owner decision `bu-t89d9m` option A (2026-10-20 14:36 UTC)
  authorizes implementation, the six-read selection, posture/write-group
  amendments and the same-change AGENTS Your Tools update; runtime activation
  remains separate.

## 1. Relationship module registration

- [ ] 1.1 Keep exactly `entity_resolve`, `entity_get`, `entity_neighbors`,
  `relationship_fact_evidence`, `relationship_predicate_coverage`, and
  `relationship_lookup` in the Relationship module's `entity` group.
- [ ] 1.2 Move `entity_update`, `relationship_record_coverage`, and `entity_set_posture` to the
  separately named `entity_write` group and do not activate that group.
- [ ] 1.3 Preserve `relationship_assert_fact` as an unconditional registration;
  do not assign it to either entity group or alter its approval behavior.

## 2. Activation and inventory

- [ ] 2.1 After owner approval, add only `entity` to
  `[modules.relationship].groups`; do not add `entity_write` and do not alter
  `[modules.memory].groups`.
- [ ] 2.2 Update the maintained Relationship group inventory/counts to reflect
  the six-read `entity` group and the three-tool `entity_write` group.
- [ ] 2.3 Keep `memory_entity_create` as the sole canonical runtime entity-create
  MCP name; do not register a bare `entity_create` alias.

## 3. Focused FastMCP contract tests

- [ ] 3.1 Add
  `roster/relationship/tests/test_assert_fact_registration.py::test_entity_groups_register_exact_membership`
  using a real FastMCP registry. Assert the exact six reads and
  `relationship_assert_fact` are present, while `entity_update`,
  `relationship_record_coverage`, `entity_set_posture`, and bare `entity_create` are absent.
- [ ] 3.2 Add
  `roster/relationship/tests/test_assert_fact_registration.py::test_omitting_entity_groups_keeps_unconditional_writer`.
  Register with both entity groups omitted and assert all nine grouped tools
  are absent while `relationship_assert_fact` remains registered.
- [ ] 3.3 Extend focused handler tests for unknown fact evidence, uncovered and
  unavailable predicate coverage, and a representative backing-store failure
  that must remain an MCP error rather than a successful empty/unknown result.

## 4. Verification and delivery boundaries

- [ ] 4.1 Run the exact focused Relationship FastMCP tests, then the
  repository test planner and every scope it requires for the implementation
  diff.
- [ ] 4.2 Run strict OpenSpec validation, the spec-overwrite guard, repository
  guards, and terminal hosted CI on the exact implementation head.
- [ ] 4.3 Report `Tests: +a ~b -c` for the implementation and archive/sync the
  approved delta only with the completed implementation delivery.
- [ ] 4.4 Do not restart, deploy, merge, or queue without the distinct authority
  required for each effect.
