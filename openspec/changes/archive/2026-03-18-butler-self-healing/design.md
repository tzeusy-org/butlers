## Context

When a butler session fails today, the spawner's `_run()` except block (spawner.py:1155–1237) captures the exception, writes a session record with `success=False`, stores process-level diagnostics (stderr, exit code), writes an audit entry, resets the runtime, and returns a `SpawnerResult` with the error. That's the end of the line — no automated follow-up occurs.

The butler daemon already runs on the same machine as the source code. The spawner already knows how to resolve models via the catalog, spawn LLM CLI sessions, and capture their output. Git worktrees are cheap. The missing piece is a post-error decision layer that connects "session failed" to "investigate and propose a fix."

**Dual-path architecture:** The self-healing surface is exposed to runtime butlers via a new MCP module (`modules/self_healing/`) that registers tools on every butler's MCP server. This is the **primary** entry point — the butler agent recognizes errors during its session and calls `report_error` with structured context and its own diagnostic reasoning. A **secondary** fallback in the spawner's except block catches hard crashes where the agent never got a chance to self-report. Both paths converge on the same dispatch engine in `src/butlers/core/healing/`.

Key existing patterns this design extends:
- **Module system**: `Module` ABC with `register_tools()`, `on_startup()`, `on_shutdown()` — self-healing is a module like any other
- **Shared skills**: `roster/shared/skills/` + `BUTLER_SKILLS.md` — teaches all butlers the error reporting protocol
- **Ingestion events `dedupe_key`**: UNIQUE constraint deduplication for error fingerprinting
- **`CredentialRedactionFilter`**: Regex-based scrubbing for the anonymizer
- **`resolve_model()` with `Complexity` enum**: Model tier routing for the self-healing tier
- **`_merge_tool_call_records()` signature hashing**: Deterministic fingerprint approach

## Goals / Non-Goals

**Goals:**
- Automatically investigate session failures and propose code fixes via PR — no human needed to start the loop
- Deduplicate: same root cause → at most one active investigation at a time
- Isolate: healing agents never touch the main working tree or merge into `main`
- Anonymize: zero PII, user data, or credentials in any PR content on the public repo
- Control cost: dedicated model tier, rate limits, circuit breaker, per-butler opt-in
- Observable: dashboard visibility into healing attempts, their status, and outcomes

**Non-Goals:**
- Auto-merging PRs (humans review and merge)
- Fixing errors in user data or external services (only code/config bugs)
- Healing errors from the healing agents themselves (no recursive self-healing — one level deep)
- Real-time error response (healing is async, fire-and-forget after the failed session returns)
- Replacing proper testing or CI (healing PRs still go through normal review)

## Decisions

### 1. Error Fingerprinting: SHA-256 of structured tuple

**Decision:** Fingerprint = `SHA-256(exception_type + "||" + call_site + "||" + sanitized_message_pattern)`.

- `exception_type`: Fully qualified class name (e.g. `asyncpg.exceptions.UndefinedTableError`)
- `call_site`: File path (relative to repo root) + function name from the innermost non-stdlib frame in the traceback. Not line number — line numbers change across commits.
- `sanitized_message_pattern`: Error message with dynamic values (UUIDs, timestamps, table names, numeric IDs) replaced by placeholders (`<UUID>`, `<TS>`, `<ID>`). This collapses "relation foo_123 does not exist" and "relation foo_456 does not exist" into the same fingerprint.

**Why not just exception type?** Too coarse — `KeyError` from two different call sites are unrelated bugs. Why not include line numbers? Too brittle — any code change shifts line numbers and creates "new" fingerprints for the same bug.

**Alternatives considered:**
- Full stack trace hash: too brittle (line number changes)
- Exception type only: too coarse (same type, different bugs)
- ML-based clustering: over-engineered for v1, revisit if fingerprint collision rate is high

### 2. Dual-path dispatch: module (primary) + spawner fallback (secondary)

**Decision:** Self-healing has two entry points that share the same dispatch engine:

**Primary path — Self-healing MCP module:**
- A new `modules/self_healing/` module registers `report_error` and `get_healing_status` tools on every butler's MCP server
- When a butler agent encounters an error, it calls `report_error` with structured context: error type, message, traceback, call site, and — critically — its own diagnostic reasoning about what went wrong
- The tool handler computes a fingerprint from the structured input, runs gate checks, and dispatches a healing agent
- The tool returns immediately with an accept/reject status; the healing agent spawns asynchronously
- A shared skill (`roster/shared/skills/self-healing/`) teaches all butlers the protocol

