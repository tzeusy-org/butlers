## MODIFIED Requirements

### Requirement: Chronicler Day-Close Cache Surface

The API SHALL expose a read endpoint for cached `chronicler_day_close` Tier-2
prose and a re-invocation endpoint that triggers the existing scheduled day-close
path on demand. The read endpoint SHALL validate cache admission before it
evaluates staleness, SHALL detect staleness against the canonical
`chronicler.episodes`, `chronicler.point_events`, and `chronicler.overrides`
rows in the cached window, and SHALL expose an explicit invalid-without-prose
state. The re-invocation endpoint SHALL be rate-limited and SHALL NOT introduce
a new LLM call path beyond the one already declared in RFC 0014 §D5. A manual
refresh SHALL target only a settled historical local day in its supplied IANA
timezone, and a successful executed empty bundle SHALL remain distinct from a
persisted cache response.

The endpoints SHALL be:

- `GET /api/chronicler/aggregate/day-close?date=YYYY-MM-DD&tz=IANA-name`
- `POST /api/chronicler/aggregate/day-close/refresh` (body: `{date, tz}`)

Both endpoints SHALL require a non-empty IANA `tz` that `zoneinfo.ZoneInfo`
resolves. They SHALL use the exact accepted `(date, tz)` tuple as their cache
identity: `day_close:{YYYY-MM-DD}:tz:{IANA-name}`. The tuple key SHALL be used
for cache read/write, staleness provenance lookup,
refresh rate limiting, and refresh response lookup. Neither endpoint SHALL
default, canonicalize, or substitute a timezone for a missing value.

The writer SHALL serialize its exact `(date, tz)` tuple through a
collision-safe transaction lock backed by the actual tuple values, not a
fixed-width advisory-lock hash. The public OpenAPI contract SHALL describe
`tz` as a required, non-nullable non-empty string on both endpoints, while an
omitted or null runtime value remains eligible for the structured `400`
validation envelope below.

#### Scenario: Admissible cache hit returns prose with provenance

- **WHEN** a client requests `GET /api/chronicler/aggregate/day-close` for a
  `(date, tz)` whose cache entry is fresh, is admissible human-facing
  retrospective prose, and is bound to that closed local day
- **THEN** the API SHALL return the cached `prose` text plus
  `provenance_refs` (the source-ref tuples cited by the prose) and
  `cache_built_at`
- **AND** no LLM SHALL be invoked

#### Scenario: Missing or invalid timezone fails before cache work

- **WHEN** a day-close GET omits `tz`, or either day-close endpoint receives an
  empty or unresolvable IANA timezone
- **THEN** the API SHALL reject the request with a structured `400` error whose
  code is `missing_parameter` or `invalid_timezone`
- **AND** it SHALL not query `tier2_cache`, acquire a cache lock, apply a rate
  limit, or dispatch a Tier-2 invocation

#### Scenario: Same date is isolated by exact timezone

- **WHEN** two valid cache entries address the same ISO date with different
  IANA timezone strings
- **THEN** each entry SHALL have a distinct tuple key and local-day window
- **AND** a GET, refresh rate limit, writer lock, staleness provenance lookup,
  or refresh response for one tuple SHALL not select or block the other
- **AND** the writer lock SHALL retain the exact date and timezone values rather
  than relying on a collision-prone fixed-width hash

#### Scenario: Legacy date-only cache is a miss

- **WHEN** only a legacy `day_close:{YYYY-MM-DD}` row exists for a requested
  date and timezone
- **THEN** the tuple-keyed GET SHALL return the existing `404` cache-miss
  behavior
- **AND** the endpoint SHALL not rewrite, delete, relabel, or return the
  legacy row

#### Scenario: Invalid cache has an explicit no-prose response

- **WHEN** a requested cache row fails deterministic prose admission or its
  structured `date_label` binding does not match the request
- **THEN** the API SHALL respond successfully with
  `{invalid: true, invalid_reason, cache_built_at}` where `invalid_reason` is
  `inadmissible_prose` or `date_mismatch`
- **AND** a serialized JSON or Python-literal container, including a
  whitespace-formatted empty-set literal, or an assignment-form
  tool/function-call payload, SHALL be classified as `inadmissible_prose`
