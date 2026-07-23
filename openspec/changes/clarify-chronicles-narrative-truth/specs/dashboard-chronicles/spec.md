# dashboard-chronicles Specification Delta for clarify-chronicles-narrative-truth

## MODIFIED Requirements

### Requirement: Editorial Briefing Endpoint

The chronicler API SHALL expose `GET /api/chronicler/briefing?date=YYYY-MM-DD`
returning a `ChroniclesBriefing` object. The endpoint SHALL determine
authoritative local-day coverage and availability before selecting any day-close
cache content, SHALL NOT initiate an LLM call, and SHALL serve any historical
date deterministically. When `date` is omitted it SHALL default to the most
recent settled day (yesterday in the owner timezone).

#### Scenario: Response shape

- **WHEN** the endpoint returns successfully
- **THEN** the response body contains: `date`, `state_class` (one of
  `urgent`, `busy`, `mild`, `quiet`, `no_data`, `unavailable`, or `degraded`),
  `headline` (string), `voice_paragraph` (string), `voice_source` (one of
  `llm·cached`, `templated`, `stale`), `kpi` (object), `attention_items`
  (array), `recent_days` (array), and `earliest_date`
- **AND** `earliest_date` is the earliest authoritatively covered local calendar
  day in the owner timezone, or `null` when no such coverage is established
- **AND** every numeric field is `tabular-nums` safe (integer or fixed decimal)

#### Scenario: Local-day coverage requires a durable witness

- **WHEN** the endpoint determines whether a selected local day is covered
- **THEN** it SHALL call that day authoritatively covered only when a durable,
  Chronicler-owned `covered-local-day` witness exists for that exact
  owner-timezone local date
- **AND** Chronicler SHALL record that witness only after every required owned
  Chronicle evidence read for that local day has succeeded
- **AND** `earliest_date` SHALL be the minimum successful exact-date witness,
  but SHALL NOT imply that a later local day is covered
- **WHEN** no authoritative coverage floor is established, the selected day
  lacks its successful exact-date witness, its coverage evidence is incomplete
  or failed, or the selected day falls in a coverage-evidence gap on or after
  the floor
- **THEN** the coverage verdict SHALL resolve `unavailable`
- **AND** it SHALL NOT resolve `no_data` or `quiet`, or permit cached prose
- **WHEN** the coverage verdict positively establishes that the selected local
  day precedes the authoritative coverage floor
- **THEN** it SHALL resolve `no_data`; `no_data` SHALL NOT be inferred from
  missing evidence, an empty result, or an operational proxy

#### Scenario: Cache applies only to a covered and available payload

- **WHEN** the selected local day is authoritatively covered, all required
  owned reads succeed, and a day-close cache row is fresh and admissible
- **THEN** `voice_paragraph` is the cached prose
- **AND** `voice_source` is `llm·cached`
- **AND** a valid-but-stale cache row MAY be returned with `voice_source` set to
  `stale` only after those coverage and availability conditions hold

#### Scenario: Missing or invalid cache uses deterministic fallback

- **WHEN** the selected local day is authoritatively covered and available but
  no day-close cache row exists or the row is invalid
- **THEN** `voice_paragraph` is deterministic templated text keyed by the
  resolved state class and trustworthy KPI shape
- **AND** `voice_source` is `templated`
- **AND** the endpoint SHALL NOT render, return, or transform invalid cached LLM
  prose

#### Scenario: No-data and degraded states bypass cached prose

- **WHEN** the authoritative coverage verdict positively establishes that the
  selected local day precedes the authoritative coverage floor
- **THEN** `state_class` is `no_data`
- **AND** the response uses deterministic no-data copy and SHALL NOT read or use
  a fresh or stale day-close cache row
- **WHEN** an owned query fails, coverage cannot be established, or the
  availability result supplied by the owning availability path is
  `unavailable` or `degraded`
- **THEN** `state_class` is respectively `unavailable` or `degraded`
- **AND** the response uses deterministic state-specific copy and SHALL NOT read
  or use a fresh or stale day-close cache row
- **AND** neither availability state SHALL be represented as `no_data` or
  `quiet`

#### Scenario: Covered empty day is confirmed quiet

- **WHEN** the selected local day is authoritatively covered, all required owned
  reads succeed, and no reader-visible evidence or attention item is present
- **THEN** `state_class` is `quiet`
- **AND** the resulting quiet state SHALL remain distinct from pre-coverage
  `no_data` and unavailable/degraded availability

#### Scenario: State precedence is deterministic

- **WHEN** a payload has both a cache row and a coverage or availability signal
- **THEN** unavailable/degraded availability or coverage evidence that is
  absent, incomplete, or failed SHALL take precedence over `no_data`, `quiet`,
  cache freshness, and cached prose
- **AND** a positive pre-floor verdict SHALL take precedence over empty evidence
  and cached prose
- **AND** `quiet` SHALL be selected only after affirmative coverage and
  successful owned reads

### Requirement: Archive Date Navigation

The Chronicles landing SHALL let the owner navigate between settled past days.
The selected day SHALL be URL state so a day view is deep-linkable, and the
selected day SHALL drive both the editorial briefing and the drilldown.
Navigation SHALL NOT initiate an LLM call; viewing any past date uses only a
coverage-eligible cache result or deterministic state-specific copy.

#### Scenario: Default landing day

- **WHEN** the owner opens `/chronicles` with no `date` query parameter
- **THEN** the page SHALL show the most recent settled day (yesterday in
  the owner timezone)

#### Scenario: Day selection is URL state

- **WHEN** the owner opens `/chronicles?date=YYYY-MM-DD` for a settled day
- **THEN** the briefing, attention, KPI, recent-days index, and drilldown
  SHALL all reconstruct that day
- **AND** changing the selected day SHALL update the `date` query parameter

#### Scenario: Stepper clamps to authoritative coverage

- **WHEN** the owner steps forward
- **THEN** the page SHALL NOT advance past the most recent settled day
  (today is incomplete and not shown)
- **WHEN** the owner steps backward
- **THEN** the page SHALL NOT step before `earliest_date` when that value is an
  authoritative covered local day
- **AND** the page SHALL disable backward navigation when `earliest_date` is
  `null` because no authoritative coverage is established or coverage is
  unavailable
- **AND** the page SHALL NOT derive an archive floor from source registry
  seeding, current feeder state or checkpoints, or trailing `daily_rollups`

#### Scenario: Pre-coverage URL is truthful

- **WHEN** a deep-linked selected day is before the authoritative coverage floor
- **THEN** the page SHALL render the deterministic `no_data` state rather than
  a quiet editorial day
- **AND** it SHALL not permit a further backward step
- **AND** it SHALL preserve the selected URL date unless the owner explicitly
  selects another day

#### Scenario: Recent-days rows navigate only to covered days

- **WHEN** the recent-days index is returned
- **THEN** every row SHALL identify an authoritatively covered local day
- **AND** activating a row SHALL select that row's day and reconstruct it

#### Scenario: No new LLM call on navigation

- **WHEN** the owner navigates to any settled past day
- **THEN** the briefing handler SHALL NOT initiate an LLM call
- **AND** a covered, available day MAY use only admissible day-close cache prose
  (fresh or stale) or deterministic templated fallback
- **AND** no-data or unavailable/degraded navigation SHALL use deterministic
  state-specific copy without cached LLM prose