**Secondary path — Spawner fallback:**
- A lightweight hook in the spawner's except block catches hard crashes (OOM, process kill, adapter timeout) where the agent never got to call `report_error`
- Uses `compute_fingerprint(exc, tb)` to extract structured fields from the raw exception
- Runs the same gate checks and dispatch logic as the module path
- No agent reasoning available (the agent is dead)

**Why two paths?** The module path is architecturally superior — the butler agent has richer context (its reasoning, what it was trying to do, which tool failed). But hard crashes bypass the agent entirely. The spawner fallback ensures no error goes unobserved.

**Why not spawner-only?** Three reasons: (1) The agent has richer context — "I was trying to send an email notification for a birthday reminder and the SMTP tool returned a connection error, which suggests the SMTP credentials may have rotated" is far more useful to a healing agent than a bare `smtplib.SMTPAuthenticationError` + traceback. (2) The agent can report "soft" errors — tool call failures that return error responses without raising exceptions to the spawner level. (3) Clean module separation — the spawner stays lean, self-healing is opt-in via `[modules.self_healing]`.

**Deduplication across paths:** If the agent reports an error via `report_error` and then the session crashes with the same exception, the spawner fallback fires too. The partial unique index on `healing_attempts` ensures only one investigation exists — the second path sees the active attempt and appends its session ID instead.

The shared gate sequence (both paths):

1. **No-recursion guard**: Reject if `trigger_source == "healing"`
2. **Opt-in gate**: Module loaded and enabled
3. **Fingerprint computation**: Compute or accept pre-computed fingerprint
4. **Fingerprint persistence**: Update session record (best-effort)
5. **Severity gate**: Severity ≤ threshold (lower = more severe)
6. **Novelty gate**: No active attempt for this fingerprint (atomic check+insert)
7. **Cooldown gate**: No recent terminal attempt within cooldown window
8. **Concurrency cap**: Active investigations < `max_concurrent`
9. **Circuit breaker**: Not tripped by consecutive failures
10. **Model resolution**: `resolve_model(butler_name, "self_healing")` succeeds

If all gates pass → create `healing_attempts` row atomically → create worktree → spawn healing agent → start timeout watchdog.

**Semaphore behavior:** Healing sessions bypass the per-butler semaphore but still acquire the global semaphore. This is essential for the module path where the calling session is still active and holding the per-butler semaphore.

### 3. Self-healing module: MCP tools as the primary surface

**Decision:** The self-healing module (`src/butlers/modules/self_healing/`) implements the `Module` ABC and registers two MCP tools:

- **`report_error`**: Primary entry point. Butler agent calls this with structured error context + its own diagnostic reasoning. Returns immediately with `{accepted, fingerprint, reason}`. Dispatch happens async.
- **`get_healing_status`**: Query tool. Butler agent can check if its error is being investigated, or see recent attempts.

The module delegates all logic to `src/butlers/core/healing/` — it's a thin MCP wrapper. Config lives in `[modules.self_healing]` in `butler.toml`, consistent with how all modules are configured.

**Why a module instead of a core spawner feature?** Modules are the framework's extension point for adding domain-specific tools. Self-healing is conceptually a tool a butler can use, not a core spawner responsibility. This also makes it opt-in per butler — just add/remove `[modules.self_healing]` from `butler.toml`.

### 4. Shared skill: teaching butlers the protocol

**Decision:** A skill at `roster/shared/skills/self-healing/SKILL.md` teaches all butlers:
- **When** to call `report_error` (unexpected exceptions, code bugs — not user input errors or transient network blips)
- **How** to call it (what to include in each field, especially `context` with diagnostic reasoning)
- **What NOT to include** (user data, PII, credentials — describe patterns, not values)
- **How to interpret** the response (accepted → continue, deduplicated → already being investigated, rejected → system decided not to investigate)

Referenced from `roster/shared/BUTLER_SKILLS.md` so every butler gets it automatically.

**Why a skill and not just tool documentation?** Skills are prompt-injected — they shape the agent's behavior during the session. Without the skill, a butler would need to independently reason about when to call a mysterious `report_error` tool. The skill ensures consistent, correct behavior across all butlers.

### 5. Worktree lifecycle: timestamped branch + auto-cleanup

