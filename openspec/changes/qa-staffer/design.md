## Context

The butlers ecosystem already has reactive self-healing: each butler can call `report_error` when it encounters an exception, and a 10-gate dispatch engine evaluates whether to spawn a healing agent in a worktree. This works well for in-session errors but has blind spots:

1. **Cross-butler visibility**: Each butler only sees its own errors. System-wide patterns go unnoticed.
2. **Single detection channel**: Only session exceptions trigger healing. Warnings that accumulate into outages, scheduler drift, connector failures, metric anomalies — all invisible.
3. **Observability gap**: The healing pipeline has API endpoints but no dedicated dashboard for understanding the system's health trajectory.
4. **Siloed infrastructure**: Healing dispatch, worktree management, anonymization, and tracking are coupled to the per-butler module.

The QA Staffer unifies self-healing into a system-wide quality assurance function with a pluggable discovery architecture, a single investigation pipeline, and full dashboard visibility.

**Existing infrastructure to reuse/migrate:**
- `src/butlers/core/healing/dispatch.py` — 10-gate dispatch engine (migrated to QA)
- `src/butlers/core/healing/worktree.py` — git worktree lifecycle
- `src/butlers/core/healing/anonymizer.py` — PII scrubbing for PRs
- `src/butlers/core/healing/tracking.py` — `healing_attempts` CRUD
- `src/butlers/core/healing/fingerprint.py` — SHA-256 error fingerprinting
- `src/butlers/api/routers/healing.py` — existing healing dashboard API (preserved as-is for backward compat, QA API adds new endpoints)
- Staffer archetype (`config.type = ButlerType.STAFFER`)

**Constraints:**
- All PR content must pass anonymizer → `validate_anonymized()` hard gate
- Butlers run on same machine → log files and session DB are local
- Logs are structlog JSON-lines format
- PostgreSQL `butlers` DB, `public` schema for shared tables
- Dashboard is React SPA (Vite + TanStack Query) + FastAPI backend
- UUIDv7 for all new record IDs (time-ordered sortability)
- GitHub credentials from system secrets store (dashboard `/secrets` page), not environment variables
- Investigation agents run in sandboxed worktrees with no butler runtime credentials

## Goals / Non-Goals

**Goals:**
- Pluggable discovery source architecture — log scanning as one source among many
- Subsume existing per-butler self-healing into a unified pipeline
- Cross-butler, proactive error detection via periodic patrol
- Tool-based error filtering (no raw logs sent to LLM for detection — LLM only for investigation/fix)
- Dashboard visibility: patrol cycles, per-source findings, known issues with PR links, trends
- Sandboxed investigation agents with least-privilege credentials
- Anonymized PR pipeline with dashboard linkability

**Non-Goals:**
- Real-time log streaming / tail -f (patrol is batch-oriented)
- Log aggregation service (we read local files)
- Monitoring infrastructure health (CPU, memory, disk)
- PR auto-merge (humans in the merge seat)
- Implementing all future discovery sources now (architecture supports them; v1 ships three)

## Decisions

### D1: QA Staffer as a module on standard daemon engine

The QA staffer runs on `ButlerDaemon` with a QA-specific module (`modules/qa/`). The patrol loop is driven by the butler's cron scheduler.

**Why:** Staffer archetype spec (D5) mandates type-aware conditionals over class forks. The module fits naturally: `register_tools()` for manual patrol triggers, `on_startup()` for worktree cleanup, `on_shutdown()` for graceful shutdown. The scheduler handles the interval.

**Alternative rejected:** Custom asyncio loop — bypasses daemon lifecycle, requires parallel shutdown logic.

### D2: Pluggable DiscoverySource protocol, not a monolithic scanner

Error detection is abstracted behind a `DiscoverySource` protocol: `name` (str) + `async discover(lookback_minutes) -> list[QaFinding]`. Each source does its own tool-based filtering. The patrol loop polls all registered sources and feeds combined findings to triage.

