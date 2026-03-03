# Education Butler — Learning Analytics

## Purpose

Defines the learning analytics subsystem for the education butler: nightly snapshot computation, metrics calculation (mastery, retention, velocity, completion estimates, time-of-day distribution), trend retrieval, cross-topic comparison, weekly progress digest, and feedback loop triggers for automatic curriculum re-planning.

## ADDED Requirements

---

### Requirement: Nightly Snapshot Computation

The system SHALL compute one analytics snapshot per active mind map per calendar day via a scheduled job
dispatched with `dispatch_mode="job"` and `job_name="compute_analytics_snapshots"`. The job SHALL invoke
`analytics_compute_all(pool)` and MUST persist the resulting snapshot records to the
`analytics_snapshots` table before the job is considered complete. An "active" mind map is any mind map
that has at least one associated quiz response recorded in the last 90 days or at least one unmastered
node.

#### Scenario: Nightly job runs and computes snapshots for all active mind maps

WHEN the nightly scheduler fires the `compute_analytics_snapshots` job
THEN `analytics_compute_all(pool)` is called
AND one snapshot is inserted into `analytics_snapshots` for each active mind map using today's date as
`snapshot_date`
AND the function returns the count of snapshots successfully computed
AND no snapshot is created for mind maps with no quiz history in the last 90 days and no unmastered nodes

#### Scenario: Nightly job is idempotent when run more than once on the same day

WHEN `analytics_compute_all(pool)` is called a second time on the same calendar day
THEN existing snapshots for that date are overwritten via upsert (ON CONFLICT on `(mind_map_id,
snapshot_date)`)
AND the return count still reflects the number of active mind maps processed
AND no duplicate rows are created

#### Scenario: Nightly job handles zero active mind maps gracefully

WHEN `analytics_compute_all(pool)` is called and there are no active mind maps
THEN no rows are inserted into `analytics_snapshots`
AND the function returns 0

---

### Requirement: Metrics Calculation Correctness

Each analytics snapshot MUST populate all fields of the `metrics` JSONB object according to their
defined formulas. The stored object SHALL include `total_nodes`, `mastered_nodes`, `mastery_pct`,
`avg_ease_factor`, `retention_rate_7d`, `retention_rate_30d`, `velocity_nodes_per_week`,
`estimated_completion_days`, `struggling_nodes`, `strongest_subtree`, `total_quiz_responses`,
`avg_quality_score`, `sessions_this_period`, and `time_of_day_distribution`.

#### Scenario: Snapshot metrics reflect current node counts and mastery

WHEN `analytics_compute_snapshot(pool, mind_map_id, snapshot_date)` is called for a mind map with 25
nodes of which 12 have status `mastered`
THEN the stored `metrics.total_nodes` equals 25
AND `metrics.mastered_nodes` equals 12
AND `metrics.mastery_pct` equals 0.48 (rounded to 2 decimal places)

#### Scenario: avg_ease_factor is the mean of all node ease factors in the map

WHEN the mind map has nodes with ease factors `[2.0, 2.5, 2.4]`
THEN `metrics.avg_ease_factor` equals 2.3 (rounded to 2 decimal places)

#### Scenario: avg_quality_score reflects the mean quality across all quiz responses for this map

WHEN the mind map has accumulated quiz responses with quality scores totalling 330.6 across 87 responses
THEN `metrics.total_quiz_responses` equals 87
AND `metrics.avg_quality_score` equals 3.8 (rounded to 1 decimal place)

#### Scenario: estimated_completion_days is null when velocity is zero

WHEN `velocity_nodes_per_week` is 0 (no nodes mastered in the last 4 weeks)
THEN `metrics.estimated_completion_days` is `null` (division by zero is avoided)

---

### Requirement: Retention Rate Computation

The system SHALL compute `retention_rate_7d` and `retention_rate_30d` using only quiz responses where
`response_type = 'review'`. Each rate is the ratio of responses with `quality >= 3` (successful recall)
to total review responses within the respective time window (7 days and 30 days prior to
`snapshot_date`). Diagnostic and teach response types MUST be excluded from this calculation.

#### Scenario: retention_rate_7d counts only review responses in the last 7 days