**Decision:** Each healing attempt gets its own worktree:
- Branch: `self-healing/<butler-name>/<fingerprint-short>-<epoch>` (fingerprint-short = first 12 hex chars)
- Worktree path: `<repo-root>/.healing-worktrees/<branch-name>/`
- Created via `git worktree add`; branched from current `main` HEAD
- Cleaned up via `git worktree remove` + `git branch -d` after healing completes (success or failure)
- Stale worktree reaper: on dispatcher startup, remove any worktree whose `healing_attempts` row is terminal (`pr_open`, `pr_merged`, `failed`, `unfixable`) and older than 24h

**Why not reuse the main working tree?** The daemon's main checkout may be mid-session or have uncommitted state. Worktrees provide zero-conflict isolation. They're lightweight (shared `.git/objects`), and `git worktree add` is atomic.

**Why not Docker containers?** Unnecessary overhead — worktrees are lighter, faster to create/destroy, and the healing agent needs access to the same Python environment and `uv` installation.

### 6. Healing agent prompt construction

**Decision:** The healing agent is spawned as a regular LLM CLI session via the existing spawner infrastructure, but with:
- **Complexity tier**: `self-healing` (resolved via `resolve_model()`)
- **CWD**: The worktree path (NOT the main repo checkout)
- **System prompt**: A dedicated healing prompt that includes:
  - The error fingerprint and severity
  - The exception type, sanitized message, and sanitized traceback
  - The session ID and trigger source (but NOT the session prompt or output — those may contain user data)
  - The butler name and module context
  - **Agent diagnostic context** (module path only): The reporting butler's reasoning about what went wrong, what it was trying to do, and what might fix it. Anonymized before inclusion.
  - Instructions: investigate the root cause, write a fix, write/update tests, commit to the worktree branch
- **Trigger source**: `healing` (new value, distinguishable from `tick`/`external`/`schedule`)
- **No recursive healing**: Sessions with `trigger_source=healing` never trigger the healing dispatcher, even on failure

The healing agent has full access to the codebase (via the worktree), `git`, `uv`, `pytest`, `ruff`, and `gh` CLI — the standard development tools. It does NOT get access to the butler's MCP tools (empty `mcp_servers` config), database, or runtime state. The only credential passed is `GH_TOKEN` for PR creation.

**Module vs. fallback prompt quality:** When the error comes via `report_error`, the healing agent gets the butler's own analysis ("I was trying to send a birthday notification email and the SMTP auth failed — likely credential rotation"). When it comes via the spawner fallback, it gets only the bare exception and traceback. The healing agent is instructed to expect either form.

### 7. Anonymizer: layered scrubbing before PR creation

**Decision:** The anonymizer is a pipeline of regex and structural transforms applied to all text before it's included in a PR title, body, or commit message:

1. **Credential redaction**: Extend existing `_REDACTION_RULES` with patterns for API keys, database URLs, AWS keys, JWT tokens
2. **PII scrubbing**: Email addresses, phone numbers, IP addresses → `[REDACTED-EMAIL]`, `[REDACTED-PHONE]`, `[REDACTED-IP]`
3. **Path normalization**: Absolute paths → relative-to-repo paths (strips `/home/<user>/...` prefixes)
4. **User content removal**: Session prompt and output are NEVER included in PR content. Only the exception type, sanitized message, call site, and butler name are included.
5. **Environment scrubbing**: Environment variable values, hostnames, database names → placeholders

The anonymizer runs as a mandatory transform. The PR creation function accepts only pre-anonymized content — there is no code path that bypasses it.

**Validation step:** After anonymization, a second pass scans for residual patterns (anything matching email/IP/JWT/URL-with-credentials regexes). If any are found, the PR is NOT created and the healing attempt is marked `anonymization_failed`.

### 8. PR creation: `gh pr create` with structured template

**Decision:** Healing agents create PRs via `gh pr create` (GitHub CLI, already available on the machine). The PR body follows a fixed template:

```
## Self-Healing Fix: <fingerprint-short>

**Butler:** <butler-name>
**Error:** <exception-type>
**Call site:** <file:function>
**First seen:** <timestamp>
**Occurrences:** <count of sessions with this fingerprint>

### Root Cause
<agent's analysis — anonymized>

### Fix Summary
<agent's description of the fix>

### Test Coverage
<what tests were added or modified>

---
*Automated fix proposed by butler self-healing. Review carefully before merging.*
*Fingerprint: `<full-fingerprint>`*
```

The PR is created with labels `self-healing` and `automated` for easy filtering. The branch targets `main`.

### 9. Self-healing model tier: new `Complexity.SELF_HEALING` enum value

