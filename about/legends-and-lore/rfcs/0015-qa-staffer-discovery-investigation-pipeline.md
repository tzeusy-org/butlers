# RFC 0015: QA Staffer Discovery & Investigation Pipeline

**Status:** Accepted
**Date:** 2026-04-25

## Summary

The QA Staffer is a permanently-running infrastructure agent (type = `staffer`) that acts as the
system-wide SRE for the Butlers ecosystem. It owns a pluggable discovery source architecture, a
fingerprint-based triage and deduplication layer, an investigation dispatch pipeline that runs in
isolated git worktrees, and a dashboard surface for operator visibility. The QA Staffer subsumes
and supersedes the per-butler `self-healing-*` capability family: the unified QA pipeline delivers
richer cross-system coverage than per-butler autonomous repair, and maintaining two parallel
self-repair stacks would create ambiguity about which system owns a given finding. The legacy
`self-healing-dispatch`, `self-healing-module`, and `self-healing-skill` specs are **deprecated**
as of this RFC (see §"Legacy Self-Healing Deprecation").

## Motivation

Five capability specs — `staffer-qa`, `qa-dashboard`, `qa-triage`, `qa-investigation-dispatch`,
and `qa-log-scanner` — collectively define a coherent QA pipeline. The capabilities have clear
wire contracts that cross-reference each other but previously had no unifying design document at
the RFC tier.

Before this RFC:

1. The per-butler `self-healing-dispatch` capability owned the 10-gate dispatch sequence,
   healing worktrees, and PR creation. It is invoked per butler when an agent calls `report_error`.
2. The `self-healing-module` registers the `report_error` MCP tool on every butler's server.
3. The `self-healing-skill` teaches agents how to call `report_error`.

The QA Staffer does not merely duplicate this: it adds a proactive, multi-source patrol loop that
runs independent of whether an agent self-reports, provides cross-butler visibility, and builds
an operator-facing dashboard that the per-butler self-healing path never surfaced. The QA dispatch
pipeline preserves the 10-gate sequence (inherited from `self-healing-dispatch`) but applies it
from a single unified coordinator rather than from each butler's own module.

## Design

### D1: DiscoverySource Protocol

The QA Staffer uses a pluggable `DiscoverySource` protocol for error detection. Every source
must implement:

```python
class DiscoverySource(Protocol):
    name: str  # unique identifier used in config, DB, and metrics labels
    async def discover(self, lookback_minutes: int) -> list[QaFinding]: ...
```

Key invariants that all sources must honour:

- **No LLM calls.** All filtering must use tool-based approaches: JSON parsing, SQL queries,
  regex. LLM invocation is reserved for the investigation agent spawned downstream.
- **`QaFinding` carries `source_session_trigger_source`** — the `trigger_source` of the originating
  session or log entry. The triage layer uses this field to suppress QA-self-recursive findings
  (see §D3 Self-Recursion Barrier).
- Sources are registered at startup from `[modules.qa].enabled_sources` and polled every patrol
  cycle. A source that raises during `discover()` does not abort the cycle — findings from other
  sources are still processed and the failed source is recorded in `patrol.error_detail`.

**V1 sources:**

| Source name | Access path | Notes |
|---|---|---|
| `log_scanner` | `logs/butlers/*.log`, `logs/connectors/*.log`, `logs/uvicorn/*.log` | Reads backwards from end of active file; skips rotated files |
| `session_records` | `public.v_qa_recent_failures` view (RFC 0010 pattern) | Read-only cross-butler union view; `event_summary` anonymized before storage |
| `butler_reports` | In-memory buffer drained from `report_finding` MCP tool | Volatile; buffer cleared on restart; `session_records` provides recovery coverage |

**Future sources** (protocol accommodates without triage/dispatch/dashboard changes):
`prometheus_metrics`, `mcp_reachability`, `scheduler_drift`, `connector_heartbeat`, `git_regression`.

### D2: Fingerprint-Based Triage and Deduplication

The triage layer accepts `QaFinding` objects from all sources in a single pass per patrol cycle.
Source type does not affect the triage decision — all sources are merged and deduplicated by
fingerprint before any dispatch gate runs.

**Fingerprint computation** (shared with `src/butlers/core/healing/fingerprint.py`):

