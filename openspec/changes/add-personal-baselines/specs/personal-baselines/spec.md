## ADDED Requirements

### Requirement: Baseline band with honest denominator

A personal baseline SHALL be computed by a pure function from per-day values: the median as `center`, and `dispersion` as `1.4826 * MAD`, floored at the metric's declared `min_dispersion`. Every baseline SHALL carry `n_observed`, `n_expected`, `coverage = n_observed / n_expected`, `method_version` and the instrument's `measurability`. Its window SHALL be the `window_days` days ending `k_consecutive` days before the last scored day, and days inside any deviation episode SHALL be excluded from both `n_observed` and `n_expected`. A baseline whose `n_observed` is below `min_n` or whose coverage is below `min_coverage` SHALL have status `insufficient_history` and no center or dispersion.

#### Scenario: A band reports the days it rests on

- **WHEN** 54 of 60 window days have a reading
- **THEN** the baseline SHALL carry `n_observed = 54`, `n_expected = 60` and `coverage = 0.9`

#### Scenario: A flat series is floored

- **WHEN** every reading in the window is identical
- **THEN** `dispersion` SHALL equal the metric's `min_dispersion`, never zero

#### Scenario: The run being judged does not contribute to its own band

- **WHEN** the last three days are scored
- **THEN** none of those three days SHALL be in the window the band is computed from

#### Scenario: Episode days are excluded from numerator and denominator

- **WHEN** five window days fall inside a deviation episode
- **THEN** `n_expected` SHALL be reduced by five and those readings SHALL not influence the center

### Requirement: Scoring never converts absence into normality

Scoring a value SHALL return exactly one of `within`, `above`, `below`, `insufficient_history`, `unmeasurable` or `stale`, with precedence unmeasurable, then stale, then insufficient_history, then the value. A baseline computed longer ago than twice the metric's refresh interval SHALL score `stale`. `within` SHALL be returned only from a ready, fresh, measurable baseline. A deviation SHALL be `concerning` only when its direction is within the metric's declared concern (`above`, `below` or `both`).

#### Scenario: Thin history is not within

- **WHEN** a baseline has `status = insufficient_history`
- **THEN** a value scored against it SHALL be `insufficient_history`

#### Scenario: A dead instrument is unmeasurable

- **WHEN** the metric's producer is unmeasurable under the expected-signals contract
- **THEN** the baseline SHALL have `measurability = unmeasurable`, no center, and every score against it SHALL be `unmeasurable`

#### Scenario: A stale band is not scored

- **WHEN** a baseline was computed more than twice the refresh interval ago
- **THEN** a value scored against it SHALL be `stale`

### Requirement: Deviation episodes

A deviation episode SHALL open only when each of the last `k_consecutive` days has a reading scored `above` or `below` in a concerning direction, all in the same direction; its `opened_on` SHALL be the first of those days. An open episode SHALL close only when the latest day is scored and is not a concerning deviation in the episode's direction. A latest day that is missing, unmeasurable, stale or without sufficient history SHALL hold the episode open. There SHALL be at most one open episode per metric, and `(metric_key, opened_on)` SHALL be unique.

#### Scenario: Three concerning days open one episode

- **WHEN** three consecutive days score `above` for a metric whose concern includes `above`
- **THEN** exactly one episode SHALL open with `opened_on` equal to the first of the three days

#### Scenario: A broken run opens nothing

- **WHEN** one of the three days is within the band, missing, or deviates against the metric's concern
- **THEN** no episode SHALL open

#### Scenario: Silence does not close an episode

- **WHEN** the latest day has no reading or its instrument is unmeasurable
- **THEN** the open episode SHALL remain open

#### Scenario: Concurrent runs open one episode

- **WHEN** a catch-up run and the scheduled run decide to open the same episode concurrently
- **THEN** one episode SHALL exist and the second decision SHALL change nothing

### Requirement: Baselines are stored in the owning butler's schema

`metric_baselines` and `metric_deviation_episodes` SHALL be per-butler core tables created in each butler's own schema and SHALL NOT exist in `public`. A butler job SHALL read and write only its own schema's baselines. DML SHALL be granted only to the runtime role of the schema that owns the table. An upsert with an unchanged `input_digest` SHALL change nothing.

#### Scenario: Health baselines are not visible to another butler

- **WHEN** the core migration runs for the health and general schemas
- **THEN** each schema SHALL have its own tables, `public` SHALL have none, and the Finance runtime role SHALL hold no privilege on `health.metric_baselines`

### Requirement: Health baseline watch

The Health butler SHALL run a deterministic daily `baseline_watch` job, with no LLM, for `resting_hr`, `hrv` and `sleep_duration`. Per-day values SHALL be taken in the owner's timezone: the latest reading of the day for resting heart rate and heart rate variability, and the total of sessions ending on the day for sleep. Measurability SHALL come from the shared expected-signal evaluation of the metric's producer. An opened episode SHALL be reconciled into the owner-condition ledger under source `health:baseline-deviation` and proposed as one insight candidate in category `baseline-deviation` with `baseline_evidence`, an `owner_condition` premise and a dedup key unique to the episode. The candidate wording SHALL state the deviation from the owner's own range and the days it rests on, and SHALL NOT interpret it or advise. The job SHALL NOT log measured values.

#### Scenario: Elevated days open one episode and one candidate

- **WHEN** 60 days of resting heart rate with 54 observed and a healthy connector are followed by three elevated days
- **THEN** one episode SHALL open, one candidate SHALL be accepted carrying `baseline_evidence.n_observed = 54` and an `owner_condition` premise, and the owner-condition SHALL be open

#### Scenario: A rerun adds nothing

- **WHEN** the job runs again with unchanged inputs
- **THEN** no baseline row, episode or candidate SHALL be written

#### Scenario: A dead connector is silent and named

- **WHEN** the connector behind the readings is offline
- **THEN** no candidate SHALL be proposed and the baseline SHALL be `unmeasurable`

#### Scenario: Return to band resolves without a message

- **WHEN** the latest day is scored back inside the band
- **THEN** the episode SHALL close, the owner-condition SHALL resolve, and no new message SHALL be sent for the closure
