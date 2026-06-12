# Butler Health — Provider-Dispatching Wellness Ingest Delta

## MODIFIED Requirements

### Requirement: Wellness envelope translation dispatches on source provider

The Health butler's wellness ingest SHALL dispatch translation on
`source.provider`. Envelopes with `provider = "google_health"` SHALL be
translated exactly as before this change (resource-segment parsing of
`external_event_id`, the existing resource→predicate table, and owner-account
sender validation per the `connector-google-health-multi-account` delta).
Envelopes with `provider = "home_assistant"` SHALL be translated from the
normalized `payload.raw.wellness_measurement` object. Envelopes with any other
provider SHALL be rejected with a labeled rejection metric and no fact written.

#### Scenario: Home Assistant measurement translated to a fact

- **WHEN** a wellness envelope arrives with `source.provider =
  "home_assistant"` and a well-formed `wellness_measurement` payload
  (`metric`, numeric `value`, `unit`, `valid_at`, `source_entity_id`)
- **THEN** the Health butler SHALL write exactly one fact with predicate
  `measurement_{metric}`, `scope = "health"`, `valid_at` from the payload,
  `entity_id` = the owner entity, and `metadata` containing at least
  `provider`, `source_entity_id`, `unit`, and the numeric `value`
- **AND** SHALL NOT spawn an LLM session to do so

#### Scenario: Malformed Home Assistant payload rejected

- **WHEN** a wellness envelope arrives with `source.provider =
  "home_assistant"` but `wellness_measurement` is missing, non-numeric, or
  lacks `metric`/`valid_at`
- **THEN** the Health butler SHALL reject the envelope without storing any fact
- **AND** SHALL increment a rejection metric labeled with the failure reason

#### Scenario: Sender validation is provider-appropriate

- **WHEN** a `home_assistant` wellness envelope arrives with
  `sender.identity` set to the source HA entity_id (a device identifier, not
  an owner Google account)
- **THEN** the Health butler SHALL NOT apply Google-account sender validation
  to it
- **AND** SHALL accept it on the basis of `source.provider =
  "home_assistant"` plus a well-formed `wellness_measurement` payload
- **AND** Google Health envelopes SHALL continue to validate against the
  owner's health-scoped Google accounts unchanged

## ADDED Requirements

### Requirement: Cross-provider measurement idempotency

Facts translated from `home_assistant` wellness envelopes SHALL carry an
explicit provider-agnostic idempotency key derived from
`(owner_entity_id, scope, predicate, valid_at)` — excluding provider and
source episode — so that the same physical reading delivered through multiple
providers or replays at the same `valid_at` stores exactly one fact
(first-writer-wins via the existing `(tenant_id, idempotency_key)` no-op
check).

#### Scenario: Duplicate delivery stores one fact

- **WHEN** the same `home_assistant` wellness envelope is delivered twice
  (e.g. connector replay after checkpoint overlap)
- **THEN** exactly one fact SHALL exist for that
  `(predicate, valid_at)`
- **AND** the second write SHALL be a no-op returning the existing fact id

#### Scenario: Same reading from two providers stores one fact

- **WHEN** a reading with identical `predicate` and `valid_at` has already
  been stored from another wellness provider using the same provider-agnostic
  key
- **THEN** the `home_assistant` translation SHALL be a no-op
- **AND** the surviving fact SHALL be the first writer's

#### Scenario: Distinct readings are not collapsed

- **WHEN** two readings share a predicate but differ in `valid_at`
- **THEN** both SHALL be stored as separate facts
