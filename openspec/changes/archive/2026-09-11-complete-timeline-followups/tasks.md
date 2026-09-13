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

- [x] 3.1 Implement identifier-only attention response, canonical failure predicates, exact source counts and five-row cap through the same read boundary.
- [x] 3.2 Wire disclosure, labelled 24h scope, counts, links, truncation and degraded/retry behavior.
- [x] 3.3 Extend real-Postgres API tests for >5 failures, terminal versus pending/success and changed delivery status including acknowledgment and retry claim before success; extend TimelinePage tests for collapse, links, empty/partial states. Target net Tests: +4 ~1 -0.

## 4. Keyboard vertical slice

Owner bead: `bu-ddo0n.3`.

- [x] 4.1 Register j/k through the existing list-triage shortcut machinery and register existing presets as palette-only useRegisterCommands entries, reuse native Enter and shell search, preserve r/n.
- [x] 4.2 Extend TimelinePage.a11y/TimelineLedger tests for real focus, clamp, group traversal, refresh identity, inputs/IME/modal suppression and one activation; reuse existing shell shortcut tests. Target net Tests: +3 ~2 -0. Actual Tests: +3 ~4 -0.

## 5. Combined reconciliation and landing

Owner bead: `bu-ddo0n.4`.

- [x] 5.1 Exercise a failed notification link, histogram historical selection, keyboard drawer entry/exit and back navigation in one combined browser flow; verify error honesty and reduced motion.
- [x] 5.2 Run planner-selected API/read-model and frontend scopes; collection if fixture topology changes; lint, knip, build, copy/shard/guard checks as applicable. Record actual test deltas rather than treating targets as a quota.
- [x] 5.3 Use exact-head hosted CI and protected queue for broad evidence. Reuse matching receipts rather than repeat broad local lanes.
- [x] 5.4 Archive only after completed behavior and approval; scan active requirement bodies for collisions, reconcile without unrelated losses, and close parent after archive and merged evidence.

## 6. Terminal reconciliation evidence

Reconciled on 2026-09-11 against merged main `fb88594d0d230cb57c633ab6bf005cecc6575dc6`.
This archive delivery extends the existing composed browser flow (`Tests: +0 ~1 -0`) and reuses
the remaining behavior evidence below.

### 6.1 Mandatory scenario map