**Decision:** Add `SELF_HEALING = "self_healing"` to the `Complexity` enum in `model_routing.py`. The DB migration extends the CHECK constraint. `model_catalog_defaults.toml` seeds a reasonable default (e.g. Sonnet at priority 10 — capable enough for code investigation, cheaper than Opus).

**Why a dedicated tier instead of reusing `high`?**
- Cost isolation: operators can assign a cheaper model to healing without affecting production butler work
- Capability tuning: healing tasks may benefit from different models than normal butler tasks (e.g. a model better at code analysis)
- Observability: `sessions.complexity = 'self_healing'` makes healing sessions instantly filterable
- Kill switch: disabling all models in the self-healing tier effectively disables healing without touching `butler.toml`

Per-butler overrides work naturally — a butler can override the self-healing tier to use a different model than the global default.

### 10. Healing attempt state machine

```
[new] → investigating → pr_open → pr_merged
                      ↘ failed
                      ↘ unfixable
                      ↘ anonymization_failed
                      ↘ timeout
```

- `investigating`: Agent spawned, worktree created, work in progress
- `pr_open`: Agent completed, PR created, awaiting human review
- `failed`: Agent errored or produced no viable fix
- `unfixable`: Agent determined the error is not a code bug (external service, data issue, etc.)
- `anonymization_failed`: Fix was produced but PR blocked by anonymization validation
- `timeout`: Agent exceeded time limit (configurable, default: 30 min)
- `pr_merged`: PR was merged (updated by webhook or polling)

### 11. Database schema

**New table: `shared.healing_attempts`**
```sql
CREATE TABLE shared.healing_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fingerprint     TEXT NOT NULL,           -- SHA-256 hex
    butler_name     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'investigating',
    severity        INTEGER NOT NULL,        -- 0=critical .. 4=low
    exception_type  TEXT NOT NULL,
    call_site       TEXT NOT NULL,           -- file:function
    sanitized_msg   TEXT,                    -- pattern with placeholders
    branch_name     TEXT,
    worktree_path   TEXT,
    pr_url          TEXT,
    pr_number       INTEGER,
    session_ids     UUID[] NOT NULL DEFAULT '{}',  -- failed sessions that share this fingerprint
    healing_session_id UUID,                 -- the session that ran the healing agent
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    error_detail    TEXT                     -- why it failed/timed out (anonymized)
);

CREATE INDEX idx_healing_fingerprint ON shared.healing_attempts(fingerprint);
CREATE INDEX idx_healing_status ON shared.healing_attempts(status);

-- Prevents duplicate active investigations for the same fingerprint (race condition guard)
CREATE UNIQUE INDEX idx_healing_active_fingerprint
    ON shared.healing_attempts(fingerprint)
    WHERE status IN ('investigating', 'pr_open');
```

**Modified table: per-butler `sessions`**
- Add `healing_fingerprint TEXT` column — populated on failed sessions after fingerprinting. NULL for successful sessions and healing sessions themselves.

### 12. Timeout watchdog

**Decision:** The dispatcher creates an `asyncio.Task` watchdog alongside each healing agent session. The watchdog sleeps for `timeout_minutes` (default: 30, configurable in `[healing]`), then cancels the healing session if still running. On timeout: attempt status → `timeout`, worktree cleaned up, session killed.

**Why not rely on the spawner's existing timeout?** The spawner doesn't have per-session timeouts — sessions run until the LLM CLI exits. Healing sessions need a hard cap because a confused agent could loop indefinitely.

### 13. Daemon restart recovery

**Decision:** On dispatcher startup (daemon boot), before accepting new errors:

1. **Recover stale attempts**: Scan `healing_attempts` rows with status `investigating` and `updated_at` older than `timeout_minutes`. Transition to `timeout` (agent was interrupted). Rows with `healing_session_id = NULL` and `created_at` > 5 minutes transition to `failed` (agent was never spawned).
2. **Reap stale worktrees**: Run the worktree reaper — removes terminal worktrees > 24h old AND orphaned worktrees with no matching attempt row AND orphaned `self-healing/*/` branches.
3. **Begin accepting dispatches**: Only after recovery completes.

**Why recovery before dispatch?** Without recovery, stale `investigating` rows block the novelty gate and concurrency cap forever. The daemon would never heal the same fingerprint again after a crash.

### 14. PR creation flow

**Decision:** After the healing agent completes and commits fixes:

