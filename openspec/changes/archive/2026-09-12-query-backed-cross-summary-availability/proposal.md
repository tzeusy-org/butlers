## Why

`GET /api/ingestion/connectors/cross-summary` published
`aggregates_available` from `_get_prometheus_url() is not None` whenever the
shared pipeline cache was cold. Configuration is not observation: a Prometheus
that was down, unreachable, or answering with unreadable samples produced
exactly the same `true` as one that answered, and the handler had never asked
it anything.

This is the sibling `honest-ingestion-aggregate-availability` (bu-0m31b)
deliberately left in its Out of Scope: same family — a value published with an
authority the code was never positioned to give — but a different endpoint and
a different fix. `/pipeline/stats` could fail-fast on seven real query results;
`/cross-summary` was answering from a cold cache with no query result to
inspect at all.

Every number `/cross-summary` returns is DB-sourced. `aggregates_available` is
the one field that is not: it is a claim about the Prometheus funnel
aggregates the console renders elsewhere. Resolving it through the same cached
fetch that produces those aggregates makes the claim exactly as strong as the
data it describes, and makes it impossible for the two endpoints to disagree.

## What Changes

### Modified Capabilities

- `connector-state-aggregates`: adds `Cross-summary aggregate availability is
  query-backed` — the flag is true only when Prometheus answered, false for a
  configured-but-unreachable backend, and never derived from `PROMETHEUS_URL`
  alone. The DB-sourced fleet counts stay valid and are still served when the
  flag is false.

### Code

- `src/butlers/api/routers/ingestion_pipeline.py`: new
  `prometheus_aggregates_available(window="24h")`, the single authority for the
  flag, resolving through the existing 60-second TTL cache — a warm entry costs
  nothing, a cold one issues the real queries.
- `src/butlers/api/routers/ingestion_connectors.py`: `/cross-summary` calls it
  instead of reaching into `_pipeline_cache` and falling back to a configured
  URL. The now-unused local `_get_prometheus_url` is deleted, and the
  `except Exception: pass` around the cache peek becomes a logged best-effort
  guard that degrades the flag rather than the response.

## Impact

`/cross-summary` may now issue one funnel fetch when the shared cache is cold,
where it previously issued none. The cost is bounded by the same 60-second TTL
and shared with `GET /api/ingestion/pipeline`, which the ingestion console
polls alongside it; a Prometheus that is down fails on the first query rather
than running all seven.

## Out of Scope

- `ConnectorCrossSummaryResponse` in `frontend/src/api/types.ts` is missing the
  `connectors_unclassified` field the endpoint returns. Pre-existing drift,
  unrelated to availability honesty; filed separately.

## Verification

- `tests/api/test_ingestion_pipeline.py` covers the configured-but-unreachable
  case, the unreadable-sample case, a raising probe, and a warm cache served
  without re-querying. The first two fail against the pre-change handler.
