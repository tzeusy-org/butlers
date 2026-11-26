## ADDED Requirements

### Requirement: Owner-local schedules re-anchor when the owner clock changes zone

ID: REQ-core-scheduler-006
Source: bu-5ii4gj.5 (run 26 move 5), slice P5
Scope: v1-mandatory

When the owner clock's zone changes and `OWNER_CLOCK_LIVE` is on, the system SHALL recompute
`next_run_at` of each enabled owner-local cron schedule (today: the `'UTC'` follow-the-owner
sentinel) as the same cron fields read in the new zone. It SHALL NOT touch a schedule pinned to a
zone, a one-shot schedule, or one already due (which fires once on its old anchor). It SHALL move
nothing while the clock is unknown or stale, and nothing until the transition is older than ten
minutes, so a flip back is a no-op. The write SHALL be a compare-and-set on the old `next_run_at`,
so a claim that wins the race is never duplicated or overwritten.

A Travel leg SHALL confirm the clock only when it satisfies the confirmed-booking rule
(`leg_is_confirmed`), at its `arrival_at` and not at booking time; any other leg with a mapped place
SHALL only ask once. Until the anchor-kind migration, owner-local is the sentinel; that migration
replaces the predicate and the owner-confirm evidence (bu-dhloi1.21) replaces the stand-in rule, each
in one place.

#### Scenario: A confirmed trip re-anchors the morning brief

- **WHEN** the clock moves from Singapore to Tokyo and a 09:00 owner-local schedule is not yet due
- **THEN** its next run is 09:00 Tokyo, and a schedule pinned to London keeps its time

#### Scenario: A claim wins the race

- **WHEN** the dispatcher claims the occurrence between the read and the write
- **THEN** the write does not apply and the row keeps the claimed anchor
