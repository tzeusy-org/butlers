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

### Manifesto-driven design
Each butler has a `MANIFESTO.md` that defines its public identity and value proposition. Features, tools, and UX decisions for a butler should be deeply aligned with its manifesto. The manifesto is the source of truth for *what this butler is for* — CLAUDE.md is *how it behaves*, butler.toml is *what it runs*. When proposing new features or evaluating scope, check the manifesto first.

### v1 MVP Status (2026-02-09)
All 122 beads closed. 449 tests passing on main. Full implementation complete.

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
- Root `conftest.py` has `SpawnerResult` and `MockSpawner` (visible to all test trees)
- `tests/conftest.py` re-exports from root for backward compat (`from tests.conftest import ...`)
- CLI tests use Click's `CliRunner`
- Telemetry tests use `InMemorySpanExporter`
- Root `conftest.py` patches `testcontainers` teardown (`DockerContainer.stop`) with bounded retries for known transient Docker API teardown races (notably "did not receive an exit event") under `pytest-xdist`; non-transient errors must still raise.

### Memory System Architecture
Memory is a **common module** (`[modules.memory]`) enabled per butler, not a dedicated shared role/service. Memory tables (`episodes`, `facts`, `rules`, plus provenance/audit tables) live in each hosting butler's DB and memory tools are registered on that butler's MCP server. Uses pgvector + local MiniLM-L6 embeddings (384d). Dashboard remains available at `/memory` (aggregated via API fanout) and `/butlers/:name/memory` (scoped).

### Memory OpenSpec alignment contract
- `openspec/changes/memory-system/specs/*` now aligns to target-state module semantics: per-butler memory module integration, tenant-bounded operations by default, canonical fact soft-delete state `retracted` (legacy `forgotten` alias only), required `memory_events` audit stream, deterministic tokenizer-based `memory_context` budgeting/tie-breakers, consolidation terminal states (`consolidated|failed|dead_letter`) with retry metadata, and explicit `anti_pattern` rule maturity.

### Migration naming/path convention
Alembic revisions are chain-prefixed (`core_*`, `mem_*`, `sw_*`) rather than bare numeric IDs. Butler-specific migrations resolve from `roster/<butler>/migrations/` via `butlers.migrations._resolve_chain_dir()` (not legacy `butlers/<name>/migrations/` paths).
- Within a chain, set `branch_labels` only on the branch root revision (e.g. `rel_001`); repeating the same label on later revisions causes Alembic duplicate-branch errors.
- Do not leave stray migration files in chain directories: even if chain tests only assert expected filenames, Alembic will still load every `*.py` in the versions path and fail on duplicate `revision` IDs.

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

### Notifications DB fallback contract
- `src/butlers/api/routers/notifications.py` should degrade gracefully when the switchboard DB pool is unavailable: `GET /api/notifications` and `GET /api/butlers/{name}/notifications` return empty paginated payloads, and `GET /api/notifications/stats` returns zeroed stats instead of propagating a `KeyError`/404.

### Memory Writing Tool Contract
- `roster/memory/storage.py` write APIs return UUIDs (`store_episode`, `store_fact`, `store_rule`); MCP wrappers in `roster/memory/tools/writing.py` are responsible for shaping tool responses (`id`, `expires_at`, `superseded_id`) and must pass `embedding_engine` in the current positional order.

### Memory embedding progress-bar contract
- `roster/memory/embedding.py` must call `SentenceTransformer.encode(..., show_progress_bar=False)` for both single and batch embedding paths; otherwise `sentence-transformers` enables `tqdm` "Batches" output at INFO/DEBUG log levels, causing noisy interleaved logs.

### DB SSL config contract
- `src/butlers/db.py` now parses `sslmode` from `DATABASE_URL` and `POSTGRES_SSLMODE`; parsed mode is forwarded to both `asyncpg.connect()` (provisioning) and `asyncpg.create_pool()` (runtime).
- Dashboard DB setup in `src/butlers/api/deps.py` and `src/butlers/api/db.py` reuses the same env parser and forwards the same SSL mode to API pools, keeping daemon/API behavior aligned.
- When SSL mode is unset (`None`), DB connect/pool creation retries once with `ssl="disable"` if asyncpg fails during STARTTLS negotiation with `ConnectionError: unexpected connection_lost() call` (covers servers/proxies that drop SSLRequest instead of replying `S/N`).

