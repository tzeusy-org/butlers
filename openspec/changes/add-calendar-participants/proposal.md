## Why

Google-synced meetings never get attendee-to-entity links, so the prep rail, the T-20 card,
Chronicler participants and linked people are silently empty for real meetings; the link exists
only for butler-created events (bu-5ii4gj.3, calendar pursuit run 26 move 3). An invite from a
known contact is resolvable from its `has-email` fact, but nothing resolves it, and every surface
shows an empty state that reads as "no one you know" rather than "not resolved".

## What changes

- A participant record per calendar occurrence: `calendar_event_participants`, one row per
  attendee address with a role, a response status and a resolution state (`exact`, `ambiguous`,
  `unresolved`, `owner`, `resource`, `manual`), written by the calendar projection when it writes
  a provider event.
- A pure resolver. One active, live `has-email` claimant resolves an address; two or more is
  `ambiguous` and names no one; none is `unresolved`. No model, no fuzzy or name matching, no
  entity created from a stranger.
- One SECURITY DEFINER function gives the calendar-hosting roles the one cross-schema read they
  lack: exact normalized addresses in, `(email, entity_id, is_owner)` out, capped, no names.
- A participant row is an "invited" claim: never an interaction, never an encounter, never an owner
  assertion. A quarantined invitation (REQ-module-calendar-041) mints no participant rows.
- Entity rebind repoints participants. A bounded, checkpointed backfill covers rows projected
  before this change. Readers (prep, meeting card, workspace people, debrief, interaction sync,
  Chronicler) and the encounter ledger follow in later slices.

## Deferred

- Readers using participants (S2), meeting debrief and interaction sync dropping their private
  resolvers (S3), the re-resolution sweep on graph change (S4), the Chronicler adapter (S5).
- The encounter ledger and every "last met" change (surface B of bu-5ii4gj.3).