```
fingerprint = SHA-256(exception_type + call_site + normalize(event_summary))
```

`normalize()` strips variable content — UUIDs, timestamps, numeric IDs, file paths — to group
semantically identical errors across occurrences.

**Three-source deduplication check (fast-path; non-atomic):**

1. **Active investigation** — fingerprint matches a row in `public.healing_attempts` with
   `status IN ('investigating', 'pr_open')` → `dedup_reason = "active_investigation"`.
2. **Dismissal cache** — fingerprint exists in `public.qa_dismissals` with
   `dismissed_until > now()` → `dedup_reason = "dismissed"`.
3. **Cooldown window** — terminal `healing_attempts` row closed within the cooldown window
   (default: 60 minutes) → `dedup_reason = "cooldown"`.

A finding that clears all three checks is **novel** and eligible for dispatch. The triage
check is a fast-path optimisation; the authoritative atomic novelty claim happens at the
dispatch layer (gate 6, novelty gate via `SELECT … FOR UPDATE`).

All findings — novel and deduplicated — are persisted to `public.qa_findings` for dashboard
visibility. Novel findings that are skipped due to the concurrency cap set
`dispatch_queued = TRUE` in their `qa_findings` row; `get_dispatch_queued_findings()` at
the start of the next patrol cycle fetches and clears these atomically (using
`FOR UPDATE SKIP LOCKED`) and prepends them to the triage batch.

### D3: Self-Recursion Barrier

Every `QaFinding` carries `source_session_trigger_source` (nullable str) — the `trigger_source`
of the session or log entry that produced the error.

A finding where **`source_butler == "qa"` AND `source_session_trigger_source IN ('healing', 'qa')`**
(or is null/unrecognised when the source butler is QA) is routed to the **meta-review lane**
(`GET /api/qa/meta-review`) and never auto-investigated. This prevents QA from recursively
investigating its own failures and creating unbounded investigation loops.

For non-QA source butlers the barrier does not apply regardless of `source_session_trigger_source`.

### D4: Investigation Dispatch Contract

Once a novel finding passes the self-recursion barrier and is ready for dispatch, the authoritative
10-gate sequence runs (inherited from `self-healing-dispatch`, now QA-owned):

1. No-recursion guard (`trigger_source` field)
2. Opt-in gate
3. Fingerprint computation (pre-computed by triage; accepted as-is)
4. Fingerprint persistence (update session record, best-effort)
5. Severity gate (default threshold: 2)
6. **Novelty gate** — authoritative atomic claim (`SELECT … FOR UPDATE`)
7. Cooldown gate
8. Concurrency cap (`max_concurrent_investigations`, default: 2; QA-only count via `qa_patrol_id IS NOT NULL`)
9. Circuit breaker
10. Model resolution (`complexity = "self_healing"` tier)

Gate rejections before any investigation session launches are recorded as dispatch decisions
(not execution failures) and do not contribute to the circuit-breaker failure streak.

**Worktree isolation:**

- `git fetch origin main` first.
- Worktree at `self-healing/qa/<fingerprint-prefix>-<timestamp>/`.
- Branch `qa/fix-<fingerprint-prefix>-<timestamp>`.
- Cleaned up (`git worktree remove --force`) on any terminal outcome.

**Agent sandbox** — the spawned investigation agent receives:

| Available | Not available |
|---|---|
| `GH_TOKEN` (from `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")`) | Butler DB connection strings |
| `PATH`, `UV_CACHE_DIR` | API keys, OAuth tokens, user data |
| *(nothing else)* | Any `BUTLERS_*` env vars |

The spawner sets an empty MCP server config when `trigger_source="qa"`, preventing access to
live production state. The agent's working directory is a QA-owned helper subdirectory inside
the worktree with a local `AGENTS.md` override that disables unrelated repo-level workflow
instructions (`bd` usage, self-managed PR/push steps, etc.).

**Agent prompt composition:**

- Fingerprint, exception type, sanitized event summary, call site, source butler name,
  occurrence count and time range, discovery source type.
- `structured_evidence` section (Phase 1): session IDs (from `session_records` source) or
  log filename + level (from `log_scanner` source) — no raw log content, no user data.
