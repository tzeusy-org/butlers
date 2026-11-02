## ADDED Requirements

### Requirement: Count-Bucket Truth

Every count strip (activity stripes, daily bars, connector sparklines and histograms) SHALL place each bucket by its bucket-start instant, never by array position. A bucket that no successful source read reported SHALL render as not reported, distinct in shape from a measured zero, and SHALL be left out of the strip's totals and peak. A failed source read SHALL report no buckets rather than a row of zeros. Frontend source SHALL NOT build a chart series from a zero-filled array; the `count-bucket/no-zero-fill` lint rule enforces this, and a genuine non-chart numeric table takes a line-level disable with a reason.

ID: REQ-dashboard-design-language-007
Source: bu-s11n0s.5.1 (JARVIS pursuit run 16, slice S3)
Scope: v1-mandatory

#### Scenario: Activity stripe keys hours by start

- **WHEN** the hourly-activity response holds buckets for three hours, in any order and with any `hour_index`
- **THEN** the 24h activity stripe SHALL draw each at the slot of its `hour_start`
- **AND** the other 21 slots SHALL render as not reported, and the stripe's accessible label SHALL name them

#### Scenario: A failed board stripe is unreported

- **WHEN** a butler's hourly-activity query fails while the fleet board is built
- **THEN** that row's `hourly_buckets` SHALL be empty and its `hourly_total` null
- **AND** the overview stripe SHALL render 24 not-reported slots, not 24 zero cells

#### Scenario: Zero-filled chart arrays are rejected

- **WHEN** frontend source outside a test builds an array with `.fill(0)` or `Array.from({ length }, () => 0)`
- **THEN** lint SHALL fail with a message pointing at `buildBucketSeries`
