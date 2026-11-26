## ADDED Requirements

### Requirement: The calendar shows a foreign-zone event's own clock

ID: REQ-dashboard-domain-pages-065
Source: bu-5ii4gj.5 (run 26 move 5), slice P3
Scope: v1-mandatory

The calendar grid, agenda and event detail SHALL show a timed event's start in its own zone beside
the displayed time when its `source_timezone` is known and differs from the display zone at that
instant (a different zone on the same offset adds nothing). An event in the display zone, an
all-day event and an event with an unknown zone SHALL show no second time in the grid or agenda, and
the detail panel SHALL say "zone unknown" for a provider event with no known zone rather than guess.

#### Scenario: A foreign-zone event shows its own clock

- **WHEN** a London event is shown under a Sydney display zone
- **THEN** its block shows the Sydney time and "09:00 London", and a Sydney event in the same week shows no second time
