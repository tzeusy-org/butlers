## ADDED Requirements

### Requirement: Source material registry endpoint

The dashboard API SHALL expose `GET /api/education/sources`, resolving a caller-supplied,
comma-separated `source_ids` query parameter against the education butler's source-material
registry (`state` keys under `education/source/`) and returning a JSON array of
`{source_id, title, authors, type, url, registered_at}` entries, one per requested ID that the
registry still contains. `url` and `registered_at` are null when the record does not carry them;
no field is inferred or defaulted to a plausible value. The registry is never fetched in full: the
endpoint only ever resolves the IDs it is asked about, so a caller passes the `source_id`s present
on the node it is rendering. The `source_ids` parameter is required; the endpoint SHALL return
`422` when it is missing or empty. A requested ID absent from the registry (its source was
removed) is simply left out of the response rather than raised as an error. The endpoint SHALL
return `503` when the education butler's database pool is unavailable, rather than an empty array:
a caller resolving node `source_refs` against this list must be able to tell "this source is not
registered" from "the registry could not be read", and an empty array on failure would silently
convert every reference into a dangling one.
Because the registry holds metadata only, the endpoint SHALL NOT fetch, parse, or return source
contents.

ID: REQ-dashboard-education-api-001
Source: source-grounded-education design.md; REQ-education-source-grounding-002
Scope: v1-mandatory

#### Scenario: Resolving requested sources

- **WHEN** a client requests `GET /api/education/sources?source_ids=a,b`
- **AND** both `a` and `b` are registered
- **THEN** the response is `200` with one entry per requested ID, carrying its `source_id`,
  title, authors, type, URL, and registration timestamp

#### Scenario: Requested source no longer registered

- **WHEN** a client requests `GET /api/education/sources?source_ids=a,b`
- **AND** `b` is not present in the registry
- **THEN** the response is `200` with only the entry for `a`

#### Scenario: Missing source_ids parameter

- **WHEN** a client requests `GET /api/education/sources` with no `source_ids` parameter (or an
  empty one)
- **THEN** the response is `422`

#### Scenario: Education database unavailable

- **WHEN** the education butler's database pool is not available
- **THEN** the response is `503`
- **AND** the client treats every unresolved `source_id` as unchecked rather than unregistered
