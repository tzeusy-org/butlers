## Research gate (S0)

Checked against the public Google Health API v4 reference:

- Data type ids `exercise`, `weight`, `body-fat` exist; list/reconcile are
  `GET /v4/users/*/dataTypes/*/dataPoints[:reconcile]`.
- `exercise` is an interval session type. Filter example from the reference:
  `exercise.interval.civil_start_time >= "YYYY-MM-DD"`.
- Scopes: exercise falls under `googlehealth.activity_and_fitness.readonly`;
  weight and body-fat under `googlehealth.health_metrics_and_measurements.readonly`.
  Both families are already in `GOOGLE_HEALTH_SCOPES`, so **no new scope is needed**.
- Exercise point fields used: `interval.{startTime,endTime}`, `exerciseType`,
  `displayName`, `metricsSummary.{caloriesKcal,distanceMillimeters,
  averageHeartRateBeatsPerMinute}`, `dataSource.recordingMethod`.
- Not documented in the public reference: `recordingMethod` enum values and
  reconcile deletion semantics. The connector treats `MANUAL` as owner-logged and
  everything else as device-detected, and makes no deletion claim.

## Contract

Envelope: `external_event_id = google_health:<user>:workout_session:<id>`,
`idempotency_key = google_health:<user>:workout:<id>`. Fact metadata:
`activity_type`, `duration_ms`, `end_time`, `session_id`, `detection`, optional
`calories`, `distance_m`, `average_heart_rate`. Chronicler `source_ref` is
`health.facts:workout_session:<idempotency_key>`.
