## Tasks

### 1. Honest degradation in the pipeline handler

- [x] 1.1 Unreadable scalars and unusable sparkline matrices route to
      `_degraded_response` instead of substituting `0` / a uniform fill, in
      `src/butlers/api/routers/ingestion_pipeline.py`.

Acceptance:
- `_extract_scalar` returns `None` for a present-but-unparseable or non-finite
  value and `0.0` only for an empty result set.
- `_query_spark24h_buckets` returns `None` for a query error, an unexpected
  result shape, a series with no points, or an unparseable bucket; `[0] * 24`
  for an empty matrix.
- `_build_spark24h` no longer exists.

- [x] 1.2 The routed breakdown, `rate1h`, and `filtered24h` degrade on a
      Prometheus error like the funnel counters already did.

Acceptance:
- A routed-query error yields the degraded envelope, not `routed_pct: 0.0`.
- One unreadable per-butler series degrades rather than silently shrinking the
  breakdown.

- [x] 1.3 Tests assert the honest response for every failure mode above, and
      failed against the pre-change handler.

Acceptance:
- `uv run pytest tests/api/test_ingestion_pipeline.py -q` is green.

### 2. Spec

- [x] 2.1 `connector-state-aggregates` / `Degraded-mode response shape` records
      the corrected contract instead of the honesty gap.

Acceptance:
- `python3 scripts/check_spec_overwrites.py` passes, with the one intended
  scenario loss frozen by hand (never `--update-baseline`).
- `python3 scripts/check_countable_tasks.py` passes.
