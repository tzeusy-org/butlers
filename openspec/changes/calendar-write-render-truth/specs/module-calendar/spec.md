## ADDED Requirements

### Requirement: Write Overlap Check Spans Owner Time

A calendar write that is overlap-checked (event create, event update, and a replan move) SHALL be
checked against every calendar whose stored source role is `owner` or `butler`, in addition to the
calendar the write targets, never against another person's shared calendar. Busy windows that
repeat across calendars SHALL be reported once. When any of those calendars' free/busy cannot be
read, or the source role ledger cannot be read, the write SHALL be refused with a structured error
whose message is "conflict check unavailable"; a replan move SHALL be declined with the reason
`conflict_check_unavailable`. A write SHALL NOT report `clear` or an empty conflict list from a
check that did not read the owner's calendars.

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
- **THEN** the write is refused with "conflict check unavailable", never reported as clear

#### Scenario: A no-park move onto a primary-calendar meeting is declined

- **WHEN** a replan move would land on a meeting on the owner's primary calendar
- **THEN** the move is declined with the reason `conflicts` and nothing is written
