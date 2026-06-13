# Butler Switchboard — Wellness Provider Pairing Delta

## MODIFIED Requirements

### Requirement: Channel/provider pairing validation for the wellness channel

The Switchboard SHALL accept `source.channel = "wellness"` envelopes whose
`source.provider` is `"google_health"` OR `"home_assistant"`, and SHALL reject
any other provider on the wellness channel via `IngestSourceV1` validation
(`_ALLOWED_PROVIDERS_BY_CHANNEL` in
`roster/switchboard/tools/routing/contracts.py`). RFC 0003's canonical pairing
registry SHALL be amended to record the `wellness/home_assistant` pairing.

#### Scenario: Home Assistant wellness envelope accepted

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"`
  and `source.provider = "home_assistant"`
- **THEN** the envelope SHALL pass channel/provider validation
- **AND** SHALL be routed by the existing `source_channel = "wellness"` rule
  (`route_to:health`) over the policy-bypass path with no LLM session spawned

#### Scenario: Google Health wellness envelope unaffected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"`
  and `source.provider = "google_health"`
- **THEN** validation and routing behavior SHALL be identical to before this
  change

#### Scenario: Unregistered provider still rejected

- **WHEN** an ingest.v1 envelope arrives with `source.channel = "wellness"`
  and a provider other than `"google_health"` or `"home_assistant"`
- **THEN** the envelope SHALL be rejected with the `invalid_source_provider`
  validation error