1. `git push origin <branch>` from the worktree
2. Construct PR title + body from structured template
3. Run `anonymize()` on all text content
4. Run `validate_anonymized()` — if violations found: delete remote branch (`git push origin --delete <branch>`), mark attempt `anonymization_failed`
5. `gh pr create --title ... --body ... --label self-healing --label automated`
6. Update attempt: status → `pr_open`, store `pr_url` and `pr_number`

The `GH_TOKEN` is resolved from the credential store and passed to the healing agent's env. No other butler credentials are included.

## Risks / Trade-offs

**[Cost runaway]** → Mitigation: dedicated model tier (operator picks the model), concurrency cap (default 2), per-fingerprint cooldown (default 60 min), circuit breaker (5 consecutive failures → stop). Dashboard kill switch.

**[Noisy PRs]** → Mitigation: severity threshold (only investigate errors above a configurable level). Start conservative — default threshold set high so only repeated/critical errors trigger healing. Operators tune down as they gain trust.

**[PII leak in PR]** → Mitigation: layered anonymizer with validation pass. Session prompt/output never included. Anonymization failure blocks PR creation entirely. This is the hardest correctness requirement.

**[Healing agent makes things worse]** → Mitigation: worktree isolation (can't corrupt main), PR-only (can't merge), human review required. Healing agents don't have access to production DB or MCP tools.

**[Stale worktrees accumulate]** → Mitigation: auto-reaper on dispatcher startup cleans worktrees for terminal healing attempts older than 24h.

**[Fingerprint collision]** → Mitigation: SHA-256 on a structured tuple is unlikely to collide. If collision rate is observed to be high (same fingerprint, different bugs), the call_site component can be expanded to include more stack frames.

**[Recursive healing loop]** → Mitigation: hard rule — `trigger_source=healing` sessions never enter the healing dispatcher. One level deep, period.

**[Daemon crash mid-investigation]** → Mitigation: startup recovery scans stale `investigating` rows older than timeout, transitions them to `timeout`/`failed`, and reaps their worktrees. Partial unique index prevents ghost rows from blocking future investigations.

**[Race condition on concurrent dispatch]** → Mitigation: partial unique index `ON (fingerprint) WHERE status IN ('investigating', 'pr_open')` makes the novelty check + insert atomic at the DB level. Losers get `ON CONFLICT DO UPDATE` to append their session ID instead.

**[Anonymizer false positives]** → Mitigation: carve-outs for code identifiers (variable names), version strings, git SHAs, localhost/loopback IPs. Validation pass is strict, but scrubbing rules avoid known false positive patterns.

**[Git push failure after investigation]** → Mitigation: push failure transitions attempt to `failed` with error detail. Worktree and branch are cleaned up. The error is not re-investigated until cooldown expires.

## Migration Plan

1. **DB migration**: Add `shared.healing_attempts` table, add `healing_fingerprint` column to per-butler sessions tables, extend `complexity_tier` CHECK constraint
2. **Seed data**: Add `self-healing` tier entries to `model_catalog_defaults.toml`
3. **Core package**: Ship `src/butlers/core/healing/` package (fingerprinting, dispatch, worktree, anonymizer, tracking)
4. **Module**: Ship `src/butlers/modules/self_healing/` module with `report_error` and `get_healing_status` MCP tools
5. **Skill**: Ship `roster/shared/skills/self-healing/SKILL.md` and update `roster/shared/BUTLER_SKILLS.md`
6. **Spawner fallback**: Wire fallback dispatcher hook into spawner except block (fires only if module is loaded)
7. **Dashboard**: Add healing attempts list/detail views. Add `self-healing` to tier dropdown in model settings
8. **Rollback**: Remove `[modules.self_healing]` from `butler.toml`. Module not loaded → tools not registered → spawner fallback also disabled → healing package becomes dead code. Migration is additive — no destructive rollback needed

## Open Questions

1. **Worktree cleanup timing**: Should we clean up the worktree immediately after PR creation, or keep it alive until the PR is merged/closed? Keeping it alive allows the agent to respond to PR review comments, but consumes disk space.
2. **Multi-session correlation**: Should the dispatcher wait for N occurrences of the same fingerprint before investigating, or investigate on first occurrence? Waiting reduces noise but delays fixes.
3. **PR review comment response**: Should healing agents be able to respond to PR review comments (via a separate trigger), or is this out of scope for v1?
4. **Fingerprint stability across refactors**: When code is refactored (file/function renamed), the call_site component changes. Should we track fingerprint aliases or let the old fingerprint age out naturally?