**Why:** Log scanning is inherently limited — it only catches errors that produce log output. Future sources (Prometheus metrics, MCP probes, scheduler drift, connector heartbeats, git regression detection) need the same triage/dispatch pipeline. A protocol boundary means adding a source is one class with one method — no changes to triage, dispatch, or dashboard.

**V1 sources:**
1. **`log_scanner`** — reads JSON-lines log files, regex + severity filtering
2. **`session_records`** — SQL queries against butler `sessions` tables for recent failures
3. **`butler_reports`** — in-memory buffer for reactive `report_error` calls from butlers

**Critical design constraint:** All sources use tool-based filtering (regex, SQL, file parsing). No LLM is invoked during discovery — LLM is only used for investigation/fix. This prevents context wastage from feeding raw logs through an LLM.

**Alternative rejected:** Monolithic scanner that hardcodes all detection logic — not extensible, mixes concerns.

### D3: Self-healing merger — `report_error` relays to QA staffer via Switchboard

The existing `modules/self_healing/` retains its MCP tools (`report_error`, `get_healing_status`) but relays findings to the QA staffer **via Switchboard** using the `route()` tool: `switchboard_client.call_tool("route", {"target_butler": "qa", "tool_name": "report_finding", "args": {...}})`. This calls the QA staffer's `report_finding` MCP tool directly — a tool-to-tool call, not a session-spawning `route.execute`. This preserves non-negotiable rule #3: inter-butler communication is MCP-only through Switchboard.

**Why `route()` not `notify()`:** `notify()` is for outbound delivery to external channels (telegram, email, whatsapp) — it is NOT an inter-butler messaging mechanism. The Switchboard's `route(target_butler, tool_name, args)` MCP tool is the correct mechanism for calling a specific tool on another agent. The QA staffer registers `report_finding` as an MCP tool; butlers call it through the Switchboard router.

**Why not `route.execute`:** `route.execute` spawns a full LLM session on the target butler, which is heavyweight for relaying a structured finding. Direct tool routing via `route()` calls the tool handler directly without an LLM session — it's synchronous, lightweight, and returns immediately. This matches the `report_error` non-blocking requirement (returns in 1-2 seconds).

**Fallback:** If the QA staffer is not registered with the Switchboard (standalone deployment), the self-healing module falls back to its existing direct dispatch path. Availability is checked via cached `list_butlers()` result (TTL-based to avoid per-error roundtrip).

**What moves where:**
- `core/healing/dispatch.py` gate logic → `core/qa/dispatch.py` (with QA-specific additions)
- `core/healing/worktree.py`, `anonymizer.py`, `fingerprint.py`, `tracking.py` → shared utilities imported by both `core/qa/` and legacy fallback path
- `modules/self_healing/` → thin relay via Switchboard `route()`, plus fallback dispatch

### D3.5: Session records source via sanctioned SQL view (RFC 0010 pattern)

The `session_records` discovery source needs to read failed session records across all butler schemas. Per-butler schema isolation (RFC 0006) prohibits direct cross-schema queries. Following the RFC 0010 precedent (daily briefing view), we create a sanctioned read-only SQL view `public.v_qa_recent_failures` as a UNION across butler `sessions` tables.

**RFC 0010 guardrails applied:**
1. Read-only SQL view (structurally enforced — no INSERT/UPDATE/DELETE)
2. Explicit butler source column with hardcoded values
3. Date-filtered queries only (WHERE created_at >= lookback window)
4. Health check validates view accessibility
5. Migration-based GRANT (auditable in version control)

**Why not MCP tool calls:** Querying each butler via `get_sessions` MCP tool would require spawning LLM sessions just to extract structured data — violating the "tool-based filtering, no LLM for discovery" constraint. A SQL view is deterministic, zero-reasoning batch work — exactly the exception class RFC 0010 sanctions.

**Alternative rejected:** Direct cross-schema queries from QA staffer module — violates schema isolation, no audit trail.

### D4: Extend `healing_attempts` with `qa_patrol_id` (not a separate table)

QA investigations reuse the existing `healing_attempts` table with a nullable `qa_patrol_id` FK. The existing status lifecycle, tracking CRUD, and circuit breaker logic all work unchanged.

