# Butlers

![CI](https://github.com/Tzeusy/butlers/actions/workflows/ci.yml/badge.svg)
![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/Tzeusy/33d1433ee529f878bd4b4a8bf4609f96/raw/butlers-coverage.json)

A personal AI agent system where each **butler** is a long-running MCP server daemon that handles recurring tasks, manages integrations, and acts on the owner's behalf. Every instance belongs to exactly one person — there is no hosted product, no multi-tenancy, and no third-party developer surface. Internally, butlers gain capability through a set of first-party modules that the daemon loads by topological order; "modules" is an implementation detail, not an extension API for external code. See [`about/heart-and-soul/vision.md`](about/heart-and-soul/vision.md) for the full scope boundary.

> **[Full Documentation](docs/index.md)** — start here for the complete guide, from overview through architecture to operations.

## WARNING

This application is entirely vibe coded, borne out of my desire to experiment with the capabilities of Claude Code/Codex, combined with Steve Yegge's [beads](https://github.com/steveyegge/beads). This project is pretty far from being considered 'ready for usage' - play with it at your own risk!

## Motivation

I've always wanted to build a 'Jarvis' for myself - a system where I could offload the majority of 'mental toil' tasks while retaining the benefits of having kept them in mind. This has come closer and closer to true feasibility as LLMs have become more and more advanced; this project represents the first of my efforts to materialize the use cases and UX of a personal butler-like microservice.

## How It Works

Each butler runs as a persistent daemon with built-in infrastructure:

- **State store** — remembers things between sessions (see [Memory module](docs/modules/memory.md))
- **Task scheduler** — runs prompts on cron schedules (e.g., morning briefings, inbox triage)
- **LLM CLI spawner** — spins up ephemeral LLM CLI instances to reason and act
- **Session log** — tracks what happened and when
- **Custom tools** — Tools specific to a butler's functionality

On top of that, butlers gain capabilities through **modules** — first-party integrations like Email, Telegram, Calendar, and Memory. Modules are part of the codebase, not a plugin API: adding a new one means adding it to `src/butlers/modules/` or `roster/{butler}/modules/` and shipping it as part of this repo.

See [Concepts](docs/concepts/index.md) for the full mental model.

## Example

A personal assistant butler configured in `butler.toml`:

```toml
[butler]
name = "assistant"
description = "Personal assistant with email and calendar"
port = 41101

[[butler.schedule]]
name = "morning-briefing"
cron = "0 8 * * *"
prompt = "Check my calendar for today, summarize meetings, and email me a briefing."

[modules.email]

[modules.email.user]
enabled = false

[modules.calendar]
provider = "google"
calendar_id = "butler@group.calendar.google.com"
default_conflict_policy = "suggest"
```

## Architecture

```
External Clients (MCP-compatible)
        |
        v
  Switchboard Butler ---- routes requests to the right butler
        |
   +----+----+
   v    v    v
Butler Butler Butler ---- each a persistent MCP server daemon
   |    |    |
   v    v    v
LLM CLI instances -- ephemeral, locked-down, reason + act
```

- Runtime topology: **one PostgreSQL database with per-butler schemas + `public`**
- Inter-butler communication: **MCP tools through the Switchboard only**
- Butler configs: **git-based directories** with personality (`CLAUDE.md`), manifestoes (`MANIFESTO.md`), skills, and config (`butler.toml`)

For the full architecture including daemon internals, startup sequence, database design, and observability, see [Architecture docs](docs/architecture/index.md).

### Service Ports

Each butler, the dashboard API, and the frontend have fixed ports per compose mode; the single
port map is [Deployment § Port Assignments](about/lay-and-land/deployment.md#port-assignments).

## Quick Start

For full prerequisites and setup details, see [Getting Started](docs/getting_started/index.md).

```bash
# Install Python dependencies
uv sync --dev

# Start the dev stack via Docker Compose (butlers, connectors, dashboard;
# PostgreSQL is external, configured in .env.dev)
./scripts/compose.sh

# Or start the daemons manually
butlers up
```

### CLI Reference

```
butlers up [--only NAME ...] [--dir PATH]    Start all (or filtered) butler daemons
butlers run --config PATH                    Start a single butler from config dir
butlers list [--dir PATH]                    List discovered butler configurations
butlers init NAME --port PORT [--dir PATH]   Scaffold a new butler config directory
```

### Running Messenger with Switchboard

To run just the messenger and switchboard together (useful for testing delivery flows):

```bash
butlers up --only switchboard --only messenger
```

The messenger requires these credentials configured via the dashboard secrets page:

- `BUTLER_TELEGRAM_TOKEN` — Telegram bot token for outbound messages
- `BUTLER_EMAIL_ADDRESS` — Email address for outbound email delivery
- `BUTLER_EMAIL_PASSWORD` — App password for the email account

## Environment Variables

See the [environment variable reference](docs/identity_and_secrets/environment-variables.md).
Module credentials (Telegram tokens, email passwords, etc.) are managed through the dashboard
secrets page; see [Identity and Secrets](docs/identity_and_secrets/index.md).

## Development

```bash
uv sync --dev       # Install dependencies
make check           # Lint + test
make test            # Run tests
make test-qg         # Quality-gate pytest scope (default parallel)
make lint            # Lint
make format          # Format
```

Markers are defined in `pyproject.toml`; see [Testing docs](docs/testing/index.md) for the strategy and
marker semantics.

## E2E Testing

> **Token burn warning:** E2E tests spawn real LLM sessions against live APIs. Each run consumes tokens and incurs cost. Use validate mode for development; reserve benchmark mode for scheduled evaluations.

### Prerequisites

- `ANTHROPIC_API_KEY` set in your environment
- Docker running (PostgreSQL testcontainer)
- `claude` CLI binary on PATH

### Running

```bash
# Validate mode — fail-fast against current model config
make test-e2e-validate

# Benchmark mode — sweep across models, produce scorecards
make test-e2e-benchmark BENCHMARK_MODELS=claude-sonnet-4-5,gpt-4o
```

See [E2E Testing docs](docs/testing/e2e/README.md) for full details.

## Tech Stack

Python 3.12+ · FastMCP · Claude Agent SDK · PostgreSQL · asyncpg · Docker · asyncio · OpenTelemetry · Alembic · Click · Pydantic

## Status

Early development. See [`docs/roadmap/project-plan.md`](docs/roadmap/project-plan.md) for the full implementation roadmap.
