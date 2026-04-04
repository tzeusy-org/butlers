## Why

The existing self-healing module is reactive and siloed — it fires when an individual butler session hits an error, with scope limited to that butler's own failures. There is no unified quality assurance function that proactively discovers errors across the entire ecosystem, deduplicates them, and dispatches investigations. Errors that slip through (warnings that accumulate into outages, scheduler drift, connector failures that don't crash a session, metric anomalies) go unnoticed until a human checks. A dedicated QA Staffer subsumes the existing self-healing into a system-wide quality assurance function with a pluggable discovery architecture, unified investigation pipeline, dashboard visibility, and anonymized PR creation.

## What Changes

- **QA Staffer roster entry (`roster/qa/`)**: A new staffer-typed agent with `type = "staffer"`, `cross_butler_access = ["*"]`, and a MANIFESTO.md defining its SRE-like responsibilities: error discovery, issue triage, investigation dispatch, and anonymized PR creation.
- **Pluggable discovery source architecture**: A `DiscoverySource` protocol that normalizes error detection across multiple channels. Each source produces `QaFinding` objects with computed fingerprints. Sources are registered at startup and polled during each patrol cycle. The architecture is designed for easy expansion — adding a new source means implementing one async method.
- **Discovery source: Log scanner** (v1): Reads from `logs/butlers/*.log`, `logs/connectors/*.log`, and `logs/uvicorn/*.log` in JSON-lines format. Filters to ERROR/WARNING with crash sentinel patterns. Tool-based filtering — no raw logs are sent to an LLM.
- **Discovery source: Session record analysis** (v1): Queries `sessions` table for recent failures (status = error/timeout), extracts exception type, traceback, and call site from session records directly.
- **Discovery source: Reactive butler reports** (v1, migrated): The existing `report_error` MCP tool path becomes a real-time discovery source. When a butler calls `report_error`, the finding is injected directly into the QA triage pipeline instead of being dispatched independently. This merges the existing self-healing module into the QA staffer's unified pipeline.
- **Future discovery sources** (post-v1, not implemented but architecture supports): Prometheus/metrics anomaly detection (latency spikes, error rate increases via PromQL), MCP reachability probes (periodic health checks to all registered endpoints), connector heartbeat monitoring (dead connectors that produce no logs), scheduler drift detection (expected vs. actual tick times), git-based regression detection (proactive test runs after merges).
- **Deduplication against known issues**: Cross-references discovered errors against (a) active investigations in the DB (fingerprint match), (b) open GitHub PRs with `self-healing` label, and (c) a local triage cache of recently-dismissed findings. Only novel issues proceed to investigation.
- **Unified investigation pipeline**: For each novel issue (regardless of discovery source), spawns an LLM agent in a dedicated git worktree branched off latest `main`. The agent reads the error context, explores the codebase, implements a fix, runs tests, and creates an anonymized PR. This replaces the separate per-butler healing dispatch with a single, QA-owned investigation engine.
- **Anonymized PR pipeline**: Scrubs all PII, user data, hostnames, credentials, and environment-specific paths before any content reaches the public GitHub repo. PR descriptions include: root cause analysis, affected butlers, fix summary, and linked evidence (sanitized). **IMPORTANT**: All personal details and sensitive data are anonymized — this is a hard gate, not optional.
- **Dashboard: QA overview page (`/qa`)**: A new top-level dashboard page showing patrol cycle history, discovered vs. deduplicated issue counts per discovery source, investigation pipeline status (queued → investigating → PR open → merged/failed), success rate trends, and circuit breaker state.
- **Dashboard: QA activity widget on home page**: A summary card on the main dashboard showing QA staffer health, last patrol time, active investigations count, and recent PR outcomes.
- **Dashboard: QA patrol detail view (`/qa/patrols/:id`)**: Drill-down into a single patrol cycle showing raw findings by source, deduplication decisions, and dispatched investigations.
- **Dashboard: QA investigation detail (`/qa/investigations/:id`)**: Per-investigation view with timeline (discovered → claimed → worktree created → tests run → PR opened), linked PR, and agent session logs.
- **API endpoints (`/api/qa/`)**: REST API backing the dashboard — patrol history, investigation pipeline, summary statistics, and manual controls (force patrol, dismiss finding, retry investigation).
- **Self-healing module migration**: The existing `modules/self_healing/` and `core/healing/dispatch.py` are refactored. The per-butler module becomes a thin relay that forwards `report_error` calls to the QA staffer's triage pipeline. The worktree, anonymizer, tracking, and PR infrastructure move to a shared `core/qa/` package.

