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
- [ ] 3.3 Run backend lint/format/targeted tests and a full non-e2e pytest pass.

## 4. Deferred (reported as follow-up, not implemented here)

- [ ] 4.1 LLM one-sentence synthesis per cluster (slice 3), under the existing delivery budget.
- [ ] 4.2 Conflict-cluster routing to the Owner Decision Desk (slice 4), integrating with `bu-ckkpz`.
- [ ] 4.3 Hold-until-first-active briefings with hard fallback deadline + travel-day skip/defer (slice 5).
- [ ] 4.4 Wire `entity_id`/`event_window`/`event_date` into real producer metadata (finance, travel, health, relationship) so clustering activates on production data.
