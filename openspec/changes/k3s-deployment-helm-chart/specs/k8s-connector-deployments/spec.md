## ADDED Requirements

### Requirement: Connector Deployment templating

The chart SHALL include a Deployment template that iterates over a `connectors` map in `values.yaml`. Each connector entry specifies: `enabled`, `command` (the Python module to run), `env`, `secretName`, `resources`.

Supported connectors:
- `telegram-bot` — `python -m butlers.connectors.telegram_bot`
- `telegram-user-client` — `python -m butlers.connectors.telegram_user_client`
- `gmail` — `python -m butlers.connectors.gmail`

#### Scenario: Enable a subset of connectors
- **WHEN** `values.yaml` has `connectors.telegram-bot.enabled: true` and `connectors.gmail.enabled: false`
- **THEN** Helm renders a Deployment for telegram-bot but NOT for gmail

#### Scenario: Connector receives infrastructure env vars
- **WHEN** a connector Deployment is rendered
- **THEN** the container SHALL have `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `CONNECTOR_PROVIDER`, `CONNECTOR_CHANNEL` environment variables

### Requirement: Connector-specific secrets

Each connector Deployment SHALL mount a connector-specific k8s Secret (populated by ExternalSecret) containing the connector's credentials (e.g., `BUTLER_TELEGRAM_TOKEN` for telegram-bot).

#### Scenario: Telegram bot connector receives bot token
- **WHEN** the telegram-bot connector Deployment is rendered
- **THEN** the `BUTLER_TELEGRAM_TOKEN` env var is populated from the connector's k8s Secret

#### Scenario: Gmail connector receives DB-first OAuth credentials
- **WHEN** the gmail connector Deployment is rendered
- **THEN** the container SHALL have `CONNECTOR_BUTLER_DB_NAME`, `CONNECTOR_BUTLER_DB_SCHEMA`, and `BUTLER_SHARED_DB_NAME` env vars pointing to the shared credential database, matching the `dev.sh` `_build_gmail_pane_cmd()` pattern

### Requirement: Live-listener excluded

The chart SHALL NOT include a live-listener connector Deployment. The live-listener requires hardware audio device access (`hw:2,0`) which is not supported in k3s without USB passthrough.

#### Scenario: No live-listener in rendered output
- **WHEN** `helm template` is run with default values
- **THEN** no Deployment or resource referencing `live-listener` is rendered

### Requirement: Telegram user-client session persistence

If the Telegram user-client connector requires persistent Telethon session files, the Deployment SHALL include a PVC mount for session storage.

#### Scenario: Session file survives pod restart
- **WHEN** the telegram-user-client pod is restarted
- **THEN** the Telethon `.session` file is preserved on the PVC and the connector resumes without re-authentication