### Telegram DB contract
- Module lifecycle receives the `Database` wrapper (not a raw pool). Telegram message-inbox logging should acquire connections via `db.pool.acquire()`, with optional backward compatibility for pool-like objects.

### HTTP client logging contract
- CLI logging config (`src/butlers/cli.py::_configure_logging`) sets `httpx` and `httpcore` logger levels to `WARNING` to prevent request-URL token leakage (notably Telegram bot tokens in `/bot<token>/...` paths).

### Telegram reaction lifecycle contract
- `TelegramModule.process_update()` now sends lifecycle reactions for inbound message processing: starts with `:eye`, ends with `:done` when all routed targets ack, and ends with `:space invader` on any routed-target failure.
- `RoutingResult` includes `routed_targets`, `acked_targets`, and `failed_targets`; decomposition callers should populate these so Telegram can hold `:eye` until aggregate completion.
- Per-message reaction state must not grow unbounded: terminal messages should prune `_processing_lifecycle`/`_reaction_locks`, and duplicate-update idempotence should be preserved via the bounded `_terminal_reactions` cache (`TERMINAL_REACTION_CACHE_SIZE`).
- `src/butlers/modules/telegram.py::_update_reaction` treats `httpx.HTTPStatusError` 400 responses from `setMessageReaction` as expected/non-fatal when Telegram indicates reaction unsupported/unavailable; for terminal failure (`:space invader` internal alias -> 👾) it should warn-and-skip rather than emit stack traces.

### Frontend test harness
- Frontend route/component tests run with Vitest (`frontend/package.json` has `npm test` -> `vitest run`).
- Colocate tests as `frontend/src/**/*.test.tsx` (example: `frontend/src/pages/ButlersPage.test.tsx`).

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
- `roster/general/butler.toml`, `roster/health/butler.toml`, and `roster/relationship/butler.toml` must each declare `[modules.calendar]` with provider `google`, explicit dedicated subcalendar `calendar_id` values (not `primary`), and default conflict policy `suggest`.
- `roster/general/CLAUDE.md`, `roster/health/CLAUDE.md`, and `roster/relationship/CLAUDE.md` must document calendar tool usage, dedicated subcalendar assumption, default conflict behavior (`suggest`), and that attendee invites are out of v1 scope.

### Calendar conflict preflight contract
- Calendar conflict policy is `suggest|fail|allow_overlap` at tool/config boundaries; legacy config values (`allow`, `reject`) normalize to `allow_overlap`, `fail`.
- `calendar_create_event` always runs conflict preflight; `calendar_update_event` runs conflict preflight only when the start/end window changes.
- Conflict outcomes return machine-readable `conflicts` and `suggested_slots` (`suggest` policy), while `allow_overlap` currently writes through and includes conflicts in the success payload.

### Beads coordinator handoff guardrail
- Some worker runs can finish with branch pushed but bead still `in_progress` (no PR/bead transition). Coordinator should detect `agent/<id>` ahead of `main` with no PR and normalize by creating a PR and marking the bead `blocked` with `pr-review` + `external_ref`.

### Beads push guardrail
- Repo push checks enforce a clean beads state; `git push` can fail with "Uncommitted changes detected" even after commits if `.beads/issues.jsonl` was re-synced/staged during pre-push checks.
- If this happens, run `bd sync --status`, inspect staged `.beads/issues.jsonl`, commit the sync normalization (or intentionally restore it), then re-run `git push`.

### Beads PR-review `external_ref` uniqueness contract
- Beads enforces global uniqueness for `issues.external_ref`; a dedicated `pr-review-task` bead cannot reuse the same `gh-pr:<number>` already attached to the original implementation bead.
- For split original/review-bead workflows, keep `external_ref` on the original bead and store PR metadata (`PR URL`, `PR NUMBER`, original bead id) in review-bead notes/labels, then dispatch reviewer workers with explicit PR context.

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

