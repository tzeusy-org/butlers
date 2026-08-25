## Why

`restore-ingestion-console-spec-coverage` wrote `connector-state-aggregates`
from the shipped code, which is why it had to record a scenario named
"Unparseable scalar reads as zero without lowering the flag": the endpoint had
a real degraded path (`_degraded_response` -> HTTP 200, zeros,
`aggregates_available: false`) and two ways around it. An unparseable PromQL
scalar resolved to `0` in `_extract_scalar`, and an unusable sparkline matrix
was replaced by `_build_spark24h` spreading the ingested total evenly across 24
buckets. Both left `aggregates_available: true`.

That is the defect this repo keeps finding on one axis: a value published with
an authority the code was never positioned to give. On the wire, a zero meaning
"Prometheus said zero" and a zero meaning "we could not read Prometheus" were
the same zero, and the dashboard has no way to tell them apart — every consumer
of the flag (`IngestionFiltersPage`, `FiltersPipeline`, `gate-state`,
`IngestionVerdictOpeners`) already renders the honest case correctly and was
simply never reached.

Auditing the rest of the handler found the same shape in three more places that
the original spec did not name: a Prometheus error on the routed breakdown left
`routed_by_butler` empty and `routed_pct` at `0.0`, an error on `rate1h` left
`0.0`, and an error on `filtered24h` left `0` — each with the flag still true.
A single unreadable per-butler series was also dropped from the breakdown,
understating `routed_pct` without saying so.

bu-0m31b closes all of them, so the requirement no longer describes a known
honesty gap: it describes a contract.

## What Changes

### Modified Capabilities

- `connector-state-aggregates`: `Degraded-mode response shape` now requires
  that any value the handler could not observe lowers `aggregates_available`
  rather than resolving to a substitute. The recorded honesty gap is replaced
  by the corrected contract, plus the one case that legitimately keeps the flag
  true — an empty PromQL result set, which is Prometheus answering "no series"
  and therefore a real observation of zero.

### Code

- `src/butlers/api/routers/ingestion_pipeline.py`: `_extract_scalar` returns
  `None` for a present-but-unreadable value instead of `0.0`; a new `_finite`
  rejects `NaN`/`Inf`, which `float()` would otherwise accept as a measurement.
  `_query_spark24h_buckets` distinguishes an empty matrix (zeros) from an
  unreadable one (degrade). `_build_spark24h` and `_extract_float` are deleted:
  the uniform fill was the defect, and the alias had one caller.
  The routed, `rate1h`, and `filtered24h` paths now degrade on error like
  `ingested`/`filtered`/`errored` already did.

## Out of Scope

- `GET /api/ingestion/connectors/cross-summary` derives its own
  `aggregates_available` from `PROMETHEUS_URL` being set whenever the pipeline
  cache is cold (`ingestion_connectors.py`), which claims availability without
  having queried anything. Same family, different endpoint and a different
  requirement; filed for its own change rather than widened into this one.

## Verification

- `tests/api/test_ingestion_pipeline.py` covers both named failure modes and
  the three found alongside them; each assertion fails against the pre-change
  handler.
- `python3 scripts/check_spec_overwrites.py` reports one intended loss: the
  honesty-gap scenario this change exists to remove, frozen by hand in
  `scripts/spec-overwrite-baseline.json`.
