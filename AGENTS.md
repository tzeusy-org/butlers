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
2. **Run quality gates** (if code changed) - Tests, linters, builds
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

### Memory System Architecture
The memory system is a **shared Memory Butler** (port 8150, DB `butler_memory`) — not per-butler isolated. Three tables: `episodes` (session observations, 7d TTL), `facts` (subject-predicate knowledge with per-fact subjective decay), `rules` (procedural playbook, maturity: candidate→established→proven). Uses pgvector + local MiniLM-L6 embeddings (384d). Scoped (`global` or butler-name) but in one shared DB. See `MEMORY_PROJECT_PLAN.md` for full design. Dashboard integration at `/memory` (cross-butler) and `/butlers/:name/memory` (scoped).

### Migration naming/path convention
Alembic revisions are chain-prefixed (`core_*`, `mem_*`, `sw_*`) rather than bare numeric IDs. Butler-specific migrations resolve from `roster/<butler>/migrations/` via `butlers.migrations._resolve_chain_dir()` (not legacy `butlers/<name>/migrations/` paths).
- Within a chain, set `branch_labels` only on the branch root revision (e.g. `rel_001`); repeating the same label on later revisions causes Alembic duplicate-branch errors.

### Known Warnings (not bugs)
- 2 RuntimeWarnings in CLI tests from monkeypatched `asyncio.run` — unawaited coroutines in test mocking

### Quality Gates
```bash
uv run ruff check src/ tests/ roster/ conftest.py
uv run ruff format --check src/ tests/ roster/ conftest.py
uv run pytest tests/ -v --ignore=tests/test_db.py --ignore=tests/test_migrations.py
```

### Switchboard Classification Contract
- `classify_message()` returns decomposition entries (`list[{"butler","prompt"}]`), not a bare butler string. Callers must normalize both legacy string and list formats before routing.
- When `butler_registry` is empty, `classify_message()` auto-discovers butlers from `roster/` (see `roster/switchboard/tools/routing/classify.py`) before composing the "Available butlers" prompt.

### Memory Writing Tool Contract
- `roster/memory/storage.py` write APIs return UUIDs (`store_episode`, `store_fact`, `store_rule`); MCP wrappers in `roster/memory/tools/writing.py` are responsible for shaping tool responses (`id`, `expires_at`, `superseded_id`) and must pass `embedding_engine` in the current positional order.

### DB SSL config contract
- `src/butlers/db.py` now parses `sslmode` from `DATABASE_URL` and `POSTGRES_SSLMODE`; parsed mode is forwarded to both `asyncpg.connect()` (provisioning) and `asyncpg.create_pool()` (runtime).
- Dashboard DB setup in `src/butlers/api/deps.py` and `src/butlers/api/db.py` reuses the same env parser and forwards the same SSL mode to API pools, keeping daemon/API behavior aligned.

### Frontend test harness
- Frontend route/component tests run with Vitest (`frontend/package.json` has `npm test` -> `vitest run`).
- Colocate tests as `frontend/src/**/*.test.tsx` (example: `frontend/src/pages/ButlersPage.test.tsx`).

### Calendar OAuth init contract
- In `src/butlers/modules/calendar.py`, `_GoogleProvider.__init__` should validate `_GoogleOAuthCredentials.from_env()` before creating an owned `httpx.AsyncClient` so credential errors cannot leak unclosed clients.
- `_GoogleOAuthClient.get_access_token()` should enforce token non-null invariants with explicit asserts rather than returning a fallback empty string.