- Instructions to read source code, identify root cause, implement fix, run targeted tests,
  commit, and NOT include any user data, PII, or sensitive content in commits or PR descriptions.
- If the finding originated from `butler_reports` with a non-empty `context`, that anonymized
  diagnostic reasoning is included as a head-start for the agent.

**Anonymized PR pipeline:**

1. Agent commits fix and pushes branch.
2. PR title and body pass through `anonymize()` + `validate_anonymized()`.
3. If validation fails, the remote branch is deleted and the attempt transitions to
   `anonymization_failed`.
4. PR created via `gh pr create` with labels `["self-healing", "automated"]`.
5. PR body includes: root cause, affected butler(s), fix summary, patrol cycle reference, and
   (when `dashboard_base_url` is configured) a link to `/qa/investigations/<attempt_id>`.

**GitHub credentials:** scoped to branch push + PR creation + PR labeling only.
Token SHALL NOT have merge or approve permissions — humans remain in the merge seat.
Managed via the dashboard at `/settings` (QA Staffer card); if absent, the investigation
completes but transitions to `failed` with reason `"no_gh_token"`.

**Phase sessions:** v1 uses a single `investigate` phase per investigation. The tracking
infrastructure (`record_phase_session`, `update_phase_session_status`) is in place to support
separate diagnose/implement/verify phases as a future extension.

**Timeouts:** per-session timeout enforced by the spawner; overall investigation hard limit
(default: 60 minutes) enforced by the QA dispatcher — exceeding it transitions to `timeout`
and cancels any active phase session.

### D5: `healing_attempts` Table Reuse

The QA Staffer writes investigations to the existing `public.healing_attempts` table. The
`qa_patrol_id` column (nullable FK to `public.qa_patrols`) distinguishes QA-originated rows
from legacy per-butler self-healing rows:

- `qa_patrol_id IS NOT NULL` → QA-originated investigation.
- `qa_patrol_id IS NULL` → legacy per-butler self-healing attempt.

The QA concurrency cap counts only QA-originated rows (`qa_only=True`). Legacy rows do not
consume QA budget and vice versa. Both paths share the global semaphore (RFC 0001 two-tier
model). The existing `/api/healing/attempts` endpoint continues to return both sets — the
`qa_patrol_id` field distinguishes them without a schema change.

### D6: Patrol Loop Scheduling

The QA Staffer's scheduler drives a patrol loop at a configurable interval (default: 10 minutes).
Each patrol cycle is a discrete unit of work with its own `public.qa_patrols` record:

```
status: running | clean | findings_dispatched | error | skipped_overlap
columns: id (UUIDv7), started_at, completed_at, status,
         findings_count, novel_count, dispatched_count,
         log_lookback_minutes, sources_polled (text[]), error_detail
```

**Overlap prevention:** if a patrol tick fires while the previous cycle is still running, the
tick is skipped and recorded as `status = "skipped_overlap"`.

**Crash recovery:** on daemon restart, stale `status = "running"` patrol rows are transitioned to
`status = "error"` with `error_detail = "daemon restart during patrol"`. Findings that were novel
but not yet dispatched when the crash occurred are not durably queued at the patrol level — they
will be rediscovered by `session_records` or `log_scanner` on the next cycle.

**Critical-severity fast path:** a finding with `severity = 0` (critical) that arrives via
`butler_reports` (the reactive relay) triggers an immediate mini-patrol for that finding only,
without waiting for the next scheduled tick.

**Configuration** (all fields under `[modules.qa]` in `butler.toml`):

| Key | Default | Notes |
|---|---|---|
| `patrol_interval_minutes` | 10 | Interval between patrol ticks |
| `log_lookback_minutes` | 15 | Lookback window passed to all sources |
| `max_concurrent_investigations` | 2 | QA-only concurrency cap |
| `severity_threshold` | 2 | Minimum severity for dispatch |
| `enabled_sources` | `["log_scanner", "session_records", "butler_reports"]` | Active sources |
| `max_reactive_buffer` | 50 | Max buffered reactive findings; oldest dropped on overflow |
| `log_scanner_max_entries` | 10000 | Error/warning candidates per scan (benign lines excluded) |
| `log_scanner_max_findings` | 100 | Distinct fingerprints returned per scan |

