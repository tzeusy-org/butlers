## Why

An owner clock that changes zone leaves owner-local schedules on the old zone's wall clock until
their next firing (bu-5ii4gj.5 P5, run 26 move 5).

## What changes

- A deterministic re-anchoring of owner-local cron schedules when the owner clock changes zone.
- Travel legs become owner-clock evidence: a confirmed leg confirms at its arrival, any other
  mapped leg only asks once.
- Dark behind `OWNER_CLOCK_LIVE`.

## Not in this change

No migration: "owner-local" is the `'UTC'` follow-the-owner sentinel until the anchor-kind column
lands (P6), which replaces that one predicate. No owner card, no Health shift plan, no change to the
present-tense owner clock reducer (bu-dhloi1.21).
