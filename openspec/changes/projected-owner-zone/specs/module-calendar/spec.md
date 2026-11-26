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

The table is consulted by airport code first; a present airport code the table does not name is
unmapped and SHALL NOT be rescued by the city name. The leg reached is the one with the latest
arrival at or before the instant. Callers SHALL pass only the legs of current or future trips (a
past trip would pin the owner to its destination) and SHALL pass the owner's home city whenever it
is known; times SHALL be timezone-aware.

#### Scenario: Confirmed trip then return

- **WHEN** a confirmed outbound leg arrives in London and a confirmed return leg arrives home
- **THEN** instants between arrival and the return departure project to `Europe/London`, the flight intervals are unknown, and instants after the return project to the home zone

#### Scenario: An unconfirmed booking is not applied

- **WHEN** the latest leg reached is not owner-confirmed
- **THEN** the projected zone is unknown with basis `unconfirmed_booking`

### Requirement: Off-hours meetings in the zone the owner will be in

ID: REQ-module-calendar-057
Source: bu-5ii4gj.5 (run 26 move 5), slice P4b
Scope: v1-mandatory

When the owner clock is live, the overcommitment radar SHALL raise one `off_hours_in_local_zone`
owner condition for each timed owner meeting that touches local 23:00 to 07:00 in the projected
zone, only when that zone is known from a confirmed booking and is not the home zone. The
condition's text and metadata SHALL record the zone, the basis and the local start, and SHALL
carry no title, attendee or location. An unknown zone (in flight, an unconfirmed booking, an
unnamed place) SHALL raise nothing. An unreadable Travel schema SHALL make the tick incomplete, so
an open condition is never resolved by the absence of legs.

A Travel leg counts as a confirmed booking only when it carries a provider confirmation number and
its trip is not cancelled or tentative. This rule is one named function (`leg_is_confirmed`) and is
a stand-in: it is to be replaced by the owner clock's owner-confirm evidence (RFC 0038 S1,
bu-dhloi1.21) in that one place.

#### Scenario: A 09:00 home meeting during a confirmed London trip

- **WHEN** a confirmed trip puts the owner in London and an owner meeting is at 09:00 in the home zone (02:00 in London)
- **THEN** one open `off_hours_in_local_zone` condition records `Europe/London`, `confirmed_booking` and `02:00`

#### Scenario: A booking without a confirmation number

- **WHEN** the leg has no confirmation number
- **THEN** no off-hours condition is raised