### D7: Dashboard Surfacing

All QA API routes live under `/api/qa/`. The frontend surfaces live at `/qa`,
`/qa/patrols/:patrolId`, and `/qa/investigations/:attemptId`. The existing `/api/healing/`
router is unchanged.

**Retention.** Raw evidence stored on qa_findings.structured_evidence.evidence_lines[] is purged after 30 days. Cases still in non-terminal state are exempt until 14 days past their terminal transition. The cleanup job retains the narrative payload (headline, hypothesis, why_this_fix, diff_snapshot, counter_evidence, blurb_segments, claims) indefinitely; only evidence_lines[] is purged.

**Key API endpoints:**

| Endpoint | Description |
|---|---|
| `GET /api/qa/summary` | Staffer status, last/next patrol, 24h + all-time stats, circuit breaker, active sources |
| `GET /api/qa/patrols` | Paginated patrol list, `started_at` descending |
| `GET /api/qa/patrols/:patrolId` | Full patrol record with nested findings |
| `GET /api/qa/patrols/:patrolId/findings` | All findings for a patrol, with dedup reasons and source types |
| `GET /api/qa/investigations` | Paginated QA-originated `healing_attempts`; `?status=` filter |
| `GET /api/qa/known-issues` | Active/open issues (`dispatch_pending`, `investigating`, `pr_open`) grouped by fingerprint |
| `GET /api/qa/meta-review` | QA-self-recursive findings routed to operator lane; never auto-investigated |
| `POST /api/qa/dismiss` | Add a fingerprint to the dismissal cache with configurable duration |
| `GET /api/qa/dismissals` | List active dismissals |
| `DELETE /api/qa/dismissals/:fingerprint` | Remove a dismissal |
| `POST /api/qa/force-patrol` | Trigger an immediate patrol cycle |
| `GET /api/qa/trends` | Daily aggregated stats for the last N days |

Admission-control decisions that did not launch an investigation session (cooldown, concurrency
cap, circuit breaker, etc.) MUST be exposed via `GET /api/healing/dispatch-events` and MUST NOT
be conflated with failed investigation executions — preserving the RFC 0007 contract.

**Dashboard pages:**

- `/qa` — Status banner, 24h summary cards, investigation pipeline (Kanban by status),
  known-issues panel (filterable/sortable), recent patrols table, success-rate trend (7d),
  source breakdown chart.
- `/qa/patrols/:patrolId` — Patrol metadata, findings table (sortable by severity/source,
  filterable by dedup reason), dispatch summary with links to investigations.
- `/qa/investigations/:attemptId` — Investigation metadata, state-transition timeline, error
  context, PR card (clickable link to GitHub, PR number badge, current PR status), session link,
  patrol link, Retry and Dismiss actions.
- `/` (home) — QA staffer summary widget: status indicator, last patrol result, active
  investigations count, open PRs count, PRs merged (7d), click-through to `/qa`.

**PR status tracking:** on each patrol cycle the QA staffer checks the GitHub status of every
open `pr_open` attempt via `gh pr view --json state` (running in the QA daemon context, not an
agent worktree). Merged → `pr_merged`; closed-without-merge → `failed` with
`error_detail = "pr_closed_without_merge"`.

### D8: Observability (RFC 0005 Compliance)

The QA Staffer integrates with the project's observability stack:

**OpenTelemetry spans:**
- `qa.patrol` — one per patrol cycle with attributes `qa.patrol_id`, `qa.sources_polled`,
  `butler.name = "qa"`. Child spans: `qa.discover.<source_name>`, `qa.triage`, `qa.dispatch`.
- `qa.investigation` — root span (not a child of `qa.patrol`; investigations outlive the patrol).
  Attributes: `qa.attempt_id`, `qa.fingerprint`, `qa.source_butler`, `qa.severity`.

**Prometheus metrics (low-cardinality per RFC 0005 — no UUIDs, fingerprints, or butler names
as label values):**

| Metric | Type | Labels |
|---|---|---|
| `qa_patrol_total` | counter | `status` |
| `qa_findings_total` | counter | `source_type`, `dedup_reason` |
| `qa_investigations_active` | gauge | *(none)* |
| `qa_patrol_duration_seconds` | histogram | *(none)* |
| `qa_investigation_duration_seconds` | histogram | `status` |

