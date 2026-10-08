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

### Rolling back the conversation-identity split (core_265, sw_041)

A plain `make deploy-dev TAG=<previous sha>` is not enough to undo bu-7exe4.2. The new image
migrates data: `core_265` collapses Telegram anchors into `public.core_265_*` snapshots, and
`sw_041` backfills `external_conversation_id` into `switchboard.message_inbox`. Older images do
not know either revision, so their `butlers db migrate` initContainer cannot start on the
upgraded schema. The rollout is stop-the-world in both directions. Connectors and Switchboard
must flip together: the old `IngestEventV1` (`extra="forbid"`) rejects the new
`external_conversation_id` / `reply_target_ref` fields, and the new validator rejects a
`telegram_bot` envelope that lacks them.

1. Stop every writer: scale `butlers-up`, `dashboard-api`, and every connector Deployment to 0,
   and confirm no pod is left.
2. With the **current** image, which still contains both revisions, and the migration-role
   database URL, downgrade `sw_041`. Then downgrade core to `core_261` in every schema whose
   `alembic_version` holds a core revision. `core_265` restores only when the last of those
   schemas leaves it, so a partial pass changes nothing.

   ```python
   from alembic import command
   from sqlalchemy import create_engine, text
   from butlers.migrations import _build_alembic_config

   url = "<migration-role database URL>"  # e.g. read from the pod env; never print it
   command.downgrade(
       _build_alembic_config(url, ["switchboard"], target_schema="switchboard"),
       "switchboard@sw_040",
   )
   with create_engine(url).connect() as conn:
       schemas = conn.execute(text(
           "SELECT table_schema FROM information_schema.tables WHERE table_name = 'alembic_version'"
       )).scalars().all()
       core_schemas = [
           schema for schema in schemas
           if conn.execute(text(
               f'SELECT count(*) FROM "{schema}".alembic_version WHERE version_num LIKE \'core_%\''
           )).scalar()
       ]
   for schema in core_schemas:
       command.downgrade(_build_alembic_config(url, ["core"], target_schema=schema), "core@core_261")
   ```

   The last `core_265` downgrade verifies its restore against the snapshots. If a row written
   after the upgrade holds an original anchor identity, it raises `core_265 downgrade cannot
   restore ...`, and the whole downgrade rolls back with the snapshots kept. Resolve the named
   conflict and rerun. `sw_041` downgrade only drops its index; the backfilled JSON key is inert
   to older code.
3. Only then `make deploy-dev TAG=<previous sha>` (or `scripts/k8s/deploy-dev.sh <sha>`), which
   restarts the connectors and Switchboard on the old envelope together.

Never start the old image against the upgraded schema, and never run old connectors against the
new Switchboard (or the reverse). Upgrading again later re-runs `core_265` from fresh
snapshots.

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

## Current dev workflow and retained Compose roles

The live dev fleet runs in `butlers-dev` from committed images. Use the dev
[deployment workflow](#deploy), not `scripts/compose.sh`; there is no live
hotreload. The original Compose-to-k3s cutover is complete. Its earlier
`make secrets-dev` staging recipe predates dev's BWS-owned ExternalSecrets and
is not the current deployment or rollback procedure.

Compose remains a supported production/off-cluster deployment path and a local
path for a non-live database. `scripts/compose.sh --prod` retains the production
Compose configuration; it does not authorize starting a fleet against a database
already owned by Kubernetes. The launcher checks `butlers-up` replicas in the
matching namespace (`butlers-dev` for dev, `butlers` for prod) and refuses a
competing fleet. Its `BUTLERS_ALLOW_COMPOSE_WITH_K8S=1` override is only for a
non-live database, never a live-fleet cutover shortcut.

The protected restore-drill executor remains Docker-only: it attests Docker
cgroups and host iptables. Production Compose includes the protected fragment;
dev Compose includes it only with `--with-restore-drill` (bu-viat6h.4). That flag
starts a Compose fleet too, so it must not be used against the live k3s-owned
dev database. Kubernetes restore drills need the separate attestation/isolation
design before they can be enabled. The audio live-listener and optional Compose
observability profile also remain outside this chart; current dev telemetry uses
the cluster LGTM stack instead.

Normal dev image rollback redeploys an already-pushed tag through
`scripts/k8s/deploy-dev.sh <sha>`. Returning database ownership to Compose is a
separate, coordinated cutover: never run two sets of schedulers and connector
sessions against the same database.

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
