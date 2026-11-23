## Why

Calendar discovery registers every calendar the Google account can see as lane `user`, and no
reader asks which calendar an event came from. `is_owner_attending` returns true when no attendee is
the owner, which is exactly an event on someone else's calendar. A partner's, colleague's or
team's appointment therefore sets the owner's meeting context, counts toward day load and
conflicts, removes a free slot, and prompts a debrief (bu-5ii4gj.1, calendar pursuit run 26 move 1).

## What changes

- Every `calendar_sources` row gets a `time_role` (`owner`, `household_member`, `other_person`,
  `team`, `subscription`, `holidays`, `birthdays`, `butler`, `unclassified`) and a `role_basis`
  (`provider_inferred` or `owner_confirmed`).
- A deterministic classifier assigns the role from calendarList facts only (primary flag, calendar
  id shape, the resolved Butlers calendar). It never reads titles or names, and never assumes
  ownership: a non-primary calendar the account can write is `unclassified`.
- One pure predicate, `asserts_owner_time(time_role, metadata)`, decides whether an event is the
  owner's time. Owner-time roles (`owner`, `butler`) assert it. Roles that describe other people
  (`household_member`, `team`, `other_person`, `unclassified`) assert it only through a `self`
  attendee who has not declined. `holidays`, `birthdays` and `subscription` never do.
- The context producer, conflict radar and day load, free-slot default, working-location read,
  meeting card, prep and debrief all use it. Rediscovery never overwrites an `owner_confirmed` role.
- A migration adds the columns and backfills existing rows from stored discovery facts.

## Deferred

- The owner role picker in source settings that writes `owner_confirmed` (slice S2).
- `role_subject_entity_id` and the household context read (slice S3).
- Moving the role onto the connector mirror when bu-ep60gq.6 lands (slice S4).

## Decisions to confirm

- The `butler` role (the dedicated Butlers calendar and butler-lane internal sources) counts as
  owner time, so a hold the butler placed still blocks a free slot. Generated events stay excluded by
  `metadata.butler_generated`.