### D9: Roster Identity

The QA Staffer lives at `roster/qa/` with `type = "staffer"` in `butler.toml`. As a staffer it
is excluded from Switchboard user-message classification and does not register daily briefing
contribution schedules. It registers with the Switchboard for butler-to-staffer reachability
(RFC 0003 §8 covers the staffer archetype). Cross-butler DB access uses the
`public.v_qa_recent_failures` read-only view per the RFC 0010 pattern.

## Legacy Self-Healing Deprecation

### Status of legacy specs

The following capability specs are **deprecated** and superseded by the QA pipeline defined in
this RFC:

| Spec | Status | Superseded by |
|---|---|---|
| `self-healing-dispatch` | **Deprecated** | QA investigation dispatch (§D4) |
| `self-healing-module` | **Deprecated** | QA `butler_reports` discovery source (§D1) + `report_finding` MCP tool |
| `self-healing-skill` | **Deprecated** | QA skill at `roster/shared/skills/self-healing/` (content preserved; attribution changes) |

The `healing-*` support specs (`healing-anonymizer`, `healing-model-tier`, `healing-session-tracking`,
`healing-worktree`) are **implementation substrate, not deprecated**. The QA pipeline reuses
them directly:

- `healing-anonymizer` — QA anonymizes PR content through the same pipeline.
- `healing-model-tier` — QA investigations use `complexity = "self_healing"` model resolution.
- `healing-session-tracking` — QA writes to `public.healing_attempts`; the schema is extended
  with `qa_patrol_id` but the table itself is the same.
- `healing-worktree` — QA uses the shared worktree lifecycle infrastructure.

### Why option (b) — declare deprecated, do not fold

The reconciliation report (§"Doctrine conflicts found") identified two resolution options for
the `self-healing-dispatch` / `qa-investigation-dispatch` coexistence problem:

- **(a)** Fold `self-healing-*` specs into the QA capability set.
- **(b)** Mark them deprecated with superseded-by pointers.

Option (b) is recommended for three reasons:

1. **Minimal churn.** Live code currently implements the `self-healing-module` `report_error`
   tool. The correct migration path is to route `report_error` through the QA Staffer's
   `report_finding` relay rather than deleting the tool. Folding specs would suggest the code
   disappears; deprecation correctly signals "superseded, remove in a follow-up cycle."
2. **Audit trail.** Deprecation with a superseded-by pointer preserves the design history and
   makes future archive-sweep automation straightforward (`openspec-bulk-archive-change` can
   batch-archive any spec carrying a `**Status:** Deprecated` banner).