WHEN a mind map has 10 review responses in the last 7 days of which 8 have quality >= 3, plus 5
diagnostic responses in the same window
THEN `metrics.retention_rate_7d` equals 0.80
AND the 5 diagnostic responses are not counted in numerator or denominator

#### Scenario: retention_rate_30d uses a 30-day window independent of the 7-day window

WHEN a mind map has 50 review responses in the last 30 days of which 32 have quality >= 3
THEN `metrics.retention_rate_30d` equals 0.64
AND responses older than 30 days are excluded

#### Scenario: retention rate is null when no review responses exist in the window

WHEN there are no `response_type = 'review'` responses for a mind map within the 7-day window
THEN `metrics.retention_rate_7d` is `null`

#### Scenario: teach responses are excluded even if quality >= 3

WHEN all responses in the 7-day window have `response_type = 'teach'` and quality >= 3
THEN `metrics.retention_rate_7d` is `null` (no review responses in window)

---

### Requirement: Learning Velocity Calculation

The system SHALL compute `velocity_nodes_per_week` as the average number of nodes that transitioned to
`mastered` status per week over the last 4 calendar weeks prior to `snapshot_date`. Each week is a
7-day bucket. The average MUST be computed across all 4 weeks, including weeks with zero mastered-node
transitions.

#### Scenario: velocity is the 4-week average of mastered-node transitions

WHEN the last 4 weeks saw 5, 3, 4, and 2 nodes reach `mastered` status respectively
THEN `metrics.velocity_nodes_per_week` equals 3.5

#### Scenario: weeks with no newly mastered nodes contribute zero to the average

WHEN the last 4 weeks saw 0, 0, 7, and 1 nodes reach mastered status
THEN `metrics.velocity_nodes_per_week` equals 2.0

#### Scenario: velocity is 0.0 when no nodes were mastered in any of the last 4 weeks

WHEN no nodes transitioned to mastered status in the last 28 days
THEN `metrics.velocity_nodes_per_week` equals 0.0

---

### Requirement: Estimated Completion Days

The system SHALL compute `estimated_completion_days` as `ceil((total_nodes - mastered_nodes) /
velocity_nodes_per_week * 7)`. The field MUST be `null` when `velocity_nodes_per_week` is 0 or all
nodes are already mastered.

#### Scenario: completion estimate is derived from unmastered nodes and velocity

WHEN a mind map has 25 total nodes, 11 mastered, and velocity of 2.0 nodes per week
THEN remaining unmastered nodes = 14
AND `metrics.estimated_completion_days` equals 49 (ceil(14 / 2.0 * 7))

#### Scenario: completion estimate is null when all nodes are mastered

WHEN `mastered_nodes` equals `total_nodes`
THEN `metrics.estimated_completion_days` is `null`

---

### Requirement: Time-of-Day Distribution Bucketing

The system SHALL bucket quiz response timestamps (from the `responded_at` field) into three named
periods: `morning` (06:00–11:59 local time or UTC if timezone is not configured), `afternoon`
(12:00–17:59), and `evening` (18:00–05:59 spanning midnight). The `time_of_day_distribution` JSONB
field SHALL be a map of `{"morning": <int>, "afternoon": <int>, "evening": <int>}` counting the number
of quiz sessions (grouped by date) falling in each bucket, covering the same period as
`sessions_this_period`.

#### Scenario: responses are bucketed into the correct time-of-day period

WHEN quiz responses were submitted at 08:30, 14:15, 21:00, and 07:00 on various days
THEN `time_of_day_distribution.morning` counts the 08:30 and 07:00 sessions
AND `time_of_day_distribution.afternoon` counts the 14:15 session
AND `time_of_day_distribution.evening` counts the 21:00 session

#### Scenario: midnight-crossing sessions are classified as evening

WHEN a response is submitted at 02:30
THEN it is counted in `time_of_day_distribution.evening`

#### Scenario: all buckets are present in the output even when count is zero

WHEN all quiz responses occur in the morning
THEN `time_of_day_distribution` still contains `"afternoon": 0` and `"evening": 0`

---

### Requirement: Snapshot Uniqueness and Upsert

