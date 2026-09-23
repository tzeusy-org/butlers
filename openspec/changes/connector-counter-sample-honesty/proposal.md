## Why

Connector heartbeat collection currently iterates every sample in a
Prometheus Counter family.  The registry exposes both the operational
`*_total` sample and the metadata-only `*_created` timestamp; treating both as
counts can publish epoch-sized ingestion totals.  Malformed and non-finite
samples have the same failure shape when a reader coerces them to zero.

The connector aggregate reader also needs a typed unavailable result when a
Prometheus response contains no usable counter sample, so the dashboard can
distinguish an unreadable source from a measured empty matrix.

## What Changes

- Add strict heartbeat Counter-family sample normalization: only finite,
  non-negative `*_total` samples with matching connector labels count.
- Expose an internal typed counter-read availability state while preserving
  the existing `connector.heartbeat.v1` integer wire shape.
- Ground Switchboard fanout availability in the exact emitted
  `butlers_switchboard_subroute_dispatched_total` family. The counter carries
  only bounded `source="connector"` provenance and `destination_butler`; raw
  connector/account identities stay out of OTel labels.
- Source the exact connector and endpoint projection from the existing
  sessions-to-ingestion-events DB join. A live producer plus a complete DB
  fan-out permits a measured empty result; an absent producer or partial DB
  fan-out remains unavailable.
- Add additive OpenSpec coverage for sample validity and dashboard unavailable
  data states.

## Out of Scope

- No new metric family, heartbeat schema, scrape configuration, credentials,
  migration, or live telemetry action changes. The existing subroute counter's
  provenance labels are narrowed to bounded RFC 0005-safe values.

## Verification

Focused heartbeat and Switchboard fanout fixtures cover valid totals,
`*_created`, malformed, NaN, infinity, and no-usable-sample cases. Existing
pipeline/dashboard data-state tests remain the consumer evidence for rendering
unavailable metrics distinctly from zero.
