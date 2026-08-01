# Chronicler API — Spec Delta for distinguish-invalid-day-close-refresh

## MODIFIED Requirements

### Requirement: Chronicler Day-Close Cache Surface

The API SHALL expose a read endpoint for cached `chronicler_day_close` Tier-2
prose and a re-invocation endpoint that triggers the existing scheduled day-close
path on demand. The read endpoint SHALL validate cache admission before it
evaluates staleness, SHALL detect staleness against the canonical
`chronicler.episodes`, `chronicler.point_events`, and `chronicler.overrides`
rows in the cached window, and SHALL expose an explicit invalid-without-prose
state. The re-invocation endpoint SHALL be rate-limited and SHALL NOT introduce
a new LLM call path beyond the one already declared in RFC 0014 §D5. Its
successful response SHALL expose a deterministic invalid generated-candidate
outcome without exposing candidate prose or provenance.

The endpoints SHALL be:

- `GET /api/chronicler/aggregate/day-close?date=YYYY-MM-DD&tz=...`
- `POST /api/chronicler/aggregate/day-close/refresh` (body: `{date, tz}`)

#### Scenario: Admissible cache hit returns prose with provenance

- **WHEN** a client requests `GET /api/chronicler/aggregate/day-close` for a
  `(date, tz)` whose cache entry is fresh, is admissible human-facing
  retrospective prose, and is bound to that closed local day
- **THEN** the API SHALL return the cached `prose` text plus
  `provenance_refs` (the source-ref tuples cited by the prose) and
  `cache_built_at`
- **AND** no LLM SHALL be invoked

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

#### Scenario: Contained invalid refresh candidate remains distinguishable

- **WHEN** a refresh generates an invalid candidate while an admissible active
  cache row exists for the requested `(date, tz)`
- **THEN** the API SHALL return `200` with `{cache_key, cache_built_at,
  invalid: true, invalid_reason}` where `cache_built_at` belongs to the
  preserved admissible row
- **AND** `invalid_reason` SHALL be the writer's deterministic
  `inadmissible_prose` or `date_mismatch` result
- **AND** the response SHALL NOT contain `prose` or `provenance_refs`

#### Scenario: Audit-only invalid refresh candidate has no prose response

- **WHEN** a refresh generates an invalid candidate and no admissible active
  cache row exists for the requested `(date, tz)`
- **THEN** the API SHALL retain the invalid candidate only for audit/recovery
  and return `200` with `{cache_key, cache_built_at, invalid: true,
  invalid_reason}`
- **AND** the response SHALL NOT contain `prose` or `provenance_refs`

#### Scenario: Refresh rate limit enforced

- **WHEN** a client POSTs the refresh endpoint for a `(date, tz)` that has
  already been refreshed within the last 24 hours by any caller
- **THEN** the API SHALL respond `429 Too Many Requests` with `code:
  day_close_rate_limited`
- **AND** the response SHALL match the existing `ErrorResponse` envelope
  (`{ error: { code, message, butler, details } }`) with
  `retry_after_seconds` carried inside `details`
- **AND** no Tier-2 invocation SHALL occur
