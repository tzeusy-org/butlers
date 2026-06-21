# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
```

Mutations auto-commit to the shared Dolt server (`127.0.0.1:3307`, db `butlers`);
there is no `bd sync` and no SQLite. See "Beads DB Mode" below.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run right-sized quality gates** (if code changed) - Targeted tests during active development; full suite only for final merge-readiness checks
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
   (Bead mutations already auto-committed to Dolt — no `bd sync` step exists.)
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

### Beads DB Mode (Dolt server)

This repo uses `bd` v1.0.x backed by the **shared Dolt server** on
`127.0.0.1:3307` (database `butlers`), discovered via `.beads/metadata.json`
(`dolt_mode: server`). There is no SQLite database and no `beads-sync` branch.

**Data flow:**
```
bd create/update/close  →  write directly to Dolt; each write auto-commits to Dolt history
bd export -o .beads/issues.export.jsonl  →  refresh the git-tracked JSONL mirror (optional)
```

- **No `bd sync`.** That subcommand does not exist in this bd version. Dolt is the
  source of truth and persists every mutation immediately — you do not need a flush
  or sync step to make bead changes durable.
- **Never create `.beads/issues.jsonl`.** On bd 1.0.4 server mode its presence
  triggers a full-file re-import on every write that can wedge bd town-wide. The
  git-tracked mirror lives at `.beads/issues.export.jsonl` (`export.path` in
  `.beads/config.yaml`); `bd export` keeps it fresh.
- **Repair:** if bd misbehaves (e.g. `database "..." not found`), run
  `bd doctor --fix --yes` from the repo root. For Dolt server health/cleanup use
  `gt dolt status` / `gt dolt cleanup`.

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
```

### Workflow Pattern

1. **Start**: Run `bd ready` to find actionable work
2. **Claim**: Use `bd update <id> --status=in_progress`
3. **Work**: Implement the task
4. **Complete**: Use `bd close <id>`

Mutations land in Dolt immediately — no export/sync step is required for durability.

### Worktrees

- All worktrees talk to the same Dolt server, so a bead created in one worktree is
  visible from any other immediately — no hydration/import step is needed.

### Worktree node_modules

When working on frontend changes in a worktree created by `bd worktree create`, the frontend build toolchain (TypeScript, vitest) fails without `node_modules`. Rather than copy the entire cache (wasting disk space), the setup script symlinks `frontend/node_modules` from the main repo's copy, keeping your worktree in sync automatically when `package.json` changes.

Run this after creating a worktree:
```bash
./scripts/setup_worktree.sh
```

