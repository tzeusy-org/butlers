## 1. Correlated-candidate clustering (slice 1)

- [x] 1.1 `_candidate_entity_key` / `_candidate_time_window` metadata extraction helpers, fail-open on malformed data.
- [x] 1.2 `_cluster_candidates` union-find grouping by shared entity or overlapping time window, transitive.
- [x] 1.3 `_format_digest` renders multi-candidate groups as a labeled `Correlated (N):` sub-list; singleton groups format identically to before this change.
- [x] 1.4 Unit tests: entity grouping, time-window overlap, non-overlap, `event_date` normalization, transitive chains, no-correlation-data regression, malformed-metadata fail-open, determinism.

## 2. Presence-aware context-bus suppression (slice 2)

- [x] 2.1 Broker-local `get_suppressing_context_signal(pool, *, now=None)` replacing the shared-helper import; extends signal set to `{dnd, meeting, sleeping, traveling}` with per-signal max-hold TTL.
- [x] 2.2 `delivery_cycle` passes `now=now` through; suppressed ledger row gains `metadata={"held_by": ...}`.
- [x] 2.3 Unit tests (no Docker): per-signal suppression, max-hold-TTL expiry per signal, precedence (dnd > meeting > sleeping > traveling), fail-open on pool=None/context-bus error, `now` default.
- [x] 2.4 Integration tests (Docker): meeting/traveling suppress `delivery_cycle`, urgent bypass still works, `held_by` ledger metadata asserted via mocked `record_attention_event`.

## 3. Contract and verification

- [x] 3.1 Add the `proactive-insight-engine` spec delta (context-bus gating requirement broadened; clustering + `held_by` telemetry requirements added).
- [x] 3.2 Run `openspec validate --strict` on the changed spec.
- [x] 3.3 Run backend lint/format/targeted tests and a full non-e2e pytest pass.

## 4. Deferred (bu-iq8as follow-up)

- [x] 4.1 LLM one-sentence synthesis per cluster (slice 3), under the existing delivery budget. `_synthesize_cluster_sentence()` in `broker.py`; direct-API runtime lane only, fails open, no new budget knob.
- [ ] 4.2 Conflict-cluster routing to the Owner Decision Desk (slice 4), integrating with `bu-ckkpz`. **Still deferred** — see bu-iq8as's report: the Decision Desk convention/dashboard/cron (`bu-ckkpz.1/.2/.4`) is landed, but no runtime write path exists anywhere in this codebase for application code to file a decision bead programmatically (`bu-ckkpz.3`, the attention-ledger routing slice, remains `blocked`), and inventing one un-reviewed inside this bead was judged out of scope. Needs its own scoped design.
- [x] 4.3 Hold-until-first-active briefings with hard fallback deadline + travel-day skip/defer (slice 5). `daily_hold_mode` in `delivery_cycle()`; windowed cron `15,45 6-11 * * *` replaces the fixed `0 8 * * *` slot.
- [x] 4.4 Wire `entity_id`/`event_window`/`event_date` into real producer metadata so clustering activates on production data. **Partial**: health's `measurement_door.since/until` now also emits `event_window` (the recommended first target). Finance/travel/relationship producers are still unwired — follow-up.
