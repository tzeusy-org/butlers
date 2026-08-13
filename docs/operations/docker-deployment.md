# Docker Deployment

> **Purpose:** Document the Docker Compose setup for running Butlers.
> **Audience:** Operators, DevOps engineers, anyone deploying Butlers.
> **Prerequisites:** [Environment Config](environment-config.md), Docker and Docker Compose installed.

## Overview

Butlers runs as a set of containers defined in the repository-root
`docker-compose.yml` and launched through `scripts/compose.sh`. There is **one
shared PostgreSQL database** (external to Compose, reached over the network) in
which every butler owns its own schema; the butler daemon, the dashboard API,
and the external-connector services all connect to it. There are **no per-butler
containers** and **no local containerized Postgres** -- a single `butlers-up`
container runs every butler in the roster, and the database lives on a remote
host configured via `.env.dev` / `.env.prod`.

The application image (`butlers-app`) is built on `butlers-base` (Python 3.12 +
Node.js 22 + the LLM runtime CLIs + `uv`).

## Quick Start

Use the launcher; it selects the database target, sources the matching env file,
sets the per-mode host ports, and configures Tailscale serve:

```bash
./scripts/compose.sh                      # dev database + hotreload (base stack by default)
./scripts/compose.sh --with-restore-drill # dev stack plus protected restore-drill executor
./scripts/compose.sh --prod               # production database, baked image + protected executor
./scripts/compose.sh --no-hotreload   # dev DB, prod-style baked image (no source mount)
```

