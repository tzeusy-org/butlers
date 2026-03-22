## Context

Butlers runs on a single machine via `scripts/dev.sh` (tmux-orchestrated, bare-metal Python processes) or `docker-compose.yml` (containerized, all services in one compose project). The target is a k3s cluster that already runs:

- **CloudNativePG** (`helm/postgresql_cloudnativepg/`) — 3-instance HA PostgreSQL with pgvector, Barman backups to Garage S3, per-app databases via ExternalSecret credentials from Bitwarden Secrets Manager.
- **LGTM stack** (`helm/lgtm/`) — Grafana, Loki, Tempo, Prometheus, Alloy. Alloy exposes OTLP at `http://alloy.lgtm:4318`.
- **Tailscale Operator** (`helm/tailscale_operator/`) — provides Ingress resources with automatic Tailscale TLS and DNS.
- **External Secrets Operator** (`helm/external-secrets_local/`) — Bitwarden Secrets Manager integration for credential injection.
- **Garage** on Synology NAS — S3-compatible object storage at `http://tzehouse-synology.parrot-hen.ts.net:3900`.

The homelab Helm chart convention is: `helm/<name>/` containing `makefile` (includes `base.mk`), `Chart.yaml`, `values.yaml`, and `templates/`. Charts are installed via `make deploy` (or `make deploy_dev` for dev namespace).

The Butlers Docker image is built and pushed to `ghcr.io` via `.github/workflows/release.yml` on git tags.

## Goals / Non-Goals

**Goals:**
- Helm chart at `helm/butlers_local/` in the homelab repo that deploys the full Butlers stack (minus live-listener) to k3s
- Reuse existing cluster infrastructure: CloudNativePG for Postgres, Garage for S3, Alloy for OTLP, Tailscale for Ingress, Bitwarden for secrets
- Each butler runs as an independent Deployment (not `butlers up` multi-daemon mode)
- Connectors (telegram-bot, telegram-user-client, gmail) as independent Deployments
- Database migrations run as a pre-install/pre-upgrade Job
- Dashboard (API + static frontend) as a Deployment with Tailscale Ingress
- All infrastructure secrets injected via ExternalSecret; runtime secrets remain in DB `CredentialStore`
- AGENTS.md writes gracefully degrade when roster is read-only

**Non-Goals:**
- Live-listener connector (requires USB audio hardware passthrough)
- MinIO/Garage deployment (already exists on NAS)
- Multi-replica butler scaling (butlers are inherently single-instance due to scheduler + session semantics)
- CI/CD pipeline changes (existing ghcr.io publish workflow is sufficient)
- Helm chart for PostgreSQL (already managed by CNPG chart)
- `butlers up` mode support in k8s (use per-butler Deployments instead)

## Decisions

### D1: Chart lives in homelab repo, not butlers repo

**Decision:** The Helm chart lives at `/home/tze/gt/homelab/mayor/rig/helm/butlers_local/`, not in the butlers repo.

**Rationale:** All other homelab Helm charts follow this pattern. The chart references cluster-specific infrastructure (CNPG cluster names, ExternalSecret store names, Tailscale hostnames, Garage endpoints) that are deployment-specific. The butlers repo provides the Docker image; the homelab repo provides the deployment manifests.

**Alternative considered:** Chart in butlers repo with a values overlay in homelab. Rejected — adds indirection and splits the chart maintenance across two repos without benefit for a single-deployment scenario.

### D2: One Deployment per butler, not DaemonSet or StatefulSet

**Decision:** Each butler is a `Deployment` with `replicas: 1`.

**Rationale:** Butlers are inherently single-instance (scheduler, session concurrency semaphore, database schema ownership). Deployment with `replicas: 1` gives rolling update, pod restart on failure, and resource limits. StatefulSet adds unnecessary complexity (no stable network identity needed — butlers find each other via the switchboard). DaemonSet is wrong — butlers are not per-node.

