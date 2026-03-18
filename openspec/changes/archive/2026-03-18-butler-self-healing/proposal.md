## Why

Butler sessions fail — LLM tool calls hit bugs, modules throw unexpected exceptions, runtime adapters encounter edge cases. Today, these errors are logged to session records and stderr, then forgotten. A human must notice, investigate, and fix. Since butlers already run as LLM CLI sessions on the same machine as their source code, they're uniquely positioned to investigate their own failures: spawn a subagent that reads the error context, explores the codebase, and proposes a fix — all without human intervention. This closes the loop between "error observed" and "fix proposed" while keeping humans firmly in the merge seat.

## What Changes

- **Self-healing module (`modules/self_healing/`)**: A new module wired into every butler's MCP server that exposes tools for error reporting and healing status. When a butler encounters an unexpected error during a session, it calls `report_error` with the exception context and its own reasoning about what went wrong. The module handles fingerprinting, deduplication, and dispatching a healing agent. This is the **primary** entry point for self-healing.
- **Shared `/self-healing` skill**: A skill in `roster/shared/skills/self-healing/` that teaches all butlers how and when to call the self-healing tools. Installed via `BUTLER_SKILLS.md` so every butler gets the protocol automatically.
- **Spawner fallback path**: A lightweight fallback in the spawner's except block for hard crashes (OOM, process kill, adapter timeout) where the butler agent never gets a chance to call the MCP tool. This catches errors the agent couldn't self-report.
- **Error fingerprinting**: Hash-based deduplication of errors so the same root cause doesn't spawn 100 investigation agents. Handles both structured agent reports (via MCP tool) and raw exceptions (via spawner fallback).
- **Self-healing dispatcher**: The shared decision engine (used by both the module and the spawner fallback) that evaluates whether to spawn a healing agent based on the error fingerprint's novelty and severity.
- **Isolated investigation worktrees**: Healing agents work exclusively in timestamped git worktrees (`self-healing/<butler>/<fingerprint>-<ts>`), never touching the main working tree.
- **Anonymized PR pipeline**: Healing agents produce PRs against `main` with structured descriptions (root cause, fix summary, affected sessions) where all user data, PII, and sensitive content is scrubbed before any content reaches the public repo.
- **Healing session records**: New DB table tracking healing attempts — fingerprint, status, branch, PR URL, linked session IDs — providing observability into the self-healing loop.
- **Rate limiting & circuit breaker**: Guards against runaway healing: per-fingerprint cooldowns, global concurrent healing cap, and a kill switch.
- **Dedicated `self-healing` model tier**: A new complexity tier in the Model Catalog exclusively for healing agents. Investigation, code fixing, and PR creation use only models/runtimes assigned to this tier — configurable via the dashboard at `/butlers/settings`. This gives operators explicit cost and capability control over what powers self-healing, independent of the tiers used for normal butler work.

## Capabilities

### New Capabilities

- `self-healing-module`: A new butler module (`modules/self_healing/`) that registers MCP tools on every butler's MCP server. Exposes `report_error` (primary entry point — butler agent reports an error with context and reasoning) and `get_healing_status` (query active/recent healing attempts). The module delegates to the shared `src/butlers/core/healing/` package.
- `self-healing-skill`: A shared skill in `roster/shared/skills/self-healing/` that teaches butler agents the error reporting protocol: when to call `report_error`, what context to include, how to interpret `get_healing_status` responses. Appended to all butlers via `BUTLER_SKILLS.md`.
- `error-fingerprinting`: Deterministic error classification — extracts a stable fingerprint from exception type, call site, and sanitized message so duplicate errors map to the same key. Handles both structured agent reports (richer context) and raw exceptions (spawner fallback). Includes severity scoring.
- `self-healing-dispatch`: Decision engine that evaluates whether an error warrants a healing agent. Shared by the module tool handler and the spawner fallback. Checks fingerprint novelty, rate limits, circuit breaker state, and severity threshold.
- `healing-worktree`: Git worktree lifecycle management for healing agents — create timestamped branch + worktree, run agent within it, clean up on completion or timeout.
- `healing-anonymizer`: Data sanitization pipeline that scrubs PII, credentials, user content, and environment-specific paths from error context before it's included in PR descriptions or branch metadata. Extends the existing credential redaction filter pattern.
- `healing-session-tracking`: Database schema and query layer for tracking healing attempts — links error fingerprints to investigation branches, PRs, and outcome status.
- `healing-model-tier`: A new `self-healing` complexity tier in the Model Catalog. Healing agents resolve models exclusively from this tier via the existing `resolve_model()` path. Requires a DB migration to extend the `complexity_tier` CHECK constraint, validation updates in the API, and seed entries in `model_catalog_defaults.toml`.

### Modified Capabilities

- `core-spawner`: The spawner's `_run()` error-handling path gains a **fallback** hook for hard crashes where the butler agent couldn't call the `report_error` MCP tool. This is the secondary entry point — the module is primary.
- `core-sessions`: Session records gain a `healing_fingerprint` column linking failed sessions to their error fingerprint for correlation.
- `model-catalog`: The `complexity_tier` enum gains a `self-healing` value. CHECK constraint on `shared.model_catalog`, validation in `/api/settings/models`, and tier list in the dashboard settings UI all need updating.

## Impact

- **Code**: New `src/butlers/modules/self_healing/` module, new `src/butlers/core/healing/` package (fingerprinting, dispatch, worktree, anonymizer, tracking), `src/butlers/core/spawner.py` (fallback hook)
- **Skills**: New `roster/shared/skills/self-healing/` skill, updated `roster/shared/BUTLER_SKILLS.md`
- **Database**: New `shared.healing_attempts` table; new column on per-butler `sessions` table; migration to extend `complexity_tier` CHECK constraint with `self-healing` value
- **Config**: `[modules.self_healing]` in `butler.toml` (enabled per butler); `[healing]` section for dispatch tuning (severity_threshold, max_concurrent, cooldown_minutes); new seed entries in `model_catalog_defaults.toml` for the `self-healing` tier
- **Dependencies**: `git` CLI (already available), no new Python packages expected
- **APIs**: New dashboard routes for healing attempt visibility (list, detail, retry)
- **Risk**: Healing agents consume LLM tokens — rate limiting and the circuit breaker are load-bearing safety mechanisms, not nice-to-haves
- **Public repo safety**: The anonymizer is a hard gate — no PR can be created without passing sanitization. This is the most critical correctness requirement.