- REQ-dashboard-visibility-001: `test_histogram_counts_beyond_head_page_and_interval_cursor_is_lossless`
  proves the >50-event aggregate, exclusive upper bound, and lossless tied-timestamp pagination;
  `test_histogram_and_list_share_error_butler_trace_and_boundary_predicates` proves list/count
  predicate parity. `test_timeline_intervals_reject_invalid_bounds` and
  `test_histogram_reports_exact_source_availability` cover validation and the complete, partial,
  unavailable, missing-notifications, and zero-source matrix. TimelinePage's `atomically
  materializes...` and `fails closed...` cases cover URL history/reload, invalid scope, and stale
  live-row suppression; its availability table includes the distinct `No matching event sources`
  state. `useTimelineLedger` covers filter resets and stale-result exclusion.
- REQ-dashboard-visibility-002: the real-Postgres
  `test_attention_counts_current_failures_caps_rows_and_excludes_incomplete_statuses` covers exact
  counts, the five-row cap, the 24-hour/current-status predicates, and acknowledgement plus retry
  claim before success. `test_timeline_attention_is_capped_content_blind_and_current_status_scoped`
  proves descending timestamp order and the content-blind projection;
  `test_attention_equal_timestamps_have_stable_cross_kind_and_id_order` repeats an equal-timestamp
  read and proves the stable kind/id tie-break.
  `test_exact_event_lookup_resolves_notification_beyond_timeline_head` plus TimelinePage's exact
  destination/off-page drawer cases cover inspection links without mutation. TimelinePage's
  collapse/truncation and availability cases cover accessible disclosure, healthy empty, partial,
  unavailable, stale evidence, and Retry.
- REQ-dashboard-visibility-003: TimelineLedger's three keyboard traversal cases cover visual-order
  real focus, first/last entry, clamping, grouping, no implicit pagination, identity preservation,
  nearest-survivor focus, native Enter once, Escape return, and editable/IME/modal/modifier
  suppression. TimelinePage's shortcut and saved-view cases cover `r`, conditional `n`, and the
  palette-only All/Errors/Notifications commands through the existing view path. The shell
  `use-keyboard-shortcuts` suite preserves slash and Ctrl/Cmd+K ownership and shortcut help.
- Cross-requirement seam: Playwright
  `first j survives composed Timeline URL and drawer focus transitions` runs under reduced motion;
  independently asserts the histogram partial source count, attention degradation, and list
  degradation presentations; and executes failed-notification navigation with interval clearing,
  density selection, first-`j` focus, native drawer entry/exit, focus restoration, and browser Back.

### 6.2 Pre-existing reader and negative-invariant audit

- The pre-existing `GET /api/timeline` readers are TimelinePage's head and cursor/load-more paths,
  DashboardPage's five-row recent-activity reader, and the shell capability warmup. Exact persisted
  event lookup is an independent read mode on the same route. Their owning client, hook, Dashboard,
  shell-capability, and API suites pass without interval parameters, preserving the additive
  unbounded contract.
- Session/error rows still fan out only through `query_timeline_sessions_fan_out`; notification and
  failed-delivery rows still use the single Switchboard `query_timeline_notifications_single`
  boundary. Histogram and attention add bounded readers beside those paths rather than replacing
  them. The real-Postgres parity tests cover both source families, Errors, butler, trace, interval,
  and exact-event resolution.
- Butler facets and saved views remain separate readers. Their degraded/retry UI and saved-view
  client suites pass; selected chart/bucket bounds are not added to persisted saved-view fields.
- Negative invariants are explicit: invalid URL state issues neither bounded nor silently broadened
  list/histogram reads; aggregate DTOs contain no event content; zero expected sources is not fleet
  all-clear; partial/unavailable data is never healthy empty; attention exposes identifiers only
  and never claims unresolved work or recovery; keyboard traversal does not own shell search or
  fetch another page. The combined browser flow uses independently degraded histogram, attention,
  and list responses, so one healthy source cannot mask another failed source.
- Instrument audit: mock router tests establish parameter/projection behavior, real PostgreSQL
  establishes SQL count/order/pagination semantics, DOM tests establish copy and focus, and
  Playwright establishes their composed browser lifecycle. No single instrument is credited with
  cross-layer coverage. No remaining mandatory-scenario blind spot was found.

### 6.3 Merge and hosted CI identity

| Delivery | Reviewed PR head | Main merge/queue SHA | Protected queue run |
| --- | --- | --- | --- |
| PR #4134 density/history | `ff18f4d84b41576e387641d4e6f18bef1c10ea26` | `2ddf5d7bf84080fc988c64b76b58cc0f11a028c0` | `34540165801` success |
| PR #4139 attention | `e50899e71126f13c9c55e4e78488eb77b29485d9` | `2c6d9eb9778320bd61284b726928b57fe31d7a4c` | `34550084474` success |
| PR #4140 keyboard | `aeb01889ebbc3ccbb15ee377b46e024fecdacbd0` | `48cbb002da2d1dcc207f38d1475b68fe604431a4` | `34555052712` success |
| PR #4141 ordering evidence | `e1dbccaabd6b9f7e9554756c53e60eba8022bd33` | `6c6d35268bc7c3eb129993f6bb80e1abaa02343a` | `34558695577` success |
| PR #4142 focus lifecycle | `ee6ee033252a6f220b6b6c439f4e229d0ec7391a` | `c04990cd011a7d66208e7254686f5f40c4e856c5` | `34560586797` success |
| PR #4143 zero-source evidence | `3684546e705d46c670b0cd965cd1ffe51db1ca7f` | `fb88594d0d230cb57c633ab6bf005cecc6575dc6` | `34563082341` success |

Every listed PR is merged. Its final PR-head checks contain no failure, cancellation, or pending
result; every protected merge-queue run succeeded on the exact main SHA shown. The owner-approved
current-status interpretation remains explicit in the proposal and requirement, so archiving does
not silently erase the rejected unresolved-work interpretation or turn it into delivered behavior.

### 6.4 Archive preflight

- Active-change scans found no second requirement named `Timeline minute density and historical
  interval selection`, `Timeline bounded recent failures strip`, or `Timeline keyboard row
  traversal and existing-view actions`.
- `openspec validate complete-timeline-followups --strict` passed.
- `make check-spec-overwrites` reported no unfrozen baseline losses. Its unrelated notice that an
  existing ratchet entry can be tightened was intentionally not applied in this archive.
- Review correction re-ran the two named attention-ordering nodes (`2 passed`), then ran
  `npm run build` followed by the corrected composed
  `tests/e2e/timeline-focus-lifecycle.spec.ts` flow (`1 passed`). The browser assertions observe
  the histogram partial source count, attention degradation, and list degradation separately.
