## ADDED Requirements

### Requirement: Home Departure Card Job

ID: REQ-home-deterministic-jobs-001
Source: bu-jv1moa.8 and RFC 0011 Amendment 8
Scope: v1-mandatory

The Home butler SHALL queue at most one read-only departure card per confirmed owner leave edge,
listing what the owner declared in `home.departure_watch` that is not in a secure state, through
the Switchboard timed insight lane (RFC 0011 Amendment 8). The `home_departure_watch` job SHALL run
with no model in the loop (RFC 0038 D18), SHALL NOT call any Home Assistant service, and SHALL NOT
create a button, a parked action or a prepared action. Arrival cards, departure fixes and an
immediate lane are out of scope (bu-jv1moa.29). The job SHALL stay dark (`DEPARTURE_CARD_LIVE` off,
no roster schedule) until an owner-approved activation turns it on together with the timed lane.

#### Scenario: A confirmed leave queues one timed card

- **WHEN** the perception timeline holds a confirmed `owner.place` leave edge confirmed between 2
  and 30 minutes ago that is not retracted, not late and not followed by a confirmed enter
- **AND** at least one watched entity is not in one of its declared secure states
- **THEN** the job SHALL queue one timed insight candidate with dedup key
  `home:departure:<edge id>`, `deliver_after` no earlier than confirmed + 2 minutes, `expires_at` at
  confirmed + 30 minutes, and premise probe `("home", "departure_still_open")` carrying the edge id
  and its `confirmed_at`
- **AND** a later run SHALL NOT queue a second candidate for the same edge

#### Scenario: A closed edge or a secure home queues nothing

- **WHEN** the leave edge is retracted or late, the owner has a later confirmed enter edge, or every
  watched entity is in a secure state
- **THEN** no candidate SHALL be queued and the edge's card SHALL be recorded closed with the reason

#### Scenario: The card says when it cannot see

- **WHEN** the Home Assistant source health lease is missing, in error or expired
- **THEN** the card SHALL say `Home: cannot see` with the age of the last good reading, and SHALL
  NOT claim anything is secure
- **AND** a watched entity that is missing or reads `unavailable` or `unknown` SHALL be listed as
  `<owner label>: cannot see`

#### Scenario: Only the owner's labels name things

- **WHEN** a card is rendered
- **THEN** it SHALL be plain text naming each item by the owner's `owner_label` with markdown
  removed, and SHALL NOT contain Home Assistant's `friendly_name` or any coordinate

#### Scenario: The Switchboard rechecks the card before it is sent

- **WHEN** the timed lane checks the premise through `public.resolve_home_departure(edge id)`
- **THEN** it SHALL answer true only for an open card rechecked within three minutes whose lines are
  unchanged since it was queued, false for a closed or changed card (the candidate is withdrawn
  unsent), and unknown otherwise (the candidate holds and expires at the end of its window)
- **AND** the function SHALL be SECURITY DEFINER with a pinned `search_path`, SHALL return only that
  class, and SHALL be executable by `butler_switchboard_rw` and not by PUBLIC

#### Scenario: Without the perception reader the job does nothing

- **WHEN** `BUTLERS_PERCEPTION_READER_DSN` is unset or the timeline read fails
- **THEN** the job SHALL return `reader_unavailable` and SHALL NOT queue, close or change any card

#### Scenario: The owner declares what is checked

- **WHEN** the owner edits the departure watch
- **THEN** it SHALL be written through `PUT /api/home/departure-watch` and removed through
  `DELETE /api/home/departure-watch/{entity_id}`, each requiring dashboard owner authority
- **AND** each entry SHALL carry a 1-to-80 character label and 1 to 8 lower-case secure states