The `analytics_snapshots` table SHALL enforce uniqueness on `(mind_map_id, snapshot_date)` via a
unique index. The `analytics_compute_snapshot` function MUST use an upsert strategy (INSERT … ON
CONFLICT DO UPDATE) so that re-running the job for the same day replaces the prior metrics with the
freshly computed values rather than raising a constraint violation.

#### Scenario: inserting a snapshot for a new (mind_map_id, date) pair succeeds

WHEN `analytics_compute_snapshot(pool, mind_map_id, snapshot_date)` is called for a combination that
does not yet exist in `analytics_snapshots`
THEN a new row is inserted and its UUID is returned

#### Scenario: inserting a snapshot for an existing (mind_map_id, date) pair upserts

WHEN `analytics_compute_snapshot(pool, mind_map_id, snapshot_date)` is called a second time for the
same mind_map_id and snapshot_date
THEN the existing row's `metrics` column is overwritten with the newly computed values
AND no duplicate row is created
AND no constraint violation error is raised
AND the same (or updated) UUID is returned

#### Scenario: snapshots for different dates on the same mind map coexist

WHEN snapshots are computed on Monday and Tuesday for the same mind_map_id
THEN two distinct rows exist in `analytics_snapshots` with different `snapshot_date` values

---

### Requirement: Trend Retrieval

The system SHALL provide `analytics_get_trend(pool, mind_map_id, days=30)` which returns an ordered
list of snapshot records (ascending by `snapshot_date`) covering the last `days` calendar days.
Missing dates (days where no job ran or was skipped) SHALL be absent from the list rather than
represented as null-filled records. The list MAY be empty if no snapshots exist in the window.

#### Scenario: trend returns snapshots in ascending date order

WHEN five snapshots exist for a mind map across five consecutive days
AND `analytics_get_trend(pool, mind_map_id, days=7)` is called
THEN the returned list contains exactly 5 entries
AND they are ordered from oldest to newest by `snapshot_date`

#### Scenario: trend respects the days window boundary

WHEN snapshots exist for 45 consecutive days
AND `analytics_get_trend(pool, mind_map_id, days=30)` is called
THEN only the 30 most recent snapshots are returned
AND snapshots older than 30 days ago are excluded

#### Scenario: trend returns an empty list when no snapshots exist in the window

WHEN a mind map has no snapshots in the last 30 days
THEN `analytics_get_trend(pool, mind_map_id, days=30)` returns an empty list

#### Scenario: get_snapshot returns the most recent snapshot when no date is specified

WHEN `analytics_get_snapshot(pool, mind_map_id)` is called without a date argument
THEN the snapshot with the highest `snapshot_date` for that mind map is returned

#### Scenario: get_snapshot returns the snapshot for a specific date

WHEN `analytics_get_snapshot(pool, mind_map_id, date="2025-01-15")` is called
AND a snapshot exists for that date
THEN the corresponding row is returned

#### Scenario: get_snapshot returns None when no snapshot exists for the requested date

WHEN `analytics_get_snapshot(pool, mind_map_id, date="2025-01-15")` is called
AND no snapshot exists for that date
THEN `None` is returned

---

### Requirement: Cross-Topic Comparison

The system SHALL provide `analytics_get_cross_topic(pool)` which returns a dictionary containing the
latest snapshot metrics for every active mind map, plus derived comparative statistics such as the
map with the highest mastery percentage, the map with the lowest retention rate, and the overall
portfolio-level mastery percentage.

#### Scenario: cross-topic result includes an entry for each active mind map

WHEN the user has 4 active mind maps each with at least one snapshot
THEN `analytics_get_cross_topic(pool)` returns a structure with 4 per-map entries
AND each entry includes at least `mind_map_id`, `mastery_pct`, `retention_rate_7d`, and
`velocity_nodes_per_week` from the latest snapshot

#### Scenario: cross-topic identifies the highest-mastery mind map

WHEN mastery_pct values are 0.90, 0.60, 0.45, and 0.75 across four maps
THEN the result designates the map with 0.90 mastery as `strongest_topic`

#### Scenario: cross-topic identifies the lowest-retention mind map

WHEN `retention_rate_7d` values are 0.80, 0.55, 0.90, and 0.70 across four maps
THEN the result designates the map with 0.55 retention as `weakest_topic`

#### Scenario: cross-topic computes portfolio-level mastery percentage

