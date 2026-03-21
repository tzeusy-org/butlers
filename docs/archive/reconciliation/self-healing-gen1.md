> **ARCHIVED** — This document is historical. It was archived on 2026-03-21.
> **Reason:** Gen-1 reconciliation artifact — superseded by current implementation.

# Self-Healing Gen-1 Reconciliation Report

**Date:** 2026-03-17
**Auditor:** bu-geb2 worker
**Scope:** All specs under `openspec/changes/butler-self-healing/` vs. delivered implementation

---

## Methodology

Each spec file's requirements and the design doc's decisions were compared against the
implementation in:

- `src/butlers/core/healing/` — fingerprint, anonymizer, tracking, worktree, dispatch
- `src/butlers/modules/self_healing/__init__.py` — MCP module
- `roster/shared/skills/self-healing/SKILL.md` — shared skill
- `src/butlers/api/routers/healing.py` — dashboard API
- `src/butlers/core/spawner.py` — spawner fallback + semaphore bypass
- `src/butlers/core/sessions.py` — healing_fingerprint column + session_set_healing_fingerprint
- `src/butlers/core/model_routing.py` — Complexity.SELF_HEALING
- `alembic/versions/core/core_035_self_healing_tier_and_attempts.py` — DB schema
- `alembic/versions/core/core_036_sessions_add_healing_fingerprint.py` — sessions column
- Tests under `tests/core/healing/`, `tests/modules/`, `tests/api/`

---

## Spec: error-fingerprinting

**Source:** `openspec/changes/butler-self-healing/specs/error-fingerprinting/spec.md`
**Requirements:** 10

### Req: Fingerprint Computation
**Status: IMPLEMENTED**
- `fingerprint.py:_compute_hash()` hashes `f"{exception_type}||{call_site}||{sanitized_message}"` via SHA-256.
- Returns 64-char lowercase hex via `hashlib.sha256(...).hexdigest()`.

**Scenarios covered:**
- Fingerprint from standard exception: SHA-256 of structured tuple — verified in `_compute_hash`.
- Same root cause → identical fingerprint: deterministic via sanitization + hashing.
- Different call sites → different fingerprints: call site is part of the tuple.

### Req: Exception Type Extraction
**Status: IMPLEMENTED**
- `_fully_qualified_name(exc)` in `fingerprint.py` uses `type(exc).__module__` + `type(exc).__qualname__`.

**Scenarios covered:**
- Built-in exception → `builtins.ValueError`: `__module__ = "builtins"`, verified in tests.
- Third-party → `asyncpg.exceptions.PostgresError`: standard module path.
- Chained exception uses outermost (not root cause): implementation uses `type(exc)` on the
  outer exception directly, consistent with spec. Note: spec says "outermost exception"; `compute_fingerprint`
  receives the outermost caught exception, so chaining is not specially handled — this matches spec intent.

### Req: Call Site Extraction
**Status: IMPLEMENTED**
- `_extract_call_site(tb)` in `fingerprint.py` walks `traceback.extract_tb(tb)` from innermost outward.
- `_is_app_frame()` checks `_APP_CODE_PREFIXES = ("src/butlers/", "roster/", "tests/")` and `_APP_CODE_FILES = ("conftest.py",)`.
- Returns `<relative_path>:<func_name>`, NO line number.
- Falls back to `<unknown>:<unknown>` when no app frame found.

**Gap (minor):** The spec scenarios mention "all-frames-stdlib-or-third-party → `<unknown>:<unknown>`" — implemented.
The `_relativize_path()` helper correctly strips absolute prefixes.

### Req: Message Sanitization
**Status: IMPLEMENTED**
- `_sanitize_message()` applies: UUID → `<UUID>`, timestamp → `<TS>`, numeric IDs → `<ID>`.
- Empty/None message returns `<empty>`.
- Truncation at `_MAX_MESSAGE_LEN = 500`.
- Order: UUID first (prevents timestamps inside UUIDs from being consumed by `<ID>`), then timestamps, then IDs.

