# Tasks — calendar-cross-source-duplicate-review

Backend core for bead `bu-tjo2m1`: expose the clusters the read-model collapses,
persist dedup rules + keep-separate overrides. No LLM, no provider write. The
full FE review panel is a discovered follow-up (spec-exempt pure-FE surface).

## 1. Persistence (migration core_144)

- [x] 1.1 Add `public.calendar_dedup_rules` (singleton `id = TRUE`:
  `match_strategy`, `noisy_threshold`) + `public.calendar_dedup_overrides`
  (one row per pinned `cluster_key`); grant CRUD to all butler runtime roles
- [x] 1.2 Read-model store (`calendar_workspace_v1`): `load_dedup_rules`,
  `update_dedup_rules`, `load_keep_separate_keys`, `set_keep_separate` over a
  single deterministic calendar-enabled pool; reads fail-open to defaults

## 2. Reusable cluster builder

- [x] 2.1 Refactor the two-pass dedup into `_dedup_workspace_rows(rows, strategy,
  keep_separate)` returning `(deduped_rows, clusters)`; `balanced` with no
  overrides is behavior-identical to the original two-pass dedup
- [x] 2.2 `exact` runs only the origin-ref pass; `aggressive` strips
  non-alphanumerics from titles before the title pass; keep-separate clusters are
  protected from collapse in this and later passes but still reported

## 3. Endpoints

- [x] 3.1 `GET /workspace/duplicates` — clusters for a range (kept +
  duplicates), filtered by `noisy_threshold`; fail-open `available=false`
- [x] 3.2 `PATCH /workspace/dedup-rules` — persist match-strategy + threshold;
  unknown strategy → 400/422; audit-logged
- [x] 3.3 `POST /workspace/duplicates/keep-separate` — pin/unpin a cluster;
  audit-logged
- [x] 3.4 Live workspace read loads + honors persisted rules + overrides

## 4. Tests

- [x] 4.1 Pure dedup: collapse + cluster exposure, `exact` skips title pass,
  keep-separate prevents collapse but still reports the cluster
- [x] 4.2 Endpoints: duplicates exposes cross-schema clusters, fail-open on
  read error, dedup-rules persists + rejects bad strategy, keep-separate
  toggles persist/unpin
- [x] 4.3 Store fail-open: defaults when no pool, empty set on query error

## 5. Follow-up (not in this change)

- [x] 5.1 Frontend duplicate-review panel (cluster list, keep-separate toggles,
  strategy/threshold control) over these contracts — pure-FE, spec-exempt
  (shipped: PR #2678)
