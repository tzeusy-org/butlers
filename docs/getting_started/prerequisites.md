# Prerequisites

> **Purpose:** List everything you need installed and configured before running Butlers.
> **Audience:** New developers setting up for the first time.
> **Prerequisites:** [What Is Butlers?](../overview/what-is-butlers.md)

## Overview

Butlers has system dependencies (languages, tools, services), LLM runtime CLIs (the actual AI backends), and credentials (API keys, OAuth tokens). This page covers all three categories so you can get from zero to a working dev environment.

## System Dependencies

| Dependency | Version | Purpose |
| --- | --- | --- |
| **Python** | 3.12+ | Runtime for all butler daemons and the dashboard API |
| **uv** | latest | Python package manager (replaces pip); used for dependency management and running commands |
| **Node.js** | 22+ | Frontend dev server (Vite) and LLM CLI installations |
| **npm** | (bundled with Node) | Frontend dependency management and global CLI installs |
| **Docker** + **Docker Compose** | latest | Builds images; runs local stacks via `scripts/compose.sh` |
| **kubectl** + **helm** | latest | Deploy and debug the live dev stack on the homelab k3s cluster (operators) |
| **bws** | latest | Injects secrets for image builds and deploys (operators) |
| **PostgreSQL** | with `pgvector` | External database server (not a Compose service); `scripts/init-db.sql` installs extensions |
| **psql** | any | Part of `postgresql-client`; used by the OAuth gate to poll the database at startup |
| **Tailscale** | latest | Provides HTTPS for Google OAuth callbacks; can be skipped with `--skip-tailscale-check` |

### Python and uv

Butlers targets Python 3.12+. The project uses [uv](https://github.com/astral-sh/uv) as its package manager rather than pip. Install uv following the instructions on its GitHub page, then run `uv sync --dev` from the project root to install all Python dependencies.

### Node.js

Node.js 22+ is needed for two purposes: running the Vite frontend dev server, and installing LLM runtime CLIs (`claude`, `codex`, `gemini`) which are distributed as npm packages.

### Docker

`scripts/compose.sh` runs the whole stack --- butler daemons, connectors, dashboard API, and
frontend --- from `docker-compose.yml`, in dev or prod mode. PostgreSQL is external in both modes;
see [Dev Environment](dev-environment.md) for provisioning it. Use it only against a non-live
database: dev mode refuses to start while the Kubernetes `butlers-dev` release is running.

### Kubernetes access (operators)

The live dev stack runs on the homelab k3s cluster. Shipping to it or debugging it needs `kubectl`
and `helm` configured for that cluster, Docker push access to `<registry-host>`,
and `bws` with `/secrets/.bws.dev.env`. See
[Kubernetes Deployment](../operations/kubernetes-deployment.md).

### Tailscale

Google OAuth callbacks require HTTPS. Host `tailscale serve` provides a stable HTTPS hostname; for
the live stack it maps the public paths to the cluster NodePorts, and `scripts/compose.sh`
configures it for local stacks. If you are not using Google modules (Calendar, Contacts, Gmail),
skip it with `./scripts/compose.sh --skip-tailscale-check`.

## LLM Runtime CLIs

Butlers spawn ephemeral LLM CLI instances to reason and act. Each butler declares a runtime type in its `butler.toml` under `[butler.runtime].type`. You need to install and authenticate the CLI for whichever runtimes your butlers use.

| Runtime type | CLI binary | Install command | Authentication |
| --- | --- | --- | --- |
| `claude` (default) | `claude` | `npm install -g @anthropic-ai/claude-code` | Dashboard Settings page |
| `codex` | `codex` | `npm install -g @openai/codex` | Dashboard Settings page |
| `gemini` | `gemini` | `npm install -g @google/gemini-cli` | Dashboard Settings page |

The daemon verifies at startup that the configured binary is on `PATH` and will fail fast with a clear error if it is missing.

Most butlers default to `claude`. If you only plan to use the default runtime, you only need `claude` installed.

### Runtime Authentication

Runtime CLIs authenticate via **OAuth device-code flow**, managed from the dashboard Settings page. After starting the dashboard, click "Login" next to the provider, follow the device-code URL, and authorize. Tokens are persisted to the shared credential store and restored automatically on restart.

Health probes run periodically to verify tokens are still valid. The Settings page shows live auth status for each provider.

## Credentials

### Google OAuth (optional)

If you plan to use Google-based modules (Calendar, Contacts, Gmail), you need Google OAuth client credentials:

```bash
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."
```

These can also be bootstrapped via the dashboard UI after first start.

### Module-Specific Credentials

Module credentials (Telegram bot tokens, email passwords, Telegram API keys for user-client connections) are managed through the dashboard after first boot. They are stored in PostgreSQL and resolved by the daemon at startup through a layered credential store (database first, environment variable fallback).

## Verification

After installing dependencies, verify your setup:

```bash
python3 --version    # Should be 3.12+
uv --version         # Should be installed
node --version       # Should be 22+
docker info          # Docker daemon running
kubectl -n butlers-dev get pods   # Operators: cluster access to the live dev stack
claude --version     # Or whichever runtime CLI you need
```

## Related Pages

- [Dev Environment](dev-environment.md) --- step-by-step guide to starting the full dev stack
- [First Butler Launch](first-butler-launch.md) --- launching and triggering your first butler
- [Dashboard Access](dashboard-access.md) --- starting and using the web dashboard
