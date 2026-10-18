## Why

Hour-pinned roster crons are evaluated in the owner timezone, but several schedules were
written as if the scheduler read UTC. `general/eod-tomorrow-prep` says it is sent at 23:00
SGT but its cron `0 15 * * *` fires at 15:00 owner-local, and `travel/upcoming-travel-check`
says 08:00 SGT with `0 0 * * *`, which an SGT owner zone fires at 00:00 SGT. Infrastructure schedules commented in UTC have no way to
hold that time when the owner zone is not UTC. Nothing warns when a schedule can only ever
fire inside the owner quiet window, so its notifications would always be held or dropped.

## What Changes

- Owner-facing schedules that name an owner-local time fire at that hour: an owner-local cron
  with that hour, or a cron pinned to a zone in which it reaches the same instant.
- A `[[butler.schedule]]` entry may set `timezone`; sync honours it. Infrastructure
  schedules commented in UTC pin `Etc/UTC` (the literal `UTC` is the owner-zone sentinel).
- Sync logs one warning per prompt-mode notifying schedule whose every firing in a week
  falls inside the owner quiet window. Warn-only; never rejects.
- `travel/upcoming-travel-check` keeps cron `0 0 * * *` and pins `Etc/UTC` (00:00 UTC is 08:00 SGT), so the canonical `butler-travel` schedule text stays true.

## Non-goals

The quiet-window predicate, the park primitive, stagger, which jobs exist, secrets
lifecycle and wake release are unchanged.
