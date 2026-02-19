# Switchboard Observability and SLO Runbook

This runbook defines baseline SLI/SLO targets, alert thresholds, and error-budget policy
for Switchboard runtime operations.

## Metric and Trace Surfaces

- Metrics namespace: `butlers.switchboard.*`
- Root trace span for accepted ingress: `butlers.switchboard.message`
- Primary correlation key: `request_id` across logs and persisted lifecycle records

## Trace Structure: Accept-Then-Process Decoupling

After the accept-then-process split (Section 4.4), switchboard routing produces **two
sibling traces** linked by `request_id` rather than a single deeply-nested trace:

```
Trace A (switchboard, short-lived ~15ms):
  switchboard.message
    └── routing.llm_decision
          └── butler.llm_session (classification)
                └── route_to_butler
                      └── route.dispatch
                            └── butler.tool.route.execute  ← accept phase ends here
                                  attribute: request_id = "<uuid>"
                                  SpanLink → Trace B / route.process

Trace B (target butler, long-lived, e.g. health):
  route.process  ← fresh root span, no parent
    attribute: request_id = "<uuid>"   ← same as Trace A for correlation
    link → Trace A / butler.tool.route.execute
    └── butler.llm_session (target processing)
          └── ... (tool calls)
```

### Querying Across Traces

Do **not** join on parent span ID — the two traces have different trace IDs.
Use `request_id` as the join key:

```
# Find all spans for a given request across all traces:
SELECT * FROM spans WHERE attributes['request_id'] = '<uuid>'

# Find the accept span:
SELECT * FROM spans
WHERE name = 'butler.tool.route.execute'
  AND attributes['request_id'] = '<uuid>'

# Find the process span:
SELECT * FROM spans
WHERE name = 'route.process'
  AND attributes['request_id'] = '<uuid>'

# Follow the SpanLink from process back to accept:
# process_span.links[0].context.{trace_id, span_id} → accept span
```

### Accept Phase Duration

The switchboard's trace (Trace A) ends when `butler.tool.route.execute` completes.
This is expected to be **< 50ms** — it reflects only the accept phase (inbox persist).
The target butler's processing time is captured separately in Trace B.

If Trace A duration is consistently > 50ms, investigate:
- `route_inbox_insert` latency (DB write to target butler's database)
- Network latency to target butler's MCP endpoint

### Recovery Dispatch Traces

Crash-recovery dispatches (`route.process.recovery` spans) always start fresh root
spans with no SpanLink — the original accept-phase span may be from a previous daemon
run. Use `request_id` attribute for correlation with historical log records.

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
