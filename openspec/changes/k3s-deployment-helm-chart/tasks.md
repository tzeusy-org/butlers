## 1. Butlers Code Changes (butlers repo)

- [ ] 1.1 Add `db_pool` optional parameter to `read_agents_md()`, `write_agents_md()`, `append_agents_md()` in `src/butlers/core/skills.py`. Implement DB fallback: catch `OSError`/`PermissionError` on file write, store/read from `state` KV table with key `_agents_md_notes`. Merge file + DB on read.
- [ ] 1.2 Wire `db_pool` through from daemon to skills functions — pass the butler's DB pool when building the system prompt and when LLM sessions write AGENTS.md via MCP tools.
- [ ] 1.3 Add `healing.enabled` config field to `ButlerConfig` (default: `true`). Support `BUTLERS_HEALING_ENABLED` env var override.
- [ ] 1.4 Guard healing module initialization in daemon startup — skip self-healing module load, worktree reaping, and healing tool registration when `healing.enabled = false`.
- [ ] 1.5 Guard `create_healing_worktree()` and `reap_stale_worktrees()` — return early/no-op when healing is disabled, without requiring a `.git` directory.
- [ ] 1.6 Add `GET /ready` endpoint to dashboard-api (`src/butlers/api/app.py`). Check DB pool connectivity (`SELECT 1`) and roster discovery. Return 200/503 with structured JSON. Add `/ready` to `_PUBLIC_PATHS`.
- [ ] 1.7 Verify Dockerfile works with read-only `/etc/butler` mount — test `butlers run --config /etc/butler` with a read-only bind mount locally.
- [ ] 1.8 Write tests for AGENTS.md DB fallback (read-only mock, merge behavior, no-pool degradation).
- [ ] 1.9 Write tests for healing disable flag (daemon startup with `healing.enabled=false`, worktree no-op).
- [ ] 1.10 Write test for `/ready` endpoint (healthy and unhealthy DB scenarios).

## 2. CNPG Database Integration (homelab repo)

- [ ] 2.1 Add `butlers` entry to `apps` list in `helm/postgresql_cloudnativepg/values.yaml` — database: `butlers`, role: `butlers`, with ExternalSecret for credentials from Bitwarden.
- [ ] 2.2 Enable `pgvector` in CNPG values (`pgvector.enabled: true`) and verify the CNPG PostgreSQL image includes the vector extension.
- [ ] 2.3 Create Bitwarden secrets for butlers Postgres username and password.
- [ ] 2.4 Deploy CNPG update (`make deploy` in `postgresql_cloudnativepg/`) and verify the `butlers` database is created with pgvector extension.

## 3. Bitwarden Secrets Provisioning

- [ ] 3.1 Create Bitwarden secrets for S3/Garage blob storage credentials (access key, secret key).
- [ ] 3.2 Create Bitwarden secrets for Google OAuth app credentials (client ID, client secret).
- [ ] 3.3 Create Bitwarden secrets for Telegram bot token.
- [ ] 3.4 Create Bitwarden secrets for Telegram user-client credentials (if applicable).

## 4. Helm Chart Skeleton (homelab repo)

- [ ] 4.1 Create `helm/butlers_local/` directory with `Chart.yaml` (name: butlers, type: application, appVersion matching latest ghcr.io tag).
- [ ] 4.2 Create `makefile` — include `../base.mk`, set `CONTEXT=default`, `NAMESPACE=butlers`, `RELEASE_NAME=butlers`, `REPOSITORY=.`. Add `deploy`, `undeploy`, `redeploy`, `template`, `template_dev` targets.
- [ ] 4.3 Create `templates/_helpers.tpl` with standard labels, selectors, and name helpers.
- [ ] 4.4 Create `templates/namespace.yaml` for the `butlers` namespace.
- [ ] 4.5 Create `values.yaml` with full schema: `global.image`, `postgres`, `s3`, `otel`, `oauth`, `butlers.<name>`, `connectors.<name>`, `dashboard`, `migration`, `ingress`, `externalSecrets`.

## 5. Helm Chart — Secrets & ConfigMaps

- [ ] 5.1 Create `templates/externalsecret-postgres.yaml` — ExternalSecret sourcing Postgres credentials from Bitwarden via ClusterSecretStore.
- [ ] 5.2 Create `templates/externalsecret-s3.yaml` — ExternalSecret for S3 blob storage credentials.
- [ ] 5.3 Create `templates/externalsecret-oauth.yaml` — ExternalSecret for Google OAuth client ID/secret.
- [ ] 5.4 Create `templates/externalsecret-connectors.yaml` — ExternalSecret(s) for connector tokens (Telegram bot, etc.).
- [ ] 5.5 Create `templates/configmap-roster.yaml` — per-butler ConfigMap containing `butler.toml`, `CLAUDE.md`, `MANIFESTO.md`, and skill files from `roster/{butler}/`.

