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
- Make the Switchboard fanout projection ignore metadata, malformed, NaN, and
  infinity samples and return `meta.aggregates_available=false` when no usable
  total exists. When the aggregate query is empty, probe the exact
  `switchboard_routed_messages_total` family first; an absent or unreadable
  family is unavailable, while a live family with no matching increase remains
  a measured empty result.
- Add additive OpenSpec coverage for sample validity and dashboard unavailable
  data states.

## Out of Scope

- No connector metric producer, heartbeat schema, scrape configuration,
  credentials, migration, or live telemetry action changes.

## Verification

Focused heartbeat and Switchboard fanout fixtures cover valid totals,
`*_created`, malformed, NaN, infinity, and no-usable-sample cases. Existing
pipeline/dashboard data-state tests remain the consumer evidence for rendering
unavailable metrics distinctly from zero.