WHEN the four maps have total_nodes of 20, 30, 15, 35 and mastered_nodes of 10, 18, 9, 21
THEN portfolio mastery is (10+18+9+21) / (20+30+15+35) = 0.58

#### Scenario: cross-topic excludes mind maps that have no snapshots

WHEN one of five mind maps has never had a snapshot computed
THEN `analytics_get_cross_topic(pool)` returns entries for the 4 mind maps that have snapshots
AND the snapshot-less map is omitted

---

### Requirement: Weekly Progress Digest

The system SHALL send a weekly learning progress digest on Sunday mornings (cron: `"0 9 * * 0"`).
The digest MUST be dispatched as a scheduled prompt that reads the last 7 available snapshots per
active mind map, identifies trends (improving, stable, or declining retention and velocity), and
delivers a human-readable summary via `notify()` to the owner contact.

#### Scenario: digest is sent on Sunday at 09:00

WHEN the scheduler fires the weekly digest prompt at Sunday 09:00
THEN `analytics_get_trend(pool, mind_map_id, days=7)` is called for each active mind map
AND the results are summarised into a human-readable message
AND `notify()` is called with the digest content targeting the owner contact

#### Scenario: digest message includes retention trend direction

WHEN `retention_rate_7d` has increased over the last 3 snapshots for a map
THEN the digest message for that map includes a positive retention trend indicator

#### Scenario: digest message includes velocity trend direction

WHEN `velocity_nodes_per_week` has decreased over the last 3 snapshots for a map
THEN the digest message for that map flags declining learning velocity

#### Scenario: digest is skipped or notes insufficient data when no snapshots exist

WHEN an active mind map has fewer than 2 snapshots in the last 7 days
THEN the digest omits trend language for that map and notes insufficient data
AND `notify()` is still called if at least one map has sufficient data

#### Scenario: digest covers all active mind maps in one notification

WHEN three active mind maps have snapshots in the last 7 days
THEN a single `notify()` call is made containing a summary section for each of the three maps

---

### Requirement: Feedback Loop Trigger

The system SHALL inspect each freshly computed snapshot and trigger a call to `curriculum_replan()`
when either of the following thresholds is breached:
- `len(struggling_nodes) >= 3` (three or more struggling nodes), OR
- `retention_rate_7d < 0.60` (7-day retention rate falls below 60%).

`struggling_nodes` are nodes with `avg_quality_score < 2.5` across their last 5 review responses.
The trigger MUST fire at most once per mind map per day (i.e., during the nightly snapshot job, not
on demand) and MUST pass the mind_map_id and the snapshot metrics to `curriculum_replan()`.

#### Scenario: feedback loop fires when struggling_nodes count reaches threshold

WHEN a freshly computed snapshot for a mind map has `len(struggling_nodes) == 4`
THEN `curriculum_replan(mind_map_id, metrics)` is called for that mind map
AND the call is made during the same nightly job execution that computed the snapshot

#### Scenario: feedback loop fires when 7-day retention drops below 60%

WHEN a snapshot has `retention_rate_7d = 0.55` and `len(struggling_nodes) == 1`
THEN `curriculum_replan(mind_map_id, metrics)` is called
AND only one re-plan call is made (not one per condition)

#### Scenario: feedback loop does not fire when thresholds are not breached

WHEN a snapshot has `retention_rate_7d = 0.75` and `len(struggling_nodes) == 2`
THEN `curriculum_replan()` is NOT called for that mind map

#### Scenario: feedback loop fires at most once per mind map per nightly run

WHEN both conditions are met simultaneously (struggling_nodes >= 3 AND retention_rate_7d < 0.60)
THEN `curriculum_replan()` is called exactly once for that mind map

#### Scenario: struggling_nodes are identified by low average quality over last 5 review responses

WHEN a node's last 5 `response_type = 'review'` responses have quality scores [1, 2, 2, 1, 3]
(mean = 1.8, which is < 2.5)
THEN that node's UUID appears in `metrics.struggling_nodes`

#### Scenario: a node with insufficient review history is excluded from struggling_nodes

WHEN a node has fewer than 5 review responses on record
THEN it is NOT included in `metrics.struggling_nodes` (insufficient data to classify as struggling)
