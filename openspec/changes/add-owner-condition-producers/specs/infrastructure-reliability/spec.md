## MODIFIED Requirements

### Requirement: Expected-infinite dashboard loops are supervised

The dashboard lifespan SHALL supervise exactly one named expected-infinite
task for each current loop: `secrets_lifecycle`, `model_verify`,
`fleet_events_bridge`, `settings_console_delta`, `secrets_staleness`,
`migration_drift`, `calendar_sync_deadman`, `calendar_overcommitment`,
`external_deadman`, and `restore_drill`. `external_deadman` SHALL be registered only when its target
URL is configured. An unconfigured `EXTERNAL_DEADMAN_URL` SHALL start no
external-deadman loop and produce no `external-deadman-stale` QA finding, but
the `infra_state` producer SHALL retain one durable, content-blind
`ExternalDeadmanUnconfigured` condition so missing independent assurance is
visible without an LLM investigation or an invented failed ping. This
requirement does not provision an external monitor or change the QA staleness
threshold. An ordinary return or exception from a
registered loop SHALL be logged with its name and restarted with bounded
backoff; the supervisor SHALL NOT run duplicate concurrent instances. Shutdown
cancellation SHALL cancel and await every registered loop, including calendar
deadman, and SHALL NOT restart a loop cancelled for shutdown.

ID: REQ-infrastructure-reliability-001
Source: `[Observed] src/butlers/core/qa/sources/infra_state.py` unconfigured-assurance observation; RFC 0015 §D1; add-owner-condition-producers D2 (bu-vcjgo8)
Scope: v1-mandatory

#### Scenario: Unexpected loop return is restarted
- **WHEN** a named expected-infinite loop returns normally or raises an
  exception while the dashboard is not stopping
- **THEN** its supervisor records the named unexpected termination
- **AND** it restarts that one loop after bounded backoff without creating a
  concurrent duplicate

#### Scenario: Shutdown cancellation is terminal
- **WHEN** dashboard shutdown begins
- **THEN** the supervisor marks itself stopping before cancelling and awaiting
  every registered lifespan loop
- **AND** a resulting `CancelledError` produces no restart

#### Scenario: Unconfigured external deadman is a legitimate absence
- **GIVEN** `EXTERNAL_DEADMAN_URL` is unconfigured
- **WHEN** dashboard lifespan initialization and the QA Staffer's
  `infra_state` discovery source evaluate the external-deadman boundary
- **THEN** no `external_deadman` lifespan loop is registered
- **AND** no `external-deadman-stale` QA finding or failed-ping claim is created
- **AND** one durable `ExternalDeadmanUnconfigured` condition remains visible
  without a QA investigation until a complete snapshot observes the URL as
  configured; a configured but failing ping is reported under the separate
  external-deadman-stale rule
- **AND** external-monitor provisioning and the existing QA staleness policy
  remain outside this change
