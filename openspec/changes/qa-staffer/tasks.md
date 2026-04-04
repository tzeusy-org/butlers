## 1. Database Migrations

- [ ] 1.1 Create Alembic migration for `public.qa_patrols` table (UUIDv7 PK, started_at, completed_at, status, findings_count, novel_count, dispatched_count, log_lookback_minutes, sources_polled text[], error_detail)
- [ ] 1.2 Create Alembic migration for `public.qa_findings` table (UUIDv7 PK, patrol_id FK, fingerprint, source_type, source_butler, severity, exception_type, event_summary, call_site, occurrence_count, first_seen, last_seen, dedup_reason, healing_attempt_id FK, created_at)
- [ ] 1.3 Create Alembic migration for `public.qa_dismissals` table (fingerprint PK, dismissed_until timestamptz, dismissed_by text, created_at)
- [ ] 1.4 Create Alembic migration to add nullable `qa_patrol_id` (UUIDv7 FK to qa_patrols) column to `public.healing_attempts`
- [ ] 1.5 Create Alembic migration for `public.v_qa_recent_failures` read-only SQL view (sanctioned cross-schema exception per RFC 0010) — UNION across butler sessions tables, date-filtered, with explicit per-schema GRANT and health check

## 2. Core QA Package — Discovery Source Architecture

- [ ] 2.1 Define `QaFinding` dataclass in `src/butlers/core/qa/models.py` (fingerprint, source_type, source_butler, severity, exception_type, event_summary, call_site, occurrence_count, first_seen, last_seen, timestamp, context optional)
- [ ] 2.2 Define `DiscoverySource` protocol in `src/butlers/core/qa/sources/protocol.py` (name property, async discover method)
- [ ] 2.3 Implement `LogScannerSource` in `src/butlers/core/qa/sources/log_scanner.py` — JSON-lines parsing, temporal filtering, severity filtering, fingerprint computation, aggregation, performance caps
- [ ] 2.4 Implement `SessionRecordsSource` in `src/butlers/core/qa/sources/session_records.py` — queries `public.v_qa_recent_failures` sanctioned SQL view (not direct cross-schema access), fingerprint computation
- [ ] 2.5 Implement `ButlerReportsSource` in `src/butlers/core/qa/sources/butler_reports.py` — in-memory buffer for reactive `report_error` relay, drain on patrol tick, max_reactive_buffer cap
- [ ] 2.6 Add `compute_fingerprint_from_log_entry(entry: dict)` variant to `src/butlers/core/healing/fingerprint.py` for log-derived findings (compatible fingerprint algorithm)
- [ ] 2.7 Write tests for LogScannerSource (JSON parsing, temporal filter, severity filter, aggregation, performance caps)
- [ ] 2.8 Write tests for SessionRecordsSource (SQL query, fingerprint compat)
- [ ] 2.9 Write tests for ButlerReportsSource (buffer, drain, overflow)

## 3. Core QA Package — Triage Layer

- [ ] 3.1 Implement triage engine in `src/butlers/core/qa/triage.py` — source-agnostic finding intake, three-source dedup (active attempts, open PRs, dismissals), cooldown check, severity-based prioritization
- [ ] 3.2 Implement `qa_findings` CRUD in `src/butlers/core/qa/findings.py` — insert findings, query by patrol, query by fingerprint
- [ ] 3.3 Implement `qa_dismissals` CRUD in `src/butlers/core/qa/dismissals.py` — upsert, expire check, list active, delete
- [ ] 3.4 Write tests for triage engine (dedup against active attempts, open PRs, dismissals, cooldown, prioritization ordering)
- [ ] 3.5 Write tests for dismissal CRUD (upsert, expiry, deletion)

## 4. Core QA Package — Investigation Dispatch

- [ ] 4.1 Implement QA investigation dispatcher in `src/butlers/core/qa/dispatch.py` — gate sequence, worktree creation (prefix="qa"), agent spawning, timeout watchdog, outcome recording with `qa_patrol_id` linkage
- [ ] 4.2 Refactor shared worktree infrastructure: extract `create_healing_worktree()` prefix parameter, extract PR pipeline label configurability from `core/healing/worktree.py`
- [ ] 4.3 Implement PR status tracking — on each patrol cycle, check GitHub status of `pr_open` attempts via `gh pr view`, transition to `pr_merged` or `failed` as appropriate
- [ ] 4.4 Implement investigation agent prompt builder in `src/butlers/core/qa/prompts.py` — compose prompt from finding context (fingerprint, exception type, sanitized summary, source type, occurrence count), include anonymization instructions, include dashboard link
- [ ] 4.5 Implement GitHub credential retrieval from `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")` (Tier 1) — inject as `GH_TOKEN` env var for `gh` CLI compatibility in agent env AND for daemon-context PR status tracking
- [ ] 4.5.1 Extend `create_or_join_attempt()` in `core/healing/tracking.py` to accept optional `qa_patrol_id` parameter for QA-originated investigations
- [ ] 4.6 Implement investigation agent sandbox environment builder — construct minimal env (GH_TOKEN from secrets, PATH, UV_CACHE_DIR only), strip butler runtime vars
- [ ] 4.7 Write tests for QA dispatch (gate sequence, worktree prefix, patrol linkage, sandbox env, timeout)
- [ ] 4.8 Write tests for PR status tracking (merge detection, close detection)

