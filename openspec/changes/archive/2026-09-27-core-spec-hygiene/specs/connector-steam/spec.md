## MODIFIED Requirements

### Requirement: Heartbeat Protocol

The connector SHALL send `connector.heartbeat.v1` envelopes to the Switchboard as defined by connector-base-spec "Heartbeat Protocol", using the shared connector heartbeat; this requirement states only the Steam-specific values.

#### Scenario: Heartbeat envelope

- **WHEN** the heartbeat interval elapses (default 60 seconds, overridable via `STEAM_HEARTBEAT_INTERVAL_S`)
- **THEN** the connector SHALL submit a `connector.heartbeat.v1` envelope whose `connector.connector_type` is `"steam"`
- **AND** `connector.endpoint_identity` SHALL be the comma-separated list of active account endpoint identities (`steam:user:<steam_id>`), or `steam:no_accounts` when no account is active
- **AND** `status.state` SHALL be the connector's aggregated health (`healthy`, `degraded`, or `error`)

#### Scenario: Heartbeat failure does not crash

- **WHEN** a heartbeat submission fails (Switchboard unavailable)
- **THEN** the connector SHALL log a warning and continue polling
- **AND** the next heartbeat SHALL be attempted on schedule
