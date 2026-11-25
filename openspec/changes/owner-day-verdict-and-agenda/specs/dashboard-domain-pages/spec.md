## ADDED Requirements

### Requirement: The briefing day follows the owner's local clock

ID: REQ-dashboard-domain-pages-063
Source: bu-5ii4gj.2 (run 26 move 2), slice P1
Scope: v1-mandatory

The calendar day-briefing card SHALL target the day after the owner's current local day and SHALL
recompute that day at local midnight and when a hidden tab becomes visible, so a page left open
across midnight never labels or fetches a stale day.

#### Scenario: A tab left open across midnight

- **WHEN** the page is open at 23:59 on 26 Nov and the clock passes midnight
- **THEN** the briefing heading and request move to 28 Nov without a reload

## Source References

- docs/redesigns/pursuit-ledger.md north star: staleness never wears current-data authority