- **AND** the response SHALL NOT contain `prose` or `provenance_refs`
- **AND** the row SHALL NOT be relabeled as fresh or stale
- **AND** no LLM SHALL be invoked

#### Scenario: Cache miss remains distinct from invalid content

- **WHEN** no non-superseded cache row exists for the requested `(date, tz)`
- **THEN** the API SHALL return `404`
- **AND** it SHALL NOT manufacture an invalid marker for an absent row
- **AND** no LLM SHALL be invoked

#### Scenario: Valid stale cache surfaces stale marker

- **WHEN** a requested cache row is admissible and any episode, point event, or
  override in the cached window has been tombstoned, updated, or created with a
  timestamp greater than the cache's `cache_built_at`
- **THEN** the API SHALL respond with `{stale: true, cache_built_at,
  last_invalidating_event_at}` and SHALL NOT return the cached prose
- **AND** `last_invalidating_event_at` SHALL be the maximum of the qualifying
  timestamps across all invalidators within the window
- **AND** no LLM SHALL be invoked

#### Scenario: Stale due to override creation

- **WHEN** the only invalidating change in the window is an override row whose
  `created_at > cache_built_at` (with the underlying episode's `updated_at`
  unchanged because precision-reduction or correction landed on the override
  row)
- **THEN** the cache SHALL still be reported stale
- **AND** the response SHALL set `last_invalidating_event_at` to the override's
  `created_at`

#### Scenario: User-clicked refresh re-invokes existing path

- **WHEN** a client POSTs to `/api/chronicler/aggregate/day-close/refresh` for
  a `(date, tz)`
- **THEN** the API SHALL re-invoke the existing scheduled
  `chronicler_day_close` Tier-2 entry point (RFC 0014 §D5) and, on a successful
  admissible result, write a new cache entry with a fresh `cache_built_at`
- **AND** an invalid candidate SHALL NOT replace a renderable cache entry
- **AND** the API SHALL NOT introduce a new LLM call path; the invocation MUST
  go through the same Tier-2 token-bound input path the cron-driven schedule
  uses

#### Scenario: Unsettled refresh target is rejected before rate-limit or dispatch

- **WHEN** a client POSTs the refresh endpoint for today or a future date in
  the supplied request timezone
- **THEN** the API SHALL respond `400 Bad Request` with `code:
  day_close_not_settled`
- **AND** it SHALL perform no rate-limit cache lookup and no Tier-2 dispatch
- **AND** a date strictly before that request-local current date SHALL remain
  eligible for the existing rate-limit and dispatch path

#### Scenario: Executed empty bundle returns a distinct quiet success

- **WHEN** the re-invoked day-close path produces blank prose and exactly one
  successful canonical executed `chronicler_day_close_bundle` capture binds
  its input `date_label` and `timezone`, and echoed result `date`, to the
  requested local target
- **AND** that canonical result explicitly has empty `episodes` and `events`
- **THEN** the API SHALL return `200 OK` with `{cache_key, quiet: true}`
- **AND** it SHALL not write a prose cache row or include `cache_built_at`
- **AND** it SHALL not fetch or return an older cache row as this refresh's
  outcome

#### Scenario: Unproven blank refresh result remains a write failure

- **WHEN** a refresh result has blank prose but its canonical bundle capture
  is missing, malformed, mismatched, has duplicate outcome-bearing execution
  captures, or has non-empty `episodes` or `events`
- **THEN** the API SHALL respond `502 Bad Gateway` with `code:
  cache_write_failed`
- **AND** it SHALL not reuse an older cache row as the refresh result

#### Scenario: Refresh rate limit enforced

- **WHEN** a client POSTs the refresh endpoint for a `(date, tz)` that has
  already been refreshed within the last 24 hours by any caller
- **THEN** the API SHALL respond `429 Too Many Requests` with `code:
  day_close_rate_limited`
- **AND** the response SHALL match the existing `ErrorResponse` envelope
  (`{ error: { code, message, butler, details } }`) with
  `retry_after_seconds` carried inside `details`
- **AND** no Tier-2 invocation SHALL occur