### Switchboard MCP routing contract
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` calls butler endpoints via `fastmcp.Client` and should return `CallToolResult.data` when present.
- If a target returns `Unknown tool` for an identity-prefixed routing tool name, routing retries `trigger` with mapped args (`prompt` from `prompt`/`message`, optional `context`).

### Base notify and module-tool naming contract
- `docs/roles/base_butler.md` defines `notify` as a versioned envelope surface (`notify.v1` request, `notify_response.v1` response) with required `origin_butler`; reply intents require request-context targeting fields.
- Messenger delivery transport is route-wrapped: Switchboard dispatches `route.v1` to Messenger `route.execute` with `notify.v1` in `input.context.notify_request`; Messenger returns `route_response.v1` and should place normalized delivery output in `result.notify_response`.
- `notify_response.v1` uses the same canonical execution error classes as route executors (`validation_error`, `target_unavailable`, `timeout`, `overload_rejected`, `internal_error`); local admission overflow maps to `overload_rejected`.
- `docs/roles/base_butler.md` does not define channel-facing tool naming/ownership as a base requirement; that policy is role-specific.
- `docs/roles/switchboard_butler.md` owns the channel-facing tool surface policy: outbound delivery send/reply tools are messenger-only, ingress connectors remain Switchboard-owned, and non-messenger butlers must use `notify.v1`.
- `docs/roles/switchboard_butler.md` explicitly overrides base `notify` semantics so Switchboard is the notify control-plane termination point (not a self-routed notify caller).

### Pipeline identity-routing contract
- `src/butlers/modules/pipeline.py` should route inbound channel messages with identity-prefixed tool names (default `bot_switchboard_handle_message`) and include `source_metadata` (`channel`, `identity`, `tool_name`, optional `source_id`) in routed args.
- `roster/switchboard/tools/routing/dispatch.py::dispatch_decomposed` should pass through identity-aware source metadata and the prefixed logical `tool_name` for each sub-route.
- `roster/switchboard/tools/routing/route.py::_call_butler_tool` should retry `trigger` for unknown identity-prefixed tool names, preserving source metadata via trigger context.

### Spawner trigger-source/failure contract
- Core daemon `trigger` MCP tool should dispatch with `trigger_source="trigger"` (not `trigger_tool`) to stay aligned with `core.sessions` validation.
- `src/butlers/core/spawner.py::_run` should initialize duration timing before `session_create()` so early failures preserve original errors instead of masking with timer variable errors.
- `src/butlers/core/spawner.py::trigger` should fail fast when `trigger_source=="trigger"` and the per-butler lock is already held, preventing runtime self-invocation deadlocks (`trigger` tool calling back into the same spawner while a session is active).

### Spawner system prompt composition contract
- `src/butlers/core/spawner.py::_compose_system_prompt` is the canonical composition path: runtime receives raw `CLAUDE.md` system prompt when memory context is unavailable, and appends memory context as a double-newline suffix when available.
- `tests/core/test_core_spawner.py::TestFullFlow` should patch `fetch_memory_context` for deterministic assertions so local memory module/tool availability cannot change expected `system_prompt` text.

### Sessions summary contract
- `src/butlers/daemon.py` core MCP registration should include `sessions_summary`; dashboard cost fan-out relies on declared tool metadata and will log `"Tool 'sessions_summary' not listed"` warnings if not advertised.
- `src/butlers/core/sessions.py::sessions_summary` response payload should include `period`, and unsupported periods must raise `ValueError` with an `"Invalid period ..."` message.

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

### Core tool registration contract
- `src/butlers/daemon.py` exports `CORE_TOOL_NAMES` as the canonical core-tool set (including `notify`); registration tests should assert against this set to prevent drift between `_register_core_tools()` behavior and expected tool coverage.

### Approvals product-contract docs alignment
- `docs/modules/approval.md` is now a product-level contract (not just current behavior) and includes explicit guardrails for single-human approver model, idempotent decision/execution semantics, immutable approval-event auditing, data redaction/retention, risk-tier policy precedence, and friction-minimizing operator UX.
- Frontend docs now explicitly track approvals as target-state single-pane integration: planned IA routes in `docs/frontend/information-architecture.md`, current gap in `docs/frontend/feature-inventory.md`, target data-access guidance in `docs/frontend/data-access-and-refresh.md`, and target API endpoints in `docs/frontend/backend-api-contract.md`.