The script symlinks `frontend/node_modules` and silently skips if the main repo hasn't run `npm install` yet. It is safe to run multiple times (idempotent).

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
git commit -m "..."     # Commit code
git push                # Push to remote (bead mutations already in Dolt)
```

### Best Practices

- Check `bd ready` at session start to find available work
- Update status as you work (in_progress → closed)
- Create new issues with `bd create` when you discover tasks
- Use descriptive titles and set appropriate priority/type

<!-- end-bv-agent-instructions -->

---

## Notes to self

### Contributor workflow: PRs only, never push directly to main
All code changes must go through a pull request — never `git push origin main` or
`git push --force origin main`. GitHub branch protection (Settings → Branches →
"Require a pull request before merging") is the authoritative prevention mechanism
and should be kept enabled. Do branch work in a dedicated worktree; keep the repo
root on `main` (see CLAUDE.md "Repo Root Discipline"). See bu-ue37d.

### Core migration optional-schema guard contract
- Core-chain migrations must tolerate fresh/core-only databases where specialist schema tables are absent; cross-schema `ALTER/UPDATE/GRANT` statements should guard with `to_regclass(...)` / information_schema checks instead of assuming `education.*`, `general.*`, etc. always exist.

### Detail-page snapshot baseline (bu-sfeuw.1 / Gate-A)

Ten `*DetailPage.tsx` components are baseline-snapshotted as of 2026-05-10 (branch
`agent/bu-sfeuw.1`). Tests live in `frontend/src/pages/` alongside each page.

**Shell architecture:**

| Page | Shell | `<Page archetype="detail">` | Breadcrumbs owned by |
|------|-------|-----------------------------|----------------------|
| ButlerDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| ContactDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| ConnectorDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| EntityDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| EpisodeDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| FactDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| RuleDetailPage | `<DetailPage>` | yes (via DetailPage) | `<Page>` HeadingBlock |
| SessionDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |
| QaPatrolDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |
| QaInvestigationDetailPage | raw `<div>` | **no** | raw `<Breadcrumbs>` |

No page uses a Tier-2 hero (PulseStrip) unless the record has an associated entity
(ContactDetailPage conditionally renders PulseStrip when `entity_id` is set).

**Gate-A changes that invalidate snapshots:**

- Migrating Session / QaPatrol / QaInvestigation from raw `<div>` to `<DetailPage>`:
  invalidates single-H1 count (loading state goes from 0 to 0, loaded state remains 1),
  adds `max-w-5xl` constraint, and changes breadcrumb ownership — updates needed in all
  three `*DetailPage.test.tsx` files' "slot composition baseline" suites.
- Adding a PulseStrip to Session / QaPatrol / QaInvestigation: invalidates the
  "no Tier-2 hero" baseline assertions in those files.
- Changing the `<Page>` HeadingBlock to suppress the `<h1>` during loading: all
  "renders zero H1s in loading state" assertions across all 10 files would need updating.
- Renaming H1 titles (e.g. "Patrol Detail" → something else): invalidates the
  `H1 contains '...'` assertions in QaPatrolDetailPage.test.tsx and
  QaInvestigationDetailPage.test.tsx.

### Runtime timeout propagation contract
- `Spawner._run()` must forward the effective `session_timeout_s` into `runtime.invoke(timeout=...)`, not just wrap the call in outer `asyncio.wait_for(...)`; otherwise adapter-specific inner timeouts can drift from session records and produce misleading mixed timeout behavior (observed in QA self-healing Codex runs).

### OpenCode empty-response failover contract
- OpenCode exit 0 with no result text, no tool calls, no token usage, and empty stderr is a failed pre-tool-call runtime attempt; `OpenCodeAdapter` must raise a classifier-eligible `RuntimeError` so same-tier model failover can run instead of persisting a successful null session.

### Model catalog timeout authority contract
- `public.model_catalog.session_timeout_s` is the authoritative per-session runtime timeout for catalog-resolved runs; `resolve_model()` returns it, `Spawner` uses it for normal butler sessions, and `DiscretionDispatcher` uses it for discretion-tier direct adapter calls.
- Per-butler `runtime_config` is operational-only (`core_groups`, `max_concurrent`, `max_queued`); model/runtime selection, extra args, and per-session timeout must not be reintroduced there or the `/settings` model catalog stops being authoritative again.
- `[butler.runtime_seed]` is also operational-only after the cleanup pass: keep only concurrency/core-group/registration seed fields there, put runtime adapter type under top-level `[runtime]`, and keep model selection/args/timeouts in the model catalog.

### Finance overview N+1 query optimization pattern
- The subscription_audit function in `roster/finance/tools/overview.py` implements batched query optimization for fetching subscription charge dates: use single LEFT JOIN with GROUP BY instead of per-subscription queries. This pattern should be replicated for any overview/analytics tool that needs to correlate multiple parent entities with their most recent related transactions or events. The key is `COALESCE(MAX(CASE WHEN condition THEN field END), fallback)` to handle entities with no related rows.

### Compose base-image invalidation contract
- `scripts/compose.sh` rebuilds `butlers-base:latest` when the `butlers.base.dockerfile_sha` image label differs from the current `Dockerfile.base` SHA; app-image rebuilds alone are not enough to pick up base-layer tool additions like `gh`.

### Owner entity bootstrap conflict contract
- `_ensure_owner_entity` in `src/butlers/daemon.py` must first resolve an existing owner via `WHERE 'owner' = ANY(roles)` before attempting insert, and the insert must use `ON CONFLICT DO NOTHING` (no explicit conflict target) so the partial unique index `shared.ix_entities_owner_singleton` cannot raise `UniqueViolationError` during startup.

### Runtime args passthrough contract
- Runtime args are sourced solely from `public.model_catalog.extra_args` — there is no butler.toml fallback. The catalog's `extra_args` array is forwarded verbatim to the adapter as `runtime_args`.
- `Spawner` forwards non-empty runtime args into adapter invocation as `runtime_args`, and `CodexAdapter` appends them to `codex exec` before the `--` prompt delimiter (supports flags like `--config model_reasoning_effort="high"`).

### Runtime config cache invalidation contract
- `RuntimeConfigAccessor.invalidate_cache()` must set `_cache_time` to an always-expired value (for example `float("-inf")`), not `0.0`; `0.0` only invalidates once process uptime exceeds the TTL and can leak stale config on fresh processes/CI workers.

### Finance transaction dedup provenance contract
- In `roster/finance/tools/transactions.py`, composite same-day dedup should only run when there is additional provenance (`account_id` or `source_message_id`); source-less manual rows must remain insertable as distinct transactions to avoid false-positive collapse.

### Recovery workflow accounting contract
- Recovery/session timeout semantics are split: `session_timeout_s` bounds one spawned runtime session, while higher-level healing/QA workflows own any broader investigation deadline.
- Pre-launch gate rejects (cooldown, concurrency cap, circuit breaker, no-model) should be tracked as dispatch decisions, not written as failed `healing_attempts`, or breaker state and operator history become polluted.

### QA circuit breaker reset contract
- QA circuit-breaker state must use the same launched-attempt filter everywhere: count rows with `healing_session_id IS NOT NULL` plus the synthetic dashboard reset sentinel `status = 'manual_reset'`, while still excluding orphaned gate-rejection rows.
- The dashboard summary, `/api/qa/circuit-breaker`, `/api/qa/circuit-breaker/reset`, and `src/butlers/core/qa/dispatch.py::_is_circuit_breaker_tripped` must stay aligned or the UI can report a reset while dispatch still suppresses new QA investigations.

### QA recursion provenance drift
- The intended QA self-recursion barrier depends on `qa_findings.source_session_trigger_source`, but current ingress/discovery paths do not populate it end-to-end: `modules/qa.report_finding` omits `trigger_source`, `core/qa/sources/butler_reports.py` does not store it, and `core/qa/sources/session_records.py`/`log_scanner.py` do not extract it, even though `core/qa/dispatch.py` and `/api/qa/meta-review` already rely on it.

### QA doctrine doc drift
- `about/heart-and-soul/architecture.md` still describes QA as a future staffer, while `about/README.md`, `roster/qa/`, and the live daemon topology treat QA as a current third staffer; when reconciling doctrine, prefer roster + spec state over that stale paragraph until the pillar doc is corrected.

### PR merge from worktree contract
- In this repo's multi-worktree setup, `gh pr merge` from a non-main worktree can fail with `fatal: 'main' is already checked out at '/home/tze/GitHub/butlers'`; reviewer workers should merge via GitHub API (`PUT /repos/{owner}/{repo}/pulls/{number}/merge`) and delete the head ref separately when needed.

### Calendar projection linkage schema contract
- Core `scheduled_tasks` now includes calendar-linkage columns (`timezone`, `start_at`, `end_at`, `until_at`, `display_title`, `calendar_event_id`) with bounds checks and a partial unique index on `calendar_event_id`.
- Relationship `reminders` now includes projection fields (`timezone`, `until_at`, `updated_at`, `calendar_event_id`), and reminder writes/dismissals must preserve `updated_at` for deterministic projector upserts.

### Required-schema module startup gate
- `ButlerDaemon` now filters `load_all()` results via `_select_startup_modules`: if a module defines required `config_schema` fields and its `[modules.<name>]` section is absent, startup skips that module (info log) instead of config-failing on missing required fields.
- This keeps intentionally omitted modules (for example `contacts` on `messenger`/`switchboard`) out of migrations/startup/tool registration and prevents provider-required warning noise.

### FastMCP test introspection contract
- FastMCP in this repo/toolchain exposes async `get_tool(name)` and may not expose `get_tools()` or private `_tool_manager`; metadata/introspection tests should use public `get_tool` (or a `get_tools` fallback) instead of private manager internals.

### Scheduler job_args JSONB contract
- In scheduler code paths, `job_args` JSONB values can round-trip through asyncpg as JSON strings; writes should serialize dict payloads explicitly, and reads should normalize back to dicts before diffing, validation merges, list responses, or dispatch payload assembly.

### Manifesto-driven design
Each butler has a `MANIFESTO.md` that defines its public identity and value proposition. Features, tools, and UX decisions for a butler should be deeply aligned with its manifesto. The manifesto is the source of truth for *what this butler is for* — CLAUDE.md is *how it behaves*, butler.toml is *what it runs*. When proposing new features or evaluating scope, check the manifesto first.

### Calendar module config reminder
- Calendar configs run through `src/butlers/daemon.py::_validate_module_configs`, which loads the module's `config_schema` and rejects extra/missing fields; `CalendarConfig` in `src/butlers/modules/calendar.py:906-925` demands `provider` + `calendar_id`, so any butler must populate them before the module can enable.
- Calendar module today only persists sync metadata via `_state_get/_state_set` (aka the shared `state` table) and exposes Google-backed tools (list/get, create/update/delete, add/remove attendees, sync status/force) plus the background poller; there is no `calendar_events`/`scheduled_tasks` projection wiring or workspace tooling described in `docs/modules/calendar.md`.

### Calendar projection sync contract
- `CalendarModule._sync_calendar` now materializes unified projection rows: provider deltas upsert into `calendar_events` + `calendar_event_instances`, and internal scheduler/reminder sources refresh into the same tables with deterministic `origin_ref` linkage (`scheduled_tasks.id` / `reminders.id`).
- Projection checkpoints are persisted in `calendar_sync_cursors` (`provider_sync` for provider pulls, `projection` for internal sources), and each sync refresh records action status in `calendar_action_log`.
- `calendar_sync_status` and `calendar_force_sync` now include `projection_freshness` (`last_refreshed_at`, `staleness_ms`, per-source `sync_state=fresh|stale|failed`); projection writes hard-gate on strict `to_regclass(...) IS TRUE` checks so pre-migration DBs/tests safely no-op.

### Calendar workspace contract coverage
- Calendar workspace frontend tests should cover URL-backed view toggles (`view=user|butler`), butler-lane rendering/grouping, and both user/butler create-edit mutation payload shapes.
- `docs/frontend/backend-api-contract.md` calendar section must include mutation endpoints (`/api/calendar/workspace/user-events`, `/api/calendar/workspace/butler-events`), v1 recurrence scope limitation (`series` for provider recurring updates/deletes), and projection/request-id telemetry fields (`projection_freshness`, `request_id`).

### Contacts module sync contract
- The contacts module is expected to run its incremental sync as an internal poll loop, not as a standalone connector (see `docs/modules/contacts_draft.md` §8); the default cadence is an immediate incremental run on startup, recurring polling every 15 minutes, and a forced full refresh every 6 days before the sync token expires.
- Modules load inside `butlers up` via `ButlerDaemon.start()` (`src/butlers/daemon.py:852-931`), so the poller should live in-process; `scripts/dev.sh` already launches `uv run butlers up` and required connector panes, so no extra standalone contacts connector bootstrap is needed.
- Contacts rollout contract: enable `[modules.contacts]` with `provider = "google"` and `sync` defaults (`run_on_startup=true`, `interval_minutes=15`, `full_sync_interval_days=6`) in `roster/general/butler.toml`, `roster/health/butler.toml`, and `roster/relationship/butler.toml`; intentionally exclude `roster/switchboard/butler.toml` (routing plane) and `roster/messenger/butler.toml` (delivery plane).
- The concrete runtime contract lives in `src/butlers/modules/contacts/sync.py::ContactsSyncRuntime`; mode selection is state-driven (`full` when no cursor or stale >=6 days, otherwise `incremental`) and poller trigger surface is `trigger_immediate_sync()`.

### Relationship interaction-sync ACL contract
- `roster/relationship/jobs/relationship_jobs.py::run_interaction_sync_job` reads `switchboard.message_inbox` directly, so `scripts/init-db.sql` must grant `butler_relationship_rw` read-only access to schema `switchboard` plus `SELECT` on its tables (and matching default privileges for future migration-user-created tables).

### Relationship contacts sync trigger API contract
- `POST /api/relationship/contacts/sync` is the manual dashboard/API trigger for contacts sync and dispatches to the relationship butler MCP tool `contacts_sync_now` with args `{"provider":"google","mode":"incremental|full"}`.
- The `mode` query parameter is strict (`incremental` or `full` only), and credential-related MCP failures are surfaced as actionable `400` errors pointing operators to `/api/oauth/google/start` or `/api/oauth/google/credentials`.

### v1 MVP Status (2026-02-09)
All 122 beads closed. 449 tests passing on main. Full implementation complete.

### One-DB runtime topology contract (butlers-1003.5)
- `[butler.db]` is schema-aware: when `name = "butlers"`, `schema` is required (explicit target schema, no implicit fallback).
- `Database` / `DatabaseManager` apply schema-scoped `search_path` (`<schema>,shared,public`; shared pool uses `shared,public`) for one-db pool resolution.
- API startup (`init_db_manager`) treats one-db topology as canonical shared-credentials path (`db=butlers`, schema `public`).
- Daemon migration URL generation includes libpq `options=-csearch_path=...` when a schema is configured so Alembic runs in the intended schema context.

### dev.sh Gmail OAuth rerun contract
- In `dev.sh`, `_has_google_creds()` must check the same credential DB set as the OAuth gate (`_poll_db_for_refresh_token`), plus legacy/override DB names where applicable, so preflight and gate do not disagree.
- Build the Gmail pane startup command at Layer 3 launch time (after OAuth gate), not once during early preflight; otherwise reruns can keep showing the stale "waiting for OAuth" pane even when credentials already exist.
- For pane logs, prefer wrapping the launched command with stdout/stderr tee capture (`_wrap_cmd_for_log`) instead of raw `tmux pipe-pane`, so log files contain process output rather than interactive shell prompt/control-sequence noise.

### dev.sh OAuth shared-store contract
- `dev.sh` OAuth preflight (`_has_google_creds`) and Layer 2 gate (`_poll_db_for_refresh_token`) query `public.contact_info` for the owner contact's `google_oauth_refresh` row (one-db mode, schema `public` by default, overridable via `BUTLER_SHARED_DB_NAME`/`BUTLER_SHARED_DB_SCHEMA`).

### Dev debug access contract
- In repo-root dev workflows, use `docker logs` as the primary debugging surface for compose services rather than the local `logs/` folder, and build `psql` commands from `.env.dev`; `POSTGRES_DB` may be unset there, with compose/scripts defaulting the database name to `butlers`.

### butlers-dev path split contract
- The dev dashboard and API are served on different Tailscale path prefixes: `/butlers-dev/` is the Vite frontend, while live JSON APIs are under `/butlers-dev-api/api/...`; probing `/butlers-dev/api/...` returns the frontend HTML fallback, not backend JSON.

### Compose live-log inspection contract
- For the `butlers-dev` Docker compose stack, authoritative live run logs are inside the containers under `/app/logs/...` (for example `/app/logs/butlers/*.log` and `/app/logs/<run>/butlers/up/output.log`); the repo worktree's `logs/` tree can lag or reflect a different local run and should not be treated as the live source of truth.

### Compose UI restart-policy gap
- In `docker-compose.yml`, `dashboard-api` and `frontend-dev` do not set `restart: unless-stopped`, while `butlers-up` and the connectors do. After a host reboot or external stop, the UI/API pair can remain down with `ExitCode=137` even though switchboard/connectors recover, leaving Tailscale `/butlers-dev` and `/butlers-dev-api` mapped to dead localhost ports until `./scripts/compose.sh` (or targeted `docker compose up -d`) is rerun.

### QA/self-healing PR label contract
- QA and self-healing dispatch default to GitHub labels `self-healing` and `automated`; on `Tzeusy/butlers`, missing repo labels make otherwise successful investigations fail at PR creation with `gh_pr_create_failed: could not add label: '<label>' not found`, leaving `healing_attempts.status = 'failed'` even when the agent produced a valid commit.

### QA helper workspace contract
- QA investigation Codex runs now launch from `<worktree>/.tmp/qa-agent/` with a local `AGENTS.md` override that explicitly disables `bd`/generic session-close workflow instructions; the helper dir must keep symlinks to repo roots like `src/`, `tests/`, `roster/`, `frontend/`, `pyproject.toml`, and `uv.lock` so repo-relative `uv run pytest` / `ruff check src/ tests/` still work.

### QA synthetic validation contract
- `POST /api/qa/dev/synthetic-findings` is an operator-only dev/staging hook gated by `QA_ALLOW_SYNTHETIC_FINDINGS=true`; it persists a placeholder `qa_patrols` row (`status='suppressed'`) plus a queued `qa_findings` row (`dispatch_queued=TRUE`) so the next scheduled patrol validates the normal rehydrate/triage/dispatch path even when `force_patrol` is unavailable out-of-process.

### Google OAuth credential storage split
- App credentials (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_SCOPES`) live in `butler_secrets` via `CredentialStore`.
- Refresh token lives exclusively in `public.contact_info` on the owner contact (type `google_oauth_refresh`, `secured=true`). No butler_secrets fallback exists.
- No runtime env-var fallback; all credential resolution is DB-only.
- `dev.sh` gate and runtime code both read from `public.contact_info` so shell gating and runtime behavior cannot drift.
- `scripts/compose.sh`/`oauth-gate` only proves the refresh token row exists; a revoked/expired token still lets the stack start, then Google-backed connectors/modules log `invalid_grant`/`Token has been expired or revoked` until the account is reauthorized with forced consent.

### Google `public.google_accounts` is ONE shared row per account across all 4 connectors
- Drive/Calendar/Gmail/Health each run their own account-sync loop that filters `WHERE status='active'` AND a qualifying scope in `granted_scopes` (`google_drive.py:1767`, `google_calendar.py:1833`, `gmail.py:3285`). A single account row backs all of them, so narrowing scopes or flipping `status` in one connector's flow cascades and silently drops the others at their next ~15-min sync (offline in the ingestion console).
- Re-auth must request **incremental authorization** (`include_granted_scopes=true`, set on both authorize builders in `oauth.py`): the request stays minimal (esp. health, which needs a restricted-scope subset) while Google returns a token covering the union of all prior grants — the callback persists `token_data["scope"]` verbatim, so a non-incremental request *replaces* and narrows. The legacy `_widen_scopes()` only fired when both `scope_set` and `account_hint` were passed, which the Calendar/Drive/Gmail UI flows don't.
- A single connector's credential error must NOT flip the shared `status` to `revoked` unless it's a genuine `invalid_grant` — only `GoogleHealthTokenRevokedError` gates `_mark_account_revoked` now; transient/scope-local failures stay connector-local (per-account `auth_error` flag only).

### WhatsApp module bridge DSN contract
- The in-process `modules.whatsapp` bridge runs inside `butlers up` and receives the daemon `Database` object, not connector env vars; `_get_db_dsn` must build `WA_BRIDGE_DSN` from `Database` connection fields when no explicit DSN attr exists. The standalone `connector-whatsapp-user` still uses its separate env-based DSN helper.

### Chronicler day-close prose/provenance contract
- `chronicler_day_close` should keep raw `source_ref`/connector IDs internal: `chronicler_day_close_bundle` returns citations for cache provenance/staleness, but the Telegram prose should cite only human-readable sources by default.
- Day-close summaries should use the owner timezone from General settings; pass that IANA timezone into `chronicler_day_close_bundle` so the bundle includes local display timestamps and local day boundaries.

### compose.sh dev DB role-grant contract
- `scripts/compose.sh` can clear Tailscale/OAuth gates and still fail at `butlers-up` if the target DB user lacks runtime role membership/ACLs; the signature is repeated `InsufficientPrivilegeError: permission denied for schema public` plus per-butler failures like `permission denied for schema <butler>` or `permission denied to set role "butler_<name>_rw"`.
- Fix the target dev DB before retrying by ensuring the connecting user has the expected role grants from `scripts/init-db.sql` (or equivalent grants done as a sufficiently privileged Postgres role); otherwise the switchboard health endpoint never comes up and all connectors remain blocked behind `butlers-up`.
- On PostgreSQL 16+, `pg_has_role(user, role, 'MEMBER')` is not enough to guarantee `SET ROLE role` works; the `pg_auth_members` row also needs `set_option = true`. `scripts/init-db.sql` should re-grant runtime roles with explicit `WITH SET TRUE`/`WITH INHERIT TRUE` so reruns repair older memberships instead of skipping them.

### init-db privileged bootstrap contract
- `scripts/init-db.sql` is the single privileged bootstrap entrypoint: it must create managed schemas/runtime roles when missing, grant role membership to the migration user (default `butlers`), grant runtime DB/schema ACLs, and set `ALTER DEFAULT PRIVILEGES FOR ROLE <migration user>` so later Alembic runs by `butlers` do not require a post-migration privileged grant pass.
- The script intentionally grants broad DML defaults on `public` objects created by the migration user to all runtime roles (`butler_*_rw`, `butler_qa_rw`, `connector_writer`) as the operational tradeoff that keeps the bootstrap to one privileged step; rerun it only when the managed schema/role surface changes.

### Pytest xdist/testcontainers concurrency contract
- `conftest.py` now serializes `testcontainers` `DockerClient.run()` calls across xdist workers and caps `pytest_xdist_auto_num_workers()` to `3` by default (override with `PYTEST_XDIST_AUTO_WORKERS`) so CI commands that pass `-n auto` do not overwhelm Docker-backed Postgres fixtures and time out during container startup.

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
- `roster/health/tests/test_tools.py` currently reproduces the same asyncpg cross-loop failure on `main` under pytest-xdist (`uv run pytest roster/health/tests/test_tools.py::test_measurement_log_weight -n auto --maxfail=1`), so refinery scoped runs that select `roster/health/tests/` need baseline-failure triage rather than assuming an MR regression.
- Shared test doubles for spawner behavior live in `src/butlers/testing/shared_fixtures.py`; both root `conftest.py` and `tests/conftest.py` re-export them so importlib-loaded/scoped-runner contexts do not rely on importing the repository-root `conftest` as a normal module.
- CLI tests use Click's `CliRunner`
- Telemetry tests use `InMemorySpanExporter`
- Root `conftest.py` patches `testcontainers` teardown (`DockerContainer.stop`) with bounded retries for known transient Docker API teardown races (notably "did not receive an exit event") under `pytest-xdist`; non-transient errors must still raise.

### Memory System Architecture
Memory is a **common module** (`[modules.memory]`) enabled per butler, not a dedicated shared role/service. Memory tables (`episodes`, `facts`, `rules`, plus provenance/audit tables) live in each hosting butler's DB and memory tools are registered on that butler's MCP server. Uses pgvector + local MiniLM-L6 embeddings (384d). Dashboard remains available at `/memory` (aggregated via API fanout) and `/butlers/:name/memory` (scoped).

### Memory API fanout contract
- `src/butlers/api/routers/memory.py` must not require `db.pool("memory")`; `/api/memory/*` reads fan out across available butler DB pools and aggregate results.
- Pools without memory tables should be skipped gracefully so no-dedicated-memory deployments return zero/empty payloads (or 404 for ID lookups) instead of 503.
- Unscoped `/api/memory/reembed/pending` must aggregate over memory-capable schemas and skip non-memory schemas such as `chronicler`; `chronicler.episodes` is not a memory table and has no `embedding` column.

### General timezone settings contract
- Shared dashboard-level defaults live in `public.state` under key `settings.general`; `/api/settings/general` is the owner-facing surface, and `Spawner` injects the resulting general-settings block into every butler system prompt via the shared credential pool when available (falling back to local pool only when shared access is absent).
- The current shared payload is `{timezone, language, date_format, time_format, week_starts_on, currency}` with implicit defaults `UTC`, `en-US`, `YYYY-mm-dd`, `HH:MM`, `Monday`, `USD`; `measurement_system` is fixed response-only `metric` and is not persisted as a user-editable field.

### Fact provenance write contract
- `src/butlers/modules/memory/storage.py::store_fact` now auto-fills `source_butler` from runtime context and creates/reuses a canonical `episodes` row for the current runtime session when `source_episode_id` is omitted.
- Any direct fact writer that bypasses `store_fact` (for example bulk SQL insert paths or scheduled jobs without runtime context) must either pass `source_butler` explicitly or call `resolve_write_provenance(...)` before inserting, or the entity facts UI will show blank provenance/session columns.

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

### FastMCP API drift test failures (current baseline)
- `make test-qg` currently fails in this branch with tests assuming legacy FastMCP internals: `tests/modules/test_module_approvals.py::TestApproveAction::test_approve_tool_rejects_spoofed_actor_kwarg` accesses `mcp._tool_manager`, and `tests/daemon/test_daemon.py::TestNotifyTool::test_notify_tool_description_and_schema_contract` calls `runtime_mcp.get_tools()`.
- Current FastMCP surface in this env exposes `get_tool` but not `_tool_manager`/`get_tools`; update tests to use supported introspection APIs before treating these as product regressions.

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

### Calendar persistence gap
- `docs/modules/calendar.md` defines `calendar_sources`, `calendar_events`, `calendar_event_instances`, `calendar_sync_cursors`, and `calendar_action_log` as the target-state stores plus the scheduler/reminder linkage, but no migrations or tables for those models exist today (only the doc and scheduler/reminder specs reference them).
- `alembic/versions/core/core_001_target_state_baseline.py` still defines only the core tables (`state`, `scheduled_tasks`, `sessions`, `route_inbox`), so `scheduled_tasks` lacks the timezone/start/end/until/display_title/calendar_event_id columns the calendar projector expects.
- Relationship reminders (`roster/relationship/migrations/001_relationship_tables.py`) only record `cron`, `due_at`, and `dismissed`, so we still need timezone/next_trigger/until tracking for reminder projection.

### Calendar workspace audit
- Frontend now exposes a first-class `/butlers/calendar` route (`frontend/src/router.tsx`) and sidebar navigation entry (`frontend/src/components/layout/Sidebar.tsx`).
- `frontend/src/pages/CalendarWorkspacePage.tsx` provides the initial dual-view shell: query-persisted `view=user|butler` plus `range=month|week|day|list` + `anchor` controls, a primary calendar canvas, and a right-side source/lane panel.
- Calendar workspace reads are wired via `frontend/src/hooks/use-calendar-workspace.ts` to `GET /api/calendar/workspace` and `GET /api/calendar/workspace/meta`; TypeScript contracts live in `frontend/src/api/types.ts` and client bindings in `frontend/src/api/client.ts`.

### Parallel Test Command
- Default quality-gate pytest scope uses `pytest-xdist` (`-n auto`) via `make test-qg`.
- Serial fallback/debug path remains available via `make test-qg-serial`.
- `make test-qg-parallel` is an explicit alias to the same parallel default.

### Testing cadence policy
- For bugfixes/features under active development or investigation, default to targeted `pytest` runs to keep loops fast and context lean.
- Run full-suite tests when branch changes are finalized and you need a pre-merge readiness signal.

### Dashboard health endpoint alias contract
- `src/butlers/api/app.py` must expose both `GET /api/health` and `GET /health` with the same `{"status":"ok"}` payload so direct infra probes and `/api`-prefixed clients both work.

### Switchboard message_inbox test partition contract
- Switchboard integration fixtures that create partitioned `message_inbox` tables must provision partitions dynamically for `now()` (and typically the next month) via a helper, not hard-coded calendar months; otherwise conformance tests start failing once wall-clock time moves past the fixed partition range.

### Health meals API facts contract
- `GET /api/health/meals` in `roster/health/api/router.py` must query `facts` with `predicate = ANY(meal predicates)`, `scope = 'health'`, and `validity = 'active'`, applying `since/until` filters to `valid_at`.
- Response mapping remains backward compatible (`id`, `type`, `description`, `nutrition`, `eaten_at`, `notes`, `created_at`), where `nutrition` is derived from fact metadata (`estimated_calories` + `macros.{protein_g,carbs_g,fat_g}`) and is `null` when those fields are absent.

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

### Butler reminder until_at update contract
- `CalendarModule._update_reminder_event` should only write `reminders.until_at` when an explicit `until_at` value is provided; omitted `until_at` in calendar workspace edits must preserve the existing boundary instead of clearing it.

### Switchboard Classification Contract
- `classify_message()` returns decomposition entries (`list[{"butler","prompt"}]`), not a bare butler string. Callers must normalize both legacy string and list formats before routing.
- When `butler_registry` is empty, `classify_message()` auto-discovers butlers from `roster/` (see `roster/switchboard/tools/routing/classify.py`) before composing the "Available butlers" prompt.
- Classification uses `list_butlers(..., routable_only=True)` so stale/quarantined targets are excluded from planner prompt context by default.

### Switchboard Codex tool-call parsing contract
- `src/butlers/core/runtimes/codex.py` must normalize nested Codex MCP tool-call payloads (`item.type="mcp_tool_call"` with `call`/`tool_call` sub-objects) so `route_to_butler` name + arguments are preserved in `tool_calls`; otherwise switchboard can mis-detect "no route_to_butler tools" and incorrectly fall back to `general`.

### Switchboard no-tool fallback routing contract
- In `src/butlers/modules/pipeline.py`, when LLM output includes no recognized `route_to_butler` calls, fallback routing should first attempt unambiguous target inference from CC summary text patterns like `routed to <butler>` (restricted to currently available butlers) before defaulting to `general`.

### Switchboard correct_route inbox lookup contract
- `roster/switchboard/tools/routing/correct_route.py` should pass explicit timestamp bounds (`window_start`, `window_end`) as separate bind parameters when filtering `message_inbox.received_at`; avoid expressions like `$2 ± interval '1 day'` on untyped bind params because asyncpg can infer `interval` and trigger `UndefinedFunctionError` (`timestamptz >= interval`).

### Runtime tool-call capture contract
- `src/butlers/core/spawner.py` augments adapter-parsed `tool_calls` with daemon-observed MCP executions captured via `src/butlers/core/tool_call_capture.py`, keyed by `runtime_session_id`.
- Switchboard MCP URLs include `runtime_session_id=<session_uuid>` query params so daemon request middleware (`_McpRuntimeSessionGuard` in `src/butlers/daemon.py`) can bind incoming tool invocations to the active runtime session and capture ground-truth tool calls for fallback decisions.
- `_McpRuntimeSessionGuard` should proxy unknown attributes to the wrapped ASGI app (for example `.routes`) so middleware layering remains transparent to startup/tests that introspect the combined MCP app.
- Daemon-side wrappers (`_SpanWrappingMCP` and `_ToolCallLoggingMCP`) must capture tool-call outcomes (`success`/`error`/`module_disabled`) plus result/error payloads in `tool_call_capture` so session `tool_calls` preserve execution outcomes, not just invocation names/inputs.
- Spawner merge logic should preserve failed-then-retried attempts with identical inputs in chronological order; avoid signature-only dedupe that collapses retry history.

### route.execute trace continuity contract
- Non-messenger `route.execute` background processing (`route.process`) should continue the incoming distributed `trace_context` when present, so switchboard dispatch and target butler work appear under one trace in observability backends.
- Keep `request_id` attributes on both accept (`butler.tool.route.execute`) and process (`route.process`) spans, and retain the process-span `SpanLink` to the accept span for explicit async-boundary correlation.

### notify + memory_store_fact tool metadata contract
- `notify` tool metadata (description + parameter schema) should explicitly document required/optional fields, include a valid JSON example, constrain `channel`/`intent` enums, and describe `request_context` required keys (`request_id`, `source_channel`, `source_endpoint_identity`, `source_sender_identity`) plus `source_thread_identity` for telegram reply/react.
- `memory_store_fact` tool metadata should explicitly document required/optional fields, include a valid JSON example, keep `permanence` as enum (`permanent|stable|standard|volatile|ephemeral`), and state that `tags` must be a JSON array of strings (not a comma-separated string).

### Contacts-identity model contracts

#### Owner contact bootstrap drift
- Legacy `src/butlers/daemon.py::_ensure_owner_entity_and_contact` inserted `public.contacts(name='Owner')` with `ON CONFLICT DO NOTHING`; after `core_016` dropped `ix_contacts_owner_singleton`, repeated startup could create duplicate `Owner` contacts.
- Current startup contract is entity-only bootstrap (`_ensure_owner_entity`) with no automatic `public.contacts` insert/delete side effects.

#### Identity schema (shared)
- `public.contacts` and `public.contact_info` are the canonical identity store in PostgreSQL; all channel-to-person resolution goes through the `public` schema.
- `public.contacts.roles` (text[]) encodes contact relationship: `owner` marks the single human operator. A partial unique index (`ix_contacts_owner_singleton`) enforces owner singleton.
- `public.contact_info` links channel identifiers to contacts: `(type, value)` UNIQUE constraint guarantees at most one contact per channel identifier.
- `contact_info.secured = true` marks credential entries (e.g. `type='telegram_bot_token'`); secured entries are filtered from default read paths.

#### Owner bootstrap
- `_ensure_owner_contact(pool)` in `src/butlers/daemon.py` bootstraps the owner contact row on every daemon startup (idempotent via `ON CONFLICT DO NOTHING`).

#### Identity resolution (ingress / Switchboard)
- `resolve_contact_by_channel(pool, channel_type, channel_value)` in `src/butlers/identity.py` is the canonical reverse-lookup: maps `(type, value)` → `ResolvedContact` (contact_id, name, roles, entity_id).
- Unknown senders: `create_temp_contact(pool, channel_type, channel_value)` creates a temp contact with `metadata.needs_disambiguation = true`; owner is notified once per new unknown sender (idempotent via `butler_state` KV: `identity:unknown_notified:{type}:{value}`).
- `build_identity_preamble(resolved, channel)` formats the preamble injected before every routing prompt; `contact_id`, `entity_id`, and `sender_roles` are persisted to `routing_log`.
- Switchboard identity injection pipeline lives in `roster/switchboard/tools/identity/inject.py`.

#### notify() contact resolution
- `notify()` supports three-tier recipient resolution: (1) `contact_id` UUID → `public.contact_info WHERE contact_id=X AND type=channel` (primary preferred), (2) explicit `recipient` string (backwards-compatible), (3) owner contact's channel identifier (default/scheduled sends).
- `_resolve_contact_channel_identifier(contact_id, channel)` in `src/butlers/daemon.py` handles path (1).
- `resolve_owner_contact_info(pool, info_type)` in `src/butlers/credential_store.py` handles path (3); returns `None` if no matching `contact_info` entry exists (no `butler_secrets` fallback).
- `notify()` returns `{status: pending_missing_identifier, ...}` when `contact_id` is provided but no matching `contact_info` entry exists for the requested channel; owner is notified.
- Scheduled prompts that are not replying to ingress context should use `intent="send"` (not `intent="reply"`).

#### Approval gate: role-based gating
- The approval gate uses role-based target resolution, not tool-name prefix heuristics (`user_*`/`bot_*` prefixes were removed in the h9fs epic).
- Gate resolution order: (1) extract `(channel_type, channel_value)` or `contact_id` from tool args → (2) resolve to `ResolvedContact` → (3) auto-approve if `owner` role, check standing rules otherwise.
- Tool-name-based prefixes in `approval_rules.tool_name` were renamed to plain tool names via Alembic migration (h9fs.7).

### Switchboard registry liveness/compat contract
- `butler_registry` includes liveness + compatibility metadata: `eligibility_state`, `liveness_ttl_seconds`, quarantine fields, `route_contract_min/max`, and `capabilities`.
- `resolve_routing_target()` in `roster/switchboard/tools/registry/registry.py` is the canonical gate for route eligibility: it reconciles TTL staleness, enforces stale/quarantine policy overrides, and validates route contract/capability requirements.
- Eligibility transitions are audited in `butler_registry_eligibility_log`; stale transitions (`ttl_expired`) and recovery transitions (`health_restored`/`re_registered`) should remain traceable in tests.

### Switchboard telemetry/correlation contract
- `roster/switchboard/tools/routing/telemetry.py` is the canonical `butlers.switchboard.*` metrics surface with low-cardinality attribute normalization (`source`, `destination_butler`, `outcome`, `lifecycle_state`, `error_class`, `policy_tier`, `fanout_mode`, `model_family`, `prompt_version`, `schema_version` only).
- `MessagePipeline.process()` emits the root trace span `butlers.switchboard.message` and persists `request_id` alongside lifecycle payloads in `message_inbox.classification` / `message_inbox.routing_results` (`{"request_id": ..., "payload"/"results"/"error": ...}`) for log-trace-persistence reconstruction.

### Switchboard eligibility sweep schedule contract
- `roster/switchboard/butler.toml` schedules `eligibility-sweep` as a job-dispatch entry (`dispatch_mode = "job"`, `job_name = "eligibility_sweep"`) rather than a prompt-based schedule.

### Butler detail schedule serialization contract
- `GET /api/butlers/{name}` serializes `config.schedules` through `ScheduleEntry`; `ScheduleEntry.prompt` must remain nullable because `dispatch_mode="job"` schedules intentionally omit prompt text.

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

### Telegram history thread-key contract
- Connector/runtime request context may carry Telegram `source_thread_identity` as either `<chat_id>` or `<chat_id>:<message_id>` (reply-target form); realtime history loading in `src/butlers/modules/pipeline.py::_load_realtime_history` must normalize/group by numeric chat id for `source_channel="telegram"` so message-scoped identities do not collapse history to a single row.

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

### Command palette trigger contract
- Dashboard command palette opening is event-driven via `frontend/src/lib/command-palette.ts` (`OPEN_COMMAND_PALETTE_EVENT = "open-search"`).
- Global hotkeys (`frontend/src/hooks/use-keyboard-shortcuts.ts`) and the header search icon (`frontend/src/components/layout/PageHeader.tsx`, with `Cmd/Ctrl+K` hover hint) must dispatch the shared open event.
- `frontend/src/components/layout/CommandPalette.tsx` should listen for that shared event and focus its search input when opening.

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

### Beads backend contract (Dolt server)
- `bd` v1.0.x is backed by the shared Dolt server (`127.0.0.1:3307`, db `butlers`, `.beads/metadata.json` `dolt_mode: server`). No SQLite DB, no `.beads/beads.db`, no `beads-sync` branch, no `bd sync` subcommand.
- Mutations (`bd create/update/close`) write directly to Dolt and auto-commit to its history immediately — durable without any export/sync step.
- The git-tracked JSONL mirror is `.beads/issues.export.jsonl` (`export.path` in `.beads/config.yaml`); refresh it with `bd export -o .beads/issues.export.jsonl`. NEVER create `.beads/issues.jsonl` (it triggers a wedging full-reimport loop on writes — see the bd 1.0.4 note below).
- All worktrees share the one Dolt server, so a bead created anywhere is visible everywhere immediately — no hydration/import step needed.

### Beads PR-review strip guardrail
- Before a reviewer worker strips `.beads/` drift from a PR branch, persist any new coordinator-side bead mutations first; restoring `.beads/` from `origin/main` only affects the JSONL mirror (Dolt remains source of truth), but keep the mirror consistent to avoid confusing diffs.

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
- All `bd` writes go to the shared Dolt server regardless of which worktree you run them from, so bead state is consistent across worktrees without `--no-db` gymnastics.
- `bd worktree create` may append per-worktree paths to repo `.gitignore`; strip those incidental lines before committing to avoid unrelated drift on `main`.
- The `.beads/issues.export.jsonl` mirror is git-tracked; avoid carrying its churn from `agent/*` branches into `main` — Dolt is source of truth, so cherry-pick code-only and let `bd export` regenerate the mirror.

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
- If a target returns `Unknown tool` for a routing tool name, routing retries `trigger` with mapped args (`prompt` from `prompt`/`message`, optional `context`).

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
### Telegram inbox logging contract
- `TelegramModule.process_update()` should log inbound payloads via `db.pool.acquire()` when DB is available and pass the returned `message_inbox_id` into `pipeline.process(...)`.
- Keep Telegram `pipeline.process` tool args aligned with tests (`source`, `source_channel`, `source_identity`, `source_tool`, `chat_id`, `source_id`); additional metadata should not be forced into this call path without updating tests/contracts.

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

### Approvals risk-tier + precedence runtime contract
- `src/butlers/config.py::ApprovalConfig` now includes `default_risk_tier` plus per-tool `GatedToolConfig.risk_tier`; `parse_approval_config` validates both against `ApprovalRiskTier` (`low|medium|high|critical`).
- Standing rule matching precedence is deterministic in `src/butlers/modules/approvals/rules.py` (`constraint_specificity_desc`, `bounded_scope_desc`, `created_at_desc`, `rule_id_asc`); gate responses include `risk_tier` and `rule_precedence`.
- High-risk tiers (`high`, `critical`) enforce constrained standing rules in `src/butlers/modules/approvals/module.py`: at least one exact/pattern arg constraint and bounded scope (`expires_at` or `max_uses`); `create_rule_from_action` and approve+create-rule paths auto-bound high-risk rules with `max_uses=1`.

### Beads concurrent-state reconciliation guardrail
- In multi-worker coordinator runs, stale worker commits of the `.beads/issues.export.jsonl` mirror can look like they resurrect previously normalized bead state in diffs; trust Dolt (the source of truth) over the mirror, and regenerate it with `bd export` rather than reverting bead status from a stale JSONL.
- After each coordinator cycle, re-run a PR-state normalization pass (`blocked` + `pr-review` / `pr-review-task`) before dispatching more workers, rather than assuming prior status updates remained authoritative.

### Dev bootstrap connector env-file contract
- `dev.sh` connectors window runs three connector processes: Telegram bot, Telegram user-client, and Gmail.
- Each connector pane may source a local-only env file under `secrets/connectors/` (`telegram_bot`, `telegram_user_client`, `gmail`) using `set -a` so values only affect that pane process.
- Connector endpoint identity is auto-resolved at startup (telegram bot: `getMe`, telegram user: `get_me()`, gmail: `google_accounts.email`, discord: `/users/@me`). No manual identity env var needed. Cursor state is DB-backed (no file path env var needed).

### Dev script location + process-clear contract
- Canonical bootstrap implementation now lives at `scripts/dev.sh`; repository-root `dev.sh` is a compatibility shim that delegates to `scripts/dev.sh`.
- `scripts/clear-processes.sh` is the canonical pre-bootstrap cleanup helper: by default it targets listeners on `POSTGRES_PORT` (`54320`), `FRONTEND_PORT` (`41173`), and `DASHBOARD_PORT` (`41200`), with explicit override via `EXPECTED_PORTS`.

### Telemetry span concurrency guardrail
- `src/butlers/core/telemetry.py::tool_span` decorator usage is unsafe if per-invocation span/token state is stored on the decorator instance (`self._span`, `self._token`): concurrent calls to one decorated async handler can trigger OpenTelemetry `Failed to detach context` / `Token ... created in a different Context`.
- Repro pattern: concurrent `await asyncio.gather(...)` calls to a single `@tool_span(...)`-decorated function fail; per-call context-manager usage (`with tool_span(...)`) does not.
- Track holistic fix in `butlers-978`, including both decorator state isolation and concurrent-session `_active_session_context` parent-lineage hardening.

### Dev bootstrap tailscale+pipefail guardrail
- `dev.sh::_tailscale_serve_check` should prefer modern Tailscale CLI syntax (`tailscale serve --yes --bg --https=443 http://localhost:41200`) with legacy positional fallback (`https:443 ...`) for older CLI versions.
- `dev.sh` split routing defaults are `TAILSCALE_DASHBOARD_PATH_PREFIX=/butlers` (Vite frontend) and `TAILSCALE_API_PATH_PREFIX=/butlers-api` (dashboard API); non-root path routing uses `tailscale serve --set-path <prefix> ...`.
- Dashboard mapping should proxy to `http://localhost:${FRONTEND_PORT}${TAILSCALE_DASHBOARD_PATH_PREFIX}` (not bare frontend root) so prefix paths are preserved end-to-end and Vite `--base` assets avoid redirect loops under tailscale path routing.
- Frontend dev port is configurable via `FRONTEND_PORT` (default `41173`) and should be kept aligned with tailscale dashboard target and the Vite startup command (`--port ... --strictPort`).
- `docker/Dockerfile` is the dev-suite image target for `dev.sh`: include `tmux`, `postgresql-client`, Docker CLI + compose plugin, tailscale CLI, Node.js, and global runtime CLIs (`@openai/codex`, `@anthropic-ai/claude-code`, `opencode-ai`) so `dev.sh` can run in-container when host sockets are mounted.
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

### Audit log degraded-read contract
- `GET /api/audit-log` must treat `asyncpg.exceptions.UndefinedTableError` on `dashboard_audit_log` as an empty page (`data=[]`, `total=0`) rather than a 500, because the dashboard can come up against an unmigrated or offline switchboard schema.

### State API JSON-shape contract
- `src/butlers/api/models/state.py::StateEntry.value` and `StateSetRequest.value` are typed `Any` (widened from `dict[str, Any]` in PR #205); scalar/array/null JSON rows in `state.value` are now serialized correctly.
- Keep list/get state endpoint value-shape contracts aligned with the full JSON domain accepted by the underlying state storage.
- asyncpg decodes JSONB columns directly to native Python types; no secondary `json.loads` fallback is needed in the router.
- The asyncpg JSONB codec also encodes write parameters: pass dicts/lists directly to `$N::jsonb` placeholders. Wrapping with `json.dumps(...)` double-encodes and stores a JSONB string scalar instead of an object. With `metadata = COALESCE(metadata, '{}'::jsonb) || $N::jsonb`, this corruption is destructive — PostgreSQL coerces both operands to arrays and concatenates, leaving e.g. `metadata = [<orig_dict>, "<stringified-patch>"]` and breaking later reads (Pydantic `dict` validation fails). Audit any router/SQL site that combines `json.dumps(value)` with `$N::jsonb`.

### Connector credential resolution pattern (CredentialStore)
- Connectors are standalone processes and need their own short-lived asyncpg pool (min_size=1, max_size=2, command_timeout=5) gated on `DATABASE_URL` or `POSTGRES_HOST` being set.
- `TelegramBotConnectorConfig` and `TelegramUserClientConnectorConfig` are Python **dataclasses** (not Pydantic models); use `dataclasses.replace(config, field=value)` for partial updates — `model_copy()` is Pydantic-only.
- `GmailConnectorConfig` is a Pydantic `BaseModel` with `frozen=True`; use `config.model_copy(update={...})` for partial updates.
- Pydantic v2 auto-coerces `str` to `pathlib.Path` for `Path`-typed fields, but prefer explicit `Path(cursor_path_str)` at construction sites to satisfy static type checkers and remove `type: ignore` suppressions.
- `bd close` from any worktree persists directly to the shared Dolt server; verify with `bd show <id> --json` if in doubt (no `beads-sync` branch re-close step exists anymore).

### Secrets shared-target contract
- `src/butlers/api/routers/secrets.py` treats `/api/butlers/shared/secrets` as a reserved target that resolves via `DatabaseManager.credential_shared_pool()` (not `db.pool("shared")`), returning 503 with `"Shared credential database is not available"` when unset.
- `frontend/src/pages/SecretsPage.tsx` must include a first-class `shared` selector target (via `buildSecretsTargets`) so users can manage shared secrets directly, with per-butler entries representing local override stores.
- `frontend/src/hooks/use-secrets.ts::useSecrets` is responsible for effective-read fallback in the Secrets page: for non-`shared` targets it merges `listSecrets(<butler>)` with `listSecrets("shared")`, preserving local rows on key collisions and marking shared-only rows as `source="shared"` so UI status badges show inherited shared values instead of `Missing (null)`.
- `frontend/src/pages/SecretsPage.tsx` no longer includes a dedicated "Configure App Credentials" form card; Google app credentials are managed through generic secrets rows (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`) and the OAuth section focuses on status/connect/delete actions.
- `frontend/src/hooks/use-secrets-inventory.ts` must derive user provider slugs from the backend provider catalog plus aliases, not from the first underscore segment of `entity_info.type`; live rows include `home_assistant_token` -> `homeassistant` and `telegram_user_session` -> `telegram_bot`.
- `src/butlers/api/routers/oauth.py::_get_scopes()` uses the fixed `_DEFAULT_SCOPES` set for `/api/oauth/google/start`; `GOOGLE_OAUTH_SCOPES` is no longer a runtime override input.
- Fixed OAuth scopes now include People-related scopes in addition to Gmail/Calendar: `contacts`, `contacts.readonly`, `contacts.other.readonly`, and `directory.readonly`.
- Runtime authentication is handled by CLI-level OAuth tokens (device-code flow via the dashboard `/settings` page), not API keys. The spawner only includes `PATH` plus declared `[butler.env]` vars and module credential vars in the runtime environment.

### One-DB multi-schema migration planning contract
- `docs/operations/one-db-multi-schema-migration.md` is the authoritative plan for epic `butlers-1003`: target topology (cross-butler tables in `public` + per-butler schemas), role/ACL model, phased cutover + rollback, parity/isolation gates, and child-issue decomposition.
- `docs/architecture/system-architecture.md` remains current-state for deployed topology and includes a transition note linking to the migration plan.
- `docs/operations/one-db-data-migration-runbook.md` is the executable command/checklist reference for staging dry-runs, parity signoff, and rollback validation.
- `docs/operations/migration-rewrite-reset-runbook.md` is the step-by-step operator procedure for local/dev/staging destructive reset rehearsal, including safety prechecks and required SQL validation evidence.

### Telegram connector DB-first startup contract
- `run_telegram_bot_connector()` and `run_telegram_user_client_connector()` must not hard-fail on missing credential env vars when DB credentials are available; if `from_env()` fails only due missing creds and DB lookup succeeded, build config from required non-credential env vars plus DB-resolved secrets.
- Endpoint identity is auto-resolved at startup via API calls (not env vars). Only `SWITCHBOARD_MCP_URL` is required as a non-credential env var.
- Regression coverage lives in `tests/connectors/test_telegram_bot_connector.py::test_run_telegram_bot_connector_uses_db_token_when_env_missing` and `tests/connectors/test_telegram_user_client.py::test_run_telegram_user_client_connector_uses_db_credentials_when_env_missing`.

### OAuth/dev messaging DB-first contract
- User-facing OAuth guidance (dev bootstrap, startup guards, OAuth callback responses) should default to dashboard + shared `butler_secrets` persistence and avoid recommending `GMAIL_*`/manual env fallback as the normal path.
- `docs/runbooks/connector_operations.md` should not advertise removed `GMAIL_*` aliases; troubleshooting should direct operators to rerun OAuth/bootstrap so credentials persist in DB.

### Legacy-compat cleanup hotspots (dev runtime)
- Runtime source currently does not read `BUTLER_GOOGLE_CALENDAR_CREDENTIALS_JSON` or deprecated `GMAIL_*` credential aliases directly; these names mainly remain in `dev.sh`, docs, and tests.
- Active compatibility hotspots to evaluate first for removal: `dev.sh::_has_google_creds`, `src/butlers/modules/calendar.py::_resolve_credentials` fallback path, `src/butlers/google_credentials.py` legacy asyncpg/table helpers, `roster/switchboard/tools/notification/deliver.py` legacy positional-arg shim, and `src/butlers/api/routers/butlers.py` legacy module-status list parsing.

### Gmail connector shared-schema credential lookup contract
- `src/butlers/connectors/gmail.py::_resolve_gmail_credentials_from_db` must perform layered DB-first lookup across local (`CONNECTOR_BUTLER_DB_NAME` + optional `CONNECTOR_BUTLER_DB_SCHEMA`) and shared (`BUTLER_SHARED_DB_NAME` + `BUTLER_SHARED_DB_SCHEMA`, default `public`) contexts.
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
- Runtime adapter selection is read from top-level `[runtime].type` in each `roster/*/butler.toml` (defaults to `"claude"` when omitted).
- Runtime model selection is read from `[butler.runtime].model` (defaults to `src/butlers/config.py::DEFAULT_MODEL` when omitted).
- Codex runtime system instructions are loaded from per-butler `AGENTS.md` (via `src/butlers/core/runtimes/codex.py::parse_system_prompt_file`), not `CLAUDE.md`.
- `CodexAdapter.invoke()` must call `codex exec --json --full-auto` (non-interactive mode). Top-level `codex --full-auto` requires a TTY and should not be used by the spawner subprocess path.
- Codex CLI no longer supports `--instructions`; butler/system prompt content must be embedded into the `exec` initial prompt payload, and MCP endpoints should be passed via `-c mcp_servers.<name>.url="..."`.
- `CodexAdapter.invoke()` must insert a `--` option delimiter before the positional prompt argument so user prompts beginning with `-`/`--` are not parsed as Codex CLI flags.
- `CodexAdapter.invoke()` must forward configured model via CLI `--model <id>` when `model` is non-empty, so roster model pins (for example `gpt-5.3-codex-spark`) are actually enforced at launch time.
- Codex spawn failure now drives `last_test_ok` on the `cli-auth/codex` credential row. When the Codex CLI exits non-zero with a refresh-token-reuse error (`_looks_like_auth_refresh_failure`), `CodexAdapter._schedule_record_test_result` writes `last_test_ok=false` → `_derive_state` returns `'failing'` and the secrets passport banner turns red. A successful spawn always clears the field (`last_test_ok=true`) even without a token rotation, so the banner self-heals after re-auth.

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

### Butler runtime concurrency baseline
- All current roster butlers (`switchboard`, `general`, `relationship`, `health`, `messenger`) should explicitly set `[butler.runtime].max_concurrent_sessions = 3` in their `roster/*/butler.toml` to avoid unintended fallback to the serial default (`1`) for scheduled/tool-trigger workloads.

### CRM backfill pipeline (contacts module) patterns
- `src/butlers/modules/contacts/backfill.py` implements the apply_contact callback wired into `ContactsSyncEngine` at startup. Three-class design: `ContactBackfillResolver` (identity matching pipeline), `ContactBackfillWriter` (table mapping/upsert), `ContactBackfillEngine` (orchestrates resolver→writer→activity feed).
- Identity resolution order: source_link > email > phone > name (single match) > ambiguous_name (skip auto-merge).
- Conflict policy: provenance tracked in `contacts.metadata` JSONB under `sources.contacts.{provider}.{field}`. Source wins only if field is provenance-owned; locally-edited fields (no provenance) are preserved.
- `ON CONFLICT DO NOTHING` without a conflict target is valid PostgreSQL syntax. The production CRM tables (`contact_info`, `addresses`, `important_dates`) lack composite unique constraints — adding those would require separate schema migration beads.
- `upsert_source_link` accepts `local_id: uuid.UUID | None`; returns early without creating a link when `local_id is None` (tombstone with no known local contact).
- Activity feed event types: `contact_synced`, `contact_sync_updated`, `contact_sync_conflict`, `contact_sync_deleted_source`.
- Tests use `pytestmark = pytest.mark.integration` with `provisioned_postgres_pool` fixture and create all CRM tables inline in the `crm_pool` fixture.

### Contacts migration cross-schema FK contract
- `src/butlers/modules/contacts/migrations/001_contacts_sync_tables.py` must create `contacts_source_links.local_contact_id` without an inline FK and add `contacts_source_links_local_contact_id_fkey` only when `contacts` exists in the current schema (`to_regclass(format('%I.contacts', current_schema()))`).
- This guard keeps module migration `contacts_001` safe for schemas that enable contacts but do not own CRM `contacts` (for example `general` and `health`).
- `tests/config/test_schema_matrix_migrations.py` `CHAIN_TABLES` must include `contacts` module tables so one-db schema-matrix runs exercise contacts migrations across all enabled schemas.

### Calendar workspace projection baseline contract
- Core migration `core_005` adds app-native calendar projection tables in each migrated schema: `calendar_sources`, `calendar_events`, `calendar_event_instances`, `calendar_sync_cursors`, and `calendar_action_log`.
- Range-window queries are supported by GiST indexes on `tstzrange(starts_at, ends_at, '[)')` for both events and instances; source lookups use `(source_id, starts_at)` indexes.
- Deterministic source linkage/idempotency guarantees are enforced by `UNIQUE (source_id, origin_ref)` on `calendar_events`, `UNIQUE (event_id, origin_instance_ref)` on `calendar_event_instances`, and `UNIQUE (idempotency_key)` on `calendar_action_log`.
- Scheduler calendar-linkage migration is linearized as `core_006` (`down_revision="core_005"`), so `tests/config/test_migrations.py::CORE_HEAD_REVISION` should track `core_006`.
- `tests/config/test_schema_matrix_migrations.py::CORE_TABLES` must include the calendar projection tables.

### Calendar workspace API contract
- Dashboard API now exposes `/api/calendar/workspace` (range query), `/api/calendar/workspace/meta` (capabilities + connected sources + writable calendars + lane definitions), and `/api/calendar/workspace/sync` (global or source-targeted sync trigger).
- `POST /api/calendar/workspace/sync` delegates to each target butler MCP `calendar_force_sync`; source-targeted provider rows pass `{"calendar_id": <calendar_id>}`, while internal-source rows call with `{}`.
- Workspace read payload shape is `ApiResponse[CalendarWorkspaceReadResponse]` with `data.entries` (normalized `UnifiedCalendarEntry[]`), `data.source_freshness`, and `data.lanes`.

### Calendar workspace mutation contract
- Dashboard workspace mutation routes live in `src/butlers/api/routers/calendar_workspace.py`: `POST /api/calendar/workspace/user-events` (`create|update|delete`) and `POST /api/calendar/workspace/butler-events` (`create|update|delete|toggle`), with request envelope `{butler_name, action, request_id?, payload}`.
- Mutation routes proxy to MCP tools and must return projection freshness metadata (`projection_version`, `staleness_ms`, `projection_freshness`) by using tool-returned freshness or falling back to `calendar_sync_status`.
- Calendar module mutation idempotency uses `calendar_action_log.idempotency_key` keyed by action + `request_id`; repeat requests should replay stored applied/noop results instead of re-executing side effects.
- Butler-event MCP tools are `calendar_create_butler_event`, `calendar_update_butler_event`, `calendar_delete_butler_event`, and `calendar_toggle_butler_event`; high-impact delete/toggle operations integrate with approval enqueueing and set `_approval_bypass=True` on queued replays.

### Frontend dialog test contract
- Radix `Dialog` content renders through a portal (`document.body`), so jsdom tests for dialog controls should query `document` (not only the mounted container) and use the native input value setter + `input` event dispatch for controlled text inputs.

### Telegram connector rate-limit polling contract
- `src/butlers/connectors/telegram_bot.py::_get_updates` must treat Telegram `HTTP 429` as recoverable for polling: record `rate_limited` source API status/error metrics, log a warning, honor `Retry-After` (header first, then `result.parameters.retry_after`), and return `[]` instead of raising.

### Switchboard route_to_butler lineage fallback contract
- `route_to_butler` can run in a different MCP/ASGI task than `MessagePipeline.process()`, so `_routing_ctx_var` may be empty at tool-call time; switchboard now falls back to runtime-session-bound routing lineage.
- `Spawner._run()` captures pipeline routing context and stores it keyed by `runtime_session_id` for the lifetime of the runtime session; `route_to_butler` reads it via `get_current_runtime_session_routing_context()` and restores `source_channel`, `source_sender_identity`, and `source_thread_identity` when task-local context is missing.

### Tool input-shape metadata contract
- `memory_store_fact.tags` and `memory_search.types` metadata must explicitly describe list-only JSON input shapes (not plain strings), with concrete valid/invalid examples (`tags=["x"]`, `types=["fact"]`, invalid `types="facts"`).
- `memory_search.types` should be modeled as `list[Literal["episode","fact","rule"]] | None` and `memory_search.mode` as `Literal["hybrid","semantic","keyword"]` so MCP schemas expose enforceable enums.
- `notify.request_context` metadata must explicitly say it requires an object/dict value (not JSON strings or quoted placeholders), because placeholder examples in skills/docs can cause repeated runtime validation failures.

### Switchboard message-triage delegation contract
- `src/butlers/modules/pipeline.py::_build_routing_prompt` should keep the ingestion preamble minimal and explicitly instruct: `Please use the /message-triage skill ...`.
- Routing/safety behavior details (untrusted-input handling, `<user_message>` wrapping, fallback to `general`, and mandatory `route_to_butler` call) are maintained in `roster/switchboard/.agents/skills/message-triage/SKILL.md` under `Execution Contract`.

### Skill-first routed-content contract
- `src/butlers/tools/extraction.py::build_extraction_prompt` should stay minimal and delegate extraction behavior to `/signal-extraction`; schema/tool mapping details remain dynamic in the prompt payload under `Registered butler schemas`.
- Route-processing context assembly in `src/butlers/daemon.py` is centralized in `_build_route_runtime_context()` and should reference skills (`/routed-message-safety`, `/butler-notifications`) instead of duplicating long inline safety/notify preambles in both hot-path and recovery flows.
- Shared skill `roster/shared/skills/routed-message-safety/SKILL.md` must be symlinked into each `roster/*/.agents/skills/` so any routed target butler can follow the same fenced-content handling contract.

### Refinery patrol hook activation behavior
- In this rig state, `gt hook`/`gt mol status` can show a hooked `mol-refinery-patrol` bead while also reporting `No molecule attached`; `gt mol attach` rejects hooked wisps because it requires a pinned bead.
- `gt patrol new` creates and hooks a fresh patrol wisp, but the hook output may still report `No molecule attached`; use `gt mq list <rig>` as operational queue truth and continue processing merge-ready MRs.
- Current refinery patrol wisps are root-only molecules: `bd mol show <wisp-id>` reports `Steps: 1` with just the patrol root, and `bd mol current <wisp-id>` shows `0/0 steps complete`; do not block on missing child-step beads before running the patrol loop.
- If `gt prime`/`gt mail check --inject` hang in this rig, check `gt dolt status` first; when the Dolt server is down, `gt` can wedge in auto-start retries, and an explicit `gt dolt start` restores normal command responsiveness.

### Health owner entity resolution contract
- Post-`core_016`, owner-role resolution must not query `public.contacts.roles`; that column is gone. Health meal logging and other owner lookups should resolve the owner via `public.entities.roles` (or the shared owner-entity helper path) and degrade gracefully when no owner entity exists.

### Notifications API startup degradation contract
- `src/butlers/api/routers/notifications.py` must treat a missing switchboard `notifications` table the same as an unavailable switchboard pool: `GET /api/notifications` and `GET /api/butlers/{name}/notifications` return an empty paginated payload, and `GET /api/notifications/stats` returns zeroed stats instead of bubbling a 500 before switchboard migrations have run.

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs via Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
   (Bead mutations already auto-committed to Dolt — no `bd sync` step exists.)
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

<!-- END BEADS INTEGRATION -->

## Notes to self

### Egress audit operation naming convention
Dashboard egress catalog (`GET /api/system/egress`) is powered by `switchboard.dashboard_audit_log`. Four operation strings represent outbound external calls — each must be emitted at the actual call site using `write_audit_entry` (daemon) or `emit_dashboard_audit` (API layer):
- `llm_api_call` — emitted by `src/butlers/core/spawner.py` after every LLM session (success and error paths); `request_summary` carries `provider`, `model`, `session_id`, `input_tokens`, `output_tokens`.
- `telegram_send` — emitted by `TelegramModule._send_message()` in `src/butlers/modules/telegram.py`; `request_summary` carries `chat_id`, `text_length`.
- `google_calendar_write` — emitted by `CalendarModule._emit_calendar_audit()` (called from `calendar_create_event`, `calendar_update_event`, `calendar_delete_event` tools in `src/butlers/modules/calendar.py`); `request_summary` carries `action` (create/update/delete), `event_title`, `calendar_id`.
- `gmail_send` — emitted by `EmailModule._send_email()` in `src/butlers/modules/email.py`; `request_summary` carries `to`, `subject`.

Modules receive the audit pool via `Module.wire_audit_pool(pool)` — a post-startup hook called in `lifecycle.py` step 10a-ii after the audit pool is created. The base class default is a no-op; only modules that emit egress audit entries need to override it.

- `about/craft-and-care/` is the canonical fifth project-shape pillar for repository engineering standards; keep testing, verification, review, observability, interface/dependency, security, and performance guidance there instead of scattering new standards across ad hoc docs.
- Memory entity merge tombstones the source with `metadata.merged_into`, excludes it from entity list/search/`entity_resolve`, and re-links `public.contacts.entity_id` from source to target.
- Relationship entity active-surface queries should use the shared archived/tombstone/deleted filter: exclude `metadata.archived = true`, legacy `metadata.archived_at`, `metadata.tombstone = true`, `metadata.deleted_at`, and `metadata.merged_into`.
- Memory entity merge only unions the source entity's `aliases` onto the target; it does not automatically add the source `canonical_name` as a target alias, so old-name lookups only keep working if that string already exists in aliases or another resolver path still matches.
- Witness patrol wisps created by `gt patrol new` / `gt patrol report` are `hooked` (not `pinned`), so `gt mol attach <wisp> mol-witness-patrol` fails with "not pinned". Run patrol steps directly and roll cycles with `gt patrol report`.
- `gt patrol report --steps` for witness cycles expects the canonical patrol step keys (`inbox-check`, `process-cleanups`, `check-refinery`, `survey-workers`, `check-timer-gates`, `check-swarm-completion`, `patrol-cleanup`, `context-check`, `loop-or-exit`); custom labels are recorded as `SKIP`.
- `gt hook --json` may expose the current hooked wisp under `pinned_bead`; rely on each issue `status` field (`hooked` vs `pinned`) for truth.
- `gt polecat list` can temporarily show stale `working` state after Dolt disturbances; verify with `gt hook show <agent> --json` and `bd show <issue> --json` before taking cleanup action.
- `gt polecat stale` / `gt polecat check-recovery` can lag just-recovered sessions; before intervening, confirm live session truth with `gt polecat status <rig>/<name> --json`.
- Witness loop step command to resolve `role_type: witness` agent bead can return zero results in this rig; if no witness agent bead exists, skip `gt mol step await-signal` and continue patrol roll with `gt patrol report` while flagging the missing bead.
- For polecat hook inspection, use full agent paths like `gt hook show butlers/polecats/<name> --json`; shorthand `butlers/<name>` may incorrectly report `status":"empty"`.
- `gt patrol report` can rotate the hooked patrol wisp without updating `/home/tze/gt/butlers/witness/state.json`; treat the hooked patrol bead and polecat agent beads as the source of truth for current-cycle state.
- Finance transaction ingestion is split: `POST /api/finance/transactions/bulk` writes facts directly via `roster/finance/tools/facts.py::bulk_record_transactions`, while the finance module/MCP `bulk_record_transactions` routes through `roster/finance/tools/transactions.py::record_transaction` and then mirrors to facts. Their dedupe semantics differ; the direct facts bulk path still dedupes `source_message_id` per predicate and hashes signed amounts, so opposite-sign imports of the same event can persist as both `transaction_debit` and `transaction_credit`.
- Finance transaction retries can leave `finance.transactions` soft-deleted duplicates while their mirrored `finance.facts` rows remain `validity='active'`; cleanup/reconciliation should retract active transaction facts that exactly match a deleted ledger row on `merchant`, `amount`, `currency`, `posted_at`, and `direction`.
- `GET /api/memory/entities/{entity_id}` now accepts `facts_offset` / `facts_limit`; the response includes `recent_facts_total`, `recent_facts_offset`, `recent_facts_limit`, `recent_facts_has_more`, and each fact row may carry `session_id` resolved via its `source_episode_id`.
- `frontend/src/components/settings/QASettingsCard.tsx` must avoid mirroring `useQaRepoConfig()` data into local state via `useEffect`; the current frontend lint config treats `react-hooks/set-state-in-effect` as a hard error, so the repo URL field should stay query-backed until a local draft exists.
- In the current frontend toolchain, Recharts `Tooltip` formatter callbacks should accept `value: string | number | undefined`; narrower signatures can fail `npm run build` under TypeScript even when the plotted data is numeric.
- `src/butlers/core/runtimes/codex.py` should stage isolated per-invocation `HOME` roots under `~/.codex/.tmp` when a real home directory exists; current `codex-cli` warns and can fail when `codex_home` is placed under `/tmp`.
- `src/butlers/core/qa/dispatch.py::_create_qa_pr` also depends on the GitHub CLI after a successful agent session; `Dockerfile.base` must continue to ship `gh` alongside `git` and `uv` or QA investigations can finish cleanly but fail before raising a PR with `FileNotFoundError: 'gh'`.
- This repo's local beads database currently has no Dolt remote named `origin`; `bd dolt push` fails with `remote 'origin' not found`, but local `bd create/update/dep add` writes still persist in `.beads/` via Dolt.
- The `butlers` Dolt database lives on the shared GT/Gastown Dolt server exposed locally at `127.0.0.1:3307`. The literal hostname `gastown` may not resolve from agent shells; use the local forwarded endpoint in `.beads/config.yaml`/metadata (`dolt.host: 127.0.0.1`, `dolt.port: 3307`) and verify with `bd dolt show`. If `bd` reports `database "butlers" not found`, it is pointed at an auto-started local Dolt; fix by killing the PID in `.beads/dolt-server.pid` and writing `3307` to `.beads/dolt-server.port`. Do NOT run `bd doctor --fix --yes` for this — it overwrites the port file with a fresh local server and starts from an empty DB.
- `src/butlers/modules/qa/__init__.py::_handle_report_finding` currently trusts caller-supplied `fingerprint` and `severity`; QA dedup and dispatch autonomy depend on canonicalizing or validating those fields at the QA boundary rather than treating report payloads as authoritative.
- `src/butlers/core/qa/sources/log_scanner.py` currently spends `max_entries_per_scan` budget before `_should_include_entry(...)` filtering and scans oldest-first across deterministically ordered files, so noisy benign logs can starve real error discovery unless the scanner budget/traversal logic is hardened.
- QA investigation PR pushes cannot rely on an SSH `origin` inside the sandbox: use `GH_TOKEN` with `gh auth setup-git` and push over `https://github.com/<owner>/<repo>.git` so PR creation/follow-up works without SSH agent state.
- QA PR review follow-up retry timing is tracked separately from review polling: `healing_attempts.last_follow_up_at` plus `follow_up_count` implement exponential backoff; `last_review_check_at` remains the review-scan timestamp only.
- Shared worktree creation now supports an explicit `base_ref`; QA investigations should fetch `origin/main` first and branch from `origin/main` rather than assuming local `main` is current.
- Live finance schemas at `finance_006` use `merchant_mappings.raw_pattern`, `normalized_merchant`, `learned_from_count`, and `source`; finance runtime code must not query or upsert legacy `merchant`, `merchant_pattern`, or `sample_count` columns or uncategorized transaction ingestion will fail with `UndefinedColumnError`.
- `src/butlers/connectors/spotify.py` must prefer resolved human-readable `context_name` values for playlist/album/artist contexts in both `normalized_text` and `payload.raw.context_name`; raw URI suffixes are fallback-only because downstream entity extraction will otherwise store Spotify IDs as canonical names.
- `tests/config/test_migrations.py` should derive the current core Alembic head from `alembic/versions/core/` instead of pinning a `CORE_HEAD_REVISION` constant; new core migrations land often enough that static head expectations rot immediately.
- Fresh `bd worktree create` checkouts ship without `node_modules`; frontend workers must run `cd frontend && npm install` before `tsc`/`eslint`/`vitest` or commands fail with module-resolution errors.
- `npx tsc --noEmit -p .` does not traverse project references — use `npx tsc -b` (matches `npm run build` in CI) to surface TS errors in test files; many errors are invisible to the `-p` form and only fail in CI.
- `gh pr merge --squash --auto` succeeds on this repo when only the backend `check` job passes; the `frontend` job is non-blocking under branch protection. Useful when frontend CI is red for unrelated pre-existing reasons.
- Frontend test files mocking `localStorage.getItem` should type the backing store as `Record<string, string | null>` (not `Record<string, string>`); otherwise `vi.fn` infers the mock signature too narrowly and `mockImplementation((k) => k === "..." ? "x" : null)` fails `tsc -b`.
- Partial TanStack Query mocks in test files must use `as unknown as ReturnType<typeof useFoo>` (existing convention in `ConnectorDetailPage.test.tsx`, `SecretsTable.test.tsx`); `UseQueryResult<T, Error>` is a discriminated union that requires fields like `isPending`, `isLoadingError` that mocks omit, so a direct cast fails.
- `.claude/skills/butler-qa-pr-review/scripts/_github.py::fetch_required_checks` currently treats `gh pr checks --required` exit code 1 as fatal when a PR has no branch-protection required checks configured, so `validate_pr_review.py` can crash even though `statusCheckRollup` still exposes actual CI results.
- `src/butlers/connectors/whatsapp_user_client.py::_get_bridge_db_dsn()` currently hands the Go bridge a bare `butlers` DSN with no schema search_path; in live dev that means the real linked-device state restores from `public.whatsmeow_*` tables while `messenger.whatsapp_sessions` stays empty, so bridge session bookkeeping and whatsmeow device restore can silently diverge.
- `curriculum/` is the generated prerequisite-learning surface for this repo; keep it concept-first under the training-curriculum contract, with `curriculum/research-ledger.md` preserving the required 3-pass discovery audit.
- Dunbar decay scoring treats connector-provenance LLM extraction facts as mention/context facts unless `extra_metadata.source == "interaction_sync"`; `email`, `interview`, and `calendar_event` interactions are intentionally downweighted to `0.2` so one-off transactional work/onboarding exchanges do not masquerade as inner-circle relationships.
- Relationship interaction facts are canonically stored under `subject='entity:{entity_id}'`; low-level `interaction_log`/`interaction_list` still accept legacy contact UUIDs and resolve them to `contacts.entity_id` before writing/querying.
- Memory entity Dunbar enrichment must aggregate duplicate contacts linked to the same `entity_id` by keeping the highest `dunbar_score`; otherwise a zero-score duplicate contact can overwrite the real scored contact and make `/entities` show tier 1500 while `/relationship/dunbar/ranking` shows an inner tier.
- Draft RFC 0014 proposes Chronicler as the retrospective Time Butler; until accepted/synced, treat its future-source compatibility note as proposed guidance, not an enforced rule.
- Google Health OAuth scopes live per-account in `public.google_accounts.granted_scopes`; the static `GOOGLE_OAUTH_SCOPES` app secret can lag. A connector state of `degraded` with `error_message='account_not_linked'` means OAuth scopes are granted but the Google/Fitbit Health account has not been linked, so no wellness ingestion will occur yet.
- Google account-level reauthorization links must pass an explicit all-optional `scope_set` (`gmail,calendar,contacts,drive,health`; `base` is implicit) when preserving restricted/optional scopes; omitting it triggers the backend default (`base+gmail+calendar+contacts+drive`) which intentionally excludes Google Health and can overwrite `granted_scopes` without `googlehealth.*`.
- Chronicler projection adapters whose evidence is mirrored across multiple butler schemas (e.g. `CalendarCompletedAdapter`) MUST derive `source_ref` from the upstream identifier (`calendar:{origin_instance_ref}`), not from the per-schema row id, so the persistent `(source_name, source_ref)` upsert dedups the cross-schema fan-out automatically. The in-run `seen_origin` set is just an efficiency hedge.
- Chronicler `health.steps` and `health.heart_rate` are point-event-only sources: register them in source contracts and schedules, but do not add D1 lane-taxonomy mappings unless they start emitting episodes.
- `Spawner.trigger(...)` propagates `ingestion_event_id` end-to-end to `session_create`; routing handlers in `src/butlers/core_tools/_routing.py` must pass `ingestion_event_id=route_request_id` because the switchboard ingest writes the same UUID7 as both `request_id` and `public.ingestion_events.id`. Without it, every route session stores `ingestion_event_id=NULL` and chronicler titles fall through to `Conversation via unknown channel`.
- Ingestion redesign closure must include live visual parity evidence, not just spec/task completion: compare `/ingestion` routes against the design reference (`docs/redesigns/ingestion-design-language.md` + `ingestion-handoff.md`; the prototype bundle graduated out of `pr/`), verify the old `Ingestion Events` card/table and page-level tab shell are absent, and keep screenshot/report artifacts before archiving.
- Telegram user-client conversation-history batches must preserve `sender.participants` / `owner_sender_id` through `message_inbox.raw_payload`; durable-buffer routing must derive the non-owner sender and pass `source_id` so identity resolution can anchor downstream memory facts to the contact instead of the owner.
- `frontend/src/components/chronicles/MapWidgetInner.tsx` must guard `map.addSource(...)` / `map.addLayer(...)` behind `map.isStyleLoaded()` (or `map.once('load', ...)`); calling them synchronously after `new maplibreGl.Map(...)` throws `Style is not done loading` and renders the user-visible `Failed to load the map. Try again` fallback even when valid trail data exists.
- Frontend links under the `/butlers-dev` mount must respect the routing surface: React Router `Link to` values should stay app-internal (for example `/butlers/lifestyle`) because `createBrowserRouter(..., { basename })` prefixes them; raw `<a href>` targets still need an explicit `import.meta.env.BASE_URL` prefix if they must leave React Router navigation.
- Dev compose runs against an external Postgres capped at `max_connections=200`; keep `BUTLERS_DB_POOL_*` and `BUTLERS_API_DB_POOL_*` defaults conservative in `docker-compose.yml` or late-starting butlers such as `travel` can fail after other services consume regular connection slots.
- Core Alembic migration revisions must stay globally unique and linear; `tests/config/test_migration_contract.py` now asserts duplicate `revision` IDs fail before compose migrations do.
- Alembic migrations that rewrite enum-like `TEXT` values guarded by `CHECK` constraints must drop/replace the old constraint before writing new values; otherwise live upgrades fail even if fresh-schema tests pass.
- The relationship butler has `[modules.memory]`, so `relationship.facts` and `relationship.predicate_registry` are memory-module bare tables in that schema. New relationship-domain triple-store work must use `relationship.entity_facts` plus `relationship.entity_predicate_registry`, and keep tests proving migrations tolerate existing memory-shaped tables.
- Entity-redesign routing lives in `frontend/src/router-config.tsx`, not `frontend/src/router.tsx`: `/entities`, `/entities/hop`, `/entities/columns`, `/entities/concentration`, `/entities/social-map`, and `/contacts -> /entities?has=contact` are mounted there. `router.tsx` only owns redirect helper components. Cmd/Ctrl+K opens the shipped `EntityFinder`; the legacy `CommandPalette` remains mounted but uses a different event.

