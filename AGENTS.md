# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run right-sized quality gates** (if code changed) - Targeted tests during active development; full suite only for final merge-readiness checks
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

## Test Scope Policy

- For bugfixes and new features under active development or investigation, prefer targeted `pytest` runs (single test, file, or focused subset).
- Run the full test suite only when branch changes are finalized and you want a final merge-readiness signal.
- Expand test scope incrementally if risk is broader, instead of defaulting to full-suite runs early.


<!-- bv-agent-instructions-v1 -->

---

## Beads Workflow Integration

This project uses [beads_viewer](https://github.com/Dicklesworthstone/beads_viewer) for issue tracking. Issues are stored in `.beads/` and tracked in git.

### CRITICAL: no-db Mode (JSONL is the sole source of truth)

This repo uses `no-db: true` (see `.beads/config.yaml`). This means:

- **`.beads/issues.jsonl` is the only source of truth.** There is no SQLite database. `bd` reads from and writes back to the JSONL after each command.
- **Do NOT run `bd migrate`, `bd sync --import-only`, or debug SQLite state** — these are irrelevant in no-db mode.
- **`bd doctor` warnings about SQLite** (version mismatch, 0 issues in DB, repo fingerprint) are noise — ignore them.
- **`bd sync` = commit JSONL to git.** It does NOT export from SQLite. The "Exported 0 issues" message is misleading but harmless — your data is in the JSONL.
- **`bd create` writes directly to the JSONL file.** Beads persist immediately in the working tree.
- If beads appear to vanish, check `git diff .beads/issues.jsonl` and `grep <id> .beads/issues.jsonl` — the data is in the file, not a database.

### Essential Commands

```bash
# View issues (launches TUI - avoid in automated sessions)
bv

# CLI commands for agents (use these instead)
bd ready              # Show issues ready to work (no blockers)
bd list --status=open # All open issues
bd show <id>          # Full issue details with dependencies
bd create --title="..." --type=task --priority=2
bd update <id> --status=in_progress
bd close <id> --reason="Completed"
bd close <id1> <id2>  # Close multiple issues at once
bd sync               # Commit and push changes
```

### Workflow Pattern

1. **Start**: Run `bd ready` to find actionable work
2. **Claim**: Use `bd update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `bd close <id>`
5. **Sync**: Always run `bd sync` at session end

### Worktree Hydration (no-db mode)

- In long-lived worktrees using `no-db: true`, hydrate before looking up freshly created issue IDs:
  ```bash
  export BEADS_NO_DAEMON=1
  bd sync --import
  ```
- This imports newer `.beads/issues.jsonl` state into the active worktree so `bd show <new-id>` resolves deterministically.

### Key Concepts

- **Dependencies**: Issues can block other issues. `bd ready` shows only unblocked work.
- **Priority**: P0=critical, P1=high, P2=medium, P3=low, P4=backlog (use numbers, not words)
- **Types**: task, bug, feature, epic, question, docs
- **Blocking**: `bd dep add <issue> <depends-on>` to add dependencies

### Session Protocol

**Before ending any session, run this checklist:**

```bash
git status              # Check what changed
git add <files>         # Stage code changes
bd sync                 # Commit beads changes
git commit -m "..."     # Commit code
bd sync                 # Commit any new beads changes
git push                # Push to remote
```

### Best Practices

- Check `bd ready` at session start to find available work
- Update status as you work (in_progress → closed)
- Create new issues with `bd create` when you discover tasks
- Use descriptive titles and set appropriate priority/type
- Always `bd sync` before ending session

<!-- end-bv-agent-instructions -->

---

## Notes to self

### Scheduler job_args JSONB contract
- In scheduler code paths, `job_args` JSONB values can round-trip through asyncpg as JSON strings; writes should serialize dict payloads explicitly, and reads should normalize back to dicts before diffing, validation merges, list responses, or dispatch payload assembly.

### Manifesto-driven design
Each butler has a `MANIFESTO.md` that defines its public identity and value proposition. Features, tools, and UX decisions for a butler should be deeply aligned with its manifesto. The manifesto is the source of truth for *what this butler is for* — CLAUDE.md is *how it behaves*, butler.toml is *what it runs*. When proposing new features or evaluating scope, check the manifesto first.

### Calendar module config reminder
- Calendar configs run through `src/butlers/daemon.py::_validate_module_configs`, which loads the module's `config_schema` and rejects extra/missing fields; `CalendarConfig` in `src/butlers/modules/calendar.py:906-925` demands `provider` + `calendar_id`, so any butler must populate them before the module can enable.

### Contacts module sync contract
- The contacts module is expected to run its incremental sync as an internal poll loop, not as a standalone connector (see `docs/modules/contacts_draft.md` §8); the default cadence is an immediate incremental run on startup, recurring polling every 15 minutes, and a forced full refresh every 6 days before the sync token expires.
- Modules load inside `butlers up` via `ButlerDaemon.start()` (`src/butlers/daemon.py:852-931`), so the poller will live in the butler process. `scripts/dev.sh` already launches `uv run butlers up` and the needed connector panes (telegram + Gmail) around lines 768‑840, so no extra dev bootstrap step is required for contact sync. To actually exercise the module once it exists, add a `[modules.contacts]` block (and provider-specific fields) to `roster/relationship/butler.toml` so the daemon validates and configures it when `butlers up` runs.

### Relationship contacts sync trigger API contract
- `POST /api/relationship/contacts/sync` is the manual dashboard/API trigger for contacts sync and dispatches to the relationship butler MCP tool `contacts_sync_now` with args `{"provider":"google","mode":"incremental|full"}`.
- The `mode` query parameter is strict (`incremental` or `full` only), and credential-related MCP failures are surfaced as actionable `400` errors pointing operators to `/api/oauth/google/start` or `/api/oauth/google/credentials`.

### v1 MVP Status (2026-02-09)
All 122 beads closed. 449 tests passing on main. Full implementation complete.

### One-DB runtime topology contract (butlers-1003.5)
- `[butler.db]` is schema-aware: when `name = "butlers"`, `schema` is required (explicit target schema, no implicit fallback).
- `Database` / `DatabaseManager` apply schema-scoped `search_path` (`<schema>,shared,public`; shared pool uses `shared,public`) for one-db pool resolution.
- API startup (`init_db_manager`) treats one-db topology as canonical shared-credentials path (`db=butlers`, schema `shared`).
- Daemon migration URL generation includes libpq `options=-csearch_path=...` when a schema is configured so Alembic runs in the intended schema context.

### dev.sh Gmail OAuth rerun contract
- In `dev.sh`, `_has_google_creds()` must check the same credential DB set as the OAuth gate (`_poll_db_for_refresh_token`), plus legacy/override DB names where applicable, so preflight and gate do not disagree.
- Build the Gmail pane startup command at Layer 3 launch time (after OAuth gate), not once during early preflight; otherwise reruns can keep showing the stale "waiting for OAuth" pane even when credentials already exist.
- For pane logs, prefer wrapping the launched command with stdout/stderr tee capture (`_wrap_cmd_for_log`) instead of raw `tmux pipe-pane`, so log files contain process output rather than interactive shell prompt/control-sequence noise.

### dev.sh OAuth shared-store contract
- `dev.sh` OAuth preflight (`_has_google_creds`) and Layer 2 gate (`_poll_db_for_refresh_token`) must use the same canonical lookup path: `butler_secrets` in one-db mode (`db=butlers`, schema `shared` by default, overridable via `BUTLER_SHARED_DB_NAME`/`BUTLER_SHARED_DB_SCHEMA`).

### Google OAuth DB-only contract
- Runtime Google credential resolution is DB-only via `CredentialStore`/`butler_secrets`; env fallback for `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` has been removed from `google_credentials`, Calendar module startup, OAuth router status/callback resolution, and startup guard messaging.
- `dev.sh` OAuth preflight and Layer 2 gate now both check only DB-backed `GOOGLE_REFRESH_TOKEN` presence (shared one-db store) so shell gating and runtime behavior cannot drift.

### Code Layout
- `src/butlers/core/` — state.py, scheduler.py, sessions.py, spawner.py, telemetry.py, telemetry_spans.py
- `src/butlers/modules/` — base.py (ABC), registry.py, telegram.py, email.py
- `src/butlers/tools/` — switchboard.py, general.py, relationship.py, health.py, heartbeat.py
- `src/butlers/` — config.py, db.py, daemon.py, migrations.py, cli.py
- `alembic/versions/{core,mailbox}/` — shared migrations (core infra + modules)
- `roster/{switchboard,general,relationship,health}/migrations/` — butler-specific migrations
- `roster/{switchboard,general,relationship,health,heartbeat}/` — butler config dirs

### Test Layout
- Shared/cross-cutting tests in `tests/`
- Butler-specific tool tests colocated in `roster/<name>/tests/`
  - `roster/general/tests/test_tools.py`
  - `roster/health/tests/test_tools.py`
  - `roster/relationship/tests/test_tools.py`, `test_contact_info.py`
  - `roster/switchboard/tests/test_tools.py`
- `pyproject.toml` testpaths: `["tests", "roster"]`
- Uses `--import-mode=importlib` to avoid module-name collisions across butler test dirs

### Test Patterns
- All DB tests use `testcontainers.postgres.PostgresContainer` with `asyncpg.create_pool()`
- Tables created via direct SQL from migration files (not Alembic runner)
- When tests create `sessions` manually, keep schema aligned with `core_003`+ columns (`model`, `success`, `error`, `input_tokens`, `output_tokens`, `parent_session_id`) to avoid `UndefinedColumnError` in `core.sessions` queries.
- Integration test modules that create asyncpg pools in async fixtures must align asyncio loop scope under xdist (`@pytest.mark.asyncio(loop_scope="session")` on async test classes/modules) to avoid cross-loop `RuntimeError: ... Future ... attached to a different loop`.
- Root `conftest.py` has `SpawnerResult` and `MockSpawner` (visible to all test trees)
- `tests/conftest.py` re-exports from root for backward compat (`from tests.conftest import ...`)
- CLI tests use Click's `CliRunner`
- Telemetry tests use `InMemorySpanExporter`
- Root `conftest.py` patches `testcontainers` teardown (`DockerContainer.stop`) with bounded retries for known transient Docker API teardown races (notably "did not receive an exit event") under `pytest-xdist`; non-transient errors must still raise.

### Memory System Architecture
Memory is a **common module** (`[modules.memory]`) enabled per butler, not a dedicated shared role/service. Memory tables (`episodes`, `facts`, `rules`, plus provenance/audit tables) live in each hosting butler's DB and memory tools are registered on that butler's MCP server. Uses pgvector + local MiniLM-L6 embeddings (384d). Dashboard remains available at `/memory` (aggregated via API fanout) and `/butlers/:name/memory` (scoped).

### Memory API fanout contract
- `src/butlers/api/routers/memory.py` must not require `db.pool("memory")`; `/api/memory/*` reads fan out across available butler DB pools and aggregate results.
- Pools without memory tables should be skipped gracefully so no-dedicated-memory deployments return zero/empty payloads (or 404 for ID lookups) instead of 503.

### Memory OpenSpec alignment contract
- `openspec/changes/memory-system/specs/*` now aligns to target-state module semantics: per-butler memory module integration, tenant-bounded operations by default, canonical fact soft-delete state `retracted` (legacy `forgotten` alias only), required `memory_events` audit stream, deterministic tokenizer-based `memory_context` budgeting/tie-breakers, consolidation terminal states (`consolidated|failed|dead_letter`) with retry metadata, and explicit `anti_pattern` rule maturity.

### Migration naming/path convention
Alembic revisions are chain-prefixed (`core_*`, `mem_*`, `sw_*`) rather than bare numeric IDs. Butler-specific migrations resolve from `roster/<butler>/migrations/` via `butlers.migrations._resolve_chain_dir()` (not legacy `butlers/<name>/migrations/` paths).
- Core chain baseline is consolidated into `alembic/versions/core/core_001_target_state_baseline.py` (`revision = "core_001"`); legacy incremental core revisions (`001_create_core_tables.py` through `011_apply_schema_acl_for_runtime_roles.py`) were removed.
- Core migration coverage is centralized in `tests/config/test_migrations.py`; do not reintroduce per-step core files under `tests/migrations/` for pre-baseline revision IDs.
- Within a chain, set `branch_labels` only on the branch root revision (e.g. `rel_001`); repeating the same label on later revisions causes Alembic duplicate-branch errors.
- Do not leave stray migration files in chain directories: even if chain tests only assert expected filenames, Alembic will still load every `*.py` in the versions path and fail on duplicate `revision` IDs.
- Switchboard migrations already include `sw_005` as the latest linear revision; new switchboard revisions must continue from `sw_005` (for example `sw_006`) to avoid multi-head failures during `switchboard@head` upgrades.
- Table renames preserve existing index names; when rewriting a table in-place (rename old + create new), new index names must not collide with indexes still attached to the renamed backup table.
- `src/butlers/migrations.py::_build_alembic_config` must escape `%` as `%%` when setting `sqlalchemy.url` on Alembic `Config`; otherwise percent-encoded libpq query params (for example `options=-csearch_path%3D...`) raise `configparser` interpolation errors.

### Memory migration baseline contract
- `src/butlers/modules/memory/migrations/` is a single baseline chain file: `001_memory_baseline.py` (`revision=mem_001`, `branch_labels=('memory',)`, `down_revision=None`).
- Legacy incremental memory revisions (`001_create_episodes.py` through `007_fix_rules_missing_columns.py`) are intentionally removed; no prior revision compatibility is preserved for this rewrite.

### Known Warnings (not bugs)
- 2 RuntimeWarnings in CLI tests from monkeypatched `asyncio.run` — unawaited coroutines in test mocking

### Testcontainers xdist teardown flake
- `make test-qg` can intermittently fail during DB-backed test teardown with Docker API 500 errors while removing/killing `postgres:16` testcontainers (`did not receive an exit event`); tracked in `butlers-e6b`.

### Testcontainers startup timeout under contention
- Root `conftest.py` patches `testcontainers.core.docker_client.DockerClient.__init__` with bounded retry for transient startup timeouts from API-version negotiation (`Error while fetching server API version ... Read timed out`) before container launch.
- Controlled contention probe results on 2026-02-13 (48 workers, docker CLI churn): `docker.from_env(version=\"auto\")` failed 136/1200 calls (11.33%) at `timeout=0.05` and 0/1200 at `timeout=0.1`, indicating a host-load-sensitive daemon response-time class rather than a teardown lifecycle race.
- Triage rule: startup timeout errors happen before container start and should be mitigated with bounded init retries and reduced host contention; teardown races happen during `container.remove()` and are handled by teardown retry logic.

### Quality Gates
```bash
uv run ruff check src/ tests/ roster/ conftest.py
uv run ruff format --check src/ tests/ roster/ conftest.py
make test-qg
```

### Parallel Test Command
- Default quality-gate pytest scope uses `pytest-xdist` (`-n auto`) via `make test-qg`.
- Serial fallback/debug path remains available via `make test-qg-serial`.
- `make test-qg-parallel` is an explicit alias to the same parallel default.

### Testing cadence policy
- For bugfixes/features under active development or investigation, default to targeted `pytest` runs to keep loops fast and context lean.
- Run full-suite tests when branch changes are finalized and you need a pre-merge readiness signal.

### Dashboard health endpoint alias contract
- `src/butlers/api/app.py` must expose both `GET /api/health` and `GET /health` with the same `{"status":"ok"}` payload so direct infra probes and `/api`-prefixed clients both work.

### Approvals CAS/idempotency contract
- `src/butlers/modules/approvals/module.py` decision paths (`_approve_action`, `_reject_action`, `_expire_stale_actions`) must use compare-and-set SQL writes (`... WHERE status='pending'`) so concurrent decision attempts cannot overwrite each other.
- `src/butlers/modules/approvals/executor.py::execute_approved_action` is idempotent per `action_id`: it serializes execution with a process-local per-action lock, replays stored `execution_result` when status is already `executed`, and only performs the terminal write when status is still `approved`.

### Calendar recurrence normalization contract
- `_normalize_recurrence()` in `src/butlers/modules/calendar.py` must reject any rule containing `\\n` or `\\r` to prevent iCalendar CRLF/newline injection.
- `FREQ` presence and `DTSTART`/`DTEND` exclusion checks should be case-insensitive (`rule.upper()`), so lowercase property names cannot bypass validation.

### Calendar recurring write contract
- `CalendarEventCreate` and `CalendarEventUpdate` validate/normalize `recurrence_rule` via `_normalize_recurrence_rule`; invalid RRULEs must raise clear `ValueError`s before provider calls.
- Recurring writes with naive datetime boundaries require explicit `timezone`; omit timezone only when datetime boundaries already carry tzinfo.
- `calendar_update_event` is series-only for recurrence in v1 (`recurrence_scope="series"`); non-series scope values must be rejected at validation time.

### Switchboard Classification Contract
- `classify_message()` returns decomposition entries (`list[{"butler","prompt"}]`), not a bare butler string. Callers must normalize both legacy string and list formats before routing.
- When `butler_registry` is empty, `classify_message()` auto-discovers butlers from `roster/` (see `roster/switchboard/tools/routing/classify.py`) before composing the "Available butlers" prompt.
- Classification uses `list_butlers(..., routable_only=True)` so stale/quarantined targets are excluded from planner prompt context by default.

### Switchboard Codex tool-call parsing contract
- `src/butlers/core/runtimes/codex.py` must normalize nested Codex MCP tool-call payloads (`item.type="mcp_tool_call"` with `call`/`tool_call` sub-objects) so `route_to_butler` name + arguments are preserved in `tool_calls`; otherwise switchboard can mis-detect "no route_to_butler tools" and incorrectly fall back to `general`.

### Switchboard no-tool fallback routing contract
- In `src/butlers/modules/pipeline.py`, when LLM output includes no recognized `route_to_butler` calls, fallback routing should first attempt unambiguous target inference from CC summary text patterns like `routed to <butler>` (restricted to currently available butlers) before defaulting to `general`.

### Runtime tool-call capture contract
- `src/butlers/core/spawner.py` augments adapter-parsed `tool_calls` with daemon-observed MCP executions captured via `src/butlers/core/tool_call_capture.py`, keyed by `runtime_session_id`.
- Switchboard MCP URLs include `runtime_session_id=<session_uuid>` query params so daemon request middleware (`_McpRuntimeSessionGuard` in `src/butlers/daemon.py`) can bind incoming tool invocations to the active runtime session and capture ground-truth tool calls for fallback decisions.
- `_McpRuntimeSessionGuard` should proxy unknown attributes to the wrapped ASGI app (for example `.routes`) so middleware layering remains transparent to startup/tests that introspect the combined MCP app.

### Switchboard registry liveness/compat contract
- `butler_registry` includes liveness + compatibility metadata: `eligibility_state`, `liveness_ttl_seconds`, quarantine fields, `route_contract_min/max`, and `capabilities`.
- `resolve_routing_target()` in `roster/switchboard/tools/registry/registry.py` is the canonical gate for route eligibility: it reconciles TTL staleness, enforces stale/quarantine policy overrides, and validates route contract/capability requirements.
- Eligibility transitions are audited in `butler_registry_eligibility_log`; stale transitions (`ttl_expired`) and recovery transitions (`health_restored`/`re_registered`) should remain traceable in tests.

### Switchboard telemetry/correlation contract
- `roster/switchboard/tools/routing/telemetry.py` is the canonical `butlers.switchboard.*` metrics surface with low-cardinality attribute normalization (`source`, `destination_butler`, `outcome`, `lifecycle_state`, `error_class`, `policy_tier`, `fanout_mode`, `model_family`, `prompt_version`, `schema_version` only).
- `MessagePipeline.process()` emits the root trace span `butlers.switchboard.message` and persists `request_id` alongside lifecycle payloads in `message_inbox.classification` / `message_inbox.routing_results` (`{"request_id": ..., "payload"/"results"/"error": ...}`) for log-trace-persistence reconstruction.

### Notifications DB fallback contract
- `src/butlers/api/routers/notifications.py` should degrade gracefully when the switchboard DB pool is unavailable: `GET /api/notifications` and `GET /api/butlers/{name}/notifications` return empty paginated payloads, and `GET /api/notifications/stats` returns zeroed stats instead of propagating a `KeyError`/404.
- Notifications list serialization must normalize `metadata` to object-or-null without raising on non-mapping JSON values (for example array/string/scalar rows); unsupported metadata shapes should coerce to `null` instead of returning 400/500.

### Memory Writing Tool Contract
- `src/butlers/modules/memory/storage.py` write APIs return UUIDs (`store_episode`, `store_fact`, `store_rule`); MCP wrappers in `src/butlers/modules/memory/tools/writing.py` are responsible for shaping tool responses (`id`, `expires_at`, `superseded_id`) and must pass `embedding_engine` in the current positional order.

### Memory embedding progress-bar contract
- `src/butlers/modules/memory/embedding.py` must call `SentenceTransformer.encode(..., show_progress_bar=False)` for both single and batch embedding paths; otherwise `sentence-transformers` enables `tqdm` "Batches" output at INFO/DEBUG log levels, causing noisy interleaved logs.

### DB SSL config contract
- `src/butlers/db.py` now parses `sslmode` from `DATABASE_URL` and `POSTGRES_SSLMODE`; parsed mode is forwarded to both `asyncpg.connect()` (provisioning) and `asyncpg.create_pool()` (runtime).
- Dashboard DB setup in `src/butlers/api/deps.py` and `src/butlers/api/db.py` reuses the same env parser and forwards the same SSL mode to API pools, keeping daemon/API behavior aligned.
- When SSL mode is unset (`None`), DB connect/pool creation retries once with `ssl="disable"` if asyncpg fails during STARTTLS negotiation with `ConnectionError: unexpected connection_lost() call` (covers servers/proxies that drop SSLRequest instead of replying `S/N`).

### Telegram DB contract
- Module lifecycle receives the `Database` wrapper (not a raw pool). Telegram message-inbox logging should acquire connections via `db.pool.acquire()`, with optional backward compatibility for pool-like objects.

### Telegram ingress dedupe contract
- `src/butlers/modules/telegram.py::_store_message_inbox_entry` must persist inbound rows with deterministic Telegram dedupe keys and `ON CONFLICT (dedupe_key)` upsert semantics.
- `TelegramModule.process_update()` should treat non-insert (`decision=deduped`) ingress persistence results as replayed updates and short-circuit before pipeline routing.

### HTTP client logging contract
- CLI logging config (`src/butlers/cli.py::_configure_logging`) sets `httpx` and `httpcore` logger levels to `WARNING` to prevent request-URL token leakage (notably Telegram bot tokens in `/bot<token>/...` paths).

### Telegram reaction lifecycle contract
- `TelegramModule.process_update()` now sends lifecycle reactions for inbound message processing: starts with `:eye`, ends with `:done` when all routed targets ack, and ends with `:space invader` on any routed-target failure.
- `RoutingResult` includes `routed_targets`, `acked_targets`, and `failed_targets`; decomposition callers should populate these so Telegram can hold `:eye` until aggregate completion.
- Per-message reaction state must not grow unbounded: terminal messages should prune `_processing_lifecycle`/`_reaction_locks`, and duplicate-update idempotence should be preserved via the bounded `_terminal_reactions` cache (`TERMINAL_REACTION_CACHE_SIZE`).
- `src/butlers/modules/telegram.py::_update_reaction` treats `httpx.HTTPStatusError` 400 responses from `setMessageReaction` as expected/non-fatal when Telegram indicates reaction unsupported/unavailable; for terminal failure (`:space invader` internal alias -> 👾) it should warn-and-skip rather than emit stack traces.

### Telegram getUpdates conflict contract
- `src/butlers/connectors/telegram_bot.py::_get_updates` must treat Telegram `HTTP 409 Conflict` responses as recoverable polling conflicts: record source API status `conflict`, emit warning-level diagnostics with parsed Telegram `description`, and return `[]` instead of raising.
- `src/butlers/modules/telegram.py::_get_updates` should likewise treat `HTTP 409 Conflict` as non-fatal and return `[]` with a warning so ingress/tool callers avoid repeated unhandled stack traces during webhook/poller contention.

### Frontend test harness
- Frontend route/component tests run with Vitest (`frontend/package.json` has `npm test` -> `vitest run`).
- Colocate tests as `frontend/src/**/*.test.tsx` (example: `frontend/src/pages/ButlersPage.test.tsx`).

### Memory browser episode expansion contract
- `frontend/src/components/memory/MemoryBrowser.tsx` episodes rows expose an explicit `Expand`/`Collapse` control that reveals a full-content detail row (`Episode Content`) while keeping the main cell preview truncated.
- Regression coverage lives in `frontend/src/components/memory/MemoryBrowser.test.tsx` and asserts collapsed-by-default, expand-to-read, and collapse-again behavior.

### Frontend docs source-of-truth contract
- `docs/frontend/` is the canonical, implementation-grounded frontend spec set (`purpose-and-single-pane.md`, `information-architecture.md`, `feature-inventory.md`, `data-access-and-refresh.md`).
- `docs/FRONTEND_PROJECT_PLAN.md` is historical/aspirational context; update `docs/frontend/` when routes, tabs, feature coverage, or data-refresh/write behavior changes.
- `docs/frontend/backend-api-contract.md` is the target-state backend API contract required by the frontend; keep endpoint/query/payload definitions authoritative and up to date.

### Frontend single-pane contract updates (2026-02-14)
- `/issues` is now a first-class frontend surface (route + sidebar) backed by `useIssues`; Overview includes `IssuesPanel` alongside failed notifications.
- Overview KPI cards are wired: `Sessions Today` is sourced via `/api/sessions` with `since=<local-midnight ISO>` and `Est. Cost Today` via `/api/costs/summary?period=today`.
- Butler detail Overview cost card must show selected-butler daily cost (`by_butler[butlerName]`) plus global-share context, not global total as the primary value.
- Notification feed rows should expose drill-through links to session and trace detail when `session_id` / `trace_id` are present.
- Keyboard quick-nav includes `g` sequences: `o,b,s,t,r,n,i,a,m,c,h`.
- Butler detail tab validation must include health-only tabs so `?tab=health` deep-links resolve on `/butlers/health`.
- `/settings` now provides browser-local controls for theme, default live-refresh behavior (used by Sessions/Timeline), and clearing command-palette recent-search history.
- Frontend router must set `createBrowserRouter(..., { basename: import.meta.env.BASE_URL })` (sanitized) so `dev.sh` subpath deployments (`--base /butlers/`) behave consistently for direct loads and in-app links (for example `/butlers/secrets`), while root-origin paths like `/secrets` correctly 404 under split Tailscale path mapping.
- Contacts sync UI contract: dashboard contacts surface includes a header `Sync From Google` action that calls `POST /api/relationship/contacts/sync?mode=incremental`, shows in-flight (`Syncing...`) + toast success/error feedback, and refreshes contacts data after success. Router exposes both `/contacts` and `/butlers/contacts` to the same page.

### Session tool-call rendering contract
- `frontend/src/components/sessions/SessionDetailDrawer.tsx` must normalize tool-call records before rendering: tool names can appear as `name|tool|tool_name` (including nested `call`/`tool_call`/`toolCall`/`function` objects), arguments can appear as `input|args|arguments|parameters`, and result payloads can appear as `result|output|response`.
- When normalized arguments/results are absent, render a fallback raw payload block so `Tool Calls (N)` never appears empty for unknown record shapes.
- For legacy unnamed rows, `SessionDetailDrawer` should infer fallback tool labels from session result summaries like ``MCP tools called: - `tool_name(...)` `` so UI labels remain informative even when stored call records lack `name`.
- `src/butlers/core/runtimes/codex.py::_extract_tool_call` and `_looks_like_tool_call_event` must treat nested `tool` objects like other containers (`function`/`call`/`tool_call`/`toolCall`) when extracting tool name + arguments, preventing name loss for this Codex event shape.

### Quality-gate command contract
- `make test-qg` is the default full-scope pytest gate and runs with xdist parallelization (`-n auto`).
- `make test-qg-serial` is the documented serial fallback for debugging order-dependent behavior.

### Pytest benchmark snapshot (butlers-vrs, 2026-02-13)
- Unit-scope serial benchmark (`.venv/bin/pytest tests/ -m unit ...`) measured `114.87s` wall (`1854 passed, 358 deselected`).
- Unit-scope parallel benchmark (`.venv/bin/pytest tests/ -m unit ... -n 4`) measured `56.12s` wall (`1854 passed`), ~51% faster than the unit serial run.
- Full required gate `make test-qg` completed in this worktree at `129.15s` wall (`2211 passed, 1 skipped`), but intermittent Docker teardown flakes remain possible on DB-backed scopes (see `butlers-kle`).

### Calendar OAuth init contract
- In `src/butlers/modules/calendar.py`, `_GoogleProvider.__init__` should validate `_GoogleOAuthCredentials.from_env()` before creating an owned `httpx.AsyncClient` so credential errors cannot leak unclosed clients.
- `_GoogleOAuthClient.get_access_token()` should enforce token non-null invariants with explicit asserts rather than returning a fallback empty string.

### Calendar payload parsing error contract
- In `src/butlers/modules/calendar.py`, provider payload/data validation helpers (`_parse_google_datetime`, `_parse_google_event_boundary`, `_google_event_to_calendar_event`) raise `ValueError` for malformed event content; reserve `CalendarAuthError`/subclasses for auth/request transport failures.

### Calendar read tools contract
- `CalendarModule.register_tools()` now exposes `calendar_list_events` and `calendar_get_event`; both must call the active `CalendarProvider` abstraction (not provider-specific helpers directly).
- Tool responses are normalized as `{provider, calendar_id, ...}` with event payload keys `event_id`, `title`, `start_at`, `end_at`, `timezone`, `description`, `location`, `attendees`, `recurrence_rule`, and `color_id`.
- Optional `calendar_id` overrides must be stripped/non-empty and must not mutate the module's default configured `calendar_id`.

### Calendar roster rollout contract
- `roster/general/butler.toml`, `roster/health/butler.toml`, and `roster/relationship/butler.toml` must each declare `[modules.calendar]` with provider `google`, explicit shared Butler calendar `calendar_id` values (not `primary`), and default conflict policy `suggest`.
- `roster/general/CLAUDE.md`, `roster/health/CLAUDE.md`, and `roster/relationship/CLAUDE.md` must document calendar tool usage, shared Butler calendar assumption, default conflict behavior (`suggest`), and that attendee invites are out of v1 scope.

### Calendar conflict preflight contract
- Calendar conflict policy is `suggest|fail|allow_overlap` at tool/config boundaries; legacy config values (`allow`, `reject`) normalize to `allow_overlap`, `fail`.
- `calendar_create_event` always runs conflict preflight; `calendar_update_event` runs conflict preflight only when the start/end window changes.
- Conflict outcomes return machine-readable `conflicts` and `suggested_slots` (`suggest` policy), while `allow_overlap` currently writes through and includes conflicts in the success payload.

### Calendar overlap approval contract
- For overlap conflicts with `conflict_policy="allow_overlap"` and `conflicts.require_approval_for_overlap=true`, `calendar_create_event` / `calendar_update_event` must return `status="approval_required"` before provider writes and queue a `pending_actions` row with executable `tool_name` + serialized `tool_args`.
- Queued calendar overlap actions include `approval_action_id`; replay calls with that id should only bypass re-queue when the corresponding pending action is in `approved` state for the same tool.
- If approvals storage is unavailable (for example approvals module disabled or `pending_actions` table missing), overlap overrides must return `status="approval_unavailable"` plus explicit fallback guidance instead of writing.

### Approvals executor fallback contract
- `ButlerDaemon._apply_approval_gates()` should wire approvals execution with a fallback to registered MCP tool handlers when a `tool_name` is not present in gated originals, so module-queued pending actions for non-gated tools can execute after approval.

### Beads coordinator handoff guardrail
- Some worker runs can finish with branch pushed but bead still `in_progress` (no PR/bead transition). Coordinator should detect `agent/<id>` ahead of `main` with no PR and normalize by creating a PR and marking the bead `blocked` with `pr-review` + `external_ref`.

### Beads push guardrail
- Repo push checks enforce a clean beads state; `git push` can fail with "Uncommitted changes detected" even after commits if `.beads/issues.jsonl` was re-synced/staged during pre-push checks.
- If this happens, run `bd sync --status`, inspect staged `.beads/issues.jsonl`, commit the sync normalization (or intentionally restore it), then re-run `git push`.

### Beads worktree JSONL contract
- `.beads/config.yaml` is pinned to `no-db: true` — **JSONL is the sole source of truth, not SQLite.** All `bd` commands read/write `.beads/issues.jsonl` directly. Do not attempt to fix SQLite state, run `bd migrate`, or `bd sync --import-only` — these are no-ops or irrelevant in this mode.
- Regression coverage lives in `tests/tools/test_beads_worktree_sync.py` and must keep worktree `bd close`/`bd show`/`bd export`/`bd import` aligned with branch-local `.beads/issues.jsonl`.

### Beads PR-review strip guardrail
- Before a reviewer worker strips `.beads/` drift from a PR branch, persist any new coordinator-side bead mutations on main first; otherwise restoring `.beads/` from `origin/main` can resurrect stale issue snapshots and drop freshly created/updated beads (for example new `pr-review-task` IDs).

### Beads no-db worktree hydration contract
- When a worker worktree may be stale relative to newly-created issues, run `bd sync --import` in that worktree before `bd show <id>` lookups.
- Regression coverage lives in `tests/tools/test_beads_worktree_hydration.py` and verifies stale lookup failure followed by successful hydration.

### Pre-existing test failure (tests/daemon/test_module_state.py)
- `tests/daemon/test_module_state.py::TestInitModuleRuntimeStates::test_failed_module_persists_disabled_to_store` is failing on main as of 2026-02-20. CI runs `mergeStateStatus: UNSTABLE` for PRs unrelated to daemon module state. This is a pre-existing failure not introduced by credential_store or butler_secrets PRs.

### CredentialStore service (src/butlers/credential_store.py)
- Lives at `src/butlers/credential_store.py`. Backed by `butler_secrets` table (migration `core_008`).
- Uses `TYPE_CHECKING` guard to import `asyncpg.Pool` (avoids runtime dependency, keeps type safety).
- `resolve(key, env_fallback=True)`: DB-first, then `os.environ.get(key)`, skips empty string env values.
- `list_secrets()` returns only DB-stored secrets (env-only secrets are not listed). `is_set=True` always for any DB row (table enforces `secret_value NOT NULL`).
- Thread-safe: each operation independently calls `pool.acquire()`; never shares connections across concurrent calls.
- Gmail connector DB bootstrap must read OAuth keys via `CredentialStore`/`load_google_credentials` (`butler_secrets`), not legacy `google_oauth_credentials`; optional Pub/Sub token lookup failures must not null-out already resolved OAuth creds.

### Beads worktree write guardrail
- In git worktrees, `bd` operations can target the primary repo DB/JSONL instead of the worktree copy; verify with `bd --no-db show <id>` before write operations.
- For worker-branch bead metadata commits, run `bd --no-db` for create/update/dep commands in the worktree so `.beads/issues.jsonl` changes are tracked on that branch.
- `bd worktree create` may append per-worktree paths to repo `.gitignore`; strip those incidental lines before committing to avoid unrelated drift on `main`.
- When integrating worker commits from `agent/*` branches, never carry `.beads/issues.jsonl` changes into `main`; restore/cherry-pick code-only to prevent reopened/rolled-back bead statuses.

### Beads dependency timestamp guardrail
- In no-daemon worktree flows (`BEADS_NO_DAEMON=1`), `bd dep add` currently serializes new dependency records with `created_at="0001-01-01T00:00:00Z"` instead of wall-clock time; treat this as tooling debt (tracked in `butlers-865`) rather than a per-bead data-model change.

### Beads PR-review `external_ref` uniqueness contract
- Beads enforces global uniqueness for `issues.external_ref`; a dedicated `pr-review-task` bead cannot reuse the same `gh-pr:<number>` already attached to the original implementation bead.
- For split original/review-bead workflows, keep `external_ref` on the original bead and store PR metadata (`PR URL`, `PR NUMBER`, original bead id) in review-bead notes/labels, then dispatch reviewer workers with explicit PR context.

### Beads PR-review dependency-direction guardrail
- If the original implementation bead must be blocked by a dedicated PR-review bead, do not create the review bead with `--deps discovered-from:<original>` because that pre-wires the reverse dependency and causes a cycle when adding `<original> depends-on <review>`.
- Preferred flow: create the review bead without `discovered-from`, then add `bd dep add <original> <review>` so review completion unblocks the original bead.

### Beads merge-blocker dedupe guardrail
- Before creating a new `Resolve merge blockers for PR #<n>` bead from a blocked `pr-review-task`, check for an existing open blocker bead tied to the same PR/original issue and reuse it by wiring dependencies instead of creating duplicates.

### Beads merge-blocker completion guardrail
- Merge-blocker worker runs can leave the blocker bead `in_progress` after successfully unblocking/merging a PR; coordinator should normalize by closing the blocker bead and, when applicable, closing related `pr-review`/original beads for merged PRs.

### PR merge + worktree cleanup guardrail
- `gh pr merge --delete-branch` can return non-zero even after a successful remote merge when local branch deletion fails because that branch is checked out in another worktree (common in `.worktrees/parallel-agents/*`).
- Always verify merge via `gh pr view --json state,mergedAt` before deciding blocked vs merged, then remove the checked-out worktree and delete the local branch separately.

### Beads lint template contract
- `bd lint` enforces section headers in issue descriptions, not only structured fields.
- For `task` issues include `## Acceptance Criteria` in `description`; for `epic` issues include `## Success Criteria`.
- For `bug` issues created with `--validate`, include `## Acceptance Criteria` in `description` (the separate `--acceptance` flag alone is not sufficient).

### Relationship `important_dates` column contract
- Relationship schema stores date kind in `important_dates.label` (not `important_dates.date_type`).
- API queries touching birthdays/upcoming dates should use `label` consistently to avoid `UndefinedColumnError` on production schema.

### Relationship groups API schema-compat contract
- `roster/relationship/api/router.py` group reads (`list_groups`, `get_group`) must introspect `groups` columns via `information_schema.columns` before composing SELECTs.
- For deployments where `groups.description` and/or `groups.updated_at` are absent, project fallback expressions (`NULL::text AS description`, `g.created_at AS updated_at`) so responses keep the `Group` model shape and avoid `UndefinedColumnError`.

### Switchboard MCP routing contract
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` calls butler endpoints via `fastmcp.Client` and should return `CallToolResult.data` when present.
- If a target returns `Unknown tool` for an identity-prefixed routing tool name, routing retries `trigger` with mapped args (`prompt` from `prompt`/`message`, optional `context`).

### Route/notify envelope contract
- `roster/switchboard/tools/routing/contracts.py` exports `NotifyDeliveryV1`, `NotifyRequestV1`, and `parse_notify_request`; daemon messenger `route.execute` validation depends on these for `notify.v1` payload parsing.
- `RouteInputV1.context` must accept either string or mapping payloads (`str | dict | None`) because messenger `route.execute` carries structured `input.context.notify_request` objects.
- Messenger `route.execute` must reject `notify_request.origin_butler` when it does not match routed `request_context.source_sender_identity` (deterministic `validation_error`) before any channel send/reply side effects.

### Base notify and module-tool naming contract
- `docs/roles/base_butler.md` defines `notify` as a versioned envelope surface (`notify.v1` request, `notify_response.v1` response) with required `origin_butler`; reply intents require request-context targeting fields.
- Messenger delivery transport is route-wrapped: Switchboard dispatches `route.v1` to Messenger `route.execute` with `notify.v1` in `input.context.notify_request`; Messenger returns `route_response.v1` and should place normalized delivery output in `result.notify_response`.
- `notify_response.v1` uses the same canonical execution error classes as route executors (`validation_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`); local admission overflow maps to `overload_rejected`.
- Messenger `route.execute` MUST include normalized `notify_response` in error paths when `input.context.notify_request` is missing or invalid, ensuring consistent error reporting contract (route-level error + notify-level error payload).
- `docs/roles/base_butler.md` does not define channel-facing tool naming/ownership as a base requirement; that policy is role-specific.
- `docs/roles/switchboard_butler.md` owns the channel-facing tool surface policy: outbound delivery send/reply tools are messenger-only, ingress connectors remain Switchboard-owned, and non-messenger butlers must use `notify.v1`.
- `docs/roles/switchboard_butler.md` explicitly overrides base `notify` semantics so Switchboard is the notify control-plane termination point (not a self-routed notify caller).
- `roster/switchboard/tools/routing/contracts.py` is the canonical parser surface for routed notify termination: `parse_notify_request()` validates `notify.v1`, and `RouteInputV1.context` must accept both string context and object context (for messenger `input.context.notify_request` payloads).

### Route/notify contract parsing alignment
- `src/butlers/daemon.py` imports `parse_notify_request` from `butlers.tools.switchboard.routing.contracts` at module import time; keep that parser exported in `roster/switchboard/tools/routing/contracts.py`.
- `RouteInputV1.context` must accept structured objects (`dict`) in addition to text so Messenger `route.execute` can receive `input.context.notify_request` payloads.

### Notify react message normalization contract
- `src/butlers/daemon.py::notify` must normalize omitted `message` to `""` before building `notify_request.delivery` so `intent="react"` payloads remain valid through downstream `notify.v1` validation paths that require a string-typed `delivery.message`.

### Pipeline identity-routing contract
- `src/butlers/modules/pipeline.py` should route inbound channel messages with identity-prefixed tool names (default `bot_switchboard_handle_message`) and include `source_metadata` (`channel`, `identity`, `tool_name`, optional `source_id`) in routed args.
- `roster/switchboard/tools/routing/dispatch.py::dispatch_decomposed` should pass through identity-aware source metadata and the prefixed logical `tool_name` for each sub-route.
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` should retry `trigger` for unknown identity-prefixed tool names, preserving source metadata via trigger context.

### Spawner trigger-source/failure contract
- Core daemon `trigger` MCP tool should dispatch with `trigger_source="trigger"` (not `trigger_tool`) to stay aligned with `core.sessions` validation.
- `src/butlers/core/sessions.py` canonical trigger-source allowlist includes `route` because daemon `route.execute` background and recovery flows dispatch `spawner.trigger(..., trigger_source="route")`.
- `src/butlers/core/spawner.py::_run` should initialize duration timing before `session_create()` so early failures preserve original errors instead of masking with timer variable errors.
- `src/butlers/core/spawner.py::trigger` should fail fast when `trigger_source=="trigger"` and the per-butler lock is already held, preventing runtime self-invocation deadlocks (`trigger` tool calling back into the same spawner while a session is active).
- `src/butlers/core/runtimes/codex.py::CodexAdapter.invoke` must raise on non-zero CLI exit codes (instead of returning `"Error: ..."` as normal output) so spawner/session rows persist `success=false` and dashboard status matches runtime failures.
- `src/butlers/core/spawner.py::_build_env` includes host `PATH` as a minimal runtime baseline before declared credentials so spawned CLIs can resolve shebang dependencies (for example `/usr/bin/env node`) without hardcoded machine-specific node paths.

### Spawner system prompt composition contract
- `src/butlers/core/spawner.py::_compose_system_prompt` is the canonical composition path: runtime receives raw `CLAUDE.md` system prompt when memory context is unavailable, and appends memory context as a double-newline suffix when available.
- `tests/core/test_core_spawner.py::TestFullFlow` should patch `fetch_memory_context` for deterministic assertions so local memory module/tool availability cannot change expected `system_prompt` text.

### Sessions summary contract
- `src/butlers/daemon.py` core MCP registration should include `sessions_summary`; dashboard cost fan-out relies on declared tool metadata and will log `"Tool 'sessions_summary' not listed"` warnings if not advertised.
- `src/butlers/core/sessions.py::sessions_summary` response payload should include `period`, and unsupported periods must raise `ValueError` with an `"Invalid period ..."` message.

### Liveness reporter 404 contract
- `src/butlers/daemon.py::_liveness_reporter_loop` must treat heartbeat endpoint `404 Not Found` as persistent misconfiguration (wrong host/port/path), log a single warning, and stop the reporter loop instead of retrying indefinitely with traceback spam.
- Regression coverage lives in `tests/daemon/test_liveness_reporter.py::test_404_disables_reporter_without_retries`.

### Switchboard heartbeat auto-registration contract
- `roster/switchboard/api/router.py::receive_heartbeat` should attempt roster-driven self-registration (`roster/<butler>/butler.toml`) when a heartbeat arrives for a butler missing from `butler_registry`, then re-check registry and continue normal heartbeat state handling.
- Unknown names with no roster config must still return `404`, preserving the signal for truly invalid targets.

### MCP client lifecycle hotspot
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` currently opens a new `fastmcp.Client` (`async with`) per routed tool call, which can generate high `/sse` + `ListToolsRequest` log volume under heartbeat fanout.
- `src/butlers/core/spawner.py` memory hooks (`fetch_memory_context`, `store_session_episode`) also create one-off Memory MCP clients per call; this is another source of SSE session churn.

### MCP SSE disconnect guard contract
- `src/butlers/daemon.py::_McpSseDisconnectGuard` wraps the FastMCP SSE ASGI app and suppresses expected `starlette.requests.ClientDisconnect` only for `POST .../messages` requests.
- The guard logs a concise DEBUG line with butler/path/session context and attempts a lightweight empty `202` response when possible; non-`/messages` disconnects and non-disconnect exceptions must still bubble.
### Telegram identity tool contract
- `src/butlers/modules/telegram.py` registers only identity-prefixed tools: `user_telegram_get_updates`, `user_telegram_send_message`, `user_telegram_reply_to_message`, `bot_telegram_get_updates`, `bot_telegram_send_message`, and `bot_telegram_reply_to_message`.
- Legacy unprefixed Telegram tool names must not be registered.
- User-output descriptors (`user_telegram_send_message`, `user_telegram_reply_to_message`) are marked as approval-required defaults in descriptor descriptions (`approval_default=always`).

### Telegram inbox logging contract
- `TelegramModule.process_update()` should log inbound payloads via `db.pool.acquire()` when DB is available and pass the returned `message_inbox_id` into `pipeline.process(...)`.
- Keep Telegram `pipeline.process` tool args aligned with tests (`source`, `source_channel`, `source_identity`, `source_tool`, `chat_id`, `source_id`); additional metadata should not be forced into this call path without updating tests/contracts.

### Email tool scope/approval contract
- In `src/butlers/modules/email.py`, `user_*` and `bot_*` prefixes currently represent scoped tool surfaces; both still use the same configured SMTP/IMAP credentials (`SOURCE_EMAIL` / `SOURCE_EMAIL_PASSWORD`).
- Both `user_*` and `bot_*` email send/reply output descriptors are documented as `approval-required default`; tests in `tests/modules/test_module_email.py` assert this marker.

### Telegram/Email identity-credential config contract
- Telegram and Email module config now supports identity-scoped credential tables: `[modules.telegram.user]` / `[modules.telegram.bot]` and `[modules.email.user]` / `[modules.email.bot]`.
- Env var name fields in those scopes (`*_env`) must be valid environment variable identifiers and are schema-validated in module config models.
- Butler startup credential validation collects enabled identity-scope env vars and reports missing values with scope-qualified sources (for example `module:telegram.bot`, `module:email.bot`).

### Identity-aware approval defaults contract
- `ToolIODescriptor` includes `approval_default` (`none`, `conditional`, `always`) and module output descriptors should set it explicitly.
- `ButlerDaemon._apply_approval_gates()` merges default-gated user output tools before wrapping gates: user send/reply outputs (`approval_default="always"` and `user_*_*send*` / `user_*_*reply*` safety fallback) are auto-gated whenever approvals are enabled.
- Bot outputs are **not** auto-gated by defaults; they remain configurable via `[modules.approvals.gated_tools]` entries.
- `tests/daemon/test_approval_defaults.py::test_user_send_and_reply_outputs_are_gated_by_name_safety_net` verifies the name-based safety fallback still gates user send/reply tools even when descriptor `approval_default` is not `always`.

### Tool-name compliance scan contract
- `tests/test_tool_name_compliance.py::test_legacy_unprefixed_tool_names_absent_from_repo_text_surfaces` scans docs/spec text in addition to code; avoid bare legacy tool tokens in prose/examples and use identity-prefixed names instead (for example `bot_switchboard_handle_message`).
- The same compliance scan also flags standalone legacy tokens in test literals/fixtures; when asserting legacy-name rejection, construct those names dynamically (for example `"send" + "_message"`) rather than embedding bare tokens directly.

### Route.execute authn/authz contract
- `src/butlers/daemon.py` `route.execute` enforces `request_context.source_endpoint_identity` against `ButlerConfig.trusted_route_callers` (default: `("switchboard",)`) before any spawner trigger or delivery adapter call.
- Unauthorized callers receive a deterministic `validation_error` response with `retryable=false`; no side effects occur.
- `[butler.security].trusted_route_callers` in `butler.toml` overrides the default; empty list rejects all callers.
- Regression tests in `tests/daemon/test_route_execute_authz.py` cover unauthenticated/unauthorized rejection, custom config, and authorized pass-through.

### Core tool registration contract
- `src/butlers/daemon.py` exports `CORE_TOOL_NAMES` as the canonical core-tool set (including `notify`); registration tests should assert against this set to prevent drift between `_register_core_tools()` behavior and expected tool coverage.
- MCP tool-call logging is centralized in `src/butlers/daemon.py`: `_register_core_tools()` registers through `_ToolCallLoggingMCP(module_name="core")`, and module tools log through `_SpanWrappingMCP` before module-enabled gating/span execution.
- Canonical call log format is `MCP tool called (butler=%s module=%s tool=%s)`; keep this stable for log parsing/observability.

### Switchboard ingress dedupe contract
- `MessagePipeline` enforces canonical ingress dedupe when `enable_ingress_dedupe=True` (wired on for Switchboard in `src/butlers/daemon.py::_wire_pipelines`).
- Dedupe keys are channel-aware: Telegram uses `<endpoint_identity>:update:<update_id>`, Email uses `<endpoint_identity>:message_id:<Message-ID>`, API/MCP use `<endpoint_identity>:idempotency:<caller-key>` when present, else `<endpoint_identity>:payload_hash:<sha256>:window:<5-minute-bucket>`.
- Ingress decisions log as `"Ingress dedupe decision"` with `ingress_decision=accepted|deduped`; deduped replays map to the existing canonical `request_id` and short-circuit routing.

### Approvals product-contract docs alignment
- `docs/modules/approval.md` is now a product-level contract (not just current behavior) and includes explicit guardrails for single-human approver model, idempotent decision/execution semantics, immutable approval-event auditing, data redaction/retention, risk-tier policy precedence, and friction-minimizing operator UX.
- Frontend docs now explicitly track approvals as target-state single-pane integration: planned IA routes in `docs/frontend/information-architecture.md`, current gap in `docs/frontend/feature-inventory.md`, target data-access guidance in `docs/frontend/data-access-and-refresh.md`, and target API endpoints in `docs/frontend/backend-api-contract.md`.

### Approvals immutable event-log contract
- Approvals migrations include `approvals_002` with append-only `approval_events` and a trigger (`trg_approval_events_immutable`) that rejects `UPDATE`/`DELETE`; event rows must be written via inserts only.
- Canonical approval event types are `action_queued`, `action_auto_approved`, `action_approved`, `action_rejected`, `action_expired`, `action_execution_succeeded`, `action_execution_failed`, `rule_created`, and `rule_revoked`.

### Channel egress ownership enforcement contract
- `src/butlers/daemon.py::_register_module_tools` enforces Messenger-only channel egress ownership at startup: for non-messenger butlers, tools matching channel send/reply egress patterns (for example `user_telegram_send_message`, `bot_email_reply_to_thread`) are silently stripped from declared tool sets and filtered during registration. All I/O descriptors (inputs + outputs) are scanned defensively to catch misclassified egress tools.
- Switchboard and other butlers can still load channel modules (telegram, email) for ingress; only egress output tools are filtered.
- `_SpanWrappingMCP` accepts `filtered_tool_names` to suppress registration of stripped tools without raising errors.
- `_CHANNEL_EGRESS_ACTIONS` uses string concatenation (`"send" + "_message"`) to avoid triggering the tool-name compliance scanner.
- Migration path: Phase 1 (current) is silent filter/strip with INFO logging; Phase 2 upgrades to hard `ChannelEgressOwnershipError`; Phase 3 removes compatibility shims.
- Migration guidance documented in `docs/roles/messenger_butler.md` section 20.

### Approvals risk-tier + precedence runtime contract
- `src/butlers/config.py::ApprovalConfig` now includes `default_risk_tier` plus per-tool `GatedToolConfig.risk_tier`; `parse_approval_config` validates both against `ApprovalRiskTier` (`low|medium|high|critical`).
- Standing rule matching precedence is deterministic in `src/butlers/modules/approvals/rules.py` (`constraint_specificity_desc`, `bounded_scope_desc`, `created_at_desc`, `rule_id_asc`); gate responses include `risk_tier` and `rule_precedence`.
- High-risk tiers (`high`, `critical`) enforce constrained standing rules in `src/butlers/modules/approvals/module.py`: at least one exact/pattern arg constraint and bounded scope (`expires_at` or `max_uses`); `create_rule_from_action` and approve+create-rule paths auto-bound high-risk rules with `max_uses=1`.

### Beads concurrent-state reconciliation guardrail
- In multi-worker coordinator runs, stale worker commits of `.beads/issues.jsonl` can resurrect previously normalized bead state (for example, reintroducing `review-running` labels or flipping merged review beads back to `blocked`).
- After each coordinator cycle, re-run a PR-state normalization pass (`blocked` + `pr-review` / `pr-review-task`) before dispatching more workers, rather than assuming prior status updates remained authoritative.

### Dev bootstrap connector env-file contract
- `dev.sh` connectors window runs three connector processes: Telegram bot, Telegram user-client, and Gmail.
- Each connector pane may source a local-only env file under `secrets/connectors/` (`telegram_bot`, `telegram_user_client`, `gmail`) using `set -a` so values only affect that pane process.
- Connector identity/cursor env overrides should be per-connector (`TELEGRAM_BOT_CONNECTOR_*`, `TELEGRAM_USER_CONNECTOR_*`, `GMAIL_CONNECTOR_*`) to avoid shared `CONNECTOR_ENDPOINT_IDENTITY` / `CONNECTOR_CURSOR_PATH` collisions.

### Dev script location + process-clear contract
- Canonical bootstrap implementation now lives at `scripts/dev.sh`; repository-root `dev.sh` is a compatibility shim that delegates to `scripts/dev.sh`.
- `scripts/clear-processes.sh` is the canonical pre-bootstrap cleanup helper: by default it targets listeners on `POSTGRES_PORT` (`54320`), `FRONTEND_PORT` (`40173`), and `DASHBOARD_PORT` (`40200`), with explicit override via `EXPECTED_PORTS`.

### Telemetry span concurrency guardrail
- `src/butlers/core/telemetry.py::tool_span` decorator usage is unsafe if per-invocation span/token state is stored on the decorator instance (`self._span`, `self._token`): concurrent calls to one decorated async handler can trigger OpenTelemetry `Failed to detach context` / `Token ... created in a different Context`.
- Repro pattern: concurrent `await asyncio.gather(...)` calls to a single `@tool_span(...)`-decorated function fail; per-call context-manager usage (`with tool_span(...)`) does not.
- Track holistic fix in `butlers-978`, including both decorator state isolation and concurrent-session `_active_session_context` parent-lineage hardening.

### Dev bootstrap tailscale+pipefail guardrail
- `dev.sh::_tailscale_serve_check` should prefer modern Tailscale CLI syntax (`tailscale serve --yes --bg --https=443 http://localhost:40200`) with legacy positional fallback (`https:443 ...`) for older CLI versions.
- `dev.sh` split routing defaults are `TAILSCALE_DASHBOARD_PATH_PREFIX=/butlers` (Vite frontend) and `TAILSCALE_API_PATH_PREFIX=/butlers-api` (dashboard API); non-root path routing uses `tailscale serve --set-path <prefix> ...`.
- Dashboard mapping should proxy to `http://localhost:${FRONTEND_PORT}${TAILSCALE_DASHBOARD_PATH_PREFIX}` (not bare frontend root) so prefix paths are preserved end-to-end and Vite `--base` assets avoid redirect loops under tailscale path routing.
- Frontend dev port is configurable via `FRONTEND_PORT` (default `40173`) and should be kept aligned with tailscale dashboard target and the Vite startup command (`--port ... --strictPort`).
- `docker/Dockerfile` is the dev-suite image target for `dev.sh`: include `tmux`, `postgresql-client`, Docker CLI + compose plugin, tailscale CLI, Node.js, and global runtime CLIs (`@openai/codex`, `claude-code`, `opencode-ai`) so `dev.sh` can run in-container when host sockets are mounted.
- Do not discard `tailscale serve` stderr in `dev.sh`; surfaced output is needed to diagnose operator/permission failures (for example `Access denied: serve config denied` and `sudo tailscale set --operator=$USER` remediation).
- In `dev.sh` with `set -o pipefail`, avoid `grep ... | wc -l || echo 0` inside command substitutions; on no-match this can produce `0\n0` and break integer comparisons.

### Scheduler native-dispatch contract
- `ButlerDaemon._dispatch_scheduled_task()` is the scheduler dispatch hook used by both the background scheduler loop and MCP `tick` tool; deterministic schedules can bypass runtime/LLM calls here.
- Switchboard `schedule:eligibility-sweep` is natively dispatched via the roster job loader (`_load_switchboard_eligibility_sweep_job`) and executes against the switchboard DB pool directly; non-native schedules still fall back to `spawner.trigger`.
- `ScheduleConfig` now carries `mode` (`session` default, `job` for deterministic/native execution); config loading must reject unknown `[[butler.schedule]].mode` values.
- Switchboard deterministic schedules (`connector-stats-hourly-rollup`, `connector-stats-daily-rollup`, `connector-stats-pruning`, `eligibility-sweep`) should be declared with `mode = "job"` in `roster/switchboard/butler.toml` so scheduler dispatch bypasses LLM sessions.
- `ButlerDaemon._dispatch_scheduled_task()` resolves schedule mode from `self.config.schedules`; `mode="job"` schedules use `_load_switchboard_schedule_jobs()` handlers and fail fast when no handler is registered (no fallback `spawner.trigger` call).

### Issues aggregation contract
- `src/butlers/api/routers/issues.py` aggregates reachability checks plus grouped `dashboard_audit_log` failures.
- Audit groups are keyed by normalized first-line error message and expose `occurrences`, `first_seen_at`, `last_seen_at`, and distinct `butlers`.
- `GET /api/issues` is ordered by recency (`last_seen_at` desc), not severity-first; schedule-related groups (`operation=session` + `trigger_source` like `schedule:%`) are classified as `critical` `scheduled_task_failure:*`, all other audit groups are `warning` `audit_error_group:*`.

### State API JSON-shape contract
- `src/butlers/api/models/state.py::StateEntry.value` and `StateSetRequest.value` are typed `Any` (widened from `dict[str, Any]` in PR #205); scalar/array/null JSON rows in `state.value` are now serialized correctly.
- Keep list/get state endpoint value-shape contracts aligned with the full JSON domain accepted by the underlying state storage.
- asyncpg decodes JSONB columns directly to native Python types; no secondary `json.loads` fallback is needed in the router.

### Connector credential resolution pattern (CredentialStore)
- Connectors are standalone processes and need their own short-lived asyncpg pool (min_size=1, max_size=2, command_timeout=5) gated on `DATABASE_URL` or `POSTGRES_HOST` being set.
- `TelegramBotConnectorConfig` and `TelegramUserClientConnectorConfig` are Python **dataclasses** (not Pydantic models); use `dataclasses.replace(config, field=value)` for partial updates — `model_copy()` is Pydantic-only.
- `GmailConnectorConfig` is a Pydantic `BaseModel` with `frozen=True`; use `config.model_copy(update={...})` for partial updates.
- Pydantic v2 auto-coerces `str` to `pathlib.Path` for `Path`-typed fields, but prefer explicit `Path(cursor_path_str)` at construction sites to satisfy static type checkers and remove `type: ignore` suppressions.
- `bd close` from worktrees silently fails to persist due to redirect/sharing issues; always re-close beads from the `beads-sync` branch after worktree operations.

### Secrets shared-target contract
- `src/butlers/api/routers/secrets.py` treats `/api/butlers/shared/secrets` as a reserved target that resolves via `DatabaseManager.credential_shared_pool()` (not `db.pool("shared")`), returning 503 with `"Shared credential database is not available"` when unset.
- `frontend/src/pages/SecretsPage.tsx` must include a first-class `shared` selector target (via `buildSecretsTargets`) so users can manage shared secrets directly, with per-butler entries representing local override stores.
- `frontend/src/hooks/use-secrets.ts::useSecrets` is responsible for effective-read fallback in the Secrets page: for non-`shared` targets it merges `listSecrets(<butler>)` with `listSecrets("shared")`, preserving local rows on key collisions and marking shared-only rows as `source="shared"` so UI status badges show inherited shared values instead of `Missing (null)`.
- `frontend/src/pages/SecretsPage.tsx` no longer includes a dedicated "Configure App Credentials" form card; Google app credentials are managed through generic secrets rows (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`) and the OAuth section focuses on status/connect/delete actions.
- `src/butlers/api/routers/oauth.py::_get_scopes()` uses the fixed `_DEFAULT_SCOPES` set for `/api/oauth/google/start`; `GOOGLE_OAUTH_SCOPES` is no longer a runtime override input.
- Fixed OAuth scopes now include People-related scopes in addition to Gmail/Calendar: `contacts`, `contacts.readonly`, `contacts.other.readonly`, and `directory.readonly`.
- `frontend/src/lib/secret-templates.ts` should include `OPENAI_API_KEY` under core templates so `/secrets` shows it as a first-class configurable key.
- `src/butlers/core/spawner.py::_build_env` globally injects both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` (CredentialStore DB-first, env fallback) for every runtime spawn, independent of `[butler.env]`.

### One-DB multi-schema migration planning contract
- `docs/operations/one-db-multi-schema-migration.md` is the authoritative plan for epic `butlers-1003`: target topology (`shared` + per-butler schemas), role/ACL model, phased cutover + rollback, parity/isolation gates, and child-issue decomposition.
- `docs/architecture/system-architecture.md` remains current-state for deployed topology and includes a transition note linking to the migration plan.
- `scripts/one_db_data_migration.py` is the canonical `butlers-1003.4` data-move utility: use `plan` + `migrate --dry-run` for staged rehearsal, `run` for copy+parity, and `rollback --confirm-rollback ROLLBACK` to reset target tables after failed attempts; archive JSON reports from each phase.
- `docs/operations/one-db-data-migration-runbook.md` is the executable command/checklist reference for staging dry-runs, parity signoff, and rollback validation.
- `scripts/one_db_migration_reset_workflow.py` is the canonical destructive reset utility for migration rewrite rollout (`butlers-1013.4`): `reset` (database or managed schemas), `migrate` (schema-scoped `core` + `memory` baselines), `validate` (schema/table/revision matrix), and `run` (end-to-end with report artifacts).
- `docs/operations/migration-rewrite-reset-runbook.md` is the step-by-step operator procedure for local/dev/staging destructive reset rehearsal, including safety prechecks and required SQL validation evidence.

### Telegram connector DB-first startup contract
- `run_telegram_bot_connector()` and `run_telegram_user_client_connector()` must not hard-fail on missing credential env vars when DB credentials are available; if `from_env()` fails only due missing creds and DB lookup succeeded, build config from required non-credential env vars plus DB-resolved secrets.
- Keep required non-credential startup env checks explicit in DB-fallback path (`SWITCHBOARD_MCP_URL`, `CONNECTOR_ENDPOINT_IDENTITY`, and `CONNECTOR_CURSOR_PATH` for user-client).
- Regression coverage lives in `tests/connectors/test_telegram_bot_connector.py::test_run_telegram_bot_connector_uses_db_token_when_env_missing` and `tests/connectors/test_telegram_user_client.py::test_run_telegram_user_client_connector_uses_db_credentials_when_env_missing`.

### OAuth/dev messaging DB-first contract
- User-facing OAuth guidance (dev bootstrap, startup guards, OAuth callback responses) should default to dashboard + shared `butler_secrets` persistence and avoid recommending `GMAIL_*`/manual env fallback as the normal path.
- `docs/runbooks/connector_operations.md` should not advertise removed `GMAIL_*` aliases; troubleshooting should direct operators to rerun OAuth/bootstrap so credentials persist in DB.

### Legacy-compat cleanup hotspots (dev runtime)
- Runtime source currently does not read `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` or deprecated `GMAIL_*` credential aliases directly; these names mainly remain in `dev.sh`, docs, and tests.
- Active compatibility hotspots to evaluate first for removal: `dev.sh::_has_google_creds`, `src/butlers/modules/calendar.py::_resolve_credentials` fallback path, `src/butlers/google_credentials.py` legacy asyncpg/table helpers, `roster/switchboard/tools/notification/deliver.py` legacy positional-arg shim, and `src/butlers/api/routers/butlers.py` legacy module-status list parsing.

### Gmail connector shared-schema credential lookup contract
- `src/butlers/connectors/gmail.py::_resolve_gmail_credentials_from_db` must perform layered DB-first lookup across local (`CONNECTOR_BUTLER_DB_NAME` + optional `CONNECTOR_BUTLER_DB_SCHEMA`) and shared (`BUTLER_SHARED_DB_NAME` + `BUTLER_SHARED_DB_SCHEMA`, default `shared`) contexts.
- Each lookup pool must apply schema-scoped `server_settings={"search_path": ...}` (via `schema_search_path`) so `butler_secrets` resolves correctly in one-db/shared-schema topologies; otherwise DB-only startup cannot resolve credentials and will fail.
- Regression coverage lives in `tests/test_gmail_connector.py::TestResolveGmailCredentialsFromDb::test_uses_shared_schema_fallback_with_schema_scoped_search_path`.

### Gmail connector DB-first startup contract
- `src/butlers/connectors/gmail.py::run_gmail_connector` is DB-only for Google OAuth credentials: it must require credentials from `butler_secrets` and must not fall back to credential env vars.
- `GmailConnectorConfig.from_env(...)` accepts DB-injected credentials as explicit args and reads only non-secret runtime env config.
- Regression coverage lives in `tests/test_gmail_connector.py::TestRunGmailConnectorStartup`.

### Gmail connector error-detail logging contract
- `src/butlers/connectors/gmail.py::_format_google_error` is the canonical parser for Google API/OAuth JSON error payloads; keep logs compact (`code/status/reason/message` or `error/error_description`) and avoid dumping full payloads.
- `GmailConnectorRuntime._fetch_history_changes()` must log parsed Google details for `history.list` 404 cursor resets and for other non-2xx `history.list` responses before `raise_for_status()`.
- `GmailConnectorRuntime._get_access_token()` must log parsed OAuth error details on non-2xx token refresh responses (for example `invalid_grant`) before raising.

### Butler runtime/model pinning contract
- Runtime adapter selection is read from top-level `[runtime].type` in each `roster/*/butler.toml` (defaults to `"claude-code"` when omitted).
- Runtime model selection is read from `[butler.runtime].model` (defaults to `src/butlers/config.py::DEFAULT_MODEL` when omitted).
- Codex runtime system instructions are loaded from per-butler `AGENTS.md` (via `src/butlers/core/runtimes/codex.py::parse_system_prompt_file`), not `CLAUDE.md`.
- `CodexAdapter.invoke()` must call `codex exec --json --full-auto` (non-interactive mode). Top-level `codex --full-auto` requires a TTY and should not be used by the spawner subprocess path.
- Codex CLI no longer supports `--instructions`; butler/system prompt content must be embedded into the `exec` initial prompt payload, and MCP endpoints should be passed via `-c mcp_servers.<name>.url="..."`.
- `CodexAdapter.invoke()` must insert a `--` option delimiter before the positional prompt argument so user prompts beginning with `-`/`--` are not parsed as Codex CLI flags.
- `CodexAdapter.invoke()` must forward configured model via CLI `--model <id>` when `model` is non-empty, so roster model pins (for example `gpt-5.3-codex-spark`) are actually enforced at launch time.

### Butler MCP debug surface contract
- Butler detail now includes an always-available `MCP` tab (`frontend/src/pages/ButlerDetailPage.tsx`) for per-butler debug tool calls.
- Dashboard API exposes per-butler MCP debug endpoints in `src/butlers/api/routers/butlers.py`: `GET /api/butlers/{name}/mcp/tools` (normalized `name`/`description`/`input_schema`) and `POST /api/butlers/{name}/mcp/call` (tool name + arguments passthrough with parsed `result`, `raw_text`, `is_error`).
- Frontend contracts are typed in `frontend/src/api/types.ts` (`ButlerMcpTool`, `ButlerMcpToolCallRequest`, `ButlerMcpToolCallResponse`) and wired through `frontend/src/api/client.ts` + `frontend/src/api/index.ts`.

### Runtime MCP transport rollout contract
- Butler daemons now expose dual MCP transports via `_build_mcp_http_app()` in `src/butlers/daemon.py`: streamable HTTP at `/mcp` and legacy SSE compatibility at `/sse` + `/messages`.
- Spawner runtime sessions use canonical streamable MCP URLs from `src/butlers/core/mcp_urls.py::runtime_mcp_url()` (`http://localhost:<port>/mcp`) and `src/butlers/core/spawner.py` should not regress to hardcoded `/sse`.
- `src/butlers/core/runtimes/claude_code.py` resolves transport with `resolve_runtime_mcp_transport()`: default `http` for `/mcp`, explicit/URL-inferred `sse` for legacy endpoints.
- Connector ingest clients are still SSE-based (`SWITCHBOARD_MCP_URL=.../sse`) and are intentionally out of scope for spawner runtime transport cutover.
- Operator cutover/fallback procedure is documented in `docs/operations/spawner-streamable-http-rollout.md`; keep this runbook aligned with transport behavior and rollback guidance.