### D3: Roster configs as ConfigMaps (read-only), AGENTS.md in DB

**Decision:** `roster/{butler}/` directories are mounted as ConfigMaps at `/etc/butler` (read-only). AGENTS.md write operations fall back to the `state` KV table when the file is not writable.

**Rationale:** ConfigMaps are the standard k8s mechanism for config file injection. Making roster writable would require a PVC per butler, adding complexity. The only write target in roster is AGENTS.md (runtime agent notes), which can be stored in the existing `state` table with key `_agents_md_notes`. The system prompt (`CLAUDE.md`), manifesto, skills, and `butler.toml` are all read-only at runtime.

**Fallback logic:** `write_agents_md()` tries filesystem write first. On `OSError`/`PermissionError`, stores in DB. `read_agents_md()` reads filesystem first, then DB, concatenating both if present. This preserves the dev workflow (git-tracked AGENTS.md) while supporting read-only k8s volumes.

### D4: Migration Job (not init container)

**Decision:** Use a Helm `pre-install,pre-upgrade` hook Job running `butlers db migrate`, not init containers on each butler pod.

**Rationale:** Alembic migrations are NOT safe to run concurrently. Init containers on N butler pods would all try to migrate simultaneously. A single Job with `parallelism: 1` ensures serialized execution. The Job runs before any butler Deployment is created/updated.

**Rollback:** Alembic supports `downgrade`. The Job template can be parameterized for upgrade/downgrade. In practice, forward-only migration with careful PR review is safer.

### D5: ExternalSecrets for infrastructure, CredentialStore for runtime

**Decision:** Two-tier secret management:
1. **Infrastructure secrets** (Postgres URL, S3 endpoint/credentials, Google OAuth client ID/secret, OTEL endpoint) → k8s Secrets populated by ExternalSecret from Bitwarden Secrets Manager.
2. **Runtime secrets** (API keys, Telegram bot tokens, per-butler module credentials) → remain in the DB-backed `CredentialStore`, managed via dashboard UI.

**Rationale:** Infrastructure secrets are needed at pod startup (env vars). Runtime secrets are already resolved from DB at session spawn time. Changing the runtime credential model would break the dashboard secrets management UI and the `env_fallback` resolution chain. The existing `CredentialStore` is well-tested and allows secret rotation without pod restart.

### D6: Tailscale Operator Ingress, not Traefik

**Decision:** Use Tailscale Operator Ingress CRD for HTTPS termination on dashboard and API.

**Rationale:** The cluster already has Tailscale Operator installed. It provides automatic TLS via Tailscale's HTTPS cert provisioning and DNS names on the tailnet — no cert-manager or Let's Encrypt needed. The `tailscale.com/expose` annotation pattern is already used for the CNPG PostgreSQL services. Google OAuth callback URL becomes `https://<ts-hostname>/butlers-api/api/oauth/google/callback`.

### D7: Healing module disabled by default in k8s

**Decision:** `healing.enabled` config flag (default: `true` in dev, `false` in k8s via Helm value). When disabled, the healing module registers no tools and skips worktree operations.

**Rationale:** Self-healing requires a writable git repo with full history to create worktrees. In k8s, the container filesystem is ephemeral and the image doesn't include the repo's git history. Solving this (git-sync sidecar + PVC) adds significant complexity for marginal value — k8s already provides pod restart on failure and rolling updates for code fixes.

### D8: Frontend served by dashboard-api, not separate nginx

**Decision:** Build the frontend (`npm run build`) and serve via `DASHBOARD_STATIC_DIR` env var on the dashboard-api Deployment. No separate nginx pod.

**Rationale:** The dashboard-api already supports serving static files when `DASHBOARD_STATIC_DIR` is set. Adding nginx would double the pod count for the dashboard with no performance benefit at this scale. The Docker image build pipeline can include a multi-stage step that builds the frontend and copies `dist/` into the image.

