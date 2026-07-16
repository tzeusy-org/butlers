## MODIFIED Requirements

### Requirement: Editorial KPI Endpoint

The chronicler API SHALL expose `GET /api/chronicler/kpi?date=YYYY-MM-DD`
returning the KPI snapshot the briefing also embeds. Every
`hours_by_top_lanes[*].lane` value SHALL use the Activity lane taxonomy:
`sleep`, `exercise`, `work`, `butler_ops`, `play`, `social`, `travel`, `eat`,
or `rest`. These KPI entries SHALL be derived only from activity-layer records;
`work` SHALL represent the owner's occupation and `butler_ops` SHALL represent
internal butler sessions separately.

#### Scenario: KPI fields

- **WHEN** the endpoint returns successfully
- **THEN** the response includes `hours_by_top_lanes` (top three by
  total minutes), `longest_episode_minutes`, `longest_episode_title`,
  `longest_gap_minutes`, `sleep_minutes`, and `streaks` (a small object
  with `sleep` and `exercise` integer streak counts)

#### Scenario: KPI top lanes use Activity taxonomy

- **WHEN** the endpoint returns one or more `hours_by_top_lanes` entries
- **THEN** each entry's `lane` is one of `sleep`, `exercise`, `work`,
  `butler_ops`, `play`, `social`, `travel`, `eat`, or `rest`
- **AND** intent- and evidence-layer records do not produce a KPI lane
- **AND** owner occupation time counts toward `work`, while internal butler
  session time counts toward `butler_ops` and not toward `work`
