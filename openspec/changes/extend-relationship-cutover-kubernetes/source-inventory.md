# Kubernetes cutover source inventory

This is a complete inventory of the checked-in chart's database-credentialed
entry seams at source epoch `77722227d9e9481c282b97dd66a58961a05927dc`, plus
explicit external and unsupported categories. It is not a live process census
or an exhaustive behavioral mutator proof. Actual image contents, release
values, admission configuration, roles, node processes and cluster objects
remain UNKNOWN. Each future collector must discover them independently.

## Checked-in start and controller seams

| Actor / entrypoint | Actual source | Required future disposition |
| --- | --- | --- |
| Dev build/ship or tagged redeploy | [deploy-dev.sh](../../../scripts/k8s/deploy-dev.sh), [build-push.sh](../../../scripts/k8s/build-push.sh), [chart makefile](../../../deploy/helm/butlers/makefile), [site arguments](../../../scripts/k8s/site-helm-args.sh) | Fence before any deploy/ship/rollback act; resolve the exact ordered values and overrides without invoking secret injection or site commands during proof rendering. No dirty build, mutable tag, branch warning or `ALLOW_DIRTY` exception establishes target identity. |
| `dashboard-api` Deployment, its ReplicaSets and Pods | [dashboard template](../../../deploy/helm/butlers/templates/dashboard-api.yaml) | Database clients in the application and `migrations` initContainer; init runs `db migrate` on every replacement Pod before API start. Recreate is local rollout ordering, not exclusion across workloads or concurrent invokers. |
| `butlers-up` Deployment, ReplicaSets and Pods | [fleet template](../../../deploy/helm/butlers/templates/butlers-up.yaml) | All discovered roster daemons and their spawned runtime descendants belong to this actor; startup/probes can recreate clients. The command performs `uv sync --dev` at runtime, which is incompatible with immutable offline proof until separately repaired/verified. |
| Fleet startup, provisioning and per-chain migration | [lifecycle.py](../../../src/butlers/lifecycle.py), [cli.py](../../../src/butlers/cli.py), [migrations.py](../../../src/butlers/migrations.py), [online environment](../../../alembic/env.py) | Runtime pools/log handlers exist before core/butler migration; `_upgrade_chain` targets head and environment schema preflight commits separately. The future same-connection admission/ceiling and complete unsigned binding are not implemented. No automatic drain/reorder is assumed. |
| Connector Deployment for every enabled values entry | [connectors template](../../../deploy/helm/butlers/templates/connectors.yaml), [values](../../../deploy/helm/butlers/values.yaml) | All receive shared PostgreSQL environment keys. Current default entries are telegram-bot, telegram-user, whatsapp-user, google-calendar, spotify, steam, owntracks, activitywatch, home-assistant, gmail, google-drive, google-health. Exact enabled set comes from the authorized render, not this default list. Conservatively quiesce all, without claiming each directly mutates Relationship. |
| OAuth initContainers in fleet and OAuth connectors | [helpers](../../../deploy/helm/butlers/templates/_helpers.tpl), [oauth_gate.py](../../../scripts/oauth_gate.py) | Receive PostgreSQL credentials and open independent sessions; include before main-container admission. Plain wait-for helpers have no DB environment in this template, but their command/image/volume identity still belongs to the Pod set. |
| Backup CronJob and every extant Job/Pod | [cronjobs template](../../../deploy/helm/butlers/templates/cronjobs.yaml), [backup script](../../../deploy/backup/pg_dump.sh) | Shared database credentials; `suspend` blocks later schedules but neither cancels active Jobs nor prevents manually created Jobs. The template references `.Files.Get "files/pg_dump.sh"` through a tracked symbolic link (Git mode `120000`) to `../../../backup/pg_dump.sh`; its target is the existing `deploy/backup/pg_dump.sh`. The installed Helm source loader includes the linked public script in a source-only render. The future authorized render must still verify packaging and immutable executable identity; this source observation does not prove live backup effectiveness. |
| Beads export CronJob and extant Job/Pod | [export template](../../../deploy/helm/butlers/templates/beads-export.yaml), [cycle](../../../deploy/helm/butlers/files/beads_cycle.sh), [applier](../../../scripts/beads_decision_applier.py) | Own Dolt authority plus shared PostgreSQL credentials. Include the source-owned PostgreSQL decision-intent work; never expose Dolt credentials or turn this bridge into a cutover signer. |
| Local bootstrap/provision and restore actors | [init-db.sql](../../../scripts/init-db.sql), [restore script](../../../scripts/pg_restore.sh), [verification](../../../scripts/pg_verify_restore.sh), [restore contract](../../../docs/operations/backup-restore.md) | Privileged bootstrap and migration/restorer identities can alter target state outside Helm. These paths are not Kubernetes cutover entrypoints today. Deny during the window or allocate an exact supported admission path; do not execute them from this proposal or call their current catalogs historical proof. |
| Dashboard direct pools, router mutation and fleet tools | [API pool manager](../../../src/butlers/api/db.py), [Relationship API](../../../roster/relationship/api/router.py), [central writer](../../../roster/relationship/tools/relationship_assert_fact.py) | Content-blind complete mutator/code binding, not just the daemon or central insert; API and runtime children must be quiesced even when receiver health remains good. |
| Compose and host process competitors | [compose.sh](../../../scripts/compose.sh), [deploy.py](../../../src/butlers/core/deploy.py) | Retain existing Kubernetes-owned DB refusal. Their eventual Compose fence remains separate. Other projects/checkouts, raw Alembic/psql, host `uv run`, containers or restored clones with the same endpoint require inventory or explicit exclusion; neither a namespace label nor a chart release contains them. |