3. **Scope hygiene.** This RFC is a design contract, not an implementation task. Physically
   archiving 3+ specs belongs in a separate chore bead ("sweep self-healing-* specs as
   superseded") that can be reviewed and merged atomically.

### Migration path

1. Immediately: `self-healing-dispatch`, `self-healing-module`, and `self-healing-skill` specs
   receive deprecation banners pointing to this RFC (see note below about spec edits).
2. Follow-up bead: butlers' existing `report_error` MCP tool implementations are updated to
   relay via `switchboard_client.call_tool("route", {"target_butler": "qa", ...})` instead of
   invoking `dispatch_healing()` directly, making them aliases over the QA relay.
3. Archive sweep bead: once the relay migration is merged and validated, the deprecated specs
   are bulk-archived via `openspec-bulk-archive-change`.

## Non-Goals

- QA does NOT route user messages; it has no Switchboard classification authority.
- QA does NOT merge PRs; human review remains mandatory.
- QA does NOT run in per-butler processes; it is a single staffer with cross-system access.
- QA does NOT leak un-anonymized log content beyond the private operator dashboard. PR titles, PR bodies, branch commit messages, and any externally-egress paths SHALL pass through anonymize() + validate_anonymized(). Raw log lines MAY be stored on qa_findings.structured_evidence.evidence_lines[] strictly to support the internal dossier UI.
- QA does NOT provide user-facing insights or recommendations; that is the Proactive Butler's domain.
- QA does NOT implement the `prometheus_metrics`, `mcp_reachability`, `scheduler_drift`,
  `connector_heartbeat`, or `git_regression` discovery sources in v1.

## Rollout

1. Bootstrap `roster/qa/` with `butler.toml` (`type = "staffer"`), `MANIFESTO.md`, `CLAUDE.md`,
   `AGENTS.md`.
2. Apply database migrations: `public.qa_patrols`, `public.qa_findings`, `public.qa_dismissals`,
   add `qa_patrol_id` column to `public.healing_attempts`, create `public.v_qa_recent_failures`
   view with per-schema GRANTs.
3. Implement `DiscoverySource` protocol and v1 sources (`log_scanner`, `session_records`,
   `butler_reports`) under `src/butlers/core/qa/sources/`.
4. Implement triage layer (`src/butlers/core/qa/triage.py`).
5. Implement dispatch layer (`src/butlers/core/qa/dispatch.py`) — reusing `healing/` substrate.
6. Implement `qa` module (`src/butlers/modules/qa/`) with `register_tools`, `on_startup`,
   `on_shutdown`, and patrol loop scheduler entry.
7. Add API routes at `roster/qa/api/router.py` (auto-discovered per RFC 0007).
8. Add frontend pages: `/qa`, `/qa/patrols/:patrolId`, `/qa/investigations/:attemptId`,
   home-page QA widget.
9. Update Switchboard to exclude QA staffer from user-message routing and register for
   butler-to-staffer MCP reachability.
10. Provision `BUTLERS_QA_GH_TOKEN` secret via the dashboard; confirm git author identity settings.
11. Mark `self-healing-dispatch`, `self-healing-module`, `self-healing-skill` as deprecated
    in `openspec/specs/` (superseded-by pointer to RFC 0015).

## Open Questions

- **Should `butler_reports` relay persist to DB before buffering?** Currently the buffer is
  volatile (lost on restart). The `session_records` source provides recovery, but there is a
  small race window between a `report_finding` call and the next `session_records` scan if the
  session record is written after the patrol runs. For v1 the volatile buffer is acceptable.
- **Evidence Phase 2 scope.** `structured_evidence` currently carries Phase 1 fields
  (session IDs, log filename/level). Phase 2 would extend `v_qa_recent_failures` to include
  `request_id`, `trace_id`, `runtime_type`, `model`, and tool-call summaries. Deferred pending
  a migration defining the extended view.
- **Meta-review escalation.** QA-self-recursive findings currently land in the meta-review lane
  and stop. A future extension would notify the operator via the notification system (RFC 0011
  pattern) rather than requiring them to check `/api/qa/meta-review` proactively.

## References

- RFC 0001 (daemon lifecycle) — QA uses the standard butler lifecycle; two-tier concurrency model.
- RFC 0002 (modules) — QA patrol and sources are an opt-in module (`modules/qa/`).
- RFC 0003 (switchboard) — butler-to-staffer MCP routing via `route()` tool; QA excluded from
  user-message classification.
- RFC 0005 (observability) — OTel span structure and Prometheus low-cardinality label discipline.
- RFC 0006 (database isolation) — QA reads cross-butler data only via the sanctioned
  `public.v_qa_recent_failures` view (RFC 0010 pattern).
- RFC 0007 (dashboard) — QA API route auto-discovery; response envelope conventions;
  `/api/qa/summary`, `/api/qa/investigations`, `/api/qa/meta-review` endpoints registered in §D7.
- RFC 0010 (cross-butler briefing) — precedent for sanctioned cross-schema read surfaces.
- `openspec/specs/staffer-qa/` — QA Staffer identity and patrol loop requirements.
- `openspec/specs/qa-triage/` — Triage layer and deduplication requirements.
- `openspec/specs/qa-investigation-dispatch/` — Dispatch pipeline and worktree isolation requirements.
- `openspec/specs/qa-log-scanner/` — Log scanner source requirements.
- `openspec/specs/qa-dashboard/` — Dashboard page and API endpoint requirements.
- `openspec/specs/healing-anonymizer/` — PR anonymization pipeline (reused by QA).
- `openspec/specs/healing-model-tier/` — `self_healing` model tier (reused by QA).
- `openspec/specs/healing-session-tracking/` — `healing_attempts` table schema (extended by QA).
- `openspec/specs/healing-worktree/` — Worktree lifecycle infrastructure (reused by QA).