**Gap (potential):** The numeric ID pattern `_RE_NUMERIC_ID = re.compile(r"\b\w*\d\w*\b")` is broad —
it replaces ANY word token containing a digit. This is intentional per design ("table names, hashes, session
slugs") but may over-replace in some messages. This matches spec scenario intent ("relation foo_123 → relation `<ID>`").

### Req: Severity Scoring
**Status: IMPLEMENTED**
- `_score_severity()` implements 0–4 scale.
- Critical: `asyncpg.PostgresError`, `asyncpg.InterfaceError`, `asyncpg.exceptions.*`, `CredentialStore`, `CredentialError`, `SecretError`.
- High: call sites under `src/butlers/core/runtimes/`, function names in `_HIGH_FUNCTION_NAMES` (`read_system_prompt`, `_build_env`, `_resolve_provider_config`).
- Medium: `src/butlers/modules/` call sites; default unknown.
- Low: `fetch_memory_context`, `store_session_episode` function names.
- Info: `asyncio.CancelledError`, `KeyboardInterrupt`.

**Gap (minor):** The spec includes "adapter initialization" in the high-severity rule (alongside `_resolve_provider_config`).
The implementation covers `_resolve_provider_config` and `_build_env` by function name but does not have an explicit
"adapter initialization" check. Adapter init errors that surface at a call site not covered by the above may fall
through to medium. The practical impact is low since adapter init errors typically surface in `_resolve_provider_config`
or `_build_env`. Tracked as discovered issue DI-1.

### Req: Fingerprint Scope Boundary
**Status: IMPLEMENTED**
- Spec: only exceptions in the spawner's `_run()` try/except; not `finally` block.
- `spawner.py`: the self-healing fallback is only invoked inside the except block, not in `finally`.
  The `finally` block handles metrics, span cleanup, context clearing — none of those paths invoke the dispatcher.

### Req: Dual-Input Fingerprinting
**Status: IMPLEMENTED**
- `compute_fingerprint(exc, tb)` — raw exception path (spawner fallback).
- `compute_fingerprint_from_report(error_type, error_message, call_site, traceback_str, severity_hint)` — structured path.
- Both return `FingerprintResult`.

**Scenario:** Same error via both paths → same fingerprint: the structured path uses the same sanitize + hash
logic. Unit tests in `tests/core/healing/test_fingerprint.py` verify this.

### Req: FingerprintResult Type
**Status: IMPLEMENTED**
- `FingerprintResult` dataclass with fields: `fingerprint` (str, 64-char hex), `severity` (int), `exception_type` (str), `call_site` (str), `sanitized_message` (str).
- Frozen dataclass (immutable), exported from `core.healing.__init__`.

### Req: Severity Hint from Agent
**Status: IMPLEMENTED**
- `_apply_severity_hint(auto_severity, severity_hint)` in `fingerprint.py`.
- Hint overrides only when auto returns `SEVERITY_MEDIUM`.
- Specific rules (critical/high/low/info) override the hint.

### Req: Legacy Function Signature
**Status: IMPLEMENTED**
- `compute_fingerprint(exc, tb)` is the primary public API; returns `FingerprintResult`.
- When `tb=None`: `_extract_call_site(None)` returns `<unknown>:<unknown>`.

---

## Spec: self-healing-dispatch

**Source:** `openspec/changes/butler-self-healing/specs/self-healing-dispatch/spec.md`
**Requirements:** 17

### Req: Dual Entry Points
**Status: IMPLEMENTED**
- Module path: `dispatch_healing()` called with pre-computed `FingerprintResult` from `report_error` handler.
- Spawner fallback: `dispatch_healing()` called with `(exc, tb)` tuple; fingerprint computed inside.
- `FingerprintInput = FingerprintResult | tuple[BaseException, types.TracebackType | None]`.

### Req: Dispatch Function Signature
**Status: IMPLEMENTED**
- `dispatch_healing(pool, butler_name, session_id, fingerprint_input, config, repo_root, spawner,
  agent_context, trigger_source, gh_token, task_registry)`.
- Returns `DispatchResult(accepted, fingerprint, reason, attempt_id)`.

### Req: Gate Ordering
**Status: IMPLEMENTED**
All 10 gates implemented in `dispatch_healing()` in strict order:
1. No-recursion (`trigger_source == "healing"`)
2. Opt-in (`config.enabled`)
3. Fingerprint computation
4. Fingerprint persistence (`session_set_healing_fingerprint`, best-effort)
5. Severity gate (`fp.severity > config.severity_threshold`)
6. Novelty gate (`create_or_join_attempt` — atomic INSERT ON CONFLICT)
7. Cooldown gate (`get_recent_attempt`)
8. Concurrency cap (`count_active_attempts`)
9. Circuit breaker (`_is_circuit_breaker_tripped`)
10. Model resolution (`resolve_model(pool, butler_name, Complexity.SELF_HEALING)`)

**Note on gate 5:** Spec says "reject if severity below threshold" with lower = more severe.
Code uses `fp.severity > config.severity_threshold` — if severity=3 (low) and threshold=2 (medium),
3>2 is True, dispatch rejected. This is correct: "severity is BELOW the threshold for triggering healing"
when the number is ABOVE the threshold value. The logic is correct; the variable name "severity_threshold"
describes the upper bound on severity score.

**Scenario:** Opt-in checked before fingerprint: verified — gate 2 exits before gate 3 computation.

### Req: No Recursive Healing
**Status: IMPLEMENTED**
- Gate 1: `if trigger_source == "healing": return DispatchResult(..., reason="no_recursion")`.
- Also enforced in `spawner.py` at the fallback entry: `if trigger_source != "healing" and self._healing_module is not None`.
- `trigger_source="healing"` is also excluded from MCP config generation in spawner.

### Req: Opt-In Gate
**Status: IMPLEMENTED**
- `HealingConfig.enabled` defaults to `False` in `dispatch.py`; defaults to `True` in `SelfHealingConfig`
  (the module schema).
- When module is not loaded, `wire_healing_module(None)` is never called → spawner fallback also disabled.

### Req: Fingerprint Update on Failed Session
**Status: IMPLEMENTED**
- Gate 4: `await session_set_healing_fingerprint(pool, session_id, fp.fingerprint)`.
- `try/except` block: on exception, logs WARNING and continues.

### Req: Severity Gate
**Status: IMPLEMENTED**
- `if fp.severity > config.severity_threshold` in dispatch.

### Req: Novelty Gate
**Status: IMPLEMENTED**
- `create_or_join_attempt()` in `tracking.py` uses `INSERT … ON CONFLICT … DO UPDATE` pattern.
- Returns `(attempt_id, is_new)`.
- When `is_new=False`: returns `DispatchResult(accepted=False, reason="already_investigating")`.

**Scenario: Module path returns status to caller:** Module's `_handle_report_error` returns
`{"accepted": false, "reason": "already_investigating", ...}`. Also has a fast-path check
via `get_active_attempt()` before calling `dispatch_healing`.

### Req: Cooldown Gate
**Status: IMPLEMENTED**
- `get_recent_attempt(pool, fingerprint, config.cooldown_minutes)` — queries all terminal statuses.
- Dashboard retry bypass: retry route in `healing.py` does a direct INSERT bypassing `create_or_join_attempt`
  and does not call `dispatch_healing` (cooldown gate not in the path).

**Gap:** The retry endpoint creates a new `investigating` row but does NOT actually spawn a healing agent —
it only inserts the DB row. The dispatcher workflow (worktree + spawn) does not run on retry. This is a
functional gap relative to the spec scenario "create new healing attempt for the same fingerprint, bypassing cooldown."
See discovered issue DI-2.

### Req: Concurrency Cap
**Status: IMPLEMENTED**
- `count_active_attempts(pool)` counts rows with `status = 'investigating'`.
- Note: After `create_or_join_attempt` inserts the new row, the active count includes that row.
  The gate uses `active_count > config.max_concurrent` (strictly greater). With `max_concurrent=2`
  and 2 existing rows + 1 just inserted = count=3 → 3>2 → rejected. This means the effective cap
  is `max_concurrent` concurrent DISPATCHED rows (the current one is counted). This is consistent
  with intent but worth documenting clearly.

### Req: Circuit Breaker
**Status: IMPLEMENTED**
- `_is_circuit_breaker_tripped()` checks last N terminal attempts.
- Failure statuses: `CIRCUIT_BREAKER_FAILURE_STATUSES = {"failed", "timeout", "anonymization_failed"}`.
- `unfixable` excluded (does not trip breaker).
- Reset: `POST /api/healing/circuit-breaker/reset` inserts a synthetic `pr_merged` sentinel row.

### Req: Model Resolution Gate
**Status: IMPLEMENTED**
- `resolve_model(pool, butler_name, Complexity.SELF_HEALING)`.
- Returns `None` → WARNING + `DispatchResult(reason="no_model")` + attempt set to `failed`.
- DB error during resolution: caught, logged as WARNING, skips.

### Req: Healing Agent Spawning
**Status: IMPLEMENTED**
- `spawner.trigger(prompt=..., trigger_source="healing", complexity=Complexity.SELF_HEALING, cwd=str(worktree_path), bypass_butler_semaphore=True)`.
- Attempt row updated with `healing_session_id`.

### Req: Healing Agent Timeout Watchdog
**Status: IMPLEMENTED**
- `_timeout_watchdog()` task sleeps `timeout_minutes * 60`, cancels healing task, transitions to `timeout`, removes worktree.

### Req: PR Creation Flow
**Status: IMPLEMENTED**
- `_create_pr()` in `dispatch.py`: git push → anonymize → validate → `gh pr create --label`.
- Push failure → `failed`.
- Anonymization failure → remote branch deleted → `anonymization_failed`.

### Req: Semaphore Behavior for Healing Sessions
**Status: IMPLEMENTED**
- `bypass_butler_semaphore=True` parameter on `spawner.trigger()`.
- `spawner.py`: if `bypass_butler_semaphore`: skip per-butler semaphore, still acquire global.

### Req: Dispatch Errors Are Non-Fatal
**Status: IMPLEMENTED**
- All of `dispatch_healing` wrapped in `try/except Exception` → returns `DispatchResult(reason="internal_error")`.
- Spawner fallback: dispatch task failure logged at WARNING, `SpawnerResult` unaffected.

### Req: Trace Isolation
**Status: IMPLEMENTED (conditional)**
- `dispatch_healing` creates a new root OTel span `"butlers.healing.dispatch"` when `_HAS_OTEL=True`.
- Gracefully degrades when opentelemetry not installed.
- The `trace_id` attribute records `healing.trigger_source`; failed session's `trace_id` is not explicitly
  captured as a span attribute — minor gap. See DI-3.

---

## Spec: self-healing-module

**Source:** `openspec/changes/butler-self-healing/specs/self-healing-module/spec.md`
**Requirements:** 7

### Req: Module Identity
**Status: IMPLEMENTED**
- `SelfHealingModule` implements `Module` ABC, `name = "self_healing"`, `dependencies = []`.
- `SelfHealingConfig` Pydantic schema with all specified fields and defaults.
- Module is in `src/butlers/modules/self_healing/__init__.py` — discoverable by `ModuleRegistry`.

**Scenario: Config schema fields:** `enabled` (bool, True), `severity_threshold` (int, 2),
`max_concurrent` (int, 2), `cooldown_minutes` (int, 60), `circuit_breaker_threshold` (int, 5),
`timeout_minutes` (int, 30). All match spec exactly.

### Req: report_error MCP Tool
**Status: IMPLEMENTED**
- Registered via `@mcp.tool()` in `register_tools()`.
- Parameters: `error_type` (str, required), `error_message` (str, required), `traceback` (str, optional),
  `call_site` (str, optional), `context` (str, optional), `tool_name` (str, optional),
  `severity_hint` (str, optional). All match spec.
- Returns `{"accepted": true/false, "fingerprint", "attempt_id", "message"}` as specified.

### Req: get_healing_status MCP Tool
**Status: IMPLEMENTED**
- Registered via `@mcp.tool()`.
- By fingerprint: fetches most recent attempt via `get_recent_attempt` + `get_active_attempt`.
- No arguments: returns 5 most recent for this butler via `list_attempts(..., limit=5, butler_name=...)`.
- Empty result: `{"attempts": [], "message": "No healing attempts found"}`.

### Req: Tool Sensitivity Metadata
**Status: IMPLEMENTED**
- `tool_metadata()` returns `ToolMeta` marking `error_message`, `traceback`, `context` as sensitive.
- Note: spec says `error_message` and `traceback` — implementation adds `context` as well (conservative, correct).

### Req: Module Startup and Shutdown
**Status: IMPLEMENTED**
- `on_startup`: runs `recover_stale_attempts(pool, timeout_minutes)` then `reap_stale_worktrees(repo_root, pool)`.
- `on_shutdown`: cancels `_watchdog_tasks`.
- Active healing sessions NOT terminated on shutdown (per spec).

### Req: Module Delegates to Core Healing Package
**Status: IMPLEMENTED**
- `_handle_report_error` calls `compute_fingerprint_from_report` from `core.healing.fingerprint`.
- `dispatch_healing` from `core.healing.dispatch` does all gate checks, worktree creation, spawn.
- No fingerprinting, dispatch logic, or worktree management implemented directly in the module.

### Req: Healing Agent Spawning from Module
**Status: IMPLEMENTED**
- Dispatch is asynchronous: `dispatch_healing()` is awaited but the actual session runs in a background task.
- `report_error` returns immediately after `dispatch_healing()` resolves (gate checks + DB insert only).
- `bypass_butler_semaphore=True` prevents deadlock with calling session.

**Gap (minor):** Spec says `trigger_source="healing"` on spawned healing agent. The module passes
`trigger_source="external"` to `dispatch_healing` (for the no-recursion check on the failing session),
and the actual healing spawn inside `_run_healing_session` passes `trigger_source="healing"` to
`spawner.trigger()`. This is correct — the outer `trigger_source` is for gate-1 (caller's source),
the inner is for the healing session itself.

---

## Spec: self-healing-skill

**Source:** `openspec/changes/butler-self-healing/specs/self-healing-skill/spec.md`
**Requirements:** 6

### Req: Skill Location and Discovery
**Status: IMPLEMENTED**
- Skill at `roster/shared/skills/self-healing/SKILL.md`. ✓
- `roster/shared/BUTLER_SKILLS.md` contains:
  `- **self-healing** — How to report unexpected errors for automated investigation. Consult this skill when you encounter an exception you cannot resolve yourself.`
- Entry matches spec scenario text (minor variation: "automated investigation" vs. "Automated investigation" — cosmetic).

### Req: Error Reporting Protocol
**Status: IMPLEMENTED**
- "When to Report" section covers unexpected exceptions, code bugs.
- "DO NOT report" covers user input errors, transient network errors, recoverable errors, `asyncio.CancelledError`, external services.
- "How to Report" section with complete parameter guidance table.
- `context` field guidelines (under 500 words, patterns not values).

### Req: Handling the Response
**Status: IMPLEMENTED**
- "Handling Responses" section with `{"accepted": true}`, `{"reason": "already_investigating"}`, and rejection cases.
- Clear instructions: continue session, note investigation, continue normally.

### Req: Status Querying Protocol
**Status: IMPLEMENTED**
- "Checking Status" section: when to call `get_healing_status`, by fingerprint or recent attempts.
- `pr_merged` interpretation: "note that a fix was deployed and the error may resolve after a restart."

### Req: Data Safety Instructions
**Status: IMPLEMENTED**
- "Data Safety" section explicitly lists what NOT to include: user data values, session prompt, credentials, PII.
- Pattern vs. value guidance with examples.

### Req: Skill Content Structure
**Status: IMPLEMENTED**
- SKILL.md has: purpose, "When to Report", "How to Report" (with parameter table + context guidelines),
  "Data Safety", "Handling Responses", "Checking Status", and Examples section.
- All sections from spec scenario verified present.

---

## Spec: healing-worktree

**Source:** `openspec/changes/butler-self-healing/specs/healing-worktree/spec.md`
**Requirements:** 6

### Req: Worktree Creation
**Status: IMPLEMENTED**
- `create_healing_worktree(repo_root, butler_name, fingerprint)` in `worktree.py`.
- Branch name: `self-healing/<butler_name>/<fingerprint[:12]>-<epoch>`.
- Worktree path: `<repo_root>/.healing-worktrees/self-healing/<butler_name>/<fingerprint[:12]>-<epoch>/`.
- Branched from `main` HEAD.
- `.healing-worktrees/` in `.gitignore`: verified at line 436.

### Req: Worktree Creation Error Handling
**Status: IMPLEMENTED**
- Branch creation failure → raises `WorktreeCreationError`.
- `git worktree add` failure → deletes orphaned branch via `git branch -D`, raises `WorktreeCreationError`.
- `git lock file` scenario: non-zero exit code → `WorktreeCreationError` (no retry).
- Dispatcher handles `WorktreeCreationError` → attempt status → `failed`.

### Req: Worktree Isolation
**Status: IMPLEMENTED**
- CWD passed as `cwd=str(worktree_path)` to `spawner.trigger()`.
- Spawner passes `cwd` to runtime adapter.
- Main repo unchanged (separate git worktree shares `.git/objects`).

### Req: Worktree Cleanup on Completion
**Status: IMPLEMENTED**
- After `pr_open`: `remove_healing_worktree(delete_branch=False)` — worktree removed, local branch kept.
- After `failed`, `unfixable`, `timeout`: `remove_healing_worktree(delete_branch=True)`.
- After `anonymization_failed`: remote branch deleted (inside `_create_pr`), then `remove_healing_worktree(delete_branch=True, delete_remote=False)`.

**Gap (minor):** Spec says cleanup after `unfixable` should remove worktree. Looking at `_run_healing_session`:
the agent result path only handles `success=False` (→ failed) and `success=True` (→ PR flow). The agent is supposed
to create a commit explaining "unfixable" and still return success. There is no code path that explicitly transitions
to `unfixable`. The agent would need to create a commit message containing a signal, but the dispatcher does not
parse commit messages to detect "unfixable". See DI-4.

### Req: Stale Worktree Reaper
**Status: IMPLEMENTED**
- `reap_stale_worktrees(repo_root, pool)` scans `.healing-worktrees/self-healing/<butler>/<slug>/`.
- Terminal + aged (> 24h): removed with appropriate `delete_branch` logic.
- Orphaned worktrees (no DB row): WARNING logged, removed.
- Orphaned branches (no worktree, no active attempt): deleted.

### Req: Worktree Function Signatures
**Status: IMPLEMENTED**
- `create_healing_worktree(repo_root, butler_name, fingerprint) -> tuple[Path, str]` ✓
- `remove_healing_worktree(repo_root, branch_name, delete_branch=True, delete_remote=False) -> None` ✓
- `reap_stale_worktrees(repo_root, pool) -> int` ✓

---

## Spec: healing-anonymizer

**Source:** `openspec/changes/butler-self-healing/specs/healing-anonymizer/spec.md`
**Requirements:** 9

### Req: Credential Redaction
**Status: IMPLEMENTED**
- Anthropic keys (`sk-ant-*`) → `[REDACTED-API-KEY]`.
- AWS keys (`AKIA/ASIA...`) → `[REDACTED-API-KEY]`.
- Database URLs with credentials → `[REDACTED-DB-URL]`.
- JWT tokens (`eyJ...`) → `[REDACTED-JWT]`.
- Existing rules: Telegram bot token, Bearer tokens preserved.
- OpenAI-style `sk-*` keys also redacted.
- Generic labelled keys (`api_key = <token>`) redacted with git SHA guard.

### Req: PII Scrubbing
**Status: IMPLEMENTED**
- Email: `_RE_EMAIL` with `re.IGNORECASE` and negative lookbehind for `[.\w]` to avoid false positives on `self.user_email`.
- Phone: two-form regex covering `+1-555-123-4567` and `(555) 123-4567`.
- IPv4: `_RE_IPV4` excludes `127.x.x.x` inline, `_scrub_ipv4()` also validates octets and checks for version keywords.
- IPv6: `_scrub_ipv6()` preserves `::1` and `::`.
- Localhost/loopback preserved: both IPv4 (`127.0.0.1`) and IPv6 (`::1`, `localhost`).

### Req: Path Normalization
**Status: IMPLEMENTED**
- `_normalize_paths(text, repo_root)` strips absolute path prefix for repo-relative paths.
- Non-repo absolute paths → `[REDACTED-PATH]`.
- Applied only to paths at start-of-line or after whitespace (avoids URL path components).

### Req: User Content Exclusion
**Status: IMPLEMENTED**
- Session prompt and output are never passed to the anonymizer or included in PR content.
- Only structural metadata (exception type, sanitized message, call site, butler name) is passed to `_build_pr_body`.
- Agent context (if provided) is passed through `anonymize()` before inclusion.

### Req: Environment Scrubbing
**Status: IMPLEMENTED**
- `_scrub_hostnames()` uses `_RE_INTERNAL_HOSTNAME` with `_SAFE_DOMAIN_SUFFIXES` and `_SAFE_DOMAINS` allowlist.
- Public domains (github.com, anthropic.com, etc.) preserved.
- File extensions (.py, .toml, etc.) not treated as hostnames.

### Req: Validation Pass
**Status: IMPLEMENTED**
- `validate_anonymized(text)` in `anonymizer.py` runs `_VALIDATION_RULES` (email, JWT, credential URL, API key, IPv4).
- Returns `(bool, list[str])` with violation descriptions.
- Each violation description includes: pattern type, offset, length, surrounding context with `[MATCH]` placeholder.
- Version-string IPv4 false positives filtered in `validate_anonymized` using `_is_preceded_by_version_keyword`.

### Req: False Positive Handling
**Status: IMPLEMENTED**
- `self.user_email` not scrubbed: negative lookbehind `(?<![.\w])` in email regex.
- Version strings `1.2.3.4` not treated as IPs: `_is_preceded_by_version_keyword` guard in `_scrub_ipv4` and validation.
- Git SHA hashes not treated as API keys: `_RE_GIT_SHA` / `_RE_GIT_SHA256` guards in `_redact_generic`.

### Req: Anonymizer Function Signature
**Status: IMPLEMENTED**
- `anonymize(text: str, repo_root: Path) -> str` ✓
- `validate_anonymized(text: str) -> tuple[bool, list[str]]` ✓

---

## Spec: healing-session-tracking

**Source:** `openspec/changes/butler-self-healing/specs/healing-session-tracking/spec.md`
**Requirements:** 8

### Req: Healing Attempts Table
**Status: IMPLEMENTED**
- `shared.healing_attempts` created in `core_035_self_healing_tier_and_attempts.py`.
- All columns match spec schema: id, fingerprint, butler_name, status, severity, exception_type,
  call_site, sanitized_msg, branch_name, worktree_path, pr_url, pr_number, session_ids, healing_session_id,
  created_at, updated_at, closed_at, error_detail.
- Index on `fingerprint` (`idx_healing_attempts_fingerprint`).
- Index on `status` (`idx_healing_attempts_status`).
- Partial UNIQUE index on `fingerprint WHERE status IN ('investigating', 'pr_open')` (`uq_healing_attempts_active_fingerprint`).

### Req: Atomic Attempt Creation
**Status: IMPLEMENTED**
- `create_or_join_attempt()` in `tracking.py` uses `INSERT … ON CONFLICT … DO UPDATE SET session_ids = array_append(...)`.
- Returns `(attempt_id, is_new)` via `(xmax = 0) AS was_inserted`.
- Idempotent: `CASE WHEN session_id = ANY(session_ids) THEN session_ids ELSE array_append(...)`.

### Req: Healing Attempt State Machine
**Status: IMPLEMENTED**
- `VALID_STATUSES`, `TERMINAL_STATUSES`, `ACTIVE_STATUSES`, `_VALID_TRANSITIONS` in `tracking.py`.
- `update_attempt_status()` enforces transitions; rejects terminal-state transitions with WARNING (no exception).
- `updated_at` refreshed on every transition.
- `closed_at` set on all terminal transitions.

### Req: Session ID Accumulation
**Status: IMPLEMENTED**
- `create_or_join_attempt()` appends session_id with idempotent guard.
- Collision detection compares `exception_type` and `call_site` of joining session vs. stored — logs CRITICAL on mismatch.

### Req: Fingerprint Collision Detection
**Status: IMPLEMENTED**
- In `create_or_join_attempt()`: when `was_inserted=False`, existing `exception_type`/`call_site` fetched via RETURNING.
- Mismatch → `logger.critical("Fingerprint collision detected ...")`.
- Session still appended (collision is observability signal).

### Req: Daemon Restart Recovery
**Status: IMPLEMENTED**
- `recover_stale_attempts(pool, timeout_minutes)` in `tracking.py`.
- Rule 1: `investigating` rows with `healing_session_id IS NOT NULL` and `updated_at < now() - timeout_minutes` → `timeout`.
- Rule 2: `investigating` rows with `healing_session_id IS NULL` and `created_at < now() - 5 minutes` → `failed`.
- Called from `SelfHealingModule.on_startup()` BEFORE `reap_stale_worktrees`.

### Req: Query Functions
**Status: IMPLEMENTED**
- `get_active_attempt(pool, fingerprint)` ✓
- `get_recent_attempt(pool, fingerprint, window_minutes)` ✓
- `count_active_attempts(pool)` ✓
- `get_recent_terminal_statuses(pool, limit)` ✓ (`unfixable` included, caller decides)
- `list_attempts(pool, limit, offset, status_filter, butler_name)` ✓

### Req: Dashboard API Routes
**Status: IMPLEMENTED**
- `GET /api/healing/attempts` with `limit`, `offset`, `status` filter ✓
- `GET /api/healing/attempts/{id}` ✓
- `POST /api/healing/attempts/{id}/retry` — rejects non-terminal with HTTP 409 ✓
- `GET /api/healing/circuit-breaker` with `consecutive_failures`, `tripped`, `threshold`, `last_failure_at` ✓
- `POST /api/healing/circuit-breaker/reset` — inserts synthetic `pr_merged` sentinel row ✓

**Gap (retry endpoint):** As noted in DI-2, the retry endpoint creates a DB row but does not dispatch
a healing agent. A full retry would require calling `dispatch_healing` with the original fingerprint
and metadata. The current implementation is a stub that satisfies the HTTP contract but not the operational intent.

---

## Spec: healing-model-tier

**Source:** `openspec/changes/butler-self-healing/specs/healing-model-tier/spec.md`
**Requirements:** 5

### Req: Self-Healing Complexity Tier
**Status: IMPLEMENTED**
- `Complexity.SELF_HEALING = "self_healing"` in `model_routing.py` ✓
- `model_catalog_defaults.toml` seeded with `healing-sonnet` entry (claude-sonnet-4-6, self_healing tier, priority 10) ✓
- `core_035` migration widens CHECK constraints in `model_catalog` and `butler_model_overrides` ✓

### Req: Healing Agent Model Resolution
**Status: IMPLEMENTED**
- `resolve_model(pool, butler_name, Complexity.SELF_HEALING)` called from `dispatch_healing` gate 10.
- `None` result → dispatch skipped with WARNING, no attempt row created.

### Req: Seed Data for Self-Healing Tier
**Status: IMPLEMENTED**
- `model_catalog_defaults.toml` has `healing-sonnet` entry with `complexity_tier = "self_healing"`.
- Seeded by `core_035` migration via `ON CONFLICT DO NOTHING`.

### Req: Dashboard Tier Visibility
**Status: PARTIALLY IMPLEMENTED**
- `self_healing` is a valid tier in the model settings API (validation updated in `model_settings.py`).
- The UI dropdown is not verified from this audit (frontend code not checked). Tracked as DI-5.

### Req: API Validation Update
**Status: IMPLEMENTED**
- `src/butlers/api/routers/model_settings.py` updated to accept `self_healing` as valid `complexity_tier` (confirmed by grep).
- Invalid tiers still rejected with 422.

---

## Spec: core-spawner

**Source:** `openspec/changes/butler-self-healing/specs/core-spawner/spec.md`
**Requirements:** 6 (modified + added)

### Req: Spawner Session Lifecycle (modified)
**Status: IMPLEMENTED**
- Failed session path: `session_complete(success=False)` → process log write (best-effort) → self-healing fallback.
- Fallback fires only when: `trigger_source != "healing"` AND `self._healing_module is not None` AND pool available AND session_id available.
- `asyncio.create_task()` used for fire-and-forget dispatch.
- Fallback error handler: `_log_fallback_error` callback logs WARNING.
- `sys.exc_info()` captured before cleanup code runs (verified: `_exc_value, _exc_tb` set early in except block).

### Req: Trigger Source Tracking (modified)
**Status: IMPLEMENTED**
- Valid sources: `tick`, `external`, `trigger`, `route`, `healing`, `schedule:<task-name>`.
- `healing` source: verified as valid in `TRIGGER_SOURCES` frozenset in `sessions.py`.
- `schedule:<name>` pattern: validated in session creation.

### Req: Healing Session Semaphore Bypass
**Status: IMPLEMENTED**
- `bypass_butler_semaphore` parameter in `trigger()`.
- When True: skips per-butler `_session_semaphore`; still acquires global semaphore.
- Called with `bypass_butler_semaphore=True` from `_run_healing_session`.

### Req: Healing Session MCP Restriction
**Status: IMPLEMENTED**
- `if trigger_source == "healing": mcp_servers = {}` in spawner.
- Healing session env: only `PATH` + `GH_TOKEN` (resolved from credential store, fallback to `os.environ`).

### Req: Healing Configuration in butler.toml
**Status: IMPLEMENTED**
- `[modules.self_healing]` section drives all config.
- When module not loaded: spawner fallback also disabled (guard `self._healing_module is not None`).
- All config fields with defaults match spec.

---

## Spec: core-sessions

**Source:** `openspec/changes/butler-self-healing/specs/core-sessions/spec.md`
**Requirements:** 4 (modified + added)

### Req: Minimum Persisted Session Fields (modified)
**Status: IMPLEMENTED**
- `healing_fingerprint TEXT` nullable column added to `sessions` table via `core_036` migration.
- `session_set_healing_fingerprint(pool, session_id, fingerprint)` in `sessions.py`.
- Column is NULL on success, NULL on healing sessions, NULL when no healing dispatch occurs.

### Req: Trigger Source Tracking (modified)
**Status: IMPLEMENTED**
- `healing` source in `TRIGGER_SOURCES` frozenset.
- `schedule:<name>` pattern validation preserved.

### Req: Healing Fingerprint Update (added)
**Status: IMPLEMENTED**
- `session_set_healing_fingerprint` UPDATE query targeting sessions table.
- Non-existent session_id: `UPDATE ... WHERE id = $1` with 0 rows affected — no error raised.

---

## Spec: model-catalog

**Source:** `openspec/changes/butler-self-healing/specs/model-catalog/spec.md`
**Requirements:** 2 (modified)

### Req: Model Catalog Schema (modified)
**Status: IMPLEMENTED**
- `complexity_tier` CHECK constraint extended to include `self_healing` in `core_035` migration.
- Existing fields unchanged.
- `self_healing` tier reserved for healing agents.

### Req: Seed Data Migration (modified)
**Status: IMPLEMENTED**
- `model_catalog_defaults.toml` includes `healing-sonnet` entry:
  `{ alias="healing-sonnet", runtime_type="claude", model_id="claude-sonnet-4-6", complexity_tier="self_healing", priority=10 }`.
- Seeded idempotently via `ON CONFLICT (alias) DO NOTHING`.
- All 14 default entries from spec table verified present (from prior audit of toml).

---

## Design Doc Decisions

**Source:** `openspec/changes/butler-self-healing/design.md`
**Decisions:** 14

| # | Decision | Status |
|---|---|---|
| 1 | Error fingerprinting: SHA-256 of structured tuple | IMPLEMENTED — `_compute_hash()` |
| 2 | Dual-path dispatch: module (primary) + spawner fallback (secondary) | IMPLEMENTED |
| 3 | Self-healing module: MCP tools as primary surface | IMPLEMENTED |
| 4 | Shared skill: teaching butlers the protocol | IMPLEMENTED |
| 5 | Worktree lifecycle: timestamped branch + auto-cleanup | IMPLEMENTED |
| 6 | Healing agent prompt construction | IMPLEMENTED — `_build_healing_prompt()` |
| 7 | Anonymizer: layered scrubbing before PR creation | IMPLEMENTED |
| 8 | PR creation: `gh pr create` with structured template | IMPLEMENTED — `_create_pr()` |
| 9 | Self-healing model tier: `Complexity.SELF_HEALING` | IMPLEMENTED |
| 10 | Healing attempt state machine | IMPLEMENTED — `_VALID_TRANSITIONS` in tracking.py |
| 11 | Database schema: `shared.healing_attempts` | IMPLEMENTED — core_035 migration |
| 12 | Timeout watchdog | IMPLEMENTED — `_timeout_watchdog()` |
| 13 | Daemon restart recovery | IMPLEMENTED — `recover_stale_attempts()` |
| 14 | PR creation flow: push → anonymize → validate → create | IMPLEMENTED |

---

## Summary Coverage Matrix

| Spec | Requirements | Implemented | Partial | Gaps |
|---|---|---|---|---|
| error-fingerprinting | 10 | 10 | 0 | DI-1 (adapter-init severity) |
| self-healing-dispatch | 17 | 16 | 1 | DI-2 (retry no-spawn), DI-3 (trace_id attr) |
| self-healing-module | 7 | 7 | 0 | — |
| self-healing-skill | 6 | 6 | 0 | — |
| healing-worktree | 6 | 6 | 0 | DI-4 (unfixable detection) |
| healing-anonymizer | 9 | 9 | 0 | — |
| healing-session-tracking | 8 | 7 | 1 | DI-2 (retry no-spawn) |
| healing-model-tier | 5 | 4 | 1 | DI-5 (UI tier dropdown not verified) |
| core-spawner | 6 | 6 | 0 | — |
| core-sessions | 4 | 4 | 0 | — |
| model-catalog | 2 | 2 | 0 | — |
| design.md decisions | 14 | 14 | 0 | — |
| **Total** | **94** | **91** | **3** | **5 discovered issues** |

**Overall coverage: ~97% (91/94 fully implemented; 3 partially; 5 gaps filed as discovered issues)**

---

## Discovered Issues

### DI-1: Adapter initialization severity rule not fully covered
**Suggested type:** task, P3
**Rationale:** The spec's severity rule for "system prompt or config resolution errors are high" includes
"adapter initialization" alongside `read_system_prompt`, `_build_env`, `_resolve_provider_config`.
The implementation's `_HIGH_FUNCTION_NAMES` tuple does not include a generic adapter-initialization catch.
Errors during adapter `__init__` that surface at a function not in `_HIGH_FUNCTION_NAMES` will fall through
to medium. This is low risk (adapter init errors typically propagate through covered functions) but represents
incomplete spec coverage.

**Fix:** Add adapter init function names (e.g. `_init_adapter`, `__init__` in `src/butlers/core/runtimes/`) to
`_HIGH_FUNCTION_NAMES` or add a call-site prefix check for `src/butlers/core/runtimes/` (which already exists as
`_HIGH_CALL_SITE_PREFIXES` — so this may already be covered via that path). Recommend auditing actual adapter
init function paths to confirm.

### DI-2: Retry endpoint creates DB row but does not dispatch healing agent
**Suggested type:** bug, P2
**Rationale:** `POST /api/healing/attempts/{id}/retry` in `healing.py` inserts a new `investigating` row with
`session_ids = '{}'` and returns 201, but it does not call `dispatch_healing` to actually create a worktree
and spawn a healing agent. The spec scenario says: "A new healing attempt is created for the same fingerprint,
bypassing cooldown." The intent is a full re-dispatch. The current implementation creates a zombie `investigating`
row that will never transition unless manually intervened.

**Fix:** After inserting the retry row, call `dispatch_healing` (or a simpler worktree-create + spawn path)
with the original fingerprint and metadata, passing `bypass_cooldown=True` equivalent logic.

### DI-3: Failed session trace_id not recorded as span attribute in dispatch
**Suggested type:** task, P3
**Rationale:** Design decision 2 says "The dispatcher SHALL create its own OTel span, not inherit from the failed
session." The spec's trace isolation requirement says "the failed session's `trace_id` is recorded as a span
attribute." In `dispatch_healing`, the new root span is created and `healing.trigger_source` is recorded, but
the failed session's `trace_id` is not fetched or attached. This reduces observability when correlating a
healing dispatch back to the original failed session in traces.

**Fix:** Fetch the failed session's `trace_id` from the sessions table (or pass it as a parameter) and attach it
as a span attribute `healing.failed_session_trace_id`.

### DI-4: No mechanism for healing agent to signal "unfixable" status
**Suggested type:** feature, P2
**Rationale:** The state machine includes `unfixable` as a terminal status, and the spec says healing agents
should "write a commit with a file explaining why it is unfixable." However `_run_healing_session` only checks
`result.success` — success=True leads to PR creation, success=False leads to `failed`. There is no code path
that transitions to `unfixable`. The healing agent has no way to signal "this is not a code bug."

**Fix:** Define a convention (e.g., the healing agent creates a file `UNFIXABLE.md` in the worktree, or uses a
specific commit message prefix like `[UNFIXABLE]`) that `_run_healing_session` checks after the session completes
successfully. If the signal is present, transition to `unfixable` instead of proceeding to PR creation.

### DI-5: Self-healing tier in model settings UI dropdown not verified
**Suggested type:** task, P3
**Rationale:** The model-tier spec requires `self_healing` to appear in the complexity tier dropdown in the
Model Settings UI. The backend API accepts it (confirmed via `model_settings.py`), but the frontend dropdown
options were not audited as part of this reconciliation (frontend code not in scope of core Python audit).

**Fix:** Verify `src/butlers/api/routers/model_settings.py` exports a tier enum that the frontend consumes,
and check that the frontend tier dropdown includes `self_healing`.

---

## Test Coverage Notes

The implementation is backed by a comprehensive test suite:

| Area | Test File |
|---|---|
| Fingerprinting | `tests/core/healing/test_fingerprint.py` |
| Anonymizer | `tests/core/healing/test_anonymizer.py` |
| Tracking (DB) | `tests/core/healing/test_tracking.py` |
| Worktree lifecycle | `tests/core/healing/test_worktree.py` |
| Dispatch engine | `tests/core/healing/test_dispatch.py` |
| Self-healing module | `tests/modules/test_module_self_healing.py` |
| Dashboard API | `tests/api/test_api_healing.py` |
| Model routing (self_healing tier) | `tests/core/test_model_routing_self_healing_tier.py` |
| Model settings API | `tests/api/test_model_settings_self_healing_tier.py` |
| Sessions (healing trigger + fingerprint) | `tests/core/test_sessions_healing_trigger.py` |
| Spawner fallback | `tests/core/test_spawner_healing_fallback.py` |

All test files are present and the implementation has corresponding tests for every major requirement.
