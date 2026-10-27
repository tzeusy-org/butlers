## Tasks

### 1. One query-backed authority for the flag

- [x] 1.1 Add `prometheus_aggregates_available` to
      `src/butlers/api/routers/ingestion_pipeline.py`, resolving the flag
      through the existing 60-second TTL cache.

Acceptance:
- A warm cache entry answers without issuing a PromQL query.
- A cold entry issues the funnel queries and reports their outcome.

- [x] 1.2 `/cross-summary` in
      `src/butlers/api/routers/ingestion_connectors.py` reports the flag from
      that helper instead of from `PROMETHEUS_URL` being set.

Acceptance:
- `_get_prometheus_url` no longer exists in `ingestion_connectors.py`, and
  nothing there reads `_pipeline_cache` directly.
- A probe that raises logs and yields `aggregates_available: false`; the
  DB-sourced counts are still returned with HTTP 200.

- [x] 1.3 Tests assert the honest response for a configured-but-unreachable
      Prometheus and for an unreadable sample, and failed against the
      pre-change handler.

Acceptance:
- `uv run pytest tests/api/test_ingestion_pipeline.py -q` is green.

### 2. Spec

- [x] 2.1 `connector-state-aggregates` records the query-backed contract for
      `/cross-summary`.

Acceptance:
- `openspec validate query-backed-cross-summary-availability --strict` passes.
- `python3 scripts/check_countable_tasks.py` passes.
