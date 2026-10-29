# Docker Deployment

> **Purpose:** Document the Docker Compose setup for running Butlers.
> **Audience:** Operators, DevOps engineers, anyone deploying Butlers.
> **Prerequisites:** [Environment Config](environment-config.md), Docker and Docker Compose installed.

> **Production path:** Use Helm on k3s in namespace `butlers`; see
> [Kubernetes Deployment](kubernetes-deployment.md#deploy). This page covers the retained
> development-only Compose path. Complete dev Compose retirement belongs to bu-dss7he.19.
>
> **Note:** The live dev stack (namespace `butlers-dev`, database butlers-db-dev) no longer runs
> under Compose. It runs on the homelab k3s cluster from the Helm chart; see [Kubernetes
> Deployment](kubernetes-deployment.md). `./scripts/compose.sh` dev mode refuses to start while that
> release is running (`BUTLERS_ALLOW_COMPOSE_WITH_K8S=1` overrides it, for a non-live database
> only). Compose still serves local stacks against a non-live database. The restore-drill executor
> and live-listener audio are not ported yet (bu-viat6h.4), and a compose stack that includes them
> would also start a second butlers-up against the same database, so the guard blocks them too.

## Overview

Butlers runs as a set of containers defined in the repository-root
`docker-compose.yml` and launched through `scripts/compose.sh`. There is **one
shared PostgreSQL database** (external to Compose, reached over the network) in
which every butler owns its own schema; the butler daemon, the dashboard API,
and the external-connector services all connect to it. There are **no per-butler
containers** and **no local containerized Postgres**. Production keeps every
butler in one `butlers-up` process. The prepared dev stage separates Switchboard
into `butlers-control` and excludes it from the domain process, reserving one
plus four session slots. The database lives on a remote host configured via
`.env.dev`.

The application image (`butlers-app`) is built on `butlers-base` (Python 3.12 +
Node.js 22 + the LLM runtime CLIs + `uv`).

## Quick Start

Use the launcher; it selects the database target, sources the matching env file,
sets the per-mode host ports, and configures Tailscale serve:

```bash
./scripts/compose.sh                      # dev database + hotreload (base stack by default)
./scripts/compose.sh --with-restore-drill # dev stack plus protected restore-drill executor
./scripts/compose.sh --no-hotreload   # dev DB, prod-style baked image (no source mount)
```

Production deployment uses the [Helm workflow](kubernetes-deployment.md#deploy).
The legacy production command, production launcher flag and local observability
overlay have been removed. Development Compose keeps project `butlers-dev` and
its existing ports and isolation until its separately governed retirement.

Ordinary dev uses the base file plus `docker-compose.control-stage.yml`, so it does not need a
restore-drill executor secret, endpoint, or root firewall wrapper. To start the
privileged boundary in dev, pass `--with-restore-drill` after completing its
operator bootstrap. Its selection is explicit: finding a configured secret does
not turn it on. The explicit protected dev path fails closed when its prerequisites are missing.

If an opted-in `butlers-dev` project is returned to ordinary dev, the launcher
runs the ordinary dev configuration's `down --remove-orphans` before startup. That
removes the former relay and executor rather than leaving privileged services
running as orphans. For an ordinary unsplit Compose model, base-only `down --remove-orphans`
performs that cleanup without evaluating protected configuration. The staged dev launcher
includes the base and control-stage files instead; it still excludes the protected
restore-drill fragment during ordinary cleanup. Keep passing `--with-restore-drill` on later dev launches
when the protected services must remain enabled.

## Read-only protected-topology inspection

The ordinary `docker-compose.yml` deliberately omits the restore-drill executor.
To inspect the complete protected topology without starting, stopping, creating,
or restarting any container, use the verb-restricted helper:

```bash
./scripts/restore-drill-compose-inspect.sh ps
./scripts/restore-drill-compose-inspect.sh logs restore-drill-executor --tail=100
./scripts/restore-drill-compose-inspect.sh config --services
```

It merges the base and protected Compose files but accepts only read-only
`config`, `ps`, and `logs` operations. Never use those merged files with `up`;
`scripts/compose.sh --with-restore-drill` performs the required stop, versioned root preparation, create,
exact-topology firewall attestation, and only then start either restore-drill
service.

## Images

Two images, both pinned by digest where practical:

**`butlers-base`** (`Dockerfile.base`) -- the shared runtime:

1. **`python:3.12-slim`** base (digest-pinned).
2. System deps: curl, ca-certificates, gnupg, `git`, `gh`, `postgresql-client`, ripgrep.
3. **Node.js 22** via NodeSource -- required by the LLM runtime CLIs.
4. **LLM runtime CLIs**, pinned to exact versions: `@anthropic-ai/claude-code`,
   `@google/gemini-cli`, `@openai/codex`, `opencode-ai`.
5. **uv** package manager.

**`butlers-app`** (`Dockerfile`) -- the application, `FROM butlers-base`:

1. A Go builder stage compiles the bundled helper binaries.
2. Source (`pyproject.toml`, `src/`, `alembic.ini`, `alembic/`, …) is copied in
   and installed with `uv sync --frozen --no-dev` (the Go WhatsApp bridge is included separately).
3. Entrypoint `uv run --frozen --no-dev butlers`; default command
   `run --config /etc/butler`.

## Services

`docker-compose.yml` is the reference for every service's image, command, ports, mounts, and
dependencies. All application containers use the `butlers-app` image and read the shared database
env (`x-postgres-env` anchor) from the sourced `.env.dev` file; every host port binds to
`127.0.0.1` (Tailscale Serve is the external entry point). What the file does not explain:

- **No database service.** PostgreSQL is external, reached via `POSTGRES_HOST` / `POSTGRES_PORT`,
  which `scripts/compose.sh` requires in `.env.dev`. One database holds every
  butler's schema plus the shared `public` schema, so there is no local data volume to manage.
- **Dev runs Switchboard in `butlers-control` and domains in `butlers-up`.**
  The launcher selects `docker-compose.control-stage.yml` (Compose >=2.24.4),
  starts exactly one control/domain pair, and partitions the existing session
  budget as 1+4. Control publishes the unchanged Switchboard service port and its `/health`;
  domains use `butlers up --exclude switchboard`. Helm production retains its
  independently configured unsplit process.
  Each keeps per-CLI runtime state in named volumes and runs under Docker's default AppArmor
  profile: runtime sessions launch Codex with `--dangerously-bypass-approvals-and-sandbox`, so
  neither creates a user namespace (only `dashboard-api`'s CLI-auth sandbox does). Each mounts
  only the runtime-probe verifier keyring, never the signing key.
- **`dashboard-api` is the only signer.** It alone receives the runtime-probe *signing* key
  (secret, mode `0400`) plus the verifier keyring. Both mounts default to tracked, unprovisioned
  placeholders, so the stack boots on a machine that has provisioned neither --- with the control
  plane closed. See [Runtime-Probe Control Keys](runtime-probe-control-keys.md).
- **Dashboard CLI sandbox inputs are image-bound.** Dashboard runtime CLI children use the base
  image's immutable `runtime-cli-sandbox-inputs.json` version-3 asset, generated after the final
  PID1 shim install and recording the shim plus its exact interpreter/library closure separately
  from provider inputs. The application rejects an old, malformed, or mismatched asset before
  allocating a sandbox identity, so upgrades and rollbacks use a matched application/base-image
  pair, never runtime dependency discovery or a provider-derived fallback.
- **Host prerequisite for the dashboard CLI sandbox.** Hosts that run `dashboard-api` with
  `kernel.apparmor_restrict_unprivileged_userns = 1` (typical of Ubuntu 24.04 or newer) must allow its Bubblewrap sandbox to create a user namespace, or the sandbox fails
  closed and provider tests and auth-health probes run nothing. See
  [Host prerequisite: unprivileged user namespaces](#host-prerequisite-unprivileged-user-namespaces).
- **`frontend-dev` installs with `npm ci`** (never `npm install`), so startup never rewrites the
  tracked `frontend/package-lock.json`; `tests/scripts/test_compose_frontend_dev_lockfile_guard.py`
  holds that invariant. It runs only under the `dev` profile that `scripts/compose.sh` activates.
- **`migrations` is a one-shot** (`db migrate`) that every app service depends on via
  `service_completed_successfully`. A prior exited container is not evidence
  that a changed image has run its migrations; use a fresh one-shot migration invocation.
- **One container per connector**, sharing the `x-connector-env` anchor.
  `connector-whatsapp-user` owns the single authenticated WhatsApp bridge socket
  (`wa_bridge_socket`), shared with the Messenger butler; `connector-live-listener` runs only under
  the `audio` profile.
- **`oauth-gate` blocks startup** on missing OAuth tokens (`--skip-oauth-check` bypasses it).
- **`--hotreload`** swaps in `butlers-up-hotreload`, `dashboard-api-hotreload` and,
  in dev only, `butlers-control-hotreload`. The launcher scales the corresponding
  baked processes to zero; it never runs two reservations for the same role.
  These services volume-mount `src/` for live edits. See [the stage contract,
  session network and rollback](../architecture/key-absent-control.md).

## Host prerequisite: unprivileged user namespaces

Applies to a host that runs the `dashboard-api` container. The dashboard's runtime-CLI
authentication sandbox (`src/butlers/cli_auth/sandbox_platform.py`, Bubblewrap at `/usr/bin/bwrap`
inside the image) creates a user namespace. When it cannot, it raises `SandboxUnavailableError`
and runs nothing; there is no unsandboxed fallback, and this page does not change that. `butlers-up`
is not affected: Codex runs there with `--dangerously-bypass-approvals-and-sandbox`.

### Is this host affected?

```bash
. /etc/os-release && echo "$VERSION_ID"
sysctl kernel.apparmor_restrict_unprivileged_userns
```

- The sysctl value decides, not the OS version. `VERSION_ID` is only a hint for where to look.
- Affected: the sysctl prints `= 1` (typical of Ubuntu 24.04 or newer). `apparmor:unconfined` on the
  container does not help, because the kernel restricts unconfined processes too.
- Not affected: the sysctl is missing (`cannot stat ...`) or prints `= 0`. The k3s nodes probed on
  2026-10-03 ran kernel 5.15 (22.04-era) and were in this group; that is a dated probe, not a current
  node inventory (see [Kubernetes Deployment](kubernetes-deployment.md)).
- Symptom when affected: `bwrap: setting up uid map: Permission denied`.

### Recommended: a scoped AppArmor profile

Grant `userns` to one named profile and run only `dashboard-api` under it, instead of loosening the
whole host. Create `/etc/apparmor.d/butlers-dashboard-api`:

```text
abi <abi/4.0>,
include <tunables/global>

profile butlers-dashboard-api flags=(unconfined) {
  userns,
}
```

Load it, then confirm it is loaded:

```bash
sudo apparmor_parser -r /etc/apparmor.d/butlers-dashboard-api
sudo aa-status | grep butlers-dashboard-api
```

Then set `BUTLERS_DASHBOARD_APPARMOR_PROFILE=butlers-dashboard-api` in the environment file the
launcher sources (`.env.dev`); do not edit the tracked `docker-compose.yml`. The
variable feeds the `apparmor` entry of the `security_opt` of BOTH the `dashboard-api` and
`dashboard-api-hotreload` services (the launcher runs the hotreload one under `--hotreload`); the
other two entries stay, and with the variable unset the entry remains `apparmor:unconfined`. The
profile is otherwise unconfined, so the container's confinement is the same as today; only
user-namespace creation is newly allowed, and only for these containers. Reload the stack with the
development launcher.

Verify from the container that is actually running, `dashboard-api` or `dashboard-api-hotreload`
(this proves user-namespace creation only, not the full sandbox):

```bash
docker compose exec <running-service> /usr/bin/bwrap --unshare-user --ro-bind / / /usr/bin/true
```

No output and exit status 0 means the host is ready. `setting up uid map: Permission denied` means it
is not. Then run a provider test from the dashboard to prove the real sandbox.

The profile and the compose change were written from the Ubuntu AppArmor `userns` documentation and
have not been run on a 24.04 host from this repository. Run the verify command before relying on
them, and report the result.

### Fallback: relax the sysctl

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
echo 'kernel.apparmor_restrict_unprivileged_userns = 0' | sudo tee /etc/sysctl.d/60-butlers-userns.conf
```

This needs no compose change and is what the nightly `exact-image-sandbox` runner does
(bu-3kyyyy), but it lowers a kernel hardening default for every process on the host.

[decision] Prefer the scoped profile. It limits the grant to the one container that needs it, while
`sysctl=0` is host-wide. The sysctl stays documented as the fallback and is correct for ephemeral CI
runners.

### Kubernetes

The Helm chart runs `dashboardApi.privileged: true` instead (`deploy/helm/butlers/values.yaml`), and
the k3s nodes probed on 2026-10-03 (kernel 5.15) predate the restriction, so this page's steps did not
apply to them then. That probe is dated, not current: run the detection command above on any node,
especially after an upgrade to Ubuntu 24.04 or newer, and re-check the sandbox; see
[Kubernetes Deployment](kubernetes-deployment.md).

### Provisioning

Host provisioning (Ansible) is outside this repository. Put the profile file, the `apparmor_parser`
step and, if used, the sysctl drop-in in that role for every host that runs `dashboard-api`.

## Production deployment

Use `make -C deploy/helm/butlers ship-prod` or `deploy-prod TAG=<tag>`; see
[Kubernetes Deployment](kubernetes-deployment.md#deploy). Helm records release
outcomes through the retained `butlers deployments record` command. The
`public.deployments` ledger and System page remain the provenance surfaces.
Development Compose is not a production fallback.

## Environment Variables

The database target is **not** hard-coded in Compose; `scripts/compose.sh`
sources `.env.dev` and exports the per-mode ports before invoking
`docker compose`. The shared DB env (`x-postgres-env`) requires:

```yaml
POSTGRES_HOST: <from .env.dev>   # required
POSTGRES_PORT: 5432                          # default
POSTGRES_USER: butlers                       # default
POSTGRES_PASSWORD: <from .env.dev>   # required
POSTGRES_SSLMODE: <optional>
```

Development uses project `butlers-dev`, Switchboard host port 42100, dashboard
API 42200, frontend 42173 and OwnTracks 42086. The launcher's ownership guard
refuses a competing Kubernetes fleet. An override is only for a non-live database;
a `dev` label does not establish that the configured endpoint contains no real data.

## Implementation Notes

- `scripts/compose.sh` rebuilds `butlers-base:latest` when the `butlers.base.dockerfile_sha` label
  differs from `Dockerfile.base`; bump pinned runtime CLIs in `Dockerfile.base`, since an app-image
  rebuild alone does not pick up base-layer tools.
- Butler MCP ports `41100-41112` sit inside Linux's default ephemeral range, so `butlers-up` and
  `butlers-up-hotreload` set `net.ipv4.ip_local_reserved_ports=41100-41112`; without it an outbound
  DB connection can claim a listener port before startup.
- The dev Postgres is capped at `max_connections=200`; keep the `BUTLERS_DB_POOL_*` and
  `BUTLERS_API_DB_POOL_*` defaults conservative or late-starting butlers fail to connect.
- Deployment-provisioned files: credentials go in top-level `secrets:` (service `mode: 0400`),
  other material in `configs:` with an absolute `target:`. In `docker-compose.yml` use
  `${VAR:-./deploy/<thing>-unprovisioned.json}` pointing at a tracked inert placeholder the parser
  rejects, so the stack boots and the feature stays off; `${VAR:?}` is only for opt-in overlays.
  Assert "never receives the key" as `"secrets" not in service`.
- A second `secrets:`, `configs:` or `depends_on:` key in one service block is silently dropped by
  YAML last-key-wins, and `yaml.safe_load` tests pass on the survivor. Run a duplicate-key scan over
  each service mapping before calling a compose edit verified.

## Related Pages

- [Environment Config](environment-config.md) -- Full environment variable reference
- [Grafana Monitoring](grafana-monitoring.md) -- Observability setup
- [Troubleshooting](troubleshooting.md) -- Common deployment issues
- [Image Bump Procedure](image-bump-procedure.md) -- How to update pinned service image tags

## Dashboard owner access

Use the canonical Tailscale Serve HTTPS browser entry point and the
[owner authentication runbook](../identity_and_secrets/dashboard-owner-auth.md).
Starting Compose does not authorize the first browser visitor. A keyless instance
requires host approval of the exact browser-bound intent before passkey
registration; later sign-in uses the passkey without another host action.

Keep the fixed origin/RP identity, deployment-specific cookies and restricted
authentication database role coherent across API workers. Configuration changes
and restores require the explicit host reconciliation/revocation procedure.
Never downgrade a reachable instance to an empty-key legacy image. Proxy setup,
migration, restart and live owner enrollment are separate operational steps,
not side effects of specification adoption or a successful build.
