## ADDED Requirements

### Requirement: Overview Tab Process Facts Card
The Butler detail Overview tab SHALL render a process facts card that displays stable, container-boundary-safe runtime facts for the selected butler. The card SHALL include `container_name`, `port`, `registered_duration_seconds`, and `config_path`, and it SHALL NOT include `pid` or any process ID field.

The backend SHALL provide these facts through the butler detail data surface so the frontend does not have to scrape or reconcile multiple endpoints. Missing source data SHALL render as an explicit unavailable value rather than hiding the row.

#### Scenario: Container name is shown from the dashboard connection host
- **WHEN** the Overview tab renders process facts for a butler running in the compose topology
- **THEN** the card SHALL show `container_name` from the dashboard-reachable MCP host/service name
- **AND** the source SHALL be `BUTLERS_HOST` as used by `ButlerConnectionInfo.sse_url` (`src/butlers/api/deps.py:52-60`), with compose setting `BUTLERS_HOST: butlers-up` (`docker-compose.yml:170-182`)
- **AND** implementations MAY fall back to the switchboard registry `endpoint_url` host selected by the registry API (`roster/switchboard/api/router.py:381-395`) when `BUTLERS_HOST` is unavailable

#### Scenario: Port is shown from butler summary/detail data
- **WHEN** the Overview tab renders process facts
- **THEN** the card SHALL show the butler MCP port as `port`
- **AND** the source SHALL be the existing `ButlerSummary.port` field (`src/butlers/api/models/__init__.py:101-117`) populated from `ButlerConnectionInfo.port` in `_probe_butler` (`src/butlers/api/routers/butlers.py:124-131`)
- **AND** the detail endpoint SHALL keep using `config.port` when constructing `ButlerDetail` (`src/butlers/api/routers/butlers.py:238-250`)

#### Scenario: Registration duration is shown from liveness registry timestamps
- **WHEN** the Overview tab renders process facts for a butler with switchboard liveness data
- **THEN** the backend SHALL expose `registered_duration_seconds` as the numeric seconds elapsed since the selected liveness registration timestamp
- **AND** the frontend SHALL render that numeric value as a human-readable liveness or registration duration rather than requiring the backend to serialize display copy
- **AND** implementations SHOULD use the registry row's `registered_at` as the running-since timestamp when available, because the switchboard registry schema already defines `registered_at` alongside `last_seen_at` (`roster/switchboard/migrations/001_switchboard_messaging.py:44-50`) and the registry API selects it (`roster/switchboard/api/router.py:381-384`)
- **AND** implementations SHALL use `last_seen_at` only for freshness/null handling, using the heartbeat/system router's registry read of `butler_registry.last_seen_at` as an existing source citation (`src/butlers/api/routers/system.py:643-699`)

#### Scenario: Config path is shown from roster layout
- **WHEN** the Overview tab renders process facts
- **THEN** the card SHALL show `config_path` as the roster-relative path to the butler's TOML file, for example `roster/relationship/butler.toml`
- **AND** the source SHALL be the existing roster loader contract: `_get_roster_dir()` resolves the roster directory (`src/butlers/api/routers/butlers.py:58-64`), the detail endpoint constructs `butler_dir = roster_dir / name`, and `load_config(butler_dir)` loads that butler config (`src/butlers/api/routers/butlers.py:221-224`)
- **AND** roster discovery already treats `entry / "butler.toml"` as the config file for each configured butler (`src/butlers/api/deps.py:233-241`)
- **AND** the API SHALL serialize `config_path` as the stable roster-relative path, not an absolute host/container filesystem path

#### Scenario: Process ID is absent
- **WHEN** the Overview tab renders process facts
- **THEN** the card SHALL NOT render a `pid` label, value, API field, or frontend type member
- **AND** tests SHALL assert the process facts rows are exactly the expected labels for container name, port, liveness duration, and config path
- **AND** tests SHALL assert no row or label for a process ID field is rendered
