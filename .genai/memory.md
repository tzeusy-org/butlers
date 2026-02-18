# Notes to self

## Core database tables per butler

_Updated: 2026-02-09 by claude-opus-4-6_

Every butler DB has three core tables: `state` (KV JSONB), `scheduled_tasks` (cron-driven), `sessions` (runtime invocation log). Migrations use Alembic with multiple version chains in `alembic/versions/` — a `core` chain (applied to every butler) and per-butler chains (e.g., `relationship`, `health`). Modules contribute their own Alembic branch via `migration_revisions()`. The runtime runs `alembic.command.upgrade` programmatically at startup.

## v1 MVP is complete; post-v1 work underway

_Updated: 2026-02-10 by claude-opus-4-6_

The v1 MVP (epic `butlers-0qp`) is closed — all 5 butlers (Switchboard, General, Relationship, Health, Heartbeat), 2 modules (Telegram, Email), CLI, Docker deployment, and OTel instrumentation are implemented with 449 tests passing. Post-v1 work includes the action approval mechanism (`butlers-clc`) and frontend dashboard.

## OpenSpec workflow for this project

_Added: 2026-02-09 by claude-opus-4-6_

This project uses OpenSpec (experimental artifact-driven workflow) for spec management. CLI: `openspec new change`, `openspec status`, `openspec instructions`. Changes live in `openspec/changes/<name>/`. The `spec-driven` schema has 4 artifacts: proposal → design + specs (parallel) → tasks. Use `/opsx:continue` to advance through artifacts, `/opsx:apply` to implement.

## Migrations use Alembic (not plain SQL files)

_Updated: 2026-02-09 by claude-opus-4-6_

Design decision D12 uses Alembic for all database migrations. Structure: `alembic/` at project root with `alembic.ini`, `env.py`, and `versions/` subdirectories per chain (`core/`, `switchboard/`, `relationship/`, `health/`, `general/`). Migrations use raw SQL via `op.execute()` — Alembic/SQLAlchemy is only used at migration time, asyncpg for all runtime queries (D3 unchanged). Module ABC uses `migration_revisions()` returning a branch label (`str | None`) instead of the old `migrations()` returning SQL statements.

## 15 capability specs cover the full v1 MVP

_Added: 2026-02-09 by claude-opus-4-6_

The v1 MVP spec has 15 capabilities: `butler-daemon`, `module-system`, `state-store`, `task-scheduler`, `cc-spawner`, `session-log`, `switchboard`, `heartbeat`, `butler-relationship`, `butler-health`, `butler-general`, `cli-and-deployment`, `telemetry`, `butler-credentials`, `butler-skills`. Each has a `spec.md` at `openspec/changes/v1-mvp-spec/specs/<name>/spec.md`.

## Key design decisions (from design.md)

_Added: 2026-02-09 by claude-opus-4-6_

FastMCP for MCP servers. asyncpg (no ORM). croniter for cron. Click for CLI. SSE transport for inter-butler MCP. Ephemeral MCP configs lock down runtime instances. Alembic for migrations (raw SQL via op.execute, multi-chain). OTel + LGTM stack (Alloy/Tempo/Grafana) for observability. Serial CC dispatch in v1. No auth between butlers in v1.

## Beads backlog structure

_Updated: 2026-02-10 by claude-opus-4-6_

The v1 MVP backlog (`butlers-0qp`, now closed) had 122 issues across 22 milestone epics. Post-v1 epics: `butlers-clc` (action approval mechanism, 7 children), `butlers-r1v` (signal extraction, closed). Run `bd ready` to find unblocked work.

## Beads CLI: use `bd create --silent` for scripting, not `bd q`

_Added: 2026-02-09 by claude-opus-4-6_

`bd q` (quick capture) only supports `-t`, `-p`, `-l` flags. It does NOT support `--parent`, `--description`, `--acceptance`, `--design`, `--notes`, `--deps`, or `--estimate`. For scripting that needs full fields, use `bd create --silent` which outputs only the ID but accepts all flags. `bd create --silent` is the correct replacement for the `bd q` pattern shown in beads-writer skill examples.

## Memory system is a shared Memory Butler, not per-butler

_Added: 2026-02-10 by claude-opus-4-6_

The memory system (`MEMORY_PROJECT_PLAN.md`) uses a dedicated **Memory Butler** (port 8150, DB `butler_memory`) that serves as a shared MCP server for all butlers — NOT per-butler isolated memory. Three separate tables by memory type: `episodes` (raw session observations, 7-day TTL), `facts` (subject-predicate structured knowledge with subjective confidence decay), `rules` (procedural playbook with maturity progression: candidate → established → proven). Local embeddings via `sentence-transformers/all-MiniLM-L6-v2` (384-dim, pgvector), hybrid search (semantic + full-text + RRF). Facts have per-fact `decay_rate` assigned by the Memory Butler during consolidation — permanence categories: permanent (λ=0), stable (~346d half-life), standard (~87d), volatile (~23d), ephemeral (~7d). Rules use CASS-inspired 4× harmful penalty for effectiveness scoring. Memory is scoped (`global` or butler-name) but lives in one shared DB.

## Prior art for human-in-the-loop: extraction confirmation queue

_Added: 2026-02-10 by claude-opus-4-6_

`butlers-r1v.3` implemented a Switchboard-specific confirmation queue for low-confidence extractions (table `extraction_queue`, 7 tools, statuses: pending/confirmed/dismissed/expired). The generalized action approval mechanism (`butlers-clc`) follows a similar pattern but is cross-butler: hybrid core+module design where interception lives in the daemon (wraps MCP tool dispatch) and approval tools live in an opt-in module. Key addition over r1v.3: standing approval rules that auto-approve recurring action patterns via tool_name + arg constraints (exact/pattern/any).

## Moltbot/OpenClaw as a reference architecture

_Added: 2026-02-09 by claude-opus-4-6_

Moltbot (now OpenClaw) is a comparable open-source personal AI assistant. Key lessons extracted for Butlers: deterministic session keys, concurrency caps on agent spawning, context auto-compaction, config hot-reload, per-module tool policy scoping. Butlers differentiates with PostgreSQL-backed state, strict DB isolation per butler, MCP-native protocol.
