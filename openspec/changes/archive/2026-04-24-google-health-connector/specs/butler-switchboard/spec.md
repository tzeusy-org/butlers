# Butler Switchboard — Google Health Delta

## ADDED Requirements

### Requirement: Wellness Source Channel and Google Health Provider Registration

The Switchboard SHALL accept `wellness/google_health` as a valid ingestion source.

#### Scenario: SourceChannel registration

- **WHEN** the `SourceChannel` literal type is defined in `roster/switchboard/tools/routing/contracts.py`
- **THEN** it SHALL include `"wellness"` as a valid channel value
- **AND** `"wellness"` SHALL be the canonical channel for any wearable / health-device integration, regardless of device brand or API surface

#### Scenario: SourceProvider registration

- **WHEN** the `SourceProvider` literal type is defined
- **THEN** it SHALL include `"google_health"` as a valid provider value

#### Scenario: Channel-provider pair validation

- **WHEN** an ingest envelope arrives with `source.channel = "wellness"`
- **THEN** the Switchboard SHALL validate that `source.provider` is `"google_health"` (the only allowed provider for this channel in v1)
- **AND** the pair SHALL be registered in `_ALLOWED_PROVIDERS_BY_CHANNEL` as `{"wellness": frozenset({"google_health"})}`
- **AND** envelopes with `source.channel = "wellness"` and a different provider SHALL be rejected with a validation error

> **RFC 0003 amendment required:** The `wellness/google_health` channel/provider pair is not in the current Switchboard ingestion contract. RFC 0003 (Switchboard Routing and Ingestion) must be amended to register this pair before implementation lands. See the whatsapp-connector and steam-connector precedents for the amendment format.

### Requirement: Wellness Ingest Event Shape

Wellness ingest envelopes SHALL follow the canonical `ingest.v1` schema with Google-Health-specific field semantics.

#### Scenario: Wellness envelope acceptance

- **WHEN** a wellness ingest envelope arrives at the Switchboard
- **THEN** it SHALL be accepted if it conforms to `ingest.v1` with:
  - `source.channel = "wellness"`
  - `source.provider = "google_health"`
  - `source.endpoint_identity` matching pattern `"google_health:user:<google_user_id>"` (or with a `:<resource>` suffix for per-resource cursor keying)
  - `sender.identity` set to the owner's Google user ID, which the Switchboard resolves to the owner entity via the `contact_info` row pre-registered during pairing (`type="google_health"`)
  - `control.idempotency_key` matching pattern `"google_health:<resource>:<record_id>"`

#### Scenario: Identity resolution does not create temporary contacts

- **WHEN** the Switchboard processes a wellness envelope's `sender.identity`
- **THEN** it SHALL resolve the owner via the pre-registered `contact_info` row
- **AND** SHALL NOT invoke `create_temp_contact()`
- **AND** SHALL NOT emit disambiguation candidates

#### Scenario: Wellness envelopes do not route as interactive messages

- **WHEN** a wellness envelope is accepted
- **THEN** it SHALL NOT be treated as an interactive channel
- **AND** no reply pathway SHALL be established
- **AND** the envelope SHALL be routed directly to the Health butler via the ingest handler registration for `wellness/google_health`

### Requirement: Dedicated Routing to Health Butler

The Switchboard SHALL route all `wellness/google_health` envelopes to the Health butler.

#### Scenario: Direct routing

- **WHEN** an accepted wellness envelope passes deduplication
- **THEN** the Switchboard SHALL dispatch it to the Health butler
- **AND** SHALL NOT dispatch to any other butler
- **AND** wellness envelopes are non-interactive and bypass the user-message classification stage entirely, following the precedent established for `gaming/steam`, `location/owntracks`, and `spotify/spotify` (these channels do not traverse the routing-scorecard `route_contract_min`/`route_contract_max` pathway because they are non-interactive ingest, not user-authored messages)
