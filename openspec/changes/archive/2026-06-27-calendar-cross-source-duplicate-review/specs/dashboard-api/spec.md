## ADDED Requirements

### Requirement: Calendar Duplicate-Cluster Review

The dashboard API SHALL expose `GET /api/calendar/workspace/duplicates`, a
read-only surface that exposes the cross-source duplicate clusters the workspace
read-model collapses. For a `view` + `start` + `end` range it SHALL re-run the
same two-pass dedup over the un-collapsed workspace rows and return every cluster
of more than one member the dedup would collapse: the kept survivor (lowest
keyset), the collapsed-away `duplicate_entries`, the `match_pass`
(`origin_ref` | `title`) that grouped them, the `member_count`, and a
`keep_separate` flag. Clusters with fewer members than the active
`noisy_threshold` SHALL be omitted. The endpoint MUST be fail-open: any read
failure SHALL yield HTTP 200 with `available=false` and an empty `clusters` list,
never an HTTP 500. It MUST perform no provider write and MUST NOT spawn an LLM
session.

#### Scenario: Collapsed cluster exposed

- **WHEN** the same event is synced into multiple butler schemas (identical
  `origin_ref` + start) and `GET /api/calendar/workspace/duplicates` is called
  for a range covering it
- **THEN** the response contains one cluster with `match_pass="origin_ref"`,
  `member_count` equal to the number of copies, a `kept_entry`, and the remaining
  copies as `duplicate_entries`, and `available=true`

#### Scenario: Below-threshold clusters omitted

- **WHEN** a cluster's member count is less than the active `noisy_threshold`
- **THEN** that cluster is not included in the returned `clusters` list

#### Scenario: Fail-open on read failure

- **WHEN** the underlying workspace read fails
- **THEN** the endpoint returns HTTP 200 with `available=false` and an empty
  `clusters` list rather than an error

#### Scenario: Invalid range rejected

- **WHEN** `end` is not after `start`, or the range exceeds the 90-day maximum
- **THEN** the endpoint returns HTTP 400; a request missing the required
  `start`/`end` parameters returns HTTP 422

### Requirement: Calendar Dedup Rules

The dashboard API SHALL expose `PATCH /api/calendar/workspace/dedup-rules` to
persist the workspace-global cross-source dedup rules: a `match_strategy` of
`exact` (origin-ref identity pass only), `balanced` (origin-ref + title/start
collapse; the default), or `aggressive` (as `balanced` but normalising titles by
stripping non-alphanumerics), and a `noisy_threshold` (minimum cluster size for
the review surface to report a cluster, at least 2). Omitted fields SHALL be left
unchanged. An unknown `match_strategy` SHALL be rejected without persisting. The
live workspace read SHALL honor the persisted rules so that changing the strategy
changes what the read collapses. The rules SHALL persist across requests.

#### Scenario: Strategy and threshold persisted

- **WHEN** `PATCH /api/calendar/workspace/dedup-rules` is called with a valid
  `match_strategy` and `noisy_threshold`
- **THEN** the endpoint returns HTTP 200 with the new rules and a subsequent read
  of the rules returns the persisted values

#### Scenario: Unknown strategy rejected

- **WHEN** `PATCH /api/calendar/workspace/dedup-rules` is called with a
  `match_strategy` outside `exact`/`balanced`/`aggressive`
- **THEN** the endpoint rejects the request (HTTP 400 or 422) and does not change
  the persisted rules

### Requirement: Calendar Keep-Separate Override

The dashboard API SHALL expose `POST /api/calendar/workspace/duplicates/keep-separate`
to pin or unpin a duplicate cluster (identified by its `cluster_key`) so the
dedup does not collapse it. When pinned (`keep_separate=true`) the workspace read
SHALL keep all members of that cluster as distinct entries, and the review
surface SHALL still report the cluster with its `keep_separate` flag set. When
unpinned (`keep_separate=false`) the override SHALL be removed and the cluster
SHALL collapse again under the active rules. Overrides SHALL persist across
requests.

#### Scenario: Pinned cluster is not collapsed

- **WHEN** a cluster is pinned via `POST /api/calendar/workspace/duplicates/keep-separate`
  with `keep_separate=true`
- **THEN** the workspace read keeps every member of that cluster as a distinct
  entry, and the duplicates surface still lists the cluster with
  `keep_separate=true`

#### Scenario: Unpin restores collapse

- **WHEN** a previously-pinned cluster is unpinned with `keep_separate=false`
- **THEN** the override is removed and the cluster collapses again under the
  active dedup rules
