## ADDED Requirements

### Requirement: Owner day verdict

ID: REQ-module-calendar-051
Source: bu-5ii4gj.2 (run 26 move 2), slice P2
Scope: v1-mandatory

The calendar workspace SHALL expose a deterministic day verdict read, computed without a model,
from the owner day sheet (REQ-module-calendar-036) so the two cannot disagree. It SHALL return the
next commitment, committed minutes and the longest free window ahead of now for a local day, each
with the entry it came from, the meeting in progress leading before the next one. Without a date it
SHALL read the owner's today, or tomorrow from 18:00 owner-local. Tentative, declined and transparent events SHALL NOT count toward
committed minutes. The opener SHALL order its clauses by urgency: the next commitment with a
start-in countdown, then what needs the owner, then committed hours, then the longest free window,
and each clause SHALL be a door to the entry or picker it names. A stale read SHALL withhold the
free window, and only a readable, current day with nothing on it SHALL read "Nothing on today". A
failed read SHALL return `available=false` and the opener SHALL render it as unknown with a
degraded note, never as a clear, quiet or empty day.

#### Scenario: A busy day is not quiet

- **WHEN** a day holds six meetings, one tentative, one declined and one all-day out-of-office
- **THEN** committed minutes cover only the attended meetings, and the next commitment and longest free window are named

#### Scenario: Calm is earned

- **WHEN** the day has no events and every source was read current
- **THEN** the opener reads "Nothing on today"
- **AND** with any source unreadable or stale it does not

#### Scenario: Clauses are ordered doors

- **WHEN** the day has a next meeting, committed time and a free window
- **THEN** the clauses read next commitment with its countdown, needs-you count, committed hours, free window, in that order
- **AND** the next clause opens that entry

#### Scenario: The evening reads tomorrow

- **WHEN** no date is given and the owner's local time is 19:00 on 26 Nov
- **THEN** the verdict is for 27 Nov, labelled with that real date and not a fixed word

#### Scenario: A daylight-saving day keeps true durations

- **WHEN** the day is a 23-hour spring-forward or 25-hour fall-back day in the owner's zone
- **THEN** committed minutes and the free window use elapsed time, not wall-clock subtraction

#### Scenario: A failed read is unknown

- **WHEN** the verdict query fails
- **THEN** the response has `available=false` and the opener shows an unknown state with no "clear" or "quiet" wording

### Requirement: Owner agenda situation

ID: REQ-module-calendar-052
Source: bu-5ii4gj.2 (run 26 move 2), slice P3
Scope: v1-mandatory

`/now` SHALL carry an `owner.agenda` line derived at read time from the day verdict
(REQ-module-calendar-051), not from a stored slot, in the owner's local time. Its texts SHALL be
"In meeting until HH:MM; next HH:MM" when in a meeting, "Free until HH:MM" when free with a later
meeting, "No more today" when nothing remains, and "Unknown (last known ...)" when the calendar
cannot be read or is stale. Declined and transparent events SHALL be ignored. The line SHALL carry
`fresh_until`, the next boundary or the sync staleness bound, whichever is sooner, and the day
rolls over at local midnight. An unreadable or stale calendar SHALL NOT produce a free claim, and
with no last-known value (or one older than six hours) it SHALL read "Unknown (calendar ...)" with
no time in it. The last-known value is held per process and in memory only. No text SHALL carry an
event title, and a non-owner audience SHALL get no door to the event.

#### Scenario: In a meeting

- **WHEN** an attended event ends at 15:00 and the next starts at 16:00
- **THEN** the line reads "In meeting until 15:00; next 16:00"

#### Scenario: Declined and transparent events are ignored

- **WHEN** the only event now is declined or transparent
- **THEN** the line reads "Free until HH:MM" or "No more today", never "In meeting"

#### Scenario: Unreadable calendar

- **WHEN** the producer cannot read the calendar
- **THEN** the line reads "Unknown (last known ...)" with the stale last value and makes no free-until claim

#### Scenario: Non-owner audience

- **WHEN** the text is rendered for a non-owner audience
- **THEN** no event title appears

## Source References

- docs/redesigns/pursuit-ledger.md north star: failure never impersonates health
