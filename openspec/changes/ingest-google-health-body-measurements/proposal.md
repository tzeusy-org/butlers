## Why

The Google Health connector polls sleep, activity and daily vitals but no body measurements, so the
owner logs weight by hand even when a connected scale reports it (bu-q7vx1q.35, run-15 move #7
slice 2). The scope `health_metrics_and_measurements.readonly` is already requested and lists weight
and body fat, so no new consent is needed.

## What Changes

- Two connector bundles, `weight` and `body_fat`, one envelope per sample keyed on its physical time.
- `wellness_ingest` maps them to `measurement_weight` and `measurement_body_fat`.
- `owner_dominance` makes `measurement_latest`, `measurement_history` and `GET /measurements/latest`
  prefer an owner-logged value on the same owner-local day; device rows are kept.

## Capabilities

### Modified Capabilities

- `connector-google-health`: Per-Resource Polling Loops gains the body measurement resources.
- `butler-health`: owner-logged dominance (ADDED requirement).
