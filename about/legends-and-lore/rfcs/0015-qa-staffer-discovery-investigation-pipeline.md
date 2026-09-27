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
self-repair stacks would create ambiguity about which system owns a given finding. The
`self-healing-*` specs are retired; see §"Healing Substrate Reuse".

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
| `tool_call_failures` | `public.v_qa_tool_call_failures` view | Finds failed tool calls inside otherwise-successful sessions |
| `infra_state` | Connector/butler state views, backup facts, external-deadman audit events | Point-in-time health checks; ignores the patrol lookback window |

**Future sources** (protocol accommodates without triage/dispatch/dashboard changes):
`prometheus_metrics`, `mcp_reachability`, `scheduler_drift`, `git_regression`.

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
| No GitHub publication credential | Butler DB connection strings and publisher secrets |
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

1. Agent commits only in its credential-free investigation worktree; it never pushes.
2. Trusted export freezes every newly reachable source/commit/author/message plus proposed PR
   text and fixed labels, then validates the entire immutable artifact before any external mutation.
3. Validation refusal records `anonymization_failed` without remote publication or deletion.
4. The trusted deterministic publisher reserves the exact attempt-bound mutation stage durably,
   publishes only the validated branch bytes with an atomic expected-head fence, and creates/updates
   only the bound PR with sanitized fixed labels `["self-healing", "automated"]`.
5. PR body includes root cause, affected butler(s), fix summary, patrol reference and the configured
   approved dashboard link; every field remains subject to the same pre-egress validation.
6. Definite failure with proof that no PR was created may report `proposal_state = "unpublished"`
   while retaining sealed source, diff and notes. Missing local PR metadata alone is not such proof.
   Timeout or uncertain provider response records an ambiguous publication stage; it preserves
   expected head/resource binding and immutable evidence for exact read-only reconciliation.
   It never blindly retries, deletes a remote resource, or claims published/unpublished without proof.

**GitHub credentials:** resolved only by a trusted deterministic publisher from the dedicated QA category. Investigators have no token or generic publication authority. The closed surface allows only validated attempt-bound branch/PR publication and fixed labels; merge, review, approval and queue are refused. Provider grants may be coarser than this effective boundary. Humans remain in the merge seat; trusted-publisher compromise remains a stated residual risk.
Managed via the dashboard at `/settings` (QA Staffer card); if absent, the investigation
completes but transitions to `failed` with reason `"no_gh_token"`.
An authenticated-but-forbidden push or PR operation instead records `git_auth_failed` with
content-blind repository-scope/organization-authorization guidance.

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
status: running | clean | findings_dispatched | suppressed | error | skipped_overlap
columns: id (UUIDv7), started_at, completed_at, status,
         findings_count, novel_count, dispatched_count,
         log_lookback_minutes, sources_polled (text[]), error_detail,
         origin (scheduled | operator_synthetic), enabled_sources_snapshot (text[]),
         enabled_sources_config_digest, discovery_complete (boolean)
