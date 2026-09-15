## ADDED Requirements

### Requirement: Connector Status Drives Spotify Passport State

The presentation-only `u:spotify` projection SHALL derive its spine state from the closed response of `GET /api/connectors/spotify/status`. It SHALL NOT use generic credential `warn` as a standing state and SHALL NOT expose a generic Secrets probe action.

#### Scenario: Spotify projection maps closed connector status

- **WHEN** Spotify status is loading, connected, unconfigured, authorization-needed, needs-reauth, failed, or unavailable
- **THEN** the projection renders respectively as checking, healthy, not-set, authorization-needed, authorization-needed, failed, or failed
- **AND** authorization-needed and failed states appear in `needs hand`
- **AND** checking never appears in `stale`

#### Scenario: CLI Test refreshes persisted evidence

- **WHEN** a CLI Test request completes with an HTTP success response
- **THEN** Passport invalidates the Secrets inventory and CLI provider queries
- **AND** the persisted healthy or failed outcome becomes visible without a page reload
