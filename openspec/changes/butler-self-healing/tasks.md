## 1. Database Migrations

- [ ] 1.1 Create Alembic migration to extend `complexity_tier` CHECK constraint on `shared.model_catalog` and `shared.butler_model_overrides` to include `self_healing`
- [ ] 1.2 Create Alembic migration for `shared.healing_attempts` table with indexes on `fingerprint` and `status`, plus partial unique index on `fingerprint WHERE status IN ('investigating', 'pr_open')`
- [ ] 1.3 Create Alembic migration to add `healing_fingerprint TEXT` column (nullable) to each butler's `sessions` table
- [ ] 1.4 Add `healing-sonnet` seed entry to `model_catalog_defaults.toml` with tier `self_healing`, runtime `claude`, model `claude-sonnet-4-6`, priority 10

## 2. Model Tier Integration

- [ ] 2.1 Add `SELF_HEALING = "self_healing"` to `Complexity` enum in `src/butlers/core/model_routing.py`
- [ ] 2.2 Update valid tier list in `src/butlers/api/routers/model_settings.py` to include `self_healing`
- [ ] 2.3 Update `TRIGGER_SOURCES` frozenset in `src/butlers/core/sessions.py` to include `"healing"`

## 3. Error Fingerprinting

- [ ] 3.1 Create `src/butlers/core/healing/__init__.py` package
- [ ] 3.2 Implement `src/butlers/core/healing/fingerprint.py` with `FingerprintResult` dataclass and dual-input functions:
  - `compute_fingerprint(exc, tb)` — raw exception path (spawner fallback)
  - `compute_fingerprint_from_report(error_type, error_message, call_site, traceback_str, severity_hint)` — structured path (module)
- [ ] 3.3 Implement message sanitization: regex replacements for UUIDs → `<UUID>`, timestamps → `<TS>`, numeric IDs → `<ID>`, empty messages → `<empty>`, truncate to 500 chars
- [ ] 3.4 Implement call site extraction: walk traceback frames, skip stdlib/third-party (use `src/butlers/`, `roster/`, `tests/` as app code heuristic), extract `relative_path:function_name`
- [ ] 3.5 Implement severity scoring with agent hint tiebreaker: critical (DB/credential), high (runtime/config), medium (module), low (memory), info (cancellation); agent `severity_hint` overrides default-only
- [ ] 3.6 Write tests — same fingerprint regardless of input path, severity hint logic, empty messages, chained exceptions, cancellation exclusion

## 4. Healing Session Tracking

- [ ] 4.1 Implement `src/butlers/core/healing/tracking.py` — CRUD functions for `shared.healing_attempts`
- [ ] 4.2 Implement `create_or_join_attempt(pool, fingerprint, ...)` using `INSERT ... ON CONFLICT` on partial unique index — returns `(attempt_id, is_new)` for atomic novelty+insert
- [ ] 4.3 Implement `update_attempt_status(pool, attempt_id, status, ...)` with `updated_at = now()`, `closed_at` on terminal states, and rejection of transitions from terminal states
- [ ] 4.4 Implement fingerprint collision detection: compare `(exception_type, call_site)` on join, log CRITICAL if mismatch
- [ ] 4.5 Implement gate query functions: `get_active_attempt()`, `get_recent_attempt()`, `count_active_attempts()`, `get_recent_terminal_statuses()`
- [ ] 4.6 Implement `list_attempts(pool, limit, offset, status_filter)` for dashboard
- [ ] 4.7 Implement `session_set_healing_fingerprint(pool, session_id, fingerprint)` in `sessions.py`
- [ ] 4.8 Implement daemon restart recovery: `recover_stale_attempts(pool, timeout_minutes)` — transition old `investigating` rows to `timeout`/`failed`, handle `healing_session_id = NULL` case
- [ ] 4.9 Write tests for tracking CRUD, gate query functions, atomic insert race condition, collision detection, and recovery

## 5. Anonymizer

- [ ] 5.1 Implement `src/butlers/core/healing/anonymizer.py` — `anonymize(text, repo_root)` and `validate_anonymized(text)`
- [ ] 5.2 Implement credential redaction rules: API keys, DB URLs, JWT tokens, plus existing Telegram/Bearer patterns
- [ ] 5.3 Implement PII scrubbing: email addresses (case-insensitive), phone numbers, IPv4/IPv6 addresses (preserve localhost/loopback)
- [ ] 5.4 Implement path normalization: absolute paths → repo-relative, non-repo paths → `[REDACTED-PATH]`
- [ ] 5.5 Implement environment scrubbing: hostnames, env var values
- [ ] 5.6 Implement validation pass: scan for residual sensitive patterns, return `(bool, list[str])` with pattern type, offset, and anonymized surrounding context
- [ ] 5.7 Implement false positive guards: code identifiers, version strings, git SHAs not treated as PII/credentials
- [ ] 5.8 Write tests for each redaction category, validation edge cases, false positives, and case sensitivity

## 6. Healing Worktree