```

The local patrol scheduler continues when only the remote Switchboard registry
observation of QA is stale; an explicit administrative pause or quarantine is
a separate policy gate. An independent supervised control-plane observer checks
patrol age, because QA cannot discover a patrol that never ran.
Only a completed scheduled `clean`, `findings_dispatched`, or genuine
filtered-finding `suppressed` patrol with every source enabled under the
current configuration successfully completed renews the assurance clock.
The origin, captured enabled set/config digest, and completion marker prove
that claim. An `error`, `skipped_overlap`, dashboard-created synthetic
`suppressed`, still-running, older-configuration, or ambiguous legacy row does
not. Existing historical rows remain readable but are not backfilled as
qualifying from a status or free-text error alone.

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
| `enabled_sources` | `["log_scanner", "session_records", "butler_reports", "tool_call_failures", "infra_state"]` | Active sources |
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
| `GET /api/qa/known-issues` | Active/open issues (`investigating`, `pr_open`) grouped by fingerprint; `dispatch_pending` is not a persisted attempt status |
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

## Healing Substrate Reuse

QA reuses the `healing-*` substrate specs directly: `healing-anonymizer` (PR content
anonymization), `healing-model-tier` (`complexity = "self_healing"` model resolution),
`healing-session-tracking` (`public.healing_attempts`, extended with `qa_patrol_id`), and
`healing-worktree` (shared worktree lifecycle).

## Non-Goals

- QA does NOT route user messages; it has no Switchboard classification authority.
- QA does NOT merge PRs; human review remains mandatory.
- QA does NOT run in per-butler processes; it is a single staffer with cross-system access.
- QA does NOT leak un-anonymized log content beyond the private operator dashboard. PR titles, PR bodies, branch commit messages, and any externally-egress paths SHALL pass through anonymize() + validate_anonymized(). Raw log lines MAY be stored on qa_findings.structured_evidence.evidence_lines[] strictly to support the internal dossier UI.
- QA does NOT provide user-facing insights or recommendations; that is the Proactive Butler's domain.
- QA does NOT implement the `prometheus_metrics`, `mcp_reachability`, `scheduler_drift`,
  `connector_heartbeat`, or `git_regression` discovery sources in v1.

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

## Amendment (2026-09-23): Independent Patrol Assurance and Fleet Correlation

**Status:** Approved target contract in
`openspec/changes/restore-butler-control-plane-liveness`; implementation
remains separate from this design amendment.

Derived remote registry staleness cannot suppress QA's local deterministic
patrol. A separately supervised control-plane observer checks both fleet
readiness and the age of the latest completed patrol. If QA stops scheduling,
the observer still records durable, content-blind overdue evidence and the
semantic `/ready` response becomes false. Observer failure itself is
distinguishable from a healthy fleet; failed or partial observation cannot
resolve an open condition.

One correlated fleet expiry produces one condition episode and attention
stream with bounded per-butler impact evidence, rather than one autonomous QA
investigation for each stale agent. Its source and fingerprint use the
infrastructure condition ledger's explicit canonical identity, and resolution
requires a complete successful snapshot. QA discovery can continue recording
findings while model or GitHub authority is unavailable; investigation
dispatch remains subject to its existing gates. The observer is deterministic
infrastructure, not a second QA LLM agent or a replacement for the five
`DiscoverySource` implementations in §D1.
At cutover, legacy per-butler liveness episodes remain historically readable,
their impact is linked to the fleet condition, and duplicate investigation or
owner pages are suppressed. They resolve only when a complete receiver-observed
snapshot proves recovery for the affected daemon, never merely because the
fleet condition replaced their producer.

## Adopted restricted publisher target

The exact owner-adopted eight-requirement contract is preserved at `openspec/changes/confine-qa-publication-authority/adopted-contract.md` (SHA25690d904ee0e4cb9a8c2b23ca07739bf580d167fa9e2f1d7f0a5b0a59959b2d8e9). Its purpose-bound binding, process isolation, immutable export, closed operations, provider-control evidence and ambiguous-outcome handling are binding target behavior. Implementation remains outstanding; this amendment does not claim existing deployments are confined. Live credentials/provisioning/provider canary/cleanup/deployment/activation remain separately gated.

## Restricted publication authority and durable outcome contract

The following adopted target is unimplemented until its source program and isolation verification land.

## Process and filesystem boundary

Trusted dispatcher creates attempt binding before spawn. Run investigation AND review-follow-up build/test code under a distinct unprivileged OS identity and private process/mount namespace. Publisher has separate service identity, no ptrace/shared proc visibility or agent-writable executable/config/library path. Secret files and process environment belong only to publisher namespace; neither root-equivalent container privilege, host PID, shared writable sockets, Docker socket, inherited FDs nor same-UID isolation is acceptable. Sandbox sees only its disposable checkout, explicitly approved caches and bounded build environment; do not inherit HOME, PYTHONPATH, gitconfig, credential helpers, shell startup or agent-selected executable lookup. Credentials from environment override are not an isolation primitive. No GitHub token enters initial or follow-up environment. If runtime cannot enforce this profile, investigation may remain nonpublishing, and automated publication is unavailable, never token-injection fallback.

A typed dispatcher/publisher IPC message identifies only a server-created attempt and expected artifact digest; the request carries no repository/ref/URL/token/command choice. Per-attempt session identity prevents another sandbox claiming it. Dispatcher-owned state binds allowlisted repository ID, target base, newly allocated QA branch, allowed fixed labels, expected remote head and created PR ID. Peer credentials and issuer binding are validated before processing. No generic execute/publish RPC is exposed to model tools. Provider grants and live repository protections remain separately verified operational evidence.

## Immutable export before all remote effects

Inventory initial investigation, follow-up, clone/fetch/preparation, branch update and failure cleanup paths in dispatch.py. No credentialed subprocess ever executes in an agent checkout or consumes its .git/config, hooks, filters, alternates, symlinks, credential helpers, URL or executable. Trusted uncredentialed export freezes a full source snapshot and required ancestry against server-pinned base. Reconstruct trusted publication commits from sealed content rather than pushing agent-controlled commit history. Validate EVERY newly reachable blob/tree/commit/author/message and final PR text/fixed labels before push; no PII in earlier commits can hide behind a clean final diff. Unsupported binary/submodule/link/metadata sanitization fails closed rather than assuming clean. Existing anonymizer remains required but is not a semantic guarantee for arbitrary source; validation may refuse an unprovable export. Artifact is copied into publisher-owned immutable content storage and digest checked at use, so agent edits cannot race validation. No agent code is executed for validation inside credential boundary.

Repository operations use pinned trusted transport with fixed origin/repository/ref; no user git configuration. Ref creation/update uses ordinary non-force push with an atomic expected-old-ref check at receive-pack; a transport unable to express the binding fails closed. New commits descend from the bound expected head; unknown/changed remote head yields conflict, not force, rebase, deletion or repair of foreign work. PR creation binds exact repository/base/head; PR update only title/body/fixed labels of recorded attempt PR. No retarget, auto-merge, approval/review, merge queue, protected-ref write, delete, release, settings/workflow/secret/access-management API or generic URL operation. Agent workflow content is treated as untrusted source; effective provider/CI controls must be assessed before activation so branch publication cannot quietly regrant forbidden agent authority through privileged automation. This does not change ordinary human-requested source PR workflows.

## Durable state and failure semantics

Persist attempt-bound immutable export digest, exact expected source/remote head, allocated branch, PR identity, next operation and outcome before egress, outside agent filesystem. Use a new then-free core migration (current inspected head core249; no QA-specific migration chain) beside public.healing_attempts/core005 and qa_patrols/core051; a dedicated public.qa_publication_operations table is proposed with operation UUID, attempt FK, monotonically ordered stage, expected/new OID, fixed operation kind, request digest, start/end timestamps and sanitized result enum. No credential/provider body/private identifier copied into operational receipts. Server-held binding state may retain repository/ref/PR IDs as private authority data, never as LLM result fields.

Lifecycle: sealed -> validated -> branch_publish_reserved -> branch_published -> pr_create_reserved -> pr_created -> labels_reserved -> complete; terminal operation outcomes refused/conflict/ambiguous; an ambiguous operation still holds its publication lineage active as specified below. A unique attempt+operation sequence admits one mutation. Duplicate same digest returns stored status; changed payload refuses. Reserve durably BEFORE send. Crash/timeout after reservation is ambiguous, not retryable by guessing. Read-only reconcile only the exact bound ref/PR and intended head; if proof is absent retain hold. Do not scan arbitrary repositories or allocate another branch/PR. No remote deletion, including sanitizer failure or rollback. Failure before first push publishes nothing; failure after a prior valid publication retains existing resource and sanitized stage. Deletion/cleanup needs separate operational authority. An unsuccessful new update cannot erase the previous successful published proof.

Roles: investigation has no DB access. QA trusted publisher uses a separately scoped pool with effective butler_qa_rw role for operation state under enabled+forced RLS; exact migration/runtime owner privileges and bootstrap replay tests must establish no cross-butler/roleless mutation. State transition function validates expected revision and fixed columns; no caller-selected SQL/schema. Table owner is fenced, TRUNCATE denied explicitly, terminal receipts immutable. Backups retain attempt bindings/operations with their owning QA database; restore disables publication until a new executor epoch invalidates pre-restore pending mutation capabilities and ambiguous old stages are reconciled. No automatic after-restore replay; do not use short-lived runtime-probe nonce retention for this durable authority. No live DB migration is authorized.


Export sealing must quiesce the investigator and all descendants or copy via immutable handles before validation; a worktree lock alone is insufficient. No shared repository .git, host HOME or Docker socket enters either isolation profile. Existing device-auth sandbox supplies candidate low-level namespace primitives only; it does not establish the QA boundary.

QA publication rejects agent changes to workflow/action definitions, CI privilege configuration and publication-control files by default. These cannot become an alternate credential/merge route through remote automation. This is an allowed-tree constraint enforced before push, not merely a promise not to execute workflows locally. Any separately desired CI repair needs a human-owned source workflow, never a broader QA publisher operation.

### Durable backup and restore fence

Publication operation state and immutable resource bindings SHALL be backed up in a consistent snapshot with the owning QA attempt data; they are not expiring runtime-probe nonce records. Role-scoped export/import SHALL preserve forced RLS, terminal immutability and no-TRUNCATE fences without blanket runtime/migration-login access. Restore SHALL start publication disabled and rotate a host-held executor epoch outside restored content before any new mutation admission. Old pending capabilities cannot execute against a restored snapshot lacking their consumed stage; all restored ambiguous/in-flight operations require exact read-only reconciliation or remain held. Enabling the new epoch is a separately authorized operational step, not automatic source rollback or migration.

### Ambiguous publication is a durable lineage admission hold

An ambiguous publication operation SHALL create or preserve an active publication hold keyed to its server-held investigation lineage (including the original finding identity and all follow-ups), not only its individual attempt UUID or operation sequence. Initial investigation completion, follow-up completion, watchdog timeout, cooldown expiry, triage, poll/restart recovery and create-or-join admission SHALL consult that hold before allocating any fresh publication attempt/ref/PR. A terminal transport record MUST NOT be mapped to ordinary failed/timeout that later permits a new lineage publication. The local investigation may finish computing, but publication remains held until exact bound-resource read-only reconciliation proves its outcome and explicitly resolves the hold. Operator intent to retry without that proof is not reconciliation.

Cleanup SHALL retain sealed artifact, expected-head/branch/PR binding, operation stage and lineage hold across worktree teardown and process/database recovery. No expiry, retention cleanup or automatic stale-attempt recovery can erase the hold or release a replacement resource. Resolved outcomes may follow their normal bounded next action only after the same durable transaction records the resource proof and hold resolution. Missing reconciliation evidence stays held; unrelated lineages remain independently runnable.
