## ADDED Requirements

### Requirement: ExternalSecret for PostgreSQL credentials

The chart SHALL include an ExternalSecret resource that creates a k8s Secret with `username` and `password` keys, sourced from Bitwarden Secrets Manager via the cluster's ClusterSecretStore (`bitwarden-secretsmanager-prod`).

#### Scenario: Postgres Secret is populated by ESO
- **WHEN** the ExternalSecret is deployed
- **THEN** the External Secrets Operator creates a k8s Secret with `username` and `password` fields matching the Bitwarden-stored values

#### Scenario: Butler Deployments reference the Postgres Secret
- **WHEN** a butler Deployment is rendered
- **THEN** `POSTGRES_USER` and `POSTGRES_PASSWORD` env vars are sourced from the Postgres k8s Secret via `secretKeyRef`

### Requirement: ExternalSecret for S3 credentials

The chart SHALL include an ExternalSecret for S3 blob storage credentials (`access_key_id`, `secret_access_key`), used to seed the `CredentialStore` on first run or passed as env vars for bootstrap.

#### Scenario: S3 Secret is populated by ESO
- **WHEN** the ExternalSecret is deployed
- **THEN** the External Secrets Operator creates a k8s Secret with S3 credential fields

### Requirement: ExternalSecret for Google OAuth bootstrap

The chart SHALL include an ExternalSecret for Google OAuth app credentials (`client_id`, `client_secret`), passed as env vars to the dashboard-api for the OAuth bootstrap flow.

#### Scenario: OAuth Secret is populated by ESO
- **WHEN** the ExternalSecret is deployed
- **THEN** the External Secrets Operator creates a k8s Secret with `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`

#### Scenario: Dashboard-api receives OAuth credentials
- **WHEN** the dashboard-api Deployment is rendered
- **THEN** `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` env vars are sourced from the OAuth k8s Secret

### Requirement: ExternalSecret for connector tokens

Each enabled connector SHALL have its own ExternalSecret (or share a common one) providing connector-specific credentials (e.g., Telegram bot token).

#### Scenario: Telegram bot token from Bitwarden
- **WHEN** the telegram-bot connector is enabled
- **THEN** `BUTLER_TELEGRAM_TOKEN` is sourced from an ExternalSecret referencing the Bitwarden secret ID

### Requirement: Configurable SecretStore reference

All ExternalSecret resources SHALL reference a configurable `secretStoreRef` (default: `bitwarden-secretsmanager-prod`, kind: `ClusterSecretStore`), allowing different deployments to use different secret backends.

#### Scenario: Override secret store
- **WHEN** `externalSecrets.secretStoreRef.name` is set to `bitwarden-secretsmanager-dev`
- **THEN** all ExternalSecret resources reference `bitwarden-secretsmanager-dev`

### Requirement: Refresh interval

All ExternalSecret resources SHALL have a configurable `refreshInterval` (default: `4h`), matching the LGTM chart pattern.

#### Scenario: Secrets refresh on schedule
- **WHEN** the refresh interval elapses
- **THEN** ESO re-fetches secret values from Bitwarden and updates the k8s Secret if changed
