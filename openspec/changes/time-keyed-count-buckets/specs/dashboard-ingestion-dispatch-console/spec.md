## ADDED Requirements

### Requirement: Time-keyed connector throughput buckets

The connector stats route SHALL return dense, time-keyed buckets: exactly 24 hourly rows for `24h`, 7 daily rows for `7d` and 30 daily rows for `30d`, oldest first. Each row SHALL carry its bucket start. The last bucket SHALL be the UTC hour or day that contains the database's `NOW()`, so the detail histogram and the roster summary's `hourly_events` cover the same window and place one event in the same bucket. A bucket with no ingested, failed or filtered events SHALL be returned as a measured zero; a measured zero SHALL NOT be read as evidence of connector liveness. The connector detail histogram SHALL place each bucket by its bucket start, never by array position. A slot that no returned row covers SHALL render as not reported, distinct in shape from a measured zero. The histogram SHALL label its columns relative to now (for example `-24h`, `-12h`, `now`) rather than with clock hours.

ID: REQ-dashboard-ingestion-dispatch-console-004
Source: bu-s11n0s.5 (JARVIS pursuit run 16, slice S1)
Scope: v1-mandatory

#### Scenario: Sparse history lands at its real hours

- **WHEN** a connector has events 21 hours and 3 hours before the current hour, and in the current hour
- **THEN** the stats route SHALL return 24 rows whose bucket starts are the 24 consecutive UTC hours ending at the current hour
- **AND** the counts SHALL sit at slots 2, 20 and 23, with every other slot a measured zero

#### Scenario: Detail histogram keys by time, not position

- **WHEN** the detail page receives rows for slots 2 and 20 only, in any order
- **THEN** the histogram SHALL draw bars in columns 2 and 20
- **AND** every other column SHALL render as not reported rather than as a zero bar

#### Scenario: Labels are relative

- **WHEN** the detail histogram renders
- **THEN** its axis labels SHALL read relative hours ending at `now`
- **AND** no clock-hour label such as `00` or `23` SHALL appear
