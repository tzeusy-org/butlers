## 1. Research gate
- [x] 1.1 Confirm exercise data type, filter field, and scope family (design.md)

## 2. Connector and ingest
- [x] 2.1 `workout` resource bundle and `build_workout_session_envelope`
- [x] 2.2 `workout_session` mapping and metadata extractor in `wellness_ingest`
- [x] 2.3 Chronicler confidence reflects `detection`

## 3. Verification
- [x] 3.1 Connector fixture, ingest, adapter, and seam-contract tests
- [ ] 3.2 Real-seam DB test in CI (not executable on the authoring host)

## 4. Deferred
- [ ] 4.1 Upstream edit/delete reconciliation
- [ ] 4.2 Weight/body-fat bundle with owner-dominance
- [ ] 4.3 "Last <activity>" read path
- [ ] 4.4 `workout_session` predicate_registry seed after `mem_013` merges
