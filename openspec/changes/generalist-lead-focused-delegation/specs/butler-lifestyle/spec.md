## MODIFIED Requirements

### Requirement: Agent Type and Module Shape

The Lifestyle butler SHALL be a butler-typed agent (not a staffer) and SHALL expose a fixed shape of required module capabilities. As a domain capability service it SHALL NOT spawn reasoning sessions; goals reach its capabilities only through grants.

Source: RFC 0038 D1 and D2
Scope: v1-mandatory

#### Scenario: Butler-typed specialist

- **WHEN** the daemon classifies the Lifestyle butler
- **THEN** it SHALL be a butler-typed agent that registers its Lifestyle capabilities and guidance with the control plane as a domain capability service
- **AND** it SHALL NOT be a staffer (Switchboard, Messenger, QA)

#### Scenario: Required module capabilities

- **WHEN** the butler starts
- **THEN** it SHALL enable the `memory`, `calendar`, and `contacts` modules at minimum
- **AND** it MAY enable domain-specific integration modules (e.g. Spotify, Steam)
- **AND** the authoritative enabled-module list lives in `roster/lifestyle/butler.toml`

### Requirement: Domain Scope Boundary

The Lifestyle butler SHALL own a clearly defined domain that does not overlap with other butlers' primary responsibilities. These boundaries are ownership invariants that goals and other butlers rely on: Lifestyle data SHALL be written and read only through Lifestyle capabilities, and Lifestyle guidance SHALL tell a goal which domain owns adjacent work.

Source: RFC 0038 D1 and D2
Scope: v1-mandatory

#### Scenario: Lifestyle domain ownership

- **WHEN** a goal records or recalls music, listening habits, playlists, or music discovery
- **THEN** it SHALL do so through Lifestyle capabilities and guidance

#### Scenario: Entertainment domain ownership

- **WHEN** a goal records or recalls movies, TV shows, books, games, podcasts, or general entertainment
- **THEN** it SHALL do so through Lifestyle capabilities and guidance

#### Scenario: Food preference domain ownership

- **WHEN** a goal records or recalls food preferences, favorite restaurants, cuisines, recipes, or dining experiences
- **THEN** it SHALL do so through Lifestyle capabilities and guidance
- **AND** it SHALL NOT record them through Health capabilities unless they contain explicit nutritional data (calories, macros, diet tracking)

#### Scenario: Hobby and interest domain ownership

- **WHEN** a goal records or recalls hobbies, personal interests, or leisure activities
- **THEN** it SHALL do so through Lifestyle capabilities and guidance
- **AND** it SHALL NOT record them through Education capabilities unless they involve systematic learning or curriculum

#### Scenario: Routine domain ownership

- **WHEN** a goal records or recalls daily routines, morning/evening patterns, or focus/wind-down modes
- **THEN** it SHALL do so through Lifestyle capabilities and guidance
- **AND** it SHALL NOT record them through Health capabilities unless they contain explicit health metrics (sleep duration, exercise reps, vitals)

#### Scenario: Nutrition tracking refusal

- **WHEN** a goal needs to track calories, macros, or a nutrition plan
- **THEN** the Lifestyle butler SHALL register no capability for it
- **AND** Lifestyle guidance SHALL direct the goal to Health capabilities

#### Scenario: Formal learning refusal

- **WHEN** a goal needs to manage curricula, study plans, or spaced-repetition learning
- **THEN** the Lifestyle butler SHALL register no capability for it
- **AND** Lifestyle guidance SHALL direct the goal to Education capabilities

#### Scenario: Social event planning refusal

- **WHEN** a goal needs to coordinate social events or manage relationships
- **THEN** the Lifestyle butler SHALL register no capability for it
- **AND** Lifestyle guidance SHALL direct the goal to Relationship capabilities

#### Scenario: Home automation refusal

- **WHEN** a goal needs to control home devices or automate the home
- **THEN** the Lifestyle butler SHALL register no capability for it
- **AND** Lifestyle guidance SHALL direct the goal to Home capabilities

### Requirement: Interactive Response Mode

Interactive user-facing ingests that concern the Lifestyle domain SHALL be answered by the goal's lead under the shared Interactive Response Mode contract, and Lifestyle background connector events SHALL NOT be treated as interactive.

Source: RFC 0038 D1 and D2
Scope: v1-mandatory

#### Scenario: Interactive channel ingest

- **WHEN** a goal admitted from an interactive channel (e.g. `telegram_bot`) concerns the Lifestyle domain
- **THEN** its lead SHALL follow the Interactive Response Mode contract defined in the lead policy at `roster/switchboard/goal-policy/LEAD.md`

#### Scenario: Spotify connector events are non-interactive

- **WHEN** a Spotify connector event (`spotify.track_change`, `spotify.session_summary`) is ingested
- **THEN** it SHALL be treated as background knowledge-graph growth
- **AND** no user-facing reply, reaction, or Telegram notification SHALL be emitted in response to the event, whether it is projected deterministically or admitted as a goal
