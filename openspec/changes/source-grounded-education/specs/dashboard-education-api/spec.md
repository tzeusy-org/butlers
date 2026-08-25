## ADDED Requirements

### Requirement: Source material registry endpoint

The dashboard API SHALL expose `GET /api/education/sources`, returning every record in the
education butler's source-material registry (`state` keys under `education/source/`) as a JSON
array of `{source_id, title, authors, type, url, registered_at}`. `url` and `registered_at` are
null when the record does not carry them; no field is inferred or defaulted to a plausible value.

The endpoint SHALL return `503` when the education butler's database pool is unavailable, rather
than an empty array: a caller resolving node `source_refs` against this list must be able to tell
"this source is not registered" from "the registry could not be read", and an empty array on
failure would silently convert every reference into a dangling one.

Because the registry holds metadata only, the endpoint SHALL NOT fetch, parse, or return source
contents.

#### Scenario: Listing registered sources

- **WHEN** a client requests `GET /api/education/sources`
- **THEN** the response is `200` with one entry per registered source, carrying its `source_id`,
  title, authors, type, URL, and registration timestamp

#### Scenario: Empty registry

- **WHEN** no source material has been registered
- **THEN** the response is `200` with an empty array

#### Scenario: Education database unavailable

- **WHEN** the education butler's database pool is not available
- **THEN** the response is `503`
- **AND** the client treats every unresolved `source_id` as unchecked rather than unregistered