**Why:** A separate table would duplicate the entire tracking layer. The existing healing API (`/api/healing/attempts`) continues to work. QA investigations are distinguishable by `qa_patrol_id IS NOT NULL`.

### D5: New tables for QA-specific concepts (`qa_patrols`, `qa_findings`, `qa_dismissals`)

Patrol cycles, per-finding records, and dismissals need their own tables — these concepts don't exist in the healing model.

- **`qa_patrols`**: Tracks the scan→triage→dispatch lifecycle of each patrol cycle. Enables dashboard drill-down.
- **`qa_findings`**: Every discovered issue (novel or deduplicated) with source type, dedup reason, and linked attempt. Enables the "known issues" view and per-source analytics.
- **`qa_dismissals`**: Operator-managed fingerprint suppression with TTL. Prevents known-but-acceptable issues from wasting investigation tokens.

All IDs are UUIDv7 for time-ordered sortability and efficient indexing.

### D6: Credentials via Tier 1 system secrets (RFC 0006), not env vars

The QA staffer retrieves its GitHub token from `butler_secrets` (Tier 1 system secret per RFC 0006 three-tier credential authority model) at key `BUTLERS_QA_GH_TOKEN`, `category = "qa"`, `is_sensitive = true`. Managed via the dashboard at `/secrets`.

**Why:** Tier 1 (DB-first with env fallback) is the correct tier for ecosystem-wide service credentials per RFC 0006 §Credential Store. Env vars (Tier 0) are for infrastructure bootstrap only (POSTGRES_*, OTEL_*). The dashboard `/secrets` page gives visibility and management. The `is_sensitive = true` flag ensures the value is masked in API responses.

**Token scope:** Branch push + PR creation + PR labeling on `Tzeusy/butlers`. Explicitly NO merge or approve permissions.

**Migration from existing pattern:** The current self-healing uses `gh_token_env_var = "GH_TOKEN"` (Tier 0 env var). The QA staffer upgrades to `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")` (Tier 1). The fallback path (when QA staffer unavailable) should also be updated to use CredentialStore, deprecating the `gh_token_env_var` config field.

### D7: Investigation agents in sandboxed worktree environments

Investigation agents run with minimal credentials: only `GH_TOKEN` (from secrets store) + build tools. No butler DB strings, no API keys, no MCP connections, no `BUTLERS_*` env vars.

**Why:** Investigation agents explore the codebase and write code. They don't need butler runtime access. Sandboxing prevents accidental data leaks (e.g., an investigation agent logging a DB query result that contains user data).

**Trade-off:** Agents can't reproduce runtime state for debugging. They must infer state from error context + code reading. This is acceptable — the goal is code fixes, not runtime diagnostics.

### D8: Dashboard as QA cockpit with PR lifecycle tracking

The QA dashboard (`/qa`) is a top-level page, not embedded in existing pages. It includes:
- **Known issues panel**: All active investigations grouped by fingerprint with PR links (clickable to GitHub)
- **PR status tracking**: The QA staffer checks PR GitHub status on each patrol cycle, transitioning `pr_open → pr_merged` or `pr_open → failed` as appropriate
- **Investigation detail**: Full lifecycle timeline with PR card (number, title, status, link)
- **Home page widget**: Compact summary with active investigation count and open PR links

**Why:** QA is a distinct operational concern. The known issues panel with PR links is critical — operators need to see "what's broken, what's being fixed, and where's the fix" in one place. PR lifecycle tracking ensures the dashboard reflects reality without manual refreshes.

### D9: Patrol overlap prevention via asyncio.Lock

A single `asyncio.Lock` prevents concurrent patrol cycles. If a tick fires while the previous cycle is running, the tick is skipped.

**Why:** The QA staffer is single-instance (same machine). No multi-process coordination needed. The lock covers scan→triage→dispatch initiation (not investigation completion — those run asynchronously).

### D10: QA API as a separate router

`/api/qa/` is a new FastAPI router. The existing `/api/healing/` router is preserved for backward compatibility.

