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
| **Docker** + **Docker Compose** | latest | PostgreSQL database and optional production containers |
| **tmux** | any | The `dev.sh` script runs all services in tmux panes |
| **psql** | any | Part of `postgresql-client`; used by the OAuth gate to poll the database at startup |
| **Tailscale** | latest | Provides HTTPS for Google OAuth callbacks; can be skipped with `--skip-tailscale-check` |

### Python and uv

Butlers targets Python 3.12+. The project uses [uv](https://github.com/astral-sh/uv) as its package manager rather than pip. Install uv following the instructions on its GitHub page, then run `uv sync --dev` from the project root to install all Python dependencies.

### Node.js

Node.js 22+ is needed for two purposes: running the Vite frontend dev server, and installing LLM runtime CLIs (`claude`, `codex`, `gemini`) which are distributed as npm packages.

### Docker

Docker and Docker Compose are required for running PostgreSQL. The `docker-compose.yml` defines services for the database, and optionally for the dashboard API and frontend via the `dev` profile. In production, all services run in Docker.

### tmux

The development helper script (`scripts/dev.sh`) orchestrates all services --- database, butlers, connectors, dashboard --- in tmux panes. If you prefer to start services manually, tmux is not strictly required, but `dev.sh` expects it.

### Tailscale

Google OAuth callbacks require HTTPS. In development, Butlers uses Tailscale to provide a stable HTTPS hostname. If you are not using Google modules (Calendar, Contacts, Gmail), you can skip this by passing `--skip-tailscale-check` to `dev.sh`.

## LLM Runtime CLIs

Butlers spawn ephemeral LLM CLI instances to reason and act. Each butler declares a runtime type in its `butler.toml` under `[butler.runtime].type`. You need to install and authenticate the CLI for whichever runtimes your butlers use.

| Runtime type | CLI binary | Install command | Authentication |
| --- | --- | --- | --- |
| `claude` (default) | `claude` | `npm install -g @anthropic-ai/claude-code` | Dashboard Settings page |
| `codex` | `codex` | `npm install -g @openai/codex` | Dashboard Settings page |
| `gemini` | `gemini` | `npm install -g @anthropic-ai/gemini-cli` | Dashboard Settings page |

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

### Secrets Directory (dev.sh)

The `dev.sh` script sources environment files for connector processes:

```
/secrets/.dev.env                       # Global dev secrets (API keys, DB passwords)
secrets/connectors/telegram_bot         # BUTLER_TELEGRAM_TOKEN, etc.
secrets/connectors/telegram_user_client # Telegram user-client credentials
secrets/connectors/gmail                # Gmail connector credentials
```

If you do not use certain connectors, the corresponding files can be empty or absent --- those connector panes will simply fail to start without affecting the rest of the system.

## Verification

After installing dependencies, verify your setup:

```bash
python3 --version    # Should be 3.12+
uv --version         # Should be installed
node --version       # Should be 22+
docker info          # Docker daemon running
claude --version     # Or whichever runtime CLI you need
```

## Related Pages

- [Dev Environment](dev-environment.md) --- step-by-step guide to starting the full dev stack
- [First Butler Launch](first-butler-launch.md) --- launching and triggering your first butler
- [Dashboard Access](dashboard-access.md) --- starting and using the web dashboard