The shared [postgresEnv helper](../../../deploy/helm/butlers/templates/_helpers.tpl)
projects `GIT_SHA` from `image.tag`; the makefile defaults to a short SHA tag,
while [Dockerfile](../../../Dockerfile) bakes a full build argument. Thus the
running environment's `GIT_SHA` is not authoritative image/source identity.
The Dockerfile copies `src`, `alembic`, `scripts` and `roster` into the image.
The inspected fleet/API templates do not bind-mount `roster`, but all volumes,
init steps, executable overrides and image layers must be verified; image tags,
claimed SHAs and no-roster-mount observations do not prove their bytes.

## Discovery closure and credential rule

The future collector inventories every Pod in every state, init/normal/ephemeral
container, restart history and controller template that can recreate a client:
Deployment, ReplicaSet, Job, CronJob, StatefulSet, DaemonSet, standalone Pod,
custom controller and autoscaler, including ownerless or foreign-labeled objects.
The inspected chart creates Deployments and CronJobs only; the other kinds are
required unknown/negative categories, not claims of deployed objects.

An actor is conservatively credentialed if its resolved template has PostgreSQL,
libpq, DATABASE_URL, `*_DB_*` or restore-executor/proxy key names; a DB credential
reference or mount; unknown `envFrom` or opaque injected credentials; or an
independently discovered route/identity capable of reaching the target. The
collector records key NAMES and object references/UID/resourceVersion only,
never values, raw environments, full manifests with secret data, or user data.
Unknown Secret/ConfigMap indirection is not a noncredentialed exemption.

The namespace/release labels are discovery hints, not the actor boundary.
External clients, other namespaces/releases, port-forwards/proxies, node/host
processes and offline reconnecting clients require an authorized full inventory
and enforceable exclusion. Missing permission to list/watch a required class,
read safe source identity or observe database sessions fails closed. An empty
filtered list, zero replicas, completed rollout, absent heartbeat or elapsed
grace is not complete old-writer absence.

Frontend and log-cleanup templates do not receive PostgreSQL credentials at this
epoch. They may remain only after their actual projected inputs and descendants
are classified. Existing [host ingress policy](../../../deploy/helm/butlers/templates/networkpolicy.yaml)
controls incoming HTTP, and tracker-egress controls Dolt; neither is the required
database cutover fence. External Secrets can rotate shared credentials while
proof runs: reference UID/resourceVersion and all source changes invalidate the
prepared binding rather than silently copying secret values into evidence.

## Unsupported paths and residual trust

Raw Helm/kubectl apply, rollback, scale, rollout restart, direct Pod/Job creation,
`kubectl exec` or ephemeral-container insertion, alternate charts/checkouts,
unknown operators/GitOps reconcilers, host/raw database programs and image/code
overlays are unsupported until each actual seam participates in the adopted
admission fence. A future collector must refuse their presence or inability to
exclude them. Existing `suspend`, Recreate and Forbid do not supply that fence.

Trusted host, cluster administrator and privileged database actors can bypass a
managed path, forge source metadata or erase historical state. The design does
not claim protection against them; exact window authorization prohibits such
bypass. A stronger guarantee would need its own changed trust boundary. No
credential rotation, grant, new database role, policy, admission webhook or
host installation is activated by this source inventory.
