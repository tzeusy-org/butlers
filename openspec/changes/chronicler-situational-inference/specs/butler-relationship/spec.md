## ADDED Requirements

### Requirement: Owner Anchor Address Geocoding

Relationship SHALL convert the owner's anchor addresses into coordinates with a deterministic job, storing one geocode per `has-address` fact in `relationship.address_geocodes`. Anchor addresses are the owner's own `has-address` facts, the addresses of entities the owner `works-at`, and the addresses of places the owner `rental-location`s or `lives-at`. No other address and no location trail SHALL be sent to a geocoder.

ID: REQ-butler-relationship-006
Source: bu-r3fmja; owner decisions 2026-11-03 (work address converted to lat/lon; OneMap for Singapore, Nominatim otherwise)
Scope: v1-mandatory

#### Scenario: Singapore address goes to OneMap
- **WHEN** an anchor address names Singapore, or carries a 6-digit postal code and no other country
- **THEN** the job SHALL query OneMap search, using the optional `ONEMAP_EMAIL`/`ONEMAP_PASSWORD` credentials when present and unauthenticated search otherwise
- **AND** on an empty OneMap result it SHALL fall back to Nominatim

#### Scenario: Other address goes to Nominatim
- **WHEN** an anchor address is not in Singapore
- **THEN** the job SHALL query Nominatim with an identifying User-Agent and at most one request per second

#### Scenario: Geocode is keyed to the address text
- **WHEN** a geocode is stored
- **THEN** its row SHALL carry the `has-address` fact id, the hex SHA-256 of the geocoded address text with surrounding whitespace stripped (UTF-8), a status of `resolved`, `not_found` or `failed`, the provider, and coordinates at no more than five decimals when resolved
- **AND** an address whose text changes SHALL be geocoded again on the next run

#### Scenario: Failures back off
- **WHEN** a geocode is `not_found` or `failed`
- **THEN** the next attempt SHALL wait 1 hour, then 6 hours, then 24 hours, doubling up to 7 days

#### Scenario: Nothing private is logged
- **WHEN** the job runs
- **THEN** no address text or coordinate SHALL appear in a log line, session, prompt or MCP response
- **AND** only `butler_chronicler_rw` SHALL be granted read access to `relationship.address_geocodes` beyond Relationship itself