## 5. Self-Healing Module Migration

- [ ] 5.1 Refactor `src/butlers/modules/self_healing/__init__.py` — add QA relay path: when QA staffer is registered with Switchboard, relay findings via `switchboard_client.call_tool("route", {"target_butler": "qa", "tool_name": "report_finding", "args": ...})` instead of direct dispatch (preserving non-negotiable rule #3: MCP-only inter-butler communication via Switchboard `route()` tool, not `notify()` which is for external channels)
- [ ] 5.2 Preserve fallback path: when QA staffer unavailable, use existing direct dispatch via `core.healing.dispatch`
- [ ] 5.3 Write tests for relay behavior: (1) QA available → Switchboard `route()` call to `report_finding` is made with correct args, (2) QA unavailable → fallback to direct dispatch, (3) Switchboard client not connected → immediate fallback, (4) cached `list_butlers` TTL prevents per-error roundtrip

## 6. QA Staffer Module and Roster

- [ ] 6.1 Create `roster/qa/butler.toml` — type="staffer", cross_butler_access=["*"], schedules (patrol interval), modules.qa config with defaults
- [ ] 6.2 Create `roster/qa/MANIFESTO.md` — infrastructure contract: responsibilities, SLAs, failure modes, dependency graph, escalation
- [ ] 6.3 Create `roster/qa/CLAUDE.md` — system prompt for QA staffer LLM sessions
- [ ] 6.4 Create `roster/qa/AGENTS.md` — initialized with "# Notes to self" header
- [ ] 6.5 Implement `src/butlers/modules/qa/__init__.py` — Module base class, register_tools (report_finding, force_patrol, get_qa_status), on_startup (register sources, reap stale worktrees, recover stale patrol rows and dispatch_pending attempts), on_shutdown (cancel watchdogs but NOT active investigation sessions — those drain via daemon phase 3), migration_revisions() returns None (QA tables in public schema via core chain), dependencies = [], tool_metadata() declares context as sensitive on report_finding
- [ ] 6.5.1 Add OTel span instrumentation: `qa.patrol` parent span per patrol cycle, `qa.discover.<source>` child spans, `qa.triage` span, `qa.dispatch` spans; `qa.investigation` as independent root span per investigation
- [ ] 6.5.2 Register Prometheus metrics: `qa_patrol_total` (counter, labels: status), `qa_findings_total` (counter, labels: source_type, dedup_reason), `qa_investigations_active` (gauge), `qa_patrol_duration_seconds` (histogram), `qa_investigation_duration_seconds` (histogram, labels: status) — low-cardinality per RFC 0005
- [ ] 6.6 Implement patrol loop in module — scheduler-driven tick, asyncio.Lock for overlap prevention, poll all sources, feed triage, dispatch novel findings, record patrol in DB
- [ ] 6.7 Implement immediate mini-patrol for severity-0 reactive findings
- [ ] 6.8 Write tests for patrol loop (full cycle, overlap prevention, source failure isolation, mini-patrol trigger)

## 7. Dashboard API

- [ ] 7.1 Create `src/butlers/api/routers/qa.py` — FastAPI router at `/api/qa/`
- [ ] 7.2 Implement GET /api/qa/summary — staffer status, last/next patrol, 24h stats, all-time stats, circuit breaker, active sources
- [ ] 7.3 Implement GET /api/qa/patrols — paginated patrol list with sources_polled
- [ ] 7.4 Implement GET /api/qa/patrols/:patrolId — full patrol with nested findings
- [ ] 7.5 Implement GET /api/qa/patrols/:patrolId/findings — findings for a patrol
- [ ] 7.6 Implement GET /api/qa/investigations — paginated QA investigations (qa_patrol_id IS NOT NULL) with PR info
- [ ] 7.7 Implement GET /api/qa/known-issues — active issues grouped by fingerprint with PR links and status
- [ ] 7.8 Implement POST /api/qa/dismiss — add fingerprint to dismissals
- [ ] 7.9 Implement DELETE /api/qa/dismissals/:fingerprint — remove dismissal
- [ ] 7.10 Implement GET /api/qa/dismissals — list active dismissals
- [ ] 7.11 Implement POST /api/qa/force-patrol — trigger immediate patrol
- [ ] 7.12 Implement GET /api/qa/trends — daily aggregated stats with per-source breakdown
- [ ] 7.13 Write tests for all QA API endpoints

## 8. Dashboard Frontend — QA Overview Page

- [ ] 8.1 Create `QaOverviewPage.tsx` — status banner, summary stats cards, investigation pipeline kanban, known issues panel, recent patrols table, success rate chart, source breakdown chart
- [ ] 8.2 Create `use-qa.ts` TanStack Query hooks — useQaSummary, useQaPatrols, useQaInvestigations, useQaKnownIssues, useQaTrends, useQaDismissals
- [ ] 8.3 Create KnownIssuesPanel component — fingerprint grouping, PR link badges, severity badges, source type badges, dismiss action, filter/sort controls
- [ ] 8.4 Create InvestigationPipeline component — kanban-style columns by status with clickable cards
- [ ] 8.5 Create SuccessRateChart component — line chart for 7-day trend
- [ ] 8.6 Create SourceBreakdownChart component — per-source finding counts

## 9. Dashboard Frontend — Detail Pages

- [ ] 9.1 Create `QaPatrolDetailPage.tsx` — patrol metadata, findings table with source type and dedup badges, dispatch summary with investigation links
- [ ] 9.2 Create `QaInvestigationDetailPage.tsx` — metadata, timeline visualization, error context, PR card (number, title, status, GitHub link), agent session link, patrol link, retry/dismiss actions
- [ ] 9.3 Create PR status badge component — renders open/merged/closed with appropriate styling and clickable GitHub link

## 10. Dashboard Frontend — Integration

- [ ] 10.1 Add QA widget to DashboardPage — status indicator, last patrol, active investigations, open PRs with links, merged PRs count
- [ ] 10.2 Add routes to router.tsx — `/qa`, `/qa/patrols/:patrolId`, `/qa/investigations/:attemptId`
- [ ] 10.3 Add "QA" entry to sidebar navigation with active investigation badge
- [ ] 10.4 Add frontend TypeScript types for QA API responses (patrol, finding, investigation, summary, trends)

## 11. Staffer Archetype Update

- [ ] 11.1 Update `openspec/specs/staffer-archetype/spec.md` extensibility examples to include QA Staffer as a concrete third staffer alongside switchboard and messenger

## 12. Integration Testing and Documentation

- [ ] 12.1 Write integration test: full patrol cycle (log scanner discovers error → triage deduplicates → dispatch creates investigation → investigation creates worktree → timeout cleanup)
- [ ] 12.2 Write integration test: reactive relay (butler calls report_error → finding appears in next patrol → investigation dispatched by QA staffer)
- [ ] 12.3 Write integration test: deduplication (same fingerprint from log scanner and session records → single investigation, not two)
- [ ] 12.4 Write integration test: sandbox enforcement (investigation agent env does not contain butler secrets)
- [ ] 12.5 Write integration test: anonymization gate (PR with PII is blocked, attempt transitions to anonymization_failed)
- [ ] 12.6 Provision `BUTLERS_QA_GH_TOKEN` in secrets store via dashboard /secrets page (Tier 1 system secret, category="qa", is_sensitive=true)
- [ ] 12.7 Write test: patrol crash recovery (stale "running" patrol rows are cleaned on daemon restart, dispatch_pending attempts are re-dispatched)
- [ ] 12.8 Write test: healing API backward compatibility (QA-originated investigations with qa_patrol_id appear in existing GET /api/healing/attempts alongside per-butler self-healing attempts)
- [ ] 12.9 Write test: concurrency model (QA investigation sessions acquire QA staffer's per-staffer semaphore + global semaphore, do not deadlock reporting butler)

## 13. Cruft Cleanup (Post-Migration)

- [ ] 13.0 Deprecate `gh_token_env_var` field in `HealingConfig` — replace with `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")` in both QA dispatch and legacy fallback path. Remove Tier 0 env var lookup for GitHub tokens.
- [ ] 13.1 Remove gate evaluation loop from `src/butlers/core/healing/dispatch.py` (lines ~872-1052) — gates now owned by QA dispatch. Extract any reusable gate helpers into `src/butlers/core/healing/gates.py` first.
- [ ] 13.2 Remove `_create_pr()`, `_build_pr_body()`, `_build_healing_prompt()`, `_timeout_watchdog()`, `_run_healing_session()` from `dispatch.py` — these orchestration functions move to `core/qa/dispatch.py`
- [ ] 13.3 Slim down `src/butlers/modules/self_healing/__init__.py` — remove `_handle_dispatch()` internal method, remove inline gate logic, keep only: MCP tool registration (report_error, get_healing_status), Switchboard relay logic, fallback dispatch delegation, on_startup recovery, on_shutdown cleanup
- [ ] 13.4 Remove `wire_healing_module()` from `src/butlers/core/spawner.py` if the spawner fallback now delegates to QA staffer. If fallback is preserved, update the except block to call QA dispatch instead of direct `dispatch_healing()`
- [ ] 13.5 Update `tests/core/healing/test_dispatch.py` — remove tests for orchestration logic that moved to QA dispatch. Keep tests for shared gate helper functions if extracted.
- [ ] 13.6 Update `tests/modules/test_module_self_healing.py` — replace dispatch-testing with relay-testing (verify Switchboard notify call, verify fallback path)
- [ ] 13.7 Update `tests/core/test_spawner_healing_fallback.py` — update to test new dispatch target
- [ ] 13.8 Verify all existing healing API tests pass after migration (healing endpoints unchanged, QA endpoints additive)
- [ ] 13.9 Add `qa_fallback_activations_total` Prometheus counter to self-healing module — tracks how often the direct dispatch fallback fires (QA staffer unreachable). After 30 days with zero activations, the fallback path can be removed.