## Capabilities

### New Capabilities

- `staffer-qa`: The QA Staffer roster entry, patrol loop, discovery source architecture, and infrastructure contract. Covers the staffer identity (butler.toml, MANIFESTO.md, CLAUDE.md), scheduler-driven patrol cycle, `DiscoverySource` protocol, and lifecycle management.
- `qa-log-scanner`: Cross-butler log scanning discovery source that reads structured JSON log files, extracts error/warning events via tool-based filtering (no LLM), computes fingerprints, and produces normalized findings. One of multiple discovery sources.
- `qa-triage`: Deduplication and triage layer that cross-references all discovery source findings against active investigations, open PRs, cooldown windows, and a local dismissal cache. Decides which findings are novel and warrant investigation.
- `qa-investigation-dispatch`: Unified investigation lifecycle management — worktree creation, LLM agent spawning, timeout watchdog, anonymized PR creation, and outcome recording. Subsumes and replaces the per-butler self-healing dispatch. This is the single investigation engine for all discovery sources.
- `qa-dashboard`: Dashboard pages and API endpoints for QA staffer visibility — patrol history, per-source finding breakdown, investigation pipeline, success metrics, and manual controls.

### Modified Capabilities

- `staffer-archetype`: Add QA Staffer as a third staffer alongside switchboard and messenger in the extensibility examples and roster conventions.
- `self-healing-dispatch`: Migrate shared infrastructure (worktree management, anonymizer, PR pipeline, tracking) into the QA staffer's `core/qa/` package. The per-butler `report_error` MCP tool becomes a thin relay that injects findings into the QA triage pipeline. The 10-gate dispatch sequence is preserved but owned by the QA investigation dispatcher.
- `self-healing-module`: The module retains its MCP tools (`report_error`, `get_healing_status`) but delegates dispatch to the QA staffer's triage pipeline instead of running its own dispatch engine. Becomes a "reactive discovery source" adapter.

## Impact

- **Code**: New `roster/qa/` directory (butler.toml, MANIFESTO.md, CLAUDE.md, AGENTS.md); new `src/butlers/core/qa/` package (discovery sources, triage, investigation dispatch, shared worktree/anonymizer/PR infra migrated from `core/healing/`); refactored `src/butlers/modules/self_healing/` to relay to QA triage; new `src/butlers/api/routers/qa.py`.
- **Frontend**: New `QaOverviewPage`, `QaPatrolDetailPage`, `QaInvestigationDetailPage` pages; new QA summary widget on DashboardPage; new routes in router.tsx; new TanStack Query hooks.
- **Database**: New `public.qa_patrols` table (patrol cycle records); new `public.qa_findings` table (per-finding records with dedup status and source type); extends `public.healing_attempts` with optional `qa_patrol_id` FK.
- **Config**: `[modules.qa]` section in QA staffer's butler.toml (patrol_interval_minutes, log_lookback_minutes, max_concurrent_investigations, severity_threshold, enabled_sources); QA staffer schedules.
- **Permissions & security**: Dedicated GitHub token (`BUTLERS_QA_GH_TOKEN`) scoped to branch push + PR creation but **no merge access** on `Tzeusy/butlers`. Investigation agents run in isolated git worktrees with no access to butler runtime credentials, DB connection strings, or user data. Log content is read locally and never transmitted except through the anonymized PR pipeline.
- **Dependencies**: No new Python packages — reuses existing git CLI, structlog JSON parsing, asyncpg, GitHub CLI.
- **Public repo safety**: All PR content passes through the anonymizer with `validate_anonymized()` hard gate. The QA staffer's discovery sources operate on local data only (log files, DB queries). No raw log content, user data, or sensitive information reaches GitHub.
