## ADDED Requirements

### Requirement: Projected owner zone for future days

ID: REQ-module-calendar-056
Source: bu-5ii4gj.5 (run 26 move 5), slice P4
Scope: v1-mandatory

A deterministic projection SHALL answer which zone the owner will be in at a future instant from
owner-confirmed itinerary legs and the home zone, with no model and no outside service. With no leg
reached the zone is the home zone with basis `home_assumed`; after an owner-confirmed leg whose
arrival the pinned table (or the owner's home city) names it is that place's zone with basis
`confirmed_booking`; between a leg's departure and arrival, after an unconfirmed leg, and after a
leg to a place the table does not name, the zone SHALL be unknown (`zone` absent) and no zone SHALL
be guessed. A consumer SHALL NOT raise an off-hours observation for an unknown zone.

#### Scenario: Confirmed trip then return

- **WHEN** a confirmed outbound leg arrives in London and a confirmed return leg arrives home
- **THEN** instants between arrival and the return departure project to `Europe/London`, the flight intervals are unknown, and instants after the return project to the home zone

#### Scenario: An unconfirmed booking is not applied

- **WHEN** the latest leg reached is not owner-confirmed
- **THEN** the projected zone is unknown with basis `unconfirmed_booking`