`--prod` and the default `dev` run under different Compose project names
(`butlers` vs `butlers-dev`) and different host ports (see
[Environment Variables](#environment-variables)), so both stacks can run on the
same machine at once. For production **redeploys**, prefer `butlers deploy` over
any direct Compose lifecycle command -- see [Production Deploys](#production-deploys-butlers-deploy).

Ordinary dev deliberately uses only `docker-compose.yml`, so it does not need a
restore-drill executor secret, endpoint, or root firewall wrapper. To start the
privileged boundary in dev, pass `--with-restore-drill` after completing its
operator bootstrap. Its selection is explicit: finding a configured secret does
not turn it on. `--prod` and `butlers deploy` always select the protected
topology and fail closed when its prerequisites are missing.

If an opted-in `butlers-dev` project is returned to ordinary dev, the launcher
runs base-only `down --remove-orphans` before it starts the base stack. That
removes the former relay and executor rather than leaving privileged services
running as orphans. Keep passing `--with-restore-drill` on later dev launches
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
`scripts/compose.sh --with-restore-drill`, `scripts/compose.sh --prod`, and
`butlers deploy` perform the required stop, versioned root preparation, create,
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
   and installed with `uv sync --frozen --no-dev` (plus the `whatsapp` extra).
3. Entrypoint `uv run --frozen --no-dev butlers`; default command
   `run --config /etc/butler`.

## Services

All application containers use the `butlers-app` image and read the shared
database env (`x-postgres-env` anchor) from the sourced `.env.<mode>` file.

### Database (external)

There is **no `postgres` service** in `docker-compose.yml`. The database is a
remote PostgreSQL reached via `POSTGRES_HOST` / `POSTGRES_PORT` (defaults to
`5432`), which `scripts/compose.sh` requires in `.env.dev` / `.env.prod`. One
database holds every butler's schema plus the shared `public` schema; there is
no local data volume to manage.

### `butlers-up` (the butler daemon)

| Setting | Value |
|---------|-------|
| Image | `butlers-app` |
| Command | `uv run butlers up` (runs **all** roster butlers in one process) |
| Health port | `41100` (prod) / `42100` (dev) -- Switchboard `/health` |
| Config mount | `./roster` -> `/app/roster:ro` |
| Runtime volumes | `runtime_claude`, `runtime_codex`, `runtime_opencode`, `runtime_gemini` (per-CLI state) |

Depends on `migrations`, `oauth-gate`, and `log-init` completing successfully.
Runs `apparmor:unconfined` so the Codex CLI's bubblewrap sandbox can create user
namespaces. A `--hotreload` run substitutes `butlers-up-hotreload`, which
volume-mounts `src/` for live edits.

### `dashboard-api`

| Setting | Value |
|---------|-------|
| Image | `butlers-app` |
| Command | `dashboard --host 0.0.0.0 --port 41200` |
| Port | `41200` (prod) / `42200` (dev) |
| Config mount | `./roster` -> `/app/roster:ro` |

Serves the dashboard API and `/health`. The `--hotreload` variant is
`dashboard-api-hotreload`.

### `frontend-dev`

| Setting | Value |
|---------|-------|
| Profile | `dev` (activated by `scripts/compose.sh`) |
| Image | `node:24-slim` (matches CI's Node 24) |
| Startup | `npm ci && npm run dev` |
| Port | `41173` (prod) / `42173` (dev) |

Vite dev server serving the dashboard `frontend/` via a host bind-mount for
hotreload. It uses `npm ci` (never `npm install`) so startup installs strictly
from `frontend/package-lock.json` and never rewrites the tracked lockfile
(bu-0zvsd); a static guard in `tests/scripts/test_compose_frontend_dev_lockfile_guard.py`
holds that invariant.

### `migrations`

One-shot `butlers-app` container running `db migrate` (exits 0). Every other
app service depends on it via `service_completed_successfully`. See
[Production Deploys](#production-deploys-butlers-deploy) for why redeploys run it
with `run --rm` rather than relying on `up -d`.

### Connector services

Each external ingress/egress connector runs in its own container (image
`butlers-app`, connector env from the `x-connector-env` anchor):
`connector-telegram-bot`, `connector-telegram-user`, `connector-whatsapp-user`,
`connector-gmail`, `connector-google-calendar`, `connector-google-drive`,
`connector-google-health`, `connector-spotify`, `connector-steam`,
`connector-owntracks`, `connector-activitywatch`, `connector-home-assistant`,
and `connector-live-listener` (behind the `audio` profile). The
`connector-whatsapp-user` service owns the single authenticated WhatsApp bridge
socket (`wa_bridge_socket`), shared with the Messenger butler.

### Supporting services

| Service | Role |
|---------|------|
| `minio` + `minio-setup` | S3-compatible object storage + bucket bootstrap |
| `oauth-gate` | Preflight OAuth-credential check (blocks startup on missing tokens; `--skip-oauth-check` to bypass) |
| `log-init` / `log-cleanup` | Log volume permissions + retention pruning |
| `backup-cron` | Scheduled database backups to `butlers_backups` |

## Production Deploys (`butlers deploy`)

`butlers deploy` (bu-9r3hd.3, `src/butlers/core/deploy.py`) replaces the
manual build-then-`up -d` ceremony with one idempotent command:

```bash
uv run butlers deploy --dir /path/to/repo --timeout 180
```

It runs, in order:

1. **Build** — `docker build --build-arg GIT_SHA=$(git rev-parse HEAD) -t butlers-app:latest .`
2. **Migrate** — runs the merged Compose input with `run --rm migrations`,
   always a fresh container. A direct Compose lifecycle command only reruns the one-shot
   `migrations` service if its `service_completed_successfully` condition
   isn't already satisfied — once that container has exited 0 *once*, compose
   treats it as permanently satisfied even after the image is rebuilt with
   new migrations baked in (bd bu-zhfd0: core_155..161 sat unrun in prod for
   six days this way). `run --rm` sidesteps that entirely.
3. **Prepare and recreate** — stops the restore relay/executor, obtains the
   root-owned generation-bound capability, creates the protected containers,
   performs root-side attestation and applies both default-deny firewall
   policies, then runs the
   protected base-plus-restore-drill Compose overlay with `up -d --remove-orphans`,
   with **no** `--profile` flag ever passed and
   `COMPOSE_PROFILES` stripped from the subprocess environment, so a leftover dev-shell
   `COMPOSE_PROFILES=hotreload` cannot silently pull the bind-mounted
   hotreload services into a prod recreate.
4. **Verify** — polls `GET /health` until `status: "ok"` or the `--timeout`
   elapses.
5. **Record** — writes one row to `public.deployments` (git SHA, migration
   head, `success` or `failed`) via `butlers.core.deployments`, on *every*
   outcome — a failed deploy is visible in the ledger, not silent.

Safe to re-run at any point: image builds reuse layer cache, migrations
always get a fresh container, and `up -d` only recreates services whose
config or image actually changed.

## Environment Variables

The database target is **not** hard-coded in Compose; `scripts/compose.sh`
sources `.env.<mode>` and exports the per-mode ports before invoking
`docker compose`. The shared DB env (`x-postgres-env`) requires:

```yaml
POSTGRES_HOST: <from .env.dev | .env.prod>   # required
POSTGRES_PORT: 5432                          # default
POSTGRES_USER: butlers                       # default
POSTGRES_PASSWORD: <from .env.dev | .env.prod>   # required
POSTGRES_SSLMODE: <optional>
```

Per-mode host ports and project names (both can run at once):

| | prod (`--prod`) | dev (default) |
|---|---|---|
| Compose project | `butlers` | `butlers-dev` |
| Switchboard | `41100` | `42100` |
| Dashboard API | `41200` | `42200` |
| Frontend | `41173` | `42173` |
| OwnTracks | `40086` | `42086` |
| URL base path | `/butlers/` | `/butlers-dev/` |

> **Naming caution for operators:** the two database targets are named
> **counter-intuitively**. `butlers-db-dev` (selected by `.env.dev`, the default
> `scripts/compose.sh` target) is the **LIVE** system holding real data;
> `butlers-db` (`.env.prod`) is the other target. `scripts/compose.sh`'s own
> `dev` / `prod` mode labels track the env file, **not** which host is live, so
> do not trust the word "dev" here. Confirm which host an `.env.<mode>` file
> actually points at before running migrations or destructive operations.

## Volumes

| Volume | Type | Purpose |
|--------|------|---------|
| `minio_data` | Named | MinIO object storage |
| `frontend_node_modules` | Named | Frontend dev `node_modules` |
| `uv_cache` | Named | Shared `uv` package cache |
| `runtime_claude` / `runtime_codex` / `runtime_opencode` / `runtime_gemini` | Named | Per-CLI runtime state for `butlers-up` |
| `wa_bridge_socket` | Named | Shared WhatsApp bridge socket |
| `butlers_backups` | Named | Database backup output |

There is no PostgreSQL data volume: the database is external (see
[Database](#database-external)).

## Related Pages

- [Environment Config](environment-config.md) -- Full environment variable reference
- [Grafana Monitoring](grafana-monitoring.md) -- Observability setup
- [Troubleshooting](troubleshooting.md) -- Common deployment issues
- [Image Bump Procedure](image-bump-procedure.md) -- How to update pinned service image tags
