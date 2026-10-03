# Kubernetes Deployment

> **Purpose:** Run Butlers on the homelab k3s cluster with the in-repo Helm chart.
> **Audience:** Operators deploying or upgrading Butlers.
> **Prerequisites:** `kubectl` and `helm` against the k3s cluster, Docker with push access to
> `<registry-host>`, and `bws` with the dev or prod BWS env file.

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
| host `.beads/issues.export.jsonl` | Optional (`beadsExport.enabled`, default off): CronJob `butlers-beads-export` writing PVC `butlers-beads-export`, mounted read-only at `/app/.beads` (see [Beads export](#beads-export)) |

Service names match the compose service names, so in-cluster URLs (`http://butlers-up:41100/sse`,
`http://dashboard-api:41200`) are unchanged.

Not ported (compose remains the only path for these):

- the restore-drill executor (`docker-compose.restore-drill.yml`): it attests Docker cgroups and
  host iptables and needs a NetworkPolicy-based redesign;
- `connector-live-listener` (needs `/dev/snd`);
- the observability profile (the cluster's `lgtm` stack and `otel.example.ts.net` replace it);

## Beads export

`/api/decisions`, the beads tiles, `GET /api/beads/{id}` and `jobs/decision_review` read
`/app/.beads/issues.export.jsonl`. With `beadsExport.enabled=false` (default) nothing is mounted
and they report unavailable. With it enabled, CronJob `butlers-beads-export` runs `bd export`
against the Dolt tracker every `beadsExport.schedule`, writes a temp file on the PVC and renames
it into place. `dashboard-api` and `butlers-up` mount the PVC read-only as a directory (not a
`subPath`, which would pin the replaced inode). A failed run leaves the previous file; once it is
older than `STALE_BEADS_EXPORT_AGE` readers report unavailable, never empty.

Trust boundary: only the CronJob pod receives the Dolt host and the credential Secret
(`beadsExport.credentialSecretName`); runtime pods get just the PVC, which holds only the export
file. Enabling it places a tracker credential in the namespace, which
`REQ-beads-projection-001` makes owner-gated, so do not enable it without owner approval
(owner decision on credential placement and the exporter image: `bu-sng0tu`).

Open facts, all unverified; confirm each before enabling:

- `bd` is not in any image. The app image does not contain it, so `beadsExport.image` must be a
  dedicated exporter image with a `bd` version pinned to match the tracker host.
- `bd export` may need a scratch `.beads/metadata.json` workspace inside the pod; the script does
  not create one.
- The `BEADS_DOLT_*` env names the CronJob sets are unconfirmed against the pinned `bd` version.
- Dolt reachability from `butlers-dev` pods is untested.

Storage: the export PVC is `ReadWriteOnce`, written by the CronJob and read by two runtime pods.
That only works with single-node k3s scheduling. Before going multi-node, require co-scheduling of
those pods or switch `storage.storageClassName` to an RWX class.

Once those are settled, set `beadsExport.image`, `beadsExport.doltHost` and
`beadsExport.credentialSecretName` in `values.local.yaml`. Roll back by setting
`beadsExport.enabled=false`.

## Ingress

The host's `tailscale serve` mappings stay the public entry point, so the dashboard URL, OAuth
redirect URIs, the OwnTracks webhook URL and enrolled passkeys (RP ID `butlers.example.ts.net`)
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

For dev, one command from the repo root builds and pushes images for `HEAD`, upgrades the
release and prints status:

```bash
scripts/k8s/deploy-dev.sh          # or: scripts/k8s/deploy-dev.sh <sha> to redeploy a pushed tag
```

It reads the BWS env from `BWS_ENV_FILE` (default `~/.secrets/.bws.dev.env`) and refuses to
deploy without the site hosts (`BUTLERS_SITE_ENV`, default `/secrets/.dev.env`), which would
otherwise leave `publicUrl.host` as `butlers.invalid`. Dev takes its Secrets from BWS
(`localSecrets.source: bws`), so it never runs `make secrets-dev`.

The underlying targets, from `deploy/helm/butlers` with the matching BWS env loaded
(`make secrets-*` only for an environment still on `source: local`, i.e. prod):

```bash
set -a; source /secrets/.bws.dev.env; set +a
bws run --project-id "$BWS_PROJECT_ID" -- make image-dev    # build + push app and frontend images
make deploy-dev                                             # helm upgrade --install, waits for the API
make status-dev
```

### Iterating on a change

Commit, then ship the commit from `deploy/helm/butlers`:

```bash
bws run --project-id "$BWS_PROJECT_ID" -- make ship-dev   # build + push images, helm upgrade, wait
```

`ship-*` runs `build-push.sh` and deploys the tag it prints. Migrations run automatically: they are
an initContainer of `dashboard-api`, so every rollout runs `butlers db migrate` before the new API
starts. `dashboard-api`, `butlers-up` and the connectors use the Recreate strategy, so old and new
pods never overlap. The image installs dependencies before copying `src/`, so a commit that touches
only source rebuilds and pulls a few tens of MB (measured: `ship-dev` about 2.5 minutes, the API
unavailable for about 70 seconds, every pod ready after about 3 minutes); a `uv.lock` or `Dockerfile.base` change rebuilds
the multi-GB dependency layer and takes about 12 minutes.
Compose's hotreload has no equivalent here: `butlers-dev` is the live system and only runs
committed images. To roll back, `make deploy-dev TAG=<previous sha>` (`helm -n butlers-dev
history butlers` lists the tags).

The prod targets (`secrets-prod`, `image-prod`, `deploy-prod`) are the same with
`/secrets/.bws.prod.env`.

- `make image-*` tags images with the 12-character commit SHA and refuses a dirty worktree
  unless `ALLOW_DIRTY=1`. `make deploy-*` uses the same `TAG` (override with `TAG=<sha>`).
- `make template-dev` renders into the gitignored `_templates/`. Put per-operator overrides in
  the gitignored `values.local.yaml`; it is applied after the environment file.
- Site-specific hosts (registry, public dashboard host, OTLP endpoint, Dolt host, Wyoming URL) are
  not tracked. `scripts/site-env.sh` loads an allowlist of keys (`BUTLERS_IMAGE_REGISTRY`,
  `BUTLERS_PUBLIC_HOST`, `BUTLERS_OTLP_ENDPOINT`, `BEADS_DOLT_SERVER_HOST`,
  `LIVE_LISTENER_TRANSCRIPTION_URL`, `OLLAMA_URL`, `BUTLERS_ARCHIVE_DEAD_IDENTITIES`) from
  `$BUTLERS_SITE_ENV` (default `/secrets/.dev.env`) then `.env.local`; nothing else in those files
  is exported. Setting `TAILNET_NAME` (and `BUTLERS_NODE_NAME` for the public host) derives all the
  hosts; any key set explicitly overrides its derived value. `build-push.sh` and the chart makefile (via `scripts/k8s/site-helm-args.sh`, which
  becomes `--set` flags) source it. For `bd`, use direnv (`.envrc`) or `. scripts/site-env.sh`.
- Secrets come from two places, and `localSecrets.source` picks who owns the last two:
  - The ExternalSecret `butlers-bws` syncs only the keys listed in `externalSecrets.data`. The BWS
    project is shared with other homelab services, so it never syncs the whole project, and every
    key the chart reads is named `BUTLERS_RUNTIME_*` so its owner is obvious in BWS. Prod still
    overrides `externalSecrets.data` with its pre-prefix names (`BUTLERS_DB_USER`, ...) until
    prefixed copies exist in the prod project.
  - `butlers-runtime-probe-control` (`runtime_probe_control_signing_key`,
    `runtime_probe_control_verifiers`) and `butlers-local-env` (`DASHBOARD_AUTH_DB_USER`,
    `DASHBOARD_API_KEY`):
    - `source: local` (chart default, prod): `scripts/k8s/bootstrap-secrets.sh` creates them from
      the files named by `RUNTIME_PROBE_CONTROL_*_FILE` and from `.env.<env>`. This is also the
      rollback path.
    - `values.dev.yaml` sets `source: bws` (since 2026-10-03, bu-03myor). The dev BWS keys were
      copied from the previously running Secrets, so the dev signer in BWS is the real dev signer,
      not the committed placeholder. Dev does not provision `DASHBOARD_API_KEY`.
    - `source: bws`: two more ExternalSecrets with the same target names and key names own them,
      reading the BWS keys named in `externalSecrets.runtimeProbeControl` and
      `externalSecrets.localEnv` (explicit keys only, never the whole project). Consumers are
      unchanged. Setting a `localEnv` key name to empty omits that optional key.
      `bootstrap-secrets.sh` refuses to run against a Secret an ExternalSecret owns.
- Migrating local -> bws, in order: (1) create the `BUTLERS_RUNTIME_*` BWS keys in the environment's
  project, e.g. by copying the live Secret values with `bws secret create` without printing them
  (the signer is a real secret; prod needs its own project and store); (2) delete the out-of-band Secrets
  (`kubectl -n <ns> delete secret butlers-runtime-probe-control butlers-local-env`); (3)
  `helm upgrade` with `--set localSecrets.source=bws`; (4) `kubectl -n <ns> get externalsecret`
  shows all three `SecretSynced`. A missing required remote key gives `SecretSyncedError` and the
  `rollout status` wait in `make deploy-*` then fails. Rollback: `source=local`, delete the two
  ExternalSecrets' Secrets, rerun `make secrets-<env>`.
- Rotation: the Secrets refresh hourly, but the signer/verifier `subPath` mounts and env
  `secretKeyRef`s do not update live. Run `kubectl -n <ns> rollout restart deploy/dashboard-api
  deploy/butlers-up` after a rotation.

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
4. Verify: `make status-dev`, the dashboard at `https://butlers.example.ts.net/butlers-dev/`, an
   owner sign-in, and connector logs (`kubectl -n butlers-dev logs deploy/connector-telegram-bot`).

Rollback: `helm -n butlers-dev uninstall butlers` (PVCs are kept), restore the compose
`tailscale serve` targets (42173, 42200, 42086), and rerun `./scripts/compose.sh`.

## Security posture differences from compose

- `dashboard-api` runs `privileged` (`dashboardApi.privileged`). Compose grants the dashboard's
  Bubblewrap login sandbox an unmasked `/proc` plus a custom seccomp profile; Kubernetes can only
  express that with user namespaces (`hostUsers: false` + `procMount: Unmasked` + a node-installed
  `Localhost` seccomp profile). Setting `privileged: false` keeps AppArmor unconfined but breaks
  dashboard-driven CLI login.

  **Dropping `privileged` is not viable on the current node.** Probe run in `butlers-dev`
  (2026-10-03; server v1.36.5+k3s1, node kernel 5.15.0-194-generic, containerd overlayfs
  snapshotter; throwaway pod from the app image mounting a local-path PVC, a dummy Secret and an
  emptyDir, running a Bubblewrap bootstrap with `--unshare-user/-pid/-ipc/-uts --as-pid-1 --tmpfs /
  --proc /proc --dev /dev`; probe resources deleted afterwards):

  | Variant | Result |
  | --- | --- |
  | `hostUsers:false` + `procMount: Unmasked` + seccomp `Localhost` + AppArmor Unconfined | Admitted (`--dry-run=server` ok), pod stuck `ContainerCreating` |
  | Same, seccomp `Unconfined` (control separating profile from userns) | Same failure |
  | `privileged: true` (control) | Bubblewrap bootstrap passes, PVC/Secret/emptyDir mounts usable |

  Failure event (both userns variants, so the cause is user namespaces, not the seccomp profile):
  `failed to create containerd container: snapshotter "overlayfs" doesn't support idmap mounts on
  this host, configure slow_chown ...`. Pod user namespaces need idmapped-mount support in the
  snapshotter, which needs a newer kernel (overlayfs idmap arrives around 6.3). The Localhost
  seccomp profile was not exercised, so whether it is installed on the node is unknown and
  owner-supplied. Revisit after a kernel upgrade; until then keep `privileged: true`. Alternatives
  (a dedicated sandbox sidecar, or accepting `privileged`) are tracked as a follow-up.
- The compose egress firewall (`scripts/egress-firewall.sh`) has no equivalent yet. It only ran
  when the launcher had passwordless sudo.