- [ ] 6.1 Implement `src/butlers/core/healing/worktree.py` — `create_healing_worktree(repo_root, butler_name, fingerprint)` → `(Path, str)`, raises `WorktreeCreationError` on failure
- [ ] 6.2 Implement worktree creation error handling: clean up orphaned branches on `git worktree add` failure, handle branch collision, handle git lock
- [ ] 6.3 Implement `remove_healing_worktree(repo_root, branch_name, delete_branch, delete_remote)` — `git worktree remove` (with `--force` fallback for dirty worktrees) + conditional branch/remote cleanup
- [ ] 6.4 Implement `reap_stale_worktrees(repo_root, pool)` — scan `.healing-worktrees/`, cross-reference `healing_attempts`, remove terminal + >24h old, remove orphaned worktrees with no matching attempt, remove orphaned `self-healing/*/` branches
- [ ] 6.5 Add `.healing-worktrees/` to `.gitignore`
- [ ] 6.6 Write tests for worktree creation, creation failure cleanup, force-remove, orphan detection, and stale reaping

## 7. Self-Healing Dispatcher (shared engine)

- [ ] 7.1 Implement `src/butlers/core/healing/dispatch.py` — unified `dispatch_healing()` accepting both `FingerprintResult` (module path) and `(exc, tb)` (spawner fallback)
- [ ] 7.2 Implement 10-gate check sequence: no-recursion → opt-in → fingerprint → persistence → severity → novelty → cooldown → concurrency → circuit breaker → model resolution
- [ ] 7.3 Implement healing agent prompt construction with two variants: with agent context (module path) and without (spawner fallback)
- [ ] 7.4 Implement healing agent spawning via `Spawner.trigger()` with `complexity="self_healing"`, `trigger_source="healing"`, CWD=worktree, empty MCP config
- [ ] 7.5 Implement PR creation flow: `git push origin <branch>` → anonymize PR content → validate → `gh pr create` with structured template, labels `self-healing` + `automated`; handle push failure and anonymization failure
- [ ] 7.6 Implement timeout watchdog: `asyncio.Task` alongside healing session, cancel after `timeout_minutes`, transition to `timeout`, trigger worktree cleanup
- [ ] 7.7 Implement independent trace span for dispatch (`healing.dispatch` root span, not child of failed session)
- [ ] 7.8 Write tests for each gate (mock DB), dual-path dispatch, prompt variants, no-recursion guard, timeout watchdog, PR flow, and trace isolation

## 8. Self-Healing Module (MCP tools)

- [ ] 8.1 Create `src/butlers/modules/self_healing/__init__.py` — `SelfHealingModule(Module)` with name `self_healing`, config schema, no dependencies
- [ ] 8.2 Implement `register_tools()` — register `report_error` and `get_healing_status` MCP tools
- [ ] 8.3 Implement `report_error` tool handler: accept structured error context, compute fingerprint via `compute_fingerprint_from_report()`, run dispatch, return `{accepted, fingerprint, reason}` JSON
- [ ] 8.4 Implement `get_healing_status` tool handler: query by fingerprint or list recent attempts for butler
- [ ] 8.5 Implement `tool_metadata()` — mark `error_message`, `traceback`, `context` as sensitive for approvals
- [ ] 8.6 Implement `on_startup()` — run `recover_stale_attempts()` + `reap_stale_worktrees()`
- [ ] 8.7 Implement `on_shutdown()` — cancel watchdog tasks (best-effort)
- [ ] 8.8 Write tests for module registration, tool handlers, response shapes, and sensitivity metadata

## 9. Self-Healing Skill (shared)

- [ ] 9.1 Create `roster/shared/skills/self-healing/SKILL.md` — protocol for when/how to call `report_error`, data safety rules, response handling, `get_healing_status` usage, examples
- [ ] 9.2 Update `roster/shared/BUTLER_SKILLS.md` to reference the `self-healing` skill

## 10. Spawner Fallback Integration

- [ ] 10.1 Add `asyncio.create_task(dispatch_healing(...))` call in spawner's except block — capture `sys.exc_info()` before cleanup, guard with `trigger_source != "healing"` AND self-healing module loaded
- [ ] 10.2 Add per-butler semaphore bypass for `trigger_source == "healing"` sessions (global semaphore still acquired)
- [ ] 10.3 Add MCP restriction: empty `mcp_servers` dict for `trigger_source == "healing"` sessions; include `GH_TOKEN` in env
- [ ] 10.4 Write integration test: hard crash → spawner fallback fires → fingerprint computed → attempt created (mock agent spawn)

## 11. Dashboard API & UI

- [ ] 11.1 Create `src/butlers/api/routers/healing.py` — list/detail/retry endpoints for healing attempts (with status filter)
- [ ] 11.2 Add circuit breaker status endpoint `GET /api/healing/circuit-breaker` and reset endpoint `POST /api/healing/circuit-breaker/reset`
- [ ] 11.3 Add retry validation: reject retry on non-terminal attempts (HTTP 409)
- [ ] 11.4 Verify `self_healing` tier appears in model settings UI tier dropdown (frontend update if needed)
- [ ] 11.5 Write API route tests for healing endpoints including circuit breaker and retry edge cases

## 12. Documentation & Cleanup

- [ ] 12.1 Add `[modules.self_healing]` config example to a sample `butler.toml` or config documentation
- [ ] 12.2 Run full test suite — verify no regressions from new migrations, enum extension, module loading, or trigger source addition
- [ ] 12.3 Sync specs to `openspec/specs/` via `/opsx:sync`
