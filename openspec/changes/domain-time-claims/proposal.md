## Why

A bill, flight, clinic visit, review or follow-up reaches the calendar either through a read-only
overlay with no freshness, or through a model-written `BUTLER:` event that nothing reconciles. The
result is stale reminders after a bill is settled or a flight rebooked, duplicates between the
overlay and written events, and no door from a calendar item back to the record that justifies it
(bu-5ii4gj.13, run 26 move 13).

## What changes

- The overlay envelope gains a freshness stamp, and a failed or stale read is reported, never
  presented as "no domain context" or as current.
- One typed claim per domain record, with a stable `claim_key` and a `record_version`, replaces the
  free entry list. The owning butler reconciles its claims in its own deterministic job, in its own
  schema, and a claim that asks for a block or reminder presence becomes one butler-lane
  `calendar_events` row keyed `claim:<claim_key>`, moved or retracted with the record.
- The workspace shows a record exactly once (claim dedup) and offers a door back to the record.
- The model-written reminder clauses leave the finance and travel agent instructions.

## Not in this change

No model writes or reconciles a claim. No butler writes another butler's data, no new cross-schema
write grant, no new MCP tool, no write to the owner's primary calendar, and no migration (claims keep
the existing `calendar/overlay/<date>` key and the existing view). No change to meeting-prep
envelopes. The `calendar-time-claims` rename of the capability is later work, to keep the archive
diff small. The Google mirror and the calendar package split are other work.
