## Why

An event on a partner or shared calendar can list the owner's address as an accepted attendee
without being the owner's time. The chronicler calendar adapter, interaction sync and the encounter
ledger each decided "the owner's time" differently, and none of them asked whose calendar the event
was on (bu-5ii4gj.16).

## What changes

- The chronicler calendar adapter projects an instance only when `counts_as_owner_time(source role,
  event metadata)` holds, so free blocks and declined events are skipped as well as foreign calendars.
- Interaction sync and the encounter ledger gate on the role (`asserts_owner_time`) and nothing about
  transparency. A free-marked meeting the owner accepted is still a meeting that happened.
  Interaction sync also requires `is_owner_attending`; the ledger records the owner's decline as its
  own `declined_by_owner` state, so it does not.
- Ghost pruning removes existing ledger rows for events on invitation-role calendars the owner has no
  accepted seat on.

## Not in this change

Episodes already projected from partner calendars are not removed; a cleanup is separate.
