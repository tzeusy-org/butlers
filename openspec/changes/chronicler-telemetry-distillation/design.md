# Design — Telemetry Distillation Layer

This file is a pointer, not a duplicate. The full grounded design — code
citations, per-source gap analysis (Home Assistant domain-retention gap,
OwnTracks place-clustering gap, the universal rollup/anomaly gap), the
adapter/rollup architecture, doctrine-compliance mapping, risks, and the
sequenced implementation-bead list — lives at:

`docs/plans/2026-07-06-telemetry-distillation-design.md`

Summary of the load-bearing decisions (see the doc for justification):

1. **Reuse, don't reinvent projection infra.** Both new adapters
   (`home_assistant.sensor_activity`, `owntracks.place_cluster`) are plain
   `ProjectionAdapter` subclasses — same contract as every adapter in
   `src/butlers/chronicler/adapters/` today. No new adapter framework.
2. **The daily-rollup materializer is the one new piece of infrastructure.**
   It must call the exact same `aggregations.lane_for_activity`/
   `union_seconds` functions the live `aggregate/by-category` endpoint uses —
   never a parallel counting implementation — so the rollup and the live
   endpoint can never diverge (the class of bug bu-whhll.1 already fixed once
   for the KPI endpoint).
3. **Corroboration gates promotion to `layer=activity`,** reusing
   `reconciliation.py`'s existing merge seam. Raw HA sensor pings are
   `layer=evidence` until something independent corroborates them.
4. **Classify before flagging.** Every anomaly rule consults
   `source_adapter_state` before emitting a behavioral flag, so a known
   feeder outage produces `feeder_dark`, not a fabricated "no sleep" or "no
   presence" behavioral claim — the same distinction the fleet-wide
   degraded-source convention already requires of API fan-out endpoints,
   applied here to batch anomaly detection.
5. **LLM stays bounded to one call per local day**, over already-reduced
   rollup output, strictly additive/optional — never per raw event, per RFC
   0014 §D5.
6. **A genuinely new adapter-risk class**: this is the first time a
   Chronicler adapter reads a table with a rolling TTL
   (`connectors.filtered_events`, 12-month default retention) rather than a
   TTL-free connector table or its own schema. The HA sensor-activity
   adapter's implementation bead must include lag-vs-retention-cutoff
   monitoring; no prior adapter needed this.
