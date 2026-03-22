## Why

Butlers currently runs on-prem via `scripts/dev.sh` (tmux orchestration) and `docker-compose.yml` (containerized mode). Neither supports the target deployment: a k3s cluster with Helm charts, managed PostgreSQL (CloudNativePG), Tailscale Operator for ingress, External Secrets Operator for credential injection, and Garage/MinIO for S3 storage. The recent S3 blob storage migration eliminated the largest blocker (local filesystem blobs), but several filesystem-dependent patterns, hardcoded service discovery, and missing k8s packaging remain.

## What Changes

- **New Helm chart** for Butlers at `/home/tze/gt/homelab/mayor/rig/helm/butlers_local/` following the existing homelab chart conventions (`makefile` with `base.mk`, `values.yaml`, `Chart.yaml`, templates). The chart packages all butler Deployments, the dashboard, connectors, migration Job, Services, ConfigMaps, ExternalSecrets, and Tailscale Ingress resources.
- **Database integration**: Butlers consumes the existing CloudNativePG cluster (`helm/postgresql_cloudnativepg/`) rather than running its own PostgreSQL. A new `apps` entry is added to the CNPG values for the `butlers` database + role, with credentials managed via ExternalSecret from Bitwarden Secrets Manager.
- **Init container migration pattern**: Database migrations (`butlers db migrate`) run as a Helm pre-install/pre-upgrade Job, replacing the `dev.sh` Layer 0.5 pattern. Alembic concurrency is guarded by `parallelism: 1`.
- **AGENTS.md writes moved to DB**: Runtime LLM sessions currently append notes to `roster/{butler}/AGENTS.md` on the filesystem. This must fall back to the `state` KV store when the roster volume is read-only (ConfigMap-mounted), preserving the write-back-to-git workflow for development but not requiring it in production.
- **File logging disabled by default**: k3s deployments set `BUTLERS_DISABLE_FILE_LOGGING=1` and rely on stdout/stderr collection via Alloy/Loki (already deployed in `helm/lgtm/`).
- **Connector Deployments**: Telegram bot, Telegram user client, and Gmail connectors become independent Deployments with their own ExternalSecrets. The live-listener connector is excluded (hardware-dependent audio device access).
- **Tailscale Operator Ingress**: Dashboard and API get Tailscale Ingress resources for HTTPS termination, replacing the `tailscale serve` pattern in `dev.sh`. Google OAuth callback URL becomes a Helm value.
- **Secrets via ExternalSecret**: Infrastructure secrets (Postgres credentials, S3 credentials, OTEL endpoint) and bootstrap secrets (Google OAuth client ID/secret) are injected via the External Secrets Operator from Bitwarden Secrets Manager, matching the existing homelab pattern. Runtime secrets (API keys, tokens) remain in the DB-backed `CredentialStore` — no change.
- **Healing worktrees**: Self-healing is disabled by default in k3s (no writable git repo). A Helm value controls whether the healing module is active.
- **Service discovery**: Inter-butler switchboard URL uses k8s DNS (`http://switchboard:41100`) instead of `localhost`. Configurable via Helm values passed as env vars.

## Capabilities

### New Capabilities
- `k8s-helm-chart`: Helm chart structure, templates (Deployment, Service, Job, ConfigMap, ExternalSecret, Ingress), values schema, and makefile. Lives in the homelab repo at `helm/butlers_local/`.
- `k8s-migration-job`: Pre-install/pre-upgrade Job running `butlers db migrate` with concurrency guard and init-container ordering.
- `k8s-connector-deployments`: Independent Deployment + Service + ExternalSecret templates for each connector (telegram-bot, telegram-user-client, gmail).
- `k8s-ingress-tls`: Tailscale Operator Ingress resources for dashboard frontend and API, with configurable hostname and path prefixes.
- `k8s-secrets-integration`: ExternalSecret resources wiring Bitwarden Secrets Manager to k8s Secrets for infrastructure credentials (Postgres, S3, Google OAuth, OTEL).
- `agents-md-db-fallback`: Fallback path for AGENTS.md writes when the roster directory is read-only — stores notes in the `state` KV table with key `agents_md_notes`.

### Modified Capabilities
- `core-daemon`: Daemon startup must handle read-only roster directory (AGENTS.md fallback), disabled healing module, and `BUTLERS_DISABLE_FILE_LOGGING=1` as default in k8s.
- `core-skills`: `write_agents_md()` and `append_agents_md()` need a DB fallback when `AGENTS.md` path is not writable.
- `healing-worktree`: Must be gracefully disableable via config flag (`healing.enabled = false`) without blocking butler startup.
- `core-telemetry`: OTEL endpoint becomes a required env var in k8s (pointed at `http://alloy.lgtm:4318` via Helm values), optional in dev.
- `dashboard-api`: Needs a `/ready` endpoint (distinct from `/health`) that checks DB connectivity and switchboard reachability for k8s readiness probes.

## Impact

- **Homelab repo** (`/home/tze/gt/homelab/mayor/rig/`): New directory `helm/butlers_local/` with full Helm chart. Modification to `helm/postgresql_cloudnativepg/values.yaml` to add butlers app entry.
- **Butlers repo** (`/home/tze/gt/butlers/mayor/rig/`):
  - `src/butlers/core/skills.py` — AGENTS.md DB fallback
  - `src/butlers/daemon.py` — healing disable flag, read-only roster handling
  - `src/butlers/core/healing/` — graceful disable
  - `src/butlers/api/app.py` — `/ready` endpoint
  - `Dockerfile` — verify production image is k8s-ready (writable `/tmp`, non-root user consideration)
- **Dependencies**: No new Python dependencies. Helm chart uses standard k8s resources.
- **Config**: New Helm `values.yaml` with per-butler enable/disable, resource limits, image tag, replica count, env overrides, ExternalSecret references, ingress hostname.
- **Docker**: Existing `Dockerfile` and `ghcr.io` release pipeline reused. Image tag configurable in Helm values.
- **Breaking**: None. All changes are additive. `dev.sh` and `docker-compose.yml` continue to work unchanged.
- **Excluded from scope**: Live-listener connector (hardware-dependent), `butlers up` multi-daemon mode (k8s uses per-butler Deployments), MinIO deployment (assumes existing S3-compatible storage on the network — Garage on Synology NAS or standalone MinIO).