**Why:** QA has distinct concerns (patrols, findings, dismissals, trends, known issues). Mixing into healing router would blur ownership. The QA router imports shared helpers from tracking as needed.

## Risks / Trade-offs

**[Risk] Log volume causes slow patrols** → Scanner has configurable caps and reads from end of files. Monitor patrol duration via `qa_patrols`. Alert if consistently exceeding interval.

**[Risk] LLM token consumption** → Concurrency cap (default: 2) and circuit breaker limit burn rate. `self_healing` model tier gives operators cost control. Start with `severity_threshold = 2` (only high/critical).

**[Risk] Fingerprint collision across sources** → Intentional deduplication mechanism. Same fingerprint algorithm ensures log-discovered errors and butler-reported errors are recognized as the same issue.

**[Risk] Stale log files from crashed butler** → Spawner fallback catches hard crashes. QA staffer's `session_records` source catches DB-recorded failures. Multiple sources provide redundancy.

**[Risk] Anonymizer false negatives** → `validate_anonymized()` hard gate blocks any PR with residual PII. Failure transitions to `anonymization_failed` — no PR is created.

**[Risk] Self-healing merger breaks standalone butler deployments** → Fallback path preserved in `modules/self_healing/`. Without QA staffer, per-butler self-healing works as before.

**[Risk] Secrets store unavailable** → If `BUTLERS_QA_GH_TOKEN` not found, investigation completes analysis but transitions to `failed` with reason `"no_gh_token"`. Investigation is not wasted — the worktree and commit are available for manual PR creation.

## Migration Plan

1. **Database migration** (additive, no breaking changes):
   - Add `qa_patrol_id` column (nullable UUIDv7 FK) to `healing_attempts`
   - Create `qa_patrols` table (UUIDv7 PK)
   - Create `qa_findings` table (UUIDv7 PK, FK to `qa_patrols`, FK to `healing_attempts`)
   - Create `qa_dismissals` table (fingerprint PK, `dismissed_until`, `dismissed_by`)

2. **Backend — shared infrastructure extraction**:
   - Extract worktree, anonymizer, fingerprint, tracking into importable shared utils
   - Create `src/butlers/core/qa/` package: `sources/` (log_scanner, session_records, butler_reports), `triage.py`, `dispatch.py`
   - Create `src/butlers/api/routers/qa.py`
   - Refactor `modules/self_healing/` to relay to QA buffer with fallback

3. **Roster**: Create `roster/qa/` (butler.toml, MANIFESTO.md, CLAUDE.md, AGENTS.md)

4. **Secrets**: Provision `BUTLERS_QA_GH_TOKEN` in secrets store via dashboard

5. **Frontend**: Add QA pages, routes, hooks, home page widget. No changes to existing pages.

6. **Deployment**: Start QA staffer daemon. First patrol fires on interval tick.

7. **Rollback**: Stop QA staffer daemon. Self-healing fallback activates automatically. No other butlers affected.

## Resolved Questions

1. **QA staffer does NOT scan its own logs.** `qa.log` is excluded from the log scanner's file set. QA staffer errors are infrastructure-level — monitored via Prometheus metrics and OTel traces, not self-investigated. Keeps patrol findings focused on butler/connector errors.

2. **Log scanner reads current file only.** No `.1` rotated file scanning. With a 15-minute lookback window the active file almost always covers it. If rotation happens mid-patrol, the `session_records` source provides redundant coverage from the DB. Simpler, less I/O.

3. **PR labels are unified: `["self-healing", "automated"]`.** QA-originated PRs use the same labels as legacy self-healing PRs. No `qa-patrol` label. The PR body mentions QA staffer origin but labels stay uniform. Single GitHub filter catches all automated fixes.

4. **Fallback removed after 30 days stable.** Once the QA staffer has been running for 30 days with no fallback activations (tracked via a counter metric), remove the direct dispatch path from `modules/self_healing/`. The relay-via-Switchboard path becomes the only path. Reduces code paths and test surface. QA staffer becomes mandatory infrastructure.
