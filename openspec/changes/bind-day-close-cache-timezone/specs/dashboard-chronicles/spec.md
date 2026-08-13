## MODIFIED Requirements

### Requirement: Day-Close Cache Invalidation

Cached `chronicler_day_close` Tier-2 prose SHALL be identified by the selected
local `(date, timezone)` tuple, invalidated, and visually flagged stale whenever
any episode, point event, or override in that tuple's cached window changes
after the cache was built.

#### Scenario: Cache stale on tombstone

- **WHEN** any row in `chronicler.episodes` or
  `chronicler.point_events` within the cached window has
  `tombstone_at > cache_built_at`
- **THEN** the cache entry SHALL be reported stale
- **AND** the page SHALL render the "Summary out of date — last
  refreshed YYYY-MM-DD" affordance instead of the cached prose

#### Scenario: Cache stale on update

- **WHEN** any row in `chronicler.episodes` or
  `chronicler.point_events` within the cached window has `updated_at
  > cache_built_at`
- **THEN** the cache entry SHALL be reported stale

#### Scenario: Cache stale on override

- **WHEN** any row in `chronicler.overrides` whose target falls in the
  cached window has `created_at > cache_built_at`
- **THEN** the cache entry SHALL be reported stale
- **AND** this rule SHALL apply even if `episodes.updated_at` is
  unchanged (because precision-reduction or correction landed on the
  override row)

#### Scenario: Client cache identity includes timezone

- **WHEN** the dashboard addresses a selected local day through the day-close
  cache client or its query key
- **THEN** it SHALL include the exact owner IANA timezone in the HTTP request
  and cache/query identity
- **AND** the same ISO date in two different timezones SHALL not reuse a
  client cache result
- **AND** a date-only legacy cache response SHALL not be requested as a
  compatibility fallback

#### Scenario: User-clicked refresh re-invokes existing path

- **WHEN** the user clicks the "regenerate" affordance on a stale
  cache entry
- **THEN** the page SHALL POST to a re-invocation endpoint that re-runs
  the existing scheduled `chronicler_day_close` Tier-2 entry point for the
  selected `(date, timezone)` tuple
- **AND** the POST body SHALL carry that exact selected `{date, tz}` pair
- **AND** a successful response SHALL re-fetch the same selected briefing
  tuple, while a failure leaves the stale state visible and reports the failed
  regeneration without substituting prose
- **AND** the re-invocation SHALL be rate-limited to 1 per day per
  tuple window
- **AND** no new LLM call path SHALL be introduced
