## ADDED Requirements

### Requirement: Write Overlap Check Spans Owner Time

A calendar write that is overlap-checked (event create, event update, and a replan move) SHALL be
checked against the calendar the write targets, through the provider's free/busy, and against
every other owner calendar from the stored projection: every source whose stored role is `owner`
or `butler`, and the primary calendar even when it has no source row yet. Another person's shared
calendar SHALL NOT be consulted. Stored rows SHALL exclude cancelled events, free (transparent)
events and quarantined invitations (REQ-module-calendar-041), so an outsider's invite cannot veto a
write, and an event being updated SHALL be excluded by its event id rather than by its time
window. An owner calendar with no source row, or with no successful sync within
`PROJECTION_STALENESS_MULTIPLIER` poll intervals (10 minutes at the 5 minute default, the sync
deadman's own grace), SHALL make the check unavailable. When the check is unavailable, because
free/busy, the role ledger or a stored read failed, the write SHALL be refused with "Couldn't read
your calendars to check for overlaps. Try again, or allow overlap to write anyway."; a replan move
SHALL be declined with the reason `conflict_check_unavailable`. Under the `allow_overlap` policy an
unavailable check SHALL NOT block the owner's write, no conflict SHALL be claimed, and no approval
SHALL be queued from it; a replan move is still declined. A write SHALL NOT report `clear` from a
check that did not read the owner's calendars, except by that explicit `allow_overlap` choice.

ID: REQ-module-calendar-046
Source: bu-5ii4gj.4 (run 26 move 4), slice A
Scope: v1-mandatory

#### Scenario: A block over a primary-calendar meeting reports the conflict

- **WHEN** a Butlers block is created over a meeting on the owner's primary calendar
- **THEN** the result is a conflict naming that meeting and nothing is written

#### Scenario: A shared calendar of another person is not checked

- **WHEN** another person's shared calendar has an event in the window
- **THEN** that calendar is not consulted and the write is not reported as conflicting

#### Scenario: An unreadable check refuses the write

- **WHEN** free/busy for an owner calendar, or the source role ledger, cannot be read
- **THEN** the write is refused with the unavailable message, never reported as clear

#### Scenario: A quarantined invitation does not block

- **WHEN** a needsAction invitation from an untrusted organizer sits on the primary calendar in the write's window
- **THEN** it is excluded from the owner's busy time and does not veto the write

#### Scenario: A stale or never-synced owner calendar makes the check unavailable

- **WHEN** the primary calendar has no successful sync within 10 minutes, or has no source row
- **THEN** the write is refused as unavailable, not checked against a narrower set

#### Scenario: Allow overlap proceeds when the check cannot run

- **WHEN** the policy is `allow_overlap` and the check is unavailable
- **THEN** the write proceeds with no conflict claimed and no approval queued

#### Scenario: A no-park move onto a primary-calendar meeting is declined

- **WHEN** a replan move would land on a meeting on the owner's primary calendar
- **THEN** the move is declined with the reason `conflicts` and nothing is written
