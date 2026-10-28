# Kubernetes Deployment

> **Purpose:** Run Butlers on the homelab k3s cluster with the in-repo Helm chart.
> **Audience:** Operators deploying or upgrading Butlers.
> **Prerequisites:** `kubectl` and `helm` against the k3s cluster, Docker with push access to
> `<registry-host>`, and `bws` with the dev or prod BWS env file.

## Overview

Helm on k3s is the production deployment path. `deploy/helm/butlers` installs one
release named `butlers` into one of two namespaces. The table describes repository
configuration; it does not attest that a production release is running:

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
- the observability profile (the cluster's `lgtm` stack and `otel.example.ts.net` replace it);

## Engineering tracker isolation

Owner decision bu-g2zuv2 = A (2026-10-21) retires the Decision Desk and beads
projection from the product. The Helm retirement removes the exporter,
applier CronJob, export PVC declaration, tracker ExternalSecret declaration,
runtime mounts and bridge image build. Legacy `beadsExport` values cannot
re-enable them. No live resource or credential operation is performed by a
source change; operators review the rendered upgrade and its resource pruning.
Existing export data and secrets are not manually deleted by this change.

The independent `tracker-egress` NetworkPolicy stays enabled in dev and now
selects **every pod**, with no bridge exception. `trackerEgress.enabled` retains
the existing environment posture (dev true, production false).
`trackerEgress.deniedCidrs` contains every tracker IPv4 address as `/32`;
`scripts/k8s/site-helm-args.sh` resolves the existing site host input
`BEADS_DOLT_SERVER_HOST`. Missing CIDRs fail closed when the deny is enabled.
The existing IPv4-only and complete-address preconditions still apply; this
configuration is not evidence of a live network probe or a production rollout.

The frontend pages, backend endpoints, Switchboard jobs/TOML, Telegram decision
transport and Compose/deploy export plumbing are retired in source. Stored
intent and prompt history remains. No table drop is included: code removal
must be deployed and every old reader and writer actually stopped before a
later separately reviewed destructive migration. Rolling back source does not
promise to restore historical data removed by that later migration. No live
resource or credential action is part of source retirement.

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

## Notification metadata repair evidence

Before any historical notification metadata repair, follow the read-only
[serving-writer evidence procedure](notification-metadata-repair.md). It accounts for actual
Switchboard and in-process dashboard writers, source/image/mount provenance, the required #3458
ancestry, applied migration frontier and bounded aggregate-only observations. This link grants no
live execution, deployment/restart, production access or historical-migration authority.

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

`make deploy-*` runs through `scripts/k8s/record-deploy.sh`, so each release appends one
`public.deployments` row with `source='deploy'` and `serving_mode='image'`: `success` once `dashboard-api`, `butlers-up` and the optional enabled `butlers-control`
rollouts have finished, `failed` when `helm upgrade` or a rollout fails. The row is written by
`butlers deployments record` inside the `dashboard-api` pod (`kubectl exec`), so no database
credential leaves the cluster. The target keeps the release's exit status; if the pod is
unreachable it prints that no row was recorded, and a failed write after a good release only
warns. Boot rows (`source='boot'`) are separate: a pod restart is not a release.

### Iterating on a change

Commit, then ship the commit from `deploy/helm/butlers`:

```bash
bws run --project-id "$BWS_PROJECT_ID" -- make ship-dev   # build + push images, helm upgrade, wait
```

`ship-*` runs `build-push.sh` and deploys the tag it prints. Migrations run automatically: they are
an initContainer of `dashboard-api`, so every rollout runs `butlers db migrate` before the new API
starts. Migrations only *check* the privileged bootstrap
([reviewed-bootstrap.md](../data_and_storage/reviewed-bootstrap.md)); they never create it. So on
dev `deploy-*` first runs `bootstrap-dev` (`scripts/k8s/bootstrap-db.sh dev <tag>`). That target
reruns the deployed tag's `scripts/init-db.sql`, which is idempotent:

- It runs as the CloudNativePG local superuser inside the `postgres-dev` primary pod, via
  `kubectl exec`, so no superuser password is involved.
- It takes the normal connecting login from the `butlers-bws` Secret, without printing it.

Without this step, a tag whose migrations need a newer bootstrap surface fails in the
`migrations` initContainer after the old pods are gone. `core_277` did this on 2026-10-27
(bu-8jtq10). Prod has no privileged bootstrap configured here: `bootstrap-prod` only prints a
reminder, and the operator runs `init-db.sql` by hand before `deploy-prod`. `dashboard-api`, `butlers-up` and the connectors use the Recreate strategy, so old and new
pods never overlap. The image installs dependencies before copying `src/`, so a commit that touches
only source rebuilds and pulls a few tens of MB (measured: `ship-dev` about 2.5 minutes, the API
unavailable for about 70 seconds, every pod ready after about 3 minutes); a `uv.lock` or `Dockerfile.base` change rebuilds
the multi-GB dependency layer and takes about 12 minutes.
Compose's hotreload has no equivalent here: `butlers-dev` is the live system and only runs
committed images. To roll back, `make deploy-dev TAG=<previous sha>` (`helm -n butlers-dev
history butlers` lists the tags). This renders the *current* chart with the older image and runs
that tag's bootstrap.

Do not use `helm rollback` across a chart change that removed a `helm.sh/resource-policy: keep`
resource. Rolling back to revision 13, which still declared the retired `butlers-beads-export`
PVC, failed with `no PersistentVolumeClaim with the name "butlers-beads-export" found`. The
failure is recorded as revision 15, although the PVC still exists.

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
   `alembic_version` is at `core_265` or a later core revision. Schemas below that never ran
   the split and are skipped; asking alembic to "downgrade" one to `core_261` would fail midway.
   `core_265` restores only when the last of the selected schemas leaves it, so an interrupted
   pass restores nothing and can simply be rerun.

   ```python
   from alembic import command
   from alembic.script import ScriptDirectory
   from sqlalchemy import create_engine, text
   from butlers.migrations import _build_alembic_config

   url = "<migration-role database URL>"  # e.g. read from the pod env; never print it
   script = ScriptDirectory.from_config(_build_alembic_config(url, ["core"]))
   # core_265 and every later core revision: only these schemas hold the split.
   split = {rev.revision for rev in script.walk_revisions(base="core_265", head="heads")}
   with create_engine(url).connect() as conn:
       schemas = conn.execute(text(
           "SELECT table_schema FROM information_schema.tables WHERE table_name = 'alembic_version'"
       )).scalars().all()
       core_schemas = [
           schema for schema in schemas
           if split & set(conn.execute(text(
               f'SELECT version_num FROM "{schema}".alembic_version'
           )).scalars())
       ]
   command.downgrade(
       _build_alembic_config(url, ["switchboard"], target_schema="switchboard"),
       "switchboard@sw_040",
   )
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

Compose is the retained pre-cutover off-cluster path, pending deletion in
bu-dss7he.14, and a local path for a non-live database. Production deployment
uses Helm on k3s (`make -C deploy/helm/butlers ship-prod`, or `deploy-prod` for
an already-pushed tag). `scripts/compose.sh --prod` retains the pre-cutover
Compose configuration; it does not authorize starting a fleet against a database
already owned by Kubernetes. In dev the launcher checks both `butlers-up` and
`butlers-control`, including observed replicas while scaled-down pods remain;
either active role refuses a competing fleet. Production retains the unsplit
`butlers-up` replica check in `butlers`. Its `BUTLERS_ALLOW_COMPOSE_WITH_K8S=1` override is only for a
non-live database, never a live-fleet cutover shortcut.
With a selected local Kubernetes context, a failed or malformed ownership query
refuses all Compose lifecycle actions. Only successful absent/zero responses
permit startup. Standalone hosts without kubectl or a selected context remain
supported; local configuration is inspected without contacting a cluster, and
invalid configuration refuses rather than being treated as standalone.

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
sessions against the same database. Stop both dev runtime Deployments and
confirm their pods terminate before returning ownership to Compose or an
unsplit release. For the reverse cutover, stop the complete Compose project
before either Kubernetes role starts. A source preflight is not an atomic lock;
the live aggregate cap and exclusive transition still need operational proof.

## Security posture differences from compose

### Accepted dashboard API posture

The owner accepted the existing k3s `dashboardApi.privileged: true` posture on
2026-10-04 (`bu-hkjj9y`, option A). It retains dashboard-driven Bubblewrap CLI
login. The [shared chart values](../../deploy/helm/butlers/values.yaml) already
set this value to `true`; the dev and prod overlays inherit it, and the
[dashboard API template](../../deploy/helm/butlers/templates/dashboard-api.yaml)
applies it only to the `dashboard-api` application container. No chart value
change is needed to record this decision.

This is the accepted legacy dashboard login posture. It does not waive Compose's
deployment constraints, change credential custody or approval authority, or
relax the separate session-host and invocation-isolation requirements in
[RFC 0038, D3](../../about/legends-and-lore/rfcs/0038-goal-runtime-and-capability-authorization.md#d3-session-sandbox).
The goal-runtime cutover remains separate work.

### Configured compensating controls and their limits

The following controls are present in repository configuration. Their deployment
and effectiveness have not been re-verified by recording this decision.

| Control | Configured behavior | Limit |
| --- | --- | --- |
| Host ingress restriction | With `hostIngress.restrict: true` (the shared default), the [host-ingress NetworkPolicy](../../deploy/helm/butlers/templates/networkpolicy.yaml) selects dashboard API, frontend and OwnTracks pods and admits ingress from `hostIngress.nodeSourceCidr` or pods in the same namespace. | This is an ingress rule, not owner authentication or an egress allowlist. It allows same-namespace pods; actual CNI enforcement and the node-source address require deployment evidence. |
| NodePort source preservation | The dashboard NodePort Service uses `externalTrafficPolicy: Local`, preserving client source IPs for the configured ingress/proxy checks. Tailscale Serve is the documented HTTPS entry point. | Source preservation and the intended proxy path do not prove the live firewall, Serve configuration, or protection of every node address. Network membership does not identify the owner. |
| Dashboard owner authentication | The [dev overlay](../../deploy/helm/butlers/values.dev.yaml) enables the chart's canonical origin, RP ID and trusted-proxy configuration. The [owner-authentication boundary](../identity_and_secrets/dashboard-owner-auth.md) independently checks dashboard request authority. | The [prod overlay](../../deploy/helm/butlers/values.prod.yaml) has `dashboardAuth.enabled: false` and does not supply that browser configuration. Do not infer live enrollment, working proxy attribution, or prod passkey protection from the chart; optional API-key provisioning is separate. |
| Declared mounts | The dashboard template declares its PVC and Secret mounts, with backups and the runtime-probe signing-key mount read-only. It declares no Docker socket or `hostPath` mount. | Read-only mounts and application authentication do not contain a privileged container compromise or protect the host from an escape. They do not replace the CLI sandbox's existing isolation and credential-handling requirements. |

The accepted posture retains broad privilege in the dashboard API container.
Ingress restriction, HTTPS and dashboard authentication reduce ordinary network
exposure; they do not establish a host-security boundary for that container.
The host remains trusted under the [security model](../../about/heart-and-soul/security.md#threat-boundaries).
Neither this decision nor the historical compatibility probe proves current
sandbox isolation, credential isolation, or node hardening.

### Historical compatibility probe

Dropping `privileged` failed on the node probed in `butlers-dev` on 2026-10-03.
That dated evidence reported server v1.36.5+k3s1, kernel
5.15.0-194-generic and the containerd overlayfs snapshotter. A throwaway pod from
the app image mounted a local-path PVC, a dummy Secret and an emptyDir and ran
a Bubblewrap bootstrap with `--unshare-user/-pid/-ipc/-uts --as-pid-1 --tmpfs /
--proc /proc --dev /dev`. Probe resources were deleted afterwards.

| Variant | Result |
| --- | --- |
| `hostUsers:false` + `procMount: Unmasked` + seccomp `Localhost` + AppArmor Unconfined | Admitted (`--dry-run=server` ok), pod stuck `ContainerCreating` |
| Same, seccomp `Unconfined` (control separating profile from userns) | Same failure |
| `privileged: true` (control) | Bubblewrap bootstrap passes, PVC/Secret/emptyDir mounts usable |

Both userns variants reported the same failure event:
`failed to create containerd container: snapshotter "overlayfs" doesn't support idmap mounts on
this host, configure slow_chown ...`. This distinguished the observed
user-namespace/idmapped-mount failure from the seccomp profile. The Localhost
seccomp profile was not exercised; its presence on the node remains unknown
from this probe. The historical follow-up identified a newer kernel (overlayfs
idmap around 6.3) as a possible prerequisite, not proof that an upgrade would
make the complete alternative work. These are dated probe facts, not a current
node inventory.

Option A is accepted. A dedicated sandbox sidecar or a userns mode was not
selected by that decision. Any later posture change needs its own authorization
and current compatibility evidence; this documentation update authorizes no
re-probe, kernel change, provisioning or rollout.

### Egress gap

The compose egress firewall (`scripts/egress-firewall.sh`) has no equivalent yet.
It only ran when the launcher had passwordless sudo. The host-ingress policy
above does not supply that parity; the separate tracker-egress policy does not
provide the general private-network allowlist either. Egress parity remains
tracked in `bu-viat6h.6`.
