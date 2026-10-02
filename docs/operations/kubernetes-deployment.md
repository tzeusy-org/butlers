# Kubernetes Deployment

> **Purpose:** Run Butlers on the homelab k3s cluster with the in-repo Helm chart.
> **Audience:** Operators deploying or upgrading Butlers.
> **Prerequisites:** `kubectl` and `helm` against the k3s cluster, Docker with push access to
> `docker-registry.parrot-hen.ts.net`, and `bws` with the dev or prod BWS env file.

## Overview

`deploy/helm/butlers` is the Kubernetes counterpart of `docker-compose.yml`. The chart installs one
release named `butlers` into one of two namespaces:

| | `butlers-dev` (`values.dev.yaml`) | `butlers` (`values.prod.yaml`) |
| --- | --- | --- |
| Database | `postgres-dev-rw.postgres-dev.svc` (butlers-db-dev, **the LIVE data**) | `postgresql-cloudnativepg-rw.postgres.svc` (butlers-db) |
| ClusterSecretStore | `bitwarden-secretsmanager-dev` | `bitwarden-secretsmanager-prod` |
| URL prefixes | `/butlers-dev`, `/butlers-dev-api`, `/owntracks-dev` | `/butlers`, `/butlers-api`, `/owntracks` |
| NodePorts (frontend, API, OwnTracks) | 32173, 32200, 32086 | 31173, 31200, 31086 |
| Owner passkey auth | on (`deployment=dev`) | off (as in compose `--prod`) |

The "dev" naming matches compose and is just as counter-intuitive: see the naming caution in
[Docker Deployment](docker-deployment.md#environment-variables).

## What the chart runs

| Compose service | Kubernetes object |
| --- | --- |
| `migrations` | initContainer of `dashboard-api` (Recreate strategy, never two at once) |
| `dashboard-api` | Deployment + Service `dashboard-api:41200` + NodePort `dashboard-api-host` |
| `frontend-dev` (Vite) | Deployment `frontend`: nginx serving a static build (`Dockerfile.frontend`) |
| `oauth-gate` | initContainer of `butlers-up` and of OAuth-dependent connectors |
| `butlers-up` | Deployment + Service `butlers-up:41100-41112` |
| `connector-*` | One Deployment per entry in `values.connectors` |
| `backup-cron` | CronJob `butlers-backup` writing to PVC `butlers-backups` (read by the dashboard) |
| `log-init`, `log-cleanup` | Shared PVC `butlers-logs` + CronJob `butlers-log-cleanup` |
| `wa_bridge_socket` volume | PVC `butlers-wa-bridge` (Unix socket shared by three pods on one node) |
| `runtime_*` CLI volumes | PVC `butlers-runtime-home`, one subPath per CLI |

Service names match the compose service names, so in-cluster URLs (`http://butlers-up:41100/sse`,
`http://dashboard-api:41200`) are unchanged.

Not ported (compose remains the only path for these):

- the restore-drill executor (`docker-compose.restore-drill.yml`): it attests Docker cgroups and
  host iptables and needs a NetworkPolicy-based redesign;
- `connector-live-listener` (needs `/dev/snd`);
- the observability profile (the cluster's `lgtm` stack and `otel.parrot-hen.ts.net` replace it);
- `/api/decisions` and the beads tiles: the 18 MB `.beads/issues.export.jsonl` host file is not
  mounted, so they report unavailable.

## Ingress

The host's `tailscale serve` mappings stay the public entry point, so the dashboard URL, OAuth
redirect URIs, the OwnTracks webhook URL and enrolled passkeys (RP ID `tzeusy.parrot-hen.ts.net`)
do not change. Only the local targets move from the compose ports to the NodePorts:

```bash
tailscale serve --bg --set-path /butlers-dev     http://localhost:32173/butlers-dev
tailscale serve --bg --set-path /butlers-dev-api http://localhost:32200
tailscale serve --bg --set-path /owntracks-dev   http://localhost:32086/owntracks
```

NodePorts listen on every node address, unlike compose's `127.0.0.1` bindings. The
`butlers-host-ingress` NetworkPolicy therefore admits only the node itself and pods in the
namespace. Host-originated connections reach pods from the `cni0` address `10.42.0.1`. That
address is both the policy's allowed source and `dashboardAuth.trustedProxyPeers`; it plays the
role the Docker bridge gateway played under compose. The NodePort Services use
`externalTrafficPolicy: Local`, so remote clients keep their own source IP and are refused.

## Deploy

From `deploy/helm/butlers`, with the matching BWS env loaded:

```bash
set -a; source /secrets/.bws.dev.env; set +a
bws run --project-id "$BWS_PROJECT_ID" -- make secrets-dev  # once, and after key rotation
bws run --project-id "$BWS_PROJECT_ID" -- make image-dev    # build + push app and frontend images
make deploy-dev                                             # helm upgrade --install, waits for the API
make status-dev
```

The prod targets (`secrets-prod`, `image-prod`, `deploy-prod`) are the same with
`/secrets/.bws.prod.env`.

- `make image-*` tags images with the 12-character commit SHA and refuses a dirty worktree
  unless `ALLOW_DIRTY=1`. `make deploy-*` uses the same `TAG` (override with `TAG=<sha>`).
- `make template-dev` renders into the gitignored `_templates/`. Put per-operator overrides in
  the gitignored `values.local.yaml`; it is applied after the environment file.
- Secrets come from two places:
  - The ExternalSecret `butlers-bws` syncs only the keys listed in `externalSecrets.data`. The BWS
    project is shared with other homelab services, so it never syncs the whole project.
  - `scripts/k8s/bootstrap-secrets.sh` creates the two Secrets that have no BWS key yet:
    the runtime-probe signer and keyring, from the files named by
    `RUNTIME_PROBE_CONTROL_*_FILE`, and `DASHBOARD_AUTH_DB_USER` / `DASHBOARD_API_KEY` from
    `.env.<env>`.

## Cutting over from compose

The dev compose stack and the `butlers-dev` release use the same database, schedulers and
connector sessions, so never run both at once:

1. While compose is still running: `make secrets-dev`, `make image-dev`, then stage the release
   with everything scaled to zero: `helm upgrade --install butlers . -n butlers-dev
   -f values.yaml -f values.dev.yaml --set suspend=true --set image.tag=<sha>
   --set frontendImage.tag=<sha>-dev`. Confirm `kubectl -n butlers-dev get externalsecret`
   reports `SecretSynced`.
2. Stop compose: `docker compose -p butlers-dev down` (named volumes are kept).
3. `make deploy-dev` (which leaves `suspend` false), then repoint the three `tailscale serve`
   mappings above.
4. Verify: `make status-dev`, the dashboard at `https://tzeusy.parrot-hen.ts.net/butlers-dev/`, an
   owner sign-in, and connector logs (`kubectl -n butlers-dev logs deploy/connector-telegram-bot`).

Rollback: `helm -n butlers-dev uninstall butlers` (PVCs are kept), restore the compose
`tailscale serve` targets (42173, 42200, 42086), and rerun `./scripts/compose.sh`.

## Security posture differences from compose

- `dashboard-api` runs `privileged` (`dashboardApi.privileged`). Compose grants the dashboard's
  Bubblewrap login sandbox an unmasked `/proc` plus a custom seccomp profile; Kubernetes can only
  express that with user namespaces. Setting `privileged: false` keeps AppArmor unconfined but
  breaks dashboard-driven CLI login.
- The compose egress firewall (`scripts/egress-firewall.sh`) has no equivalent yet. It only ran
  when the launcher had passwordless sudo.
