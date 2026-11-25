## ADDED Requirements

### Requirement: The briefing day follows the owner's local clock

ID: REQ-dashboard-domain-pages-063
Source: bu-5ii4gj.2 (run 26 move 2), slice P1
Scope: v1-mandatory

The calendar day-briefing card SHALL target the day after the browser-local current day and SHALL
recompute that day at local midnight, with a re-read at least every minute and on window focus,
pageshow and tab visibility, so a page left open across midnight or an operating-system sleep
never labels or fetches a stale day.

#### Scenario: A tab left open across midnight

- **WHEN** the page is open at 23:59 on 26 Nov and the clock passes midnight
- **THEN** the briefing heading and request move to 28 Nov without a reload

#### Scenario: A tab resumed after sleep

- **WHEN** the clock jumps past midnight while no timer ran and the window regains focus
- **THEN** the day is re-read at once

### Requirement: The owner agenda shows in the shell and Overview

ID: REQ-dashboard-domain-pages-064
Source: bu-5ii4gj.2 (run 26 move 2), slice P3
Scope: v1-mandatory

The dashboard SHALL read `/api/now` through a `useNow` hook under the `['now']` query key and show a
`NowStrip` in the Shell header and at the top of the Overview. The strip SHALL show only the
`owner.agenda` line of the card (not the other signals), which is a door to its page or entry. The key SHALL be invalidated on `calendar` bus events and on a boundary timer, not polled.
When the `/now` surface is off and answers 404, the strip SHALL render nothing and no invented
line. A change of the agenda line SHALL be announced through a polite live region only when the
line changes, and where two strips are on screen (Overview at md and wider) only one SHALL carry a
live region, so a change is read once.

#### Scenario: Agenda lines render with a door

- **WHEN** `/api/now` returns an agenda line for an event
- **THEN** the strip shows its text with a link to `/calendar?entry=` for that event

#### Scenario: The surface is off

- **WHEN** `/api/now` answers 404
- **THEN** the strip is absent and no placeholder line appears

#### Scenario: A calendar event refreshes the strip

- **WHEN** a `calendar` bus event arrives
- **THEN** the `['now']` query is invalidated

## Source References

- docs/redesigns/pursuit-ledger.md north star: nothing fabricated