## 6. Helm Chart — Migration Job

- [ ] 6.1 Create `templates/job-migrate.yaml` — pre-install/pre-upgrade hook Job running `butlers db migrate`. `parallelism: 1`, `completions: 1`, `backoffLimit: 3`, `activeDeadlineSeconds: 300`. Hook delete policy: `before-hook-creation`. Inject Postgres credentials from Secret.

## 7. Helm Chart — Butler Deployments

- [ ] 7.1 Create `templates/deployment-butler.yaml` — range over `.Values.butlers`, skip `enabled: false`. Container: image from `global.image`, command `["run", "--config", "/etc/butler"]`, env vars from Postgres Secret + OTEL + per-butler overrides. Mount roster ConfigMap at `/etc/butler` (readOnly). Set `BUTLERS_DISABLE_FILE_LOGGING=1`, `BUTLERS_HEALING_ENABLED=false`.
- [ ] 7.2 Create `templates/service-butler.yaml` — ClusterIP Service per butler on configured port.
- [ ] 7.3 Configure liveness probe (`httpGet /health`) and readiness probe (`httpGet /health`) on each butler Deployment. Set `terminationGracePeriodSeconds: 45`.

## 8. Helm Chart — Dashboard Deployment

- [ ] 8.1 Create `templates/deployment-dashboard.yaml` — dashboard-api Deployment with command `["dashboard", "--host", "0.0.0.0", "--port", "41200"]`. Mount all roster ConfigMaps at `/app/roster/` (readOnly). Inject Postgres, OAuth, and OTEL env vars. Set `DASHBOARD_STATIC_DIR=/app/frontend/dist` if frontend is baked into image.
- [ ] 8.2 Create `templates/service-dashboard.yaml` — ClusterIP Service on port 41200.
- [ ] 8.3 Configure liveness probe (`httpGet /health`) and readiness probe (`httpGet /ready`) on dashboard.

## 9. Helm Chart — Connector Deployments

- [ ] 9.1 Create `templates/deployment-connector.yaml` — range over `.Values.connectors`, skip `enabled: false`. Container command: `["uv", "run", "python", "-m", "<module>"]`. Inject Postgres credentials + connector-specific Secret env vars + `CONNECTOR_PROVIDER` + `CONNECTOR_CHANNEL`.
- [ ] 9.2 Investigate Telegram user-client session persistence — check if Telethon `.session` file is used, and if so, add a PVC template for session storage.

## 10. Helm Chart — Ingress

- [ ] 10.1 Create `templates/ingress-dashboard.yaml` — Tailscale Ingress with `tailscale.com/expose: "true"` annotation, routing `ingress.dashboardPath` to the dashboard Service.
- [ ] 10.2 Create `templates/ingress-api.yaml` — Tailscale Ingress routing `ingress.apiPath` to the dashboard-api Service (or combine with dashboard Ingress if same hostname).
- [ ] 10.3 Set `GOOGLE_OAUTH_REDIRECT_URI` env var on dashboard-api from `oauth.redirectUri` Helm value.

## 11. Docker Image Updates

- [ ] 11.1 Evaluate multi-stage Dockerfile that builds frontend (`npm run build`) and copies `dist/` into the production image alongside the Python app. Alternatively, document a separate frontend build step.
- [ ] 11.2 Ensure container runs with writable `/tmp` (for ephemeral runtime configs) and verify no `root`-only operations block non-root execution (future hardening).

## 12. Validation & Smoke Test

- [ ] 12.1 Run `make template` in `helm/butlers_local/` — verify all templates render without errors.
- [ ] 12.2 Run `make deploy` — verify namespace, ExternalSecrets, migration Job, and all Deployments are created.
- [ ] 12.3 Verify migration Job completes successfully and all butler pods reach Ready state.
- [ ] 12.4 Verify dashboard is accessible via Tailscale hostname with HTTPS.
- [ ] 12.5 Complete Google OAuth flow via dashboard and verify refresh token is stored in DB.
- [ ] 12.6 Send a test Telegram message and verify end-to-end flow: connector → switchboard → butler → response.
- [ ] 12.7 Verify OTEL traces appear in Grafana/Tempo for butler sessions.