## Risks / Trade-offs

- **[Risk] Single migration Job blocks all butler pod starts** → Mitigation: Job has a `backoffLimit: 3` and `activeDeadlineSeconds: 300`. If migration fails, all Deployments wait at pending. Manual intervention required, but this is the correct behavior — starting butlers against an unmigrated DB causes worse failures.

- **[Risk] AGENTS.md DB fallback creates divergence between dev and prod** → Mitigation: DB fallback is additive (concatenates file + DB content). In dev, AGENTS.md is the file (git-tracked). In prod, it's the DB. Notes are not critical for correctness — they're hints for LLM sessions. Acceptable divergence.

- **[Risk] Pod restart loses in-memory DurableBuffer queue entries** → Mitigation: Already handled by the cold-path scanner. On startup, the DurableBuffer scanner recovers `accepted`/`processing` rows from the `message_inbox` DB table. Tested and working today.

- **[Risk] Telegram user-client connector may need persistent session state** → Mitigation: Telethon sessions are typically stored as `.session` files. If the connector uses Telethon, it needs a small PVC for session persistence. Investigate during implementation — if needed, add a 1Gi PVC to the connector Deployment.

- **[Risk] Google OAuth bootstrap requires manual dashboard interaction** → Mitigation: Same as today (dev.sh OAuth gate). In k8s, the dashboard is accessible via Tailscale Ingress. First deploy requires visiting the dashboard URL and completing OAuth flow. Subsequent deploys reuse the refresh token stored in DB.

- **[Trade-off] No multi-replica for butlers** → Acceptable. Butlers are designed for single-instance operation (scheduler, session state). Horizontal scaling would require distributed locking and scheduler coordination, which is a different project.

## Migration Plan

### Phase 1: Code changes (butlers repo)
1. AGENTS.md DB fallback in `core/skills.py`
2. Healing module disable flag
3. `/ready` endpoint on dashboard-api
4. Verify Dockerfile works with read-only roster mount

### Phase 2: Homelab infrastructure prep
1. Add `butlers` app entry to CNPG `values.yaml`
2. Create Bitwarden secrets for Postgres credentials, S3 credentials, Google OAuth
3. Deploy CNPG update (`make deploy` in `postgresql_cloudnativepg/`)

### Phase 3: Helm chart (homelab repo)
1. Create `helm/butlers_local/` chart structure
2. Templates: migration Job, butler Deployments, connector Deployments, dashboard Deployment, Services, ConfigMaps, ExternalSecrets, Ingress
3. `make template` to validate
4. `make deploy` to install

### Phase 4: Validation
1. Verify migration Job completes
2. Verify all butler pods reach Ready
3. Verify dashboard accessible via Tailscale hostname
4. Complete Google OAuth flow via dashboard
5. Send a test message through Telegram → verify switchboard routing → butler response

### Rollback
- `make undeploy` removes all k8s resources
- Database remains (CNPG-managed, independent lifecycle)
- Bitwarden secrets remain (ExternalSecret just reads them)
- Revert to `dev.sh` on the original machine if needed

## Open Questions

1. **Telegram user-client session persistence** — Does the connector use Telethon `.session` files that need a PVC? Needs investigation.
2. **Frontend build in Docker image** — Should the multi-stage Dockerfile build the frontend, or should we publish a separate frontend image? Leaning toward multi-stage for simplicity.
3. **CNPG pgvector** — The CNPG chart has `pgvector.enabled: false` currently. Butlers' docker-compose uses `pgvector/pgvector:pg17`. Need to enable pgvector in CNPG values and verify the CNPG image includes the extension.
4. **Resource limits** — What CPU/memory limits per butler pod? Start with `requests: 100m/256Mi, limits: 500m/512Mi` and tune based on observed usage.
5. **Namespace** — Dedicated `butlers` namespace or shared? Leaning toward dedicated namespace for isolation.
