## ADDED Requirements

### Requirement: Place Stays Bridge Sparse Location Reporting

Chronicler SHALL treat silence between OwnTracks fixes as a continued stay when the evidence on both sides allows it, so a phone that reports only on movement still yields place stays. Place stays SHALL be evidence-layer rows that say where the owner was; they SHALL NOT be counted as lived activity time on their own.

ID: REQ-butler-chronicler-014
Source: bu-r3fmja; owner report 2026-11-03 (Nov 1 and Nov 2 gap prompts)
Scope: v1-mandatory

#### Scenario: Silent stay between two fixes at the same place
- **WHEN** an accepted fix falls within the cluster radius of the preceding fix's cluster after a gap longer than 60 minutes and no longer than 16 hours
- **THEN** the cluster SHALL extend across the gap
- **AND** the emitted `place_episode` SHALL record `payload.bridged = true`

#### Scenario: Silent stay ended by a departure fix
- **WHEN** a cluster's last fix is followed, after a gap longer than 60 minutes and no longer than 16 hours, by two consecutive fixes outside its radius
- **THEN** the cluster SHALL end at the first of those fixes
- **AND** the emitted `place_episode` SHALL record `payload.bridged = true` and `precision = minute`

#### Scenario: A long silence is not a stay
- **WHEN** consecutive fixes are more than 16 hours apart
- **THEN** no cluster SHALL span the gap, whether or not the fixes are at the same place

#### Scenario: Place stays are evidence
- **WHEN** a `place_episode` is projected
- **THEN** it SHALL have `layer = evidence` and contribute 0 seconds to every lane total and to tracked time

### Requirement: Owner Anchor Places

Chronicler SHALL resolve the owner's anchor places (home, work, airport, other) deterministically, in this precedence: the owner place registry, then the memory graph, then legacy owner reference points, then, for home only, the owner's usual overnight place. Coordinates SHALL reach only deterministic code.

ID: REQ-butler-chronicler-015
Source: bu-r3fmja; owner decision 2026-11-03 (work location from the memory graph, converted to lat/lon)
Scope: v1-mandatory

#### Scenario: Work comes from the employer's geocoded address
- **WHEN** the owner place registry has no live `work` place and the owner has an active `works-at` edge to an entity with an active `has-address` fact whose current text has a resolved geocode
- **THEN** the resolved work anchor SHALL be at that geocode with a 200 m radius and `source = graph`

#### Scenario: Home comes from the owner's geocoded address
- **WHEN** the registry has no live `home` place and the owner's own active `has-address` fact (no label, or label `home`) has a resolved geocode
- **THEN** the home anchor SHALL be at that geocode with a 150 m radius and `source = graph`
- **AND** failing that, the address of the place the owner `rental-location`s or `lives-at` SHALL be used the same way

#### Scenario: A stale geocode is not an anchor
- **WHEN** an address fact's text changed after its geocode was computed, or its geocode is `not_found` or `failed`
- **THEN** that fact SHALL NOT yield an anchor

#### Scenario: Registry overrides the graph
- **WHEN** the owner place registry holds a live place of a kind
- **THEN** that place SHALL be the anchor of that kind and the graph SHALL NOT be consulted for it

#### Scenario: Home inferred from overnight stays
- **WHEN** no other source yields a home and, within the last 28 days, place stays covering 03:00 local cluster within 150 m on at least 3 distinct nights
- **THEN** the cluster with the most nights SHALL be the home anchor with `source = inferred_overnight`
- **AND** work SHALL never be inferred this way

#### Scenario: No coordinate leaves deterministic code
- **WHEN** any anchor is resolved or used
- **THEN** no coordinate, address text or SSID SHALL appear in an episode payload, log line, prompt or MCP response
- **AND** payloads SHALL carry only the anchor kind, label, source and a distance from home rounded to 100 m

### Requirement: Situational Activity Precedence

Chronicler SHALL derive the owner's activity from where they were, using a deterministic adapter (`chronicler.situation_inferred`) that applies one fixed precedence: sleep, exercise, travel, at the work place (Work), play, eat, at another place (Social), at home with nothing else (Rest). A derived block SHALL never re-count time a higher-precedence activity already owns, and this projection SHALL NOT call an LLM.

ID: REQ-butler-chronicler-016
Source: bu-r3fmja; owner request 2026-11-03 ("registered work location → working; at home and Steam on → gaming"); owner decision 2026-11-03 (unregistered stays count as Social)
Scope: v1-mandatory

#### Scenario: At the work place is work
- **WHEN** a place stay lies within the work anchor
- **THEN** a `work_presence_block` in the Work lane SHALL cover the stay minus any sleep, exercise or travel spans
- **AND** overlapping music listening SHALL NOT shorten it

#### Scenario: At home with a game running is play
- **WHEN** a home stay overlaps a Steam play episode
- **THEN** the play time SHALL stay in the Play lane
- **AND** the `home_block` in the Rest lane SHALL cover only the stay minus the play span and every other higher-precedence span of at least 10 minutes

#### Scenario: A stay at an unregistered place is social
- **WHEN** a place stay within 50 km of home matches no anchor
- **THEN** an `outing_block` in the Social lane SHALL cover it minus higher-precedence spans
- **AND** its payload SHALL give the distance from home rounded to 100 m
- **AND** a stay more than 50 km from home SHALL produce no `outing_block`; trip time is counted as Travel instead (REQ-butler-chronicler-021, change `chronicler-activity-tree-refinement`)

#### Scenario: Hold at the last known place
- **WHEN** the latest accepted location fix is later than every place and movement episode end
- **THEN** the owner SHALL be held at that fix until the earliest of 12 hours later, now, or the end of the day, as a block marked `provisional`

#### Scenario: Re-derivation replaces stale blocks
- **WHEN** later evidence (a sleep record, a late fix) changes a day already derived
- **THEN** the next run SHALL upsert the day's new blocks and tombstone that day's earlier situational blocks it did not re-emit
- **AND** a block carrying an owner correction SHALL NOT be tombstoned

#### Scenario: Short pieces are dropped
- **WHEN** subtraction leaves a piece shorter than 15 minutes
- **THEN** no block SHALL be emitted for it

### Requirement: Gap Interview Asks About One Unexplained Block

When no inferred occupation block anchors the day-close gap interview, the prompt SHALL name the longest unexplained waking block and what location evidence says about it, and SHALL offer one-tap lane answers whose result is counted.

ID: REQ-butler-chronicler-017
Source: bu-r3fmja; owner decision 2026-11-03 (ask about one block with lane buttons)
Scope: v1-mandatory

#### Scenario: Question names the block and its location context
- **WHEN** the day qualifies for a gap interview, has no low-confidence occupation block, and its longest uncovered waking interval is at least 30 minutes
- **THEN** the question SHALL name that interval in owner-local time
- **AND** it SHALL say whether location shows no fixes, a distance from home, or a named anchor during it
- **AND** it SHALL NOT ask whether the day was a work day

#### Scenario: Lane answer is counted
- **WHEN** the owner answers `work`, `social`, `play`, `eat` or `rest`
- **THEN** one owner-attested `activity` episode SHALL cover the named block in that lane with `confidence = high`
- **AND** a duplicate tap SHALL NOT write a second episode

#### Scenario: Dismiss counts nothing
- **WHEN** the owner answers `dismiss`
- **THEN** the interview SHALL be marked answered and no counted episode SHALL be written

#### Scenario: Occupation confirmation is unchanged
- **WHEN** the day has a low-confidence occupation block
- **THEN** the existing work-day confirm question and `confirm`/`correct`/`dismiss` answers SHALL apply