### Backup strategy (bu-t102m)
- Strategy chosen: filesystem pg_dump cron. Simplest defensible approach for an owner-sovereign, single-instance system. No new external dependencies (no Minio/S3, no WAL archiving).
- Implementation: `deploy/backup/pg_dump.sh` runs `pg_dump | gzip` to a timestamped `.sql.gz` file in `$BACKUP_DIR` (default `/backups`). Prunes files older than `$BACKUP_RETAIN_DAYS` (default 14 days).
- Docker integration: `backup-cron` sidecar (postgres:17-alpine, crond) in `docker-compose.yml` writes to the `butlers_backups` named volume. Default schedule: 02:00 UTC daily (`BACKUP_CRON=0 2 * * *`). `dashboard-api` mounts the same volume read-only at `/backups` and reads it via `BUTLERS_BACKUP_DIR=/backups`.
- API: `GET /api/system/backups` (`get_backup_facts()` in `src/butlers/api/routers/system.py`) scans `BUTLERS_BACKUP_DIR` for `butlers_*.sql.gz` files sorted by mtime, returns the most recent file's mtime + size as `last_backup_at` / `last_backup_size_bytes`. Returns `backup_source_reachable=false` when `BUTLERS_BACKUP_DIR` is unset or the directory does not exist (unconfigured deployment, not an error).
- The `BackupTile` frontend component already handles all three states: loading, unreachable/unconfigured, and reachable with history. No frontend changes were needed.
- Manual test: after `docker compose up`, wait for first cron fire (or run `docker compose exec backup-cron /backup/pg_dump.sh` directly), then `GET /api/system/backups` should return `backup_source_reachable=true` with a non-null `last_backup_at`.
- `SpotifySessionAdapter._project_row` falls through to `track_names` when both `context_name` and `context_uri` are NULL — the underlying `connectors.spotify_listening_sessions.track_names` JSONB column is already populated; do not skip it just because no playlist/album context was attached.
- `bd worktree create <path> --branch X` creates branch X from the current HEAD, not from `origin/main`. New code worktrees must `git reset --hard origin/main` after creation; review worktrees must `git fetch origin agent/<original> && git reset --hard origin/agent/<original>` so reviewers see the actual PR HEAD.
- `bd close <id>` rejects beads that still have an open `blocks` downstream. For coordinator-driven closure where the original-vs-review pair is already done, pass `--force` rather than closing in dependency order (e.g. closing a review bead whose parent still references it).
- `bd create "title" --description="..." --json` fails when the description contains literal newlines or unescaped control characters — jq can't parse the JSON output. Recover the new ID via `bd list --json | jq -r '.[] | select(.title | startswith("...")) | .id'` and continue.
- PR reviewers must run the FULL CI-equivalent gate set (`pytest`, `npm run lint`, `npm run build`, `vitest`, frontend-e2e) — not just the gates listed in the bead's original scope. Pre-existing main breakage often extends beyond the bead, and each fix can unmask the next layer (we discovered ~7 additional failures across 5 review rounds on a 2-issue scoped bead).
- Cache-freshness markers using `time.monotonic()` must initialize to `float("-inf")`, not `0.0`. Fresh CI runners report `time.monotonic()` < typical TTLs (e.g. <900 s), so a `0.0` init makes `monotonic() - last_loaded < ttl` accidentally `True` on the first call → cache is "fresh" but empty. Bug pattern lives in `src/butlers/connectors/gmail_policy.py::GmailPolicyEvaluator` after the round-3 fix.
- `app.dependency_overrides[get_singleton]` only intercepts FastAPI `Depends(get_singleton)` lookups. A wrapper function that calls `get_singleton()` as a plain function call does NOT honor the override. Either keep `Depends(get_singleton)` in the endpoint and override `get_singleton` in tests that don't init the singleton, or accept that wrappers break overrides — don't introduce wrappers thinking they preserve dep-override semantics.
- SQL predicates used to filter rows in repository methods (e.g. `WHERE 'owner' = ANY(roles)`) break unit-test mocks that return canned rows regardless of the SQL. If the production code relies on the predicate to exclude wrong rows, tests with permissive mocks will see those wrong rows pass through. Read all needed columns from the row and filter in Python so mock-returned rows can be intentionally non-owner and the code still rejects them.
- `butlers-dev-dashboard-api-hotreload-1` does NOT auto-reload Python: it bind-mounts `~/gt/butlers/src` but runs `uv run butlers dashboard` without a file watcher. After merging backend changes and pulling main, `docker restart butlers-dev-dashboard-api-hotreload-1` is required (frontend Vite container does hot-reload on its own).
- Ingestion timeline status semantics: skip-triaged events (`triage_decision='skip'`) are stored in `public.ingestion_events` with DB `status='ingested'`; the unified timeline SELECT (`_UNION_COLUMN_SPEC` in `src/butlers/core/ingestion_events.py`) derives the display-only status `'skipped'` for them. Replay/state transitions still match on the DB value `'ingested'` — never write `'skipped'` to the table or match on it in UPDATEs.
- `public.butler_secrets` (shared credential pool) has TWO creation paths that must stay column-identical: daemon-boot `ensure_secrets_schema` DDL in `src/butlers/credential_store.py` and the core alembic chain. Core-chain schema discovery often excludes `'public'` (e.g. core_106), so a column added per-butler can silently miss the shared pool on live DBs — and `CREATE TABLE IF NOT EXISTS` won't repair existing tables. When adding columns: alembic migration targeting public explicitly + update both `_SECRETS_TABLE_DDL` and the convergence `_SECRETS_TEST_STATE_DDL`-style ALTER (test parity is pinned in `tests/migrations/test_shared_pool_test_state_migration.py`).
- In except blocks around credential/table queries, never string-match "does not exist" to mean table-missing — a missing COLUMN (schema drift) matches too and silently empties the result. Catch `asyncpg.exceptions.UndefinedTableError` for the silent path; everything else logs a warning (pattern in `_fetch_system_secrets`, `src/butlers/api/routers/secrets_v2.py`).
- `gh` (pr create/checks/merge) intermittently returns transient `HTTP 401: Requires authentication` even when `gh auth status` is healthy; also `gh pr checks` exits nonzero (8) while checks are pending. CI poll loops must tolerate both (retry on empty/401 output; don't gate the loop on exit code).
- Background subagent workers reliably go dormant while waiting on long CI (`check` jobs ≈ 25 min) even when told to poll in-foreground. Coordinators should arm their own fallback CI watcher with a short grace period and complete the merge/close/cleanup tail themselves if the PR is still open when checks finish; duplicate-merge attempts fail harmlessly.
- bd WRITE path can wedge in a full-reimport loop (observed 2026-06-12, bd 1.0.4, dolt-native server mode, shared Dolt :3307). Symptom: every `bd create/update` prints `auto-importing <N> bytes from issues.jsonl into empty database...` then fails `begin write tx: context canceled`; READS (`bd ready/list/show`) work fine. Cause: bd force-syncs the entire `issues.jsonl` (~3602 rows) into Dolt before each write; that import takes >5 min against the (saturated) shared server, so every trigger — the `bd create` itself, git `post-checkout`/`pre-commit` hooks (`bd hooks run ...` wrapped in `timeout 300`), the Codex `/beads-coordinator`'s writes — is killed before completing, the "synced" marker never advances, and the next write re-runs the doomed import. Self-amplifying: one `bd create` spawns multiple butlers-import connections that lock-contend; killed clients leave server-side INSERT connections alive (zombies) holding the write lock; other workspaces (e.g. `hud`) hit the same loop and saturate the shared server. Does NOT fix it: `no-auto-import: true` (doesn't suppress the empty-DB bootstrap import), killing contention, or letting one `bd import` run 20 min to exit 0. `bd init --reinit-local` is the only bd-level repair but is risky on the shared server. WORKAROUND (used here): Dolt :3307 itself is healthy + write-capable (direct transactional SQL write is instant), `issues.jsonl` is gitignored (Dolt is source of truth), server auto-commits SQL writes to HEAD. File a bead by INSERTing directly into `butlers.issues` — required NOT-NULL no-default cols: id (`bu-xxxxx`), title, description, design, acceptance_criteria, notes; match `created_by='Tzeusy'`, `owner='uniquosity@gmail.com'`, content_hash=sha256(title+"\n"+description). `bd show/ready` pick it up immediately; `dolt_status` shows clean. Diagnose/kill stuck writers via `mysql -h127.0.0.1 -P3307 -uroot -e "SELECT Id,db,Time,Info FROM information_schema.processlist WHERE Command='Query'"` + `KILL <id>`. (Filed bu-acknf this way.)
- RESOLVED (2026-06-12, root cause + durable fix for the bullet above; tracked as bu-g4sh0): the re-import is unconditional, not marker-based. bd v1.0.4 `maybeAutoImportJSONL` (cmd/bd/auto_import_upgrade.go, called from main.go PersistentPreRun for every non-read-only command) hardcodes `<beadsDir>/issues.jsonl`; its "is the DB empty" guard exists only in `EmbeddedDoltStore.ImportJSONLData` — server-mode DoltStore (our shared :3307) takes the fallback `importFromLocalJSONLFull`, which UPSERTs ALL rows every time the file merely exists. Upstream main has the fix (emptiness guard + insert-if-new + stamp file; regression cf. beads PR #3630, fix GH#3955) but no safe release ships it (v1.0.5 gated: migration 0043 breaks multi-machine dolt sync). FIX APPLIED: renamed `.beads/issues.jsonl` → `.beads/issues.export.jsonl` and set `export.path: issues.export.jsonl` in `.beads/config.yaml` — bd auto-export (export.auto=true, 60s throttle) keeps the new file fresh for beads-viewer, while the hardcoded auto-import check no-ops. Same disarm applied to deacon, homelab/tze_hud mayor rigs (+ property_agent/tui/viz which have separately-broken stale ports). CRITICAL: while on bd v1.0.4, NEVER create `.beads/issues.jsonl` (no `bd export -o .beads/issues.jsonl`, no symlink — os.Stat follows symlinks); use `.beads/issues.export.jsonl`. When bd ≥1.0.6 ships with the GH#3955 fix, upgrade and this constraint lifts.
- OpenSpec spec-amendment beads MUST end with sync+archive (apply deltas to `openspec/specs/`, check tasks.md, `mv` change to `openspec/changes/archive/YYYY-MM-DD-<name>`), not just merge the change directory. Verified failure mode (memory redesign, 2026-06-13): change `redesign-memory-house-ledger` merged un-applied, so a later reconciler (bu-d9im5) read the OLD spec's MUSTs as binding and re-justified UI the redesign had retired (tier cards) — drift caused by the spec machinery itself. Closeout PR #2208 pattern: sync deltas, archive, plus a code-level FE→BE wiring audit (the `superseded_by` field was returned by the API but read under a wrong name behind a stale "gated off" comment — both beads "done", affordance dead; grep the exact field name across types.ts/pages when a backend bead unblocks a gated frontend affordance).
- `butlers-redesign-prompt` assumes a `pr/overview/<slug>-redesign/` bundle, but most pages have none (shipped bundles are deleted — see `pr/overview/README.md`). For a bundle-less redesign (e.g. `health`, 2026-06-20) the slug resolver returns nothing: originate from the top-level Dispatch design language (`pr/overview/DESIGN_LANGUAGE.md` + `pr/overview/dispatch-kit/` + a shipped sibling `docs/redesigns/*-design-language.md`) and treat the live pages + that language as the "bundle". Run `/butlers-ui-maturity-audit` FIRST — it often reverses the framing (the health surface was already full-CRUD real, not a skin; real gaps were IA, orphaned-but-built endpoints like `/api/health/measurements/trend`/`/latest`/`/sources` with zero FE consumers, and unsurfaced insight). All Dispatch primitives (Eyebrow/Display/KpiStrip/AttentionList/BriefingStatus/Voice/ButlerMark/StateDot/Section) and tokens already exist in `frontend/src/` (`index.css:1-290`), so redesign work is mostly composition, not net-new UI infra. The redesign Act-2 handoff (`/project-direction`, a `th-projects` subskill) → OpenSpec change → READY beads triggers the autonomous fleet; commit+push the brief+changeset to origin/main first so workers see the spec.
- OpenSpec `## MODIFIED Requirements`: the `### Requirement: <name>` header MUST match the existing `openspec/specs/<cap>/spec.md` header VERBATIM — the CLI matches by exact text. Do NOT add `[TARGET-STATE]` (or any tag) to a MODIFIED header; it breaks the match so apply/`validate --strict` create a parallel requirement instead of modifying. `[TARGET-STATE]` is ADDED-only. Quote the real prior literal when restating a value (e.g. an existing cron `0 7 15 * * *`), and ground every new universal/negative invariant against the pre-existing path it binds (an auto-refresh carve-out must NAME the 30s rule it overrides). Cross-butler insight reads: host the reader on `butler_switchboard_rw` (holds SELECT on `public.insight_candidates` per `core_010`), never the health/per-butler role (INSERT-only) — avoids a grant migration and preserves schema isolation.
- Background subagents spawned via the Agent tool deliver their final report to "main" only as a SUMMARY line in the idle/teammate notification — the full body does NOT arrive (even a single large SendMessage truncates to its summary). To get full reports reliably: have writer-capable agents (general-purpose) WRITE the report to a file under `.tmp/` (gitignored) and ping main with "written, N lines"; read-only Explore agents can't write, so instruct them to SendMessage the body in explicit `(1/2)/(2/2)` chunks.
- `bd create --graph <plan.json>` (bd v1.0.4) CREATES the nodes but SILENTLY DROPS the `deps` and `parent` fields — verified 2026-06-20 filing the calendar-UX roadmap epic (`bu-l3k0zg`): all 26 nodes created, but every child came back with `dependencies=null` and `parent=null`, so everything showed READY. The graph JSON schema is `{"nodes":[{"key","title","type","priority","description","parent","deps":["blocks:<key>"]}]}` (dep dir: `blocks:X` = this node blocks X = X blocked-by this). Since graph ignores deps/parent, wire them AFTER create from the returned `ids` map: `bd dep <blocker> --blocks <blocked> --no-cycle-check` (then `bd dep cycles`) for edges, and `bd update <child> --parent <epic>` for hierarchy. NOTE: parent-child shows up in a child's `blocked_by` list (dependency_type `parent-child`) but does NOT gate readiness — only real `blocks` edges keep a bead out of `bd ready`. Filing READY beads here auto-triggers the fleet, so gate big/risky children behind real prerequisite beads (design/decision beads, prefs, held epics) rather than leaving them ready.
- Watching PR CI to merge: the three jobs are `frontend` (~4 min: tsc+vitest), `frontend-e2e` (~3 min), and `check` (~23 min: backend pytest + testcontainers). `check` is always the long pole, so a single `gh pr checks --watch` (or a Bash poll) will time out at least once on it; budget ~25 min. Poll the `gh pr checks <#>` STATE COLUMN (`pending`/`pass`/`fail`), NOT `gh pr view --json statusCheckRollup` — the latter returns `.conclusion` as an EMPTY STRING (not null) for in-flight checks, so jq `(.conclusion // .status)` falls through to "" and a naive `grep null` guard exits early thinking it's done. Merge convention is squash (`gh pr merge <#> --squash`; main commits carry the `(#NNNN)` suffix). main is unprotected and `frontend`/`frontend-e2e`/`check` are not required checks, but wait for all three green anyway. The chronicler editorial endpoints (`/api/chronicler/briefing|attention|kpi`) already accept arbitrary `?date=&tz=` deterministically (no LLM, no cross-schema) via `editorial.compose_briefing_payload`; date navigation is a pure-frontend concern. `lay-and-land/frontend.md` still mis-documents Chronicles as `archetype="workspace"` (it is editorial) — tracked in bu-26j38.
