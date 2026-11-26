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
minutes, so a flip back is a no-op (a settling run returns `retry_after`, and the caller SHALL re-run it
at or after that instant). The write SHALL be a compare-and-set on the old `next_run_at`,
so a claim that wins the race is never duplicated or overwritten.

The zone to move rows to is one shared rule (`target_zone`): the clock's zone when its basis is owner,
owner-confirmed booking or phone, else the settings zone, and none when the clock is unknown. The
scheduler resolver and the re-anchor both use it, so a return home re-anchors back and a
`utc_fallback` clock moves nothing already on the settings zone. A TOML sync or schedule edit made
during a trip snaps that one occurrence back to the settings zone.

A Travel leg SHALL confirm the clock only when it satisfies the confirmed-booking rule
(`leg_is_confirmed`), at its `arrival_at` and not at booking time; any other leg with a mapped place
SHALL only ask once. Until the anchor-kind migration, owner-local is the sentinel; that migration
replaces the predicate and the owner-confirm evidence (bu-dhloi1.21) replaces the stand-in rule, each
in one place.

The scheduler SHALL take the owner zone its sentinel rows follow from one resolver shared with the
re-anchor: the live clock zone when `OWNER_CLOCK_LIVE` is on and the clock is confirmed, else the
settings zone. A re-anchored row that fires SHALL recompute its next run in that same zone and not
snap back to the home zone. The re-anchor SHALL walk every butler schema; a schema that cannot be read
makes the run incomplete (never reported as done) and counts are per schema.

#### Scenario: A confirmed trip re-anchors the morning brief

- **WHEN** the clock moves from Singapore to Tokyo and a 09:00 owner-local schedule is not yet due
- **THEN** its next run is 09:00 Tokyo, and a schedule pinned to London keeps its time

#### Scenario: A claim wins the race

- **WHEN** the dispatcher claims the occurrence between the read and the write
- **THEN** the write does not apply and the row keeps the claimed anchor

#### Scenario: A re-anchored row fires

- **WHEN** a re-anchored 09:00 owner-local schedule fires once with the clock on Tokyo time
- **THEN** its next run is again 09:00 Tokyo

#### Scenario: A schema cannot be read

- **WHEN** one butler schema has no readable pool
- **THEN** the run reports `complete: false` and names that schema, and counts are per schema

### Requirement: One owner card per clock transition

ID: REQ-core-scheduler-007
Source: bu-5ii4gj.5 (run 26 move 5), slice card
Scope: v1-mandatory

A clock transition that re-anchors schedules SHALL compose exactly one owner card: how many items moved
to the new place's time, which pinned schedules were kept (with the time in their own zone and in the
new one), what needs the owner's approval, and any schema that could not be read ("will retry"). Each
kept and moved item is a door to its schedule row. An unknown clock composes a card saying so and that
schedules stay on the old zone; a dark run, or one with nothing moved, nothing to approve and no
unread schema, composes none. Only schedules whose compare-and-set landed count as moved. Schedule
titles appear on the card only, never in logs. Composing is separate from delivery, which goes through
the owner delivery ledger behind the same flag.

#### Scenario: A trip moves a brief and keeps a call

- **WHEN** the clock moves to Tokyo, one owner-local schedule moves and a schedule pinned to Singapore is kept
- **THEN** the card reads "Moved to Tokyo time: 1 item. Kept: Call Mum 19:00 Singapore (20:00 here)." with a door for each
