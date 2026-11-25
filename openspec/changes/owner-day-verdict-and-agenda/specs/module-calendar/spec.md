## ADDED Requirements

### Requirement: Owner day verdict

ID: REQ-module-calendar-051
Source: bu-5ii4gj.2 (run 26 move 2), slice P2
Scope: v1-mandatory

The calendar workspace SHALL expose a deterministic day verdict read, computed without a model, from
deduplicated workspace rows that count toward the owner's load. It SHALL return the next
commitment, committed minutes and the longest free window for a local day, each with the entry it
came from. Tentative, declined and transparent events SHALL NOT count toward committed minutes. A
failed read SHALL return `available=false` and the opener SHALL render it as unknown, never as a
clear day.

#### Scenario: A busy day is not quiet

- **WHEN** a day holds six meetings, one tentative, one declined and one all-day out-of-office
- **THEN** committed minutes cover only the attended meetings, and the next commitment and longest free window are named

#### Scenario: A failed read is unknown

- **WHEN** the verdict query fails
- **THEN** the response has `available=false` and the opener shows an unknown state with no "clear" or "quiet" wording

### Requirement: Owner agenda situation

ID: REQ-module-calendar-052
Source: bu-5ii4gj.2 (run 26 move 2), slice P3
Scope: v1-mandatory

`/now` SHALL carry an `owner.agenda` situation produced deterministically from the calendar: in a
meeting until HH:MM with the next start, free until HH:MM, none left today, or unknown with the last
known value when the calendar cannot be read. Its freshness SHALL end at the next boundary or the
sync staleness bound, whichever is sooner. An unreadable calendar SHALL NOT produce a free claim.

#### Scenario: Unreadable calendar

- **WHEN** the producer cannot read the calendar
- **THEN** the line reads unknown with the stale last value and makes no free-until claim

## Source References

- docs/redesigns/pursuit-ledger.md north star: failure never impersonates health
