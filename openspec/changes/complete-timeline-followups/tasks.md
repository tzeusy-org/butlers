## 1. Approval and semantic review

- [x] 1.1 Independently review the proposed artifact against baseline and active deltas. Explicitly verify Recent failures versus unresolved-work semantics and preservation of global slash search.
- [x] 1.2 Record owner approval of reviewed commit c64a0275cc357d4f13c7d60fbd9c8ad68899693b (2026-09-10); materialize the three approved slices and terminal reconciliation.

## 2. Density and historical seek vertical slice

Owner bead: `bu-ddo0n.1`.

- [x] 2.1 Add bounded interval arguments and SQL aggregation through timeline_v1; implement aggregate API/DTO without source-content fields.
- [x] 2.2 Wire hour controls, bucket selection, URL ownership, cache normalization and interval list fetching end to end.
- [x] 2.3 Extend tests/api/test_timeline_summary.py and add tests/integration/test_timeline_intervals.py with existing real-Postgres fixtures and canonical schema stand-ins: >50 events, boundaries, same-time pagination, source/type/trace parity, invalid bounds and partial/all-source failure and absent Switchboard pool with explicit availability arithmetic. Extend TimelinePage/Ledger tests for unloaded history, URL restoration after hour rollover with atomically materialized bounds and stale-result exclusion. Target net Tests: +6 ~3 -0; consolidate parameterized cases per invariant.

## 3. Recent failures vertical slice

Owner bead: `bu-ddo0n.2`.

- [ ] 3.1 Implement identifier-only attention response, canonical failure predicates, exact source counts and five-row cap through the same read boundary.
- [ ] 3.2 Wire disclosure, labelled 24h scope, counts, links, truncation and degraded/retry behavior.
- [ ] 3.3 Extend real-Postgres API tests for >5 failures, terminal versus pending/success and changed delivery status including acknowledgment and retry claim before success; extend TimelinePage tests for collapse, links, empty/partial states. Target net Tests: +4 ~1 -0.

## 4. Keyboard vertical slice

Owner bead: `bu-ddo0n.3`.

- [ ] 4.1 Register j/k through the existing list-triage shortcut machinery and register existing presets as palette-only useRegisterCommands entries, reuse native Enter and shell search, preserve r/n.
- [ ] 4.2 Extend TimelinePage.a11y/TimelineLedger tests for real focus, clamp, group traversal, refresh identity, inputs/IME/modal suppression and one activation; reuse existing shell shortcut tests. Target net Tests: +3 ~2 -0.

## 5. Combined reconciliation and landing

Owner bead: `bu-ddo0n.4`.

- [ ] 5.1 Exercise a failed notification link, histogram historical selection, keyboard drawer entry/exit and back navigation in one combined browser flow; verify error honesty and reduced motion.
- [ ] 5.2 Run planner-selected API/read-model and frontend scopes; collection if fixture topology changes; lint, knip, build, copy/shard/guard checks as applicable. Record actual test deltas rather than treating targets as a quota.
- [ ] 5.3 Use exact-head hosted CI and protected queue for broad evidence. Reuse matching receipts rather than repeat broad local lanes.
- [ ] 5.4 Archive only after completed behavior and approval; scan active requirement bodies for collisions, reconcile without unrelated losses, and close parent after archive and merged evidence.
