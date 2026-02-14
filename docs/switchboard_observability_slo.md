# Switchboard Observability and SLO Runbook

This runbook defines baseline SLI/SLO targets, alert thresholds, and error-budget policy
for Switchboard runtime operations.

## Metric and Trace Surfaces

- Metrics namespace: `butlers.switchboard.*`
- Root trace span for accepted ingress: `butlers.switchboard.message`
- Primary correlation key: `request_id` across logs and persisted lifecycle records

## SLI Definitions

1. Ingress acceptance latency (`butlers.switchboard.ingress_accept_latency_ms`)
- Definition: time from accepted ingress to canonical normalized request context.
- Measurement: P95 latency over 5-minute windows.

2. End-to-end fanout completion latency (`butlers.switchboard.end_to_end_latency_ms`)
- Definition: time from accepted ingress to terminal completion (`parsed` or `errored`).
- Measurement: P95 latency over 5-minute windows.

3. Route success rate (`butlers.switchboard.subroute_result`)
- Definition: successful subroutes / total subroutes.
- Measurement: rolling 30-minute ratio grouped by destination butler.

4. Interactive terminal-state latency
- Definition: `accepted` -> terminal lifecycle transition (`parsed` / `errored`).
- Measurement: P95 over 15-minute windows using lifecycle transition timing.

## Baseline SLO Targets

- Ingress acceptance latency (P95): <= 250 ms
- End-to-end completion latency (P95): <= 3000 ms
- Route success rate (30m): >= 99.0%
- Interactive terminal-state latency (P95): <= 5000 ms

## Alert Thresholds

- Warning thresholds:
- ingress P95 > 200 ms for 10 minutes
- end-to-end P95 > 2500 ms for 10 minutes
- route success rate < 99.5% for 15 minutes

- Critical thresholds:
- ingress P95 > 250 ms for 10 minutes
- end-to-end P95 > 3000 ms for 10 minutes
- route success rate < 99.0% for 15 minutes
- error class `internal_error` > 2% of subroutes for 10 minutes

## Error-Budget Policy

- Monthly availability objective for route success: 99.0%
- Monthly error budget: 1.0% failed subroutes

Burn-rate handling:

1. Fast burn (>= 2x budget in 1h)
- Page on-call.
- Auto-tighten admission:
- increase overload rejection sensitivity
- disable optional fanout branches where policy permits

2. Sustained burn (>= 1x budget in 6h)
- Create incident ticket.
- Increase fallback-to-general preference for ambiguous routes.
- Pause non-critical switchboard config rollouts.

3. Budget exhausted (>= 100% monthly budget)
- Freeze risky routing-policy changes.
- Require incident review before re-enabling relaxed routing behavior.

## Operational Notes

- Metrics must not include high-cardinality values (`request_id`, raw message text, full sender
  identity, message IDs, or emails).
- Use bounded labels only (`source`, `destination_butler`, `outcome`, `lifecycle_state`,
  `error_class`, `policy_tier`, `fanout_mode`, `model_family`, `prompt_version`,
  `schema_version`).
- For request reconstruction, pivot from `request_id` in pipeline logs to persisted
  `message_inbox.classification` / `message_inbox.routing_results`.
