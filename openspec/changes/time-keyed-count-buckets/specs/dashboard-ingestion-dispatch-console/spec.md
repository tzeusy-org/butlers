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

### Requirement: Heartbeat-derived per-bucket listening state

Every connector stats bucket, and every slot of the roster summary's `hourly_events`, SHALL carry a listening state of `live`, `deaf` or `unknown`, derived only from the Switchboard heartbeat history of that exact `(connector_type, endpoint_identity)`. A bucket SHALL be `live` when at least one heartbeat from the endpoint was recorded in it. A bucket SHALL be `deaf` only when it is closed, starts at or after the endpoint's oldest recorded heartbeat, has no recorded heartbeat, and has no ingested, failed or filtered events. Every other bucket SHALL be `unknown`, including the still-open current bucket, buckets before recorded history begins, and every bucket when the heartbeat-history read fails. A sibling endpoint's heartbeat SHALL NOT make a bucket `live`. A failed heartbeat-history read SHALL be reported through its own availability flag (`meta.listening_available` on stats, `listening_available` on summaries) and SHALL NOT suppress the counts. The stats contract SHALL NOT carry default-zero liveness counters (`heartbeat_count`, `healthy_count`, `degraded_count`, `error_count`). The roster sparkline and the detail histogram SHALL draw a `deaf` slot as a hatched cell, distinct in shape from a measured zero, and SHALL describe the deaf hours accessibly as `not listening <N>h`. `deaf` means no heartbeat was recorded, not a proven provider outage.

ID: REQ-dashboard-ingestion-dispatch-console-005
Source: bu-s11n0s.5 (JARVIS pursuit run 16, slice S2)
Scope: v1-mandatory

#### Scenario: Missing heartbeats read as not listening

- **WHEN** a connector has events at slots 2 and 20 and heartbeats in every closed hour except slots 5 to 7
- **THEN** the stats route SHALL return `deaf` at slots 5, 6 and 7
- **AND** the current open hour without a heartbeat SHALL be `unknown`, not `deaf`

#### Scenario: Deaf slots are hatched and described

- **WHEN** the detail histogram or roster sparkline receives three `deaf` slots
- **THEN** it SHALL draw those slots as hatched cells rather than zero bars
- **AND** its accessible description SHALL include `not listening 3h`

#### Scenario: A failed heartbeat read is not deafness

- **WHEN** the heartbeat-history read fails
- **THEN** every bucket SHALL be `unknown` and the listening availability flag SHALL be false
- **AND** the counts SHALL still be returned
