# QA Staffer — Infrastructure Contract

**Service type:** Staffer (infrastructure)
**Port:** 41110
**DB:** `butlers` / **Schema:** `qa`

---

## Purpose

The QA Staffer is the system-wide SRE for the butlers ecosystem. It runs a
continuous patrol loop that discovers errors across multiple channels, triages
and deduplicates findings, and dispatches automated investigation agents that
propose fixes via PRs. It subsumes per-butler self-healing into a unified
quality assurance function.

---

## Responsibilities

- **Patrol loop:** Execute a scheduler-driven scan at a configurable interval
  (default: 10 minutes). Each cycle is a discrete DB-recorded unit of work.
- **Discovery:** Poll all registered discovery sources: log scanner,
  session records SQL view, and reactive butler-reported findings.
- **Triage:** Deduplicate findings against active investigations, dismissals,
  and cooldown windows before dispatching.
- **Investigation dispatch:** Create worktrees with a `qa/` prefix, spawn
  isolated investigation agents, and monitor their outcomes via watchdog tasks.
- **PR pipeline:** Produce anonymized PRs with `self-healing` and `automated`
  labels. Humans remain in the merge seat.
- **Relay reception:** Accept findings from butler self-healing modules via
  Switchboard `route()` → `report_finding` tool. This is direct
  butler-to-staffer tool routing — not user-message classification.
- **Status reporting:** Expose `get_qa_status` and `force_patrol` MCP tools.

## Non-Responsibilities

- QA Staffer does **not** respond to user messages (staffer type).
- QA Staffer does **not** register daily briefing contributions.
- QA Staffer does **not** perform outbound user-channel delivery.
- QA Staffer does **not** merge PRs — humans review and merge.
- QA Staffer does **not** access butler schemas directly. It uses only
  `public.v_qa_recent_failures` (sanctioned read-only SQL view) and writes to
  `public.qa_patrols`, `public.qa_findings`, `public.healing_attempts`.

---

## SLAs

| Metric | Target |
|---|---|
| Patrol interval | 10 minutes (configurable) |
| Reactive finding latency | Next patrol tick (or immediate for severity 0) |
| Investigation dispatch decision | Within patrol cycle completion |
| PR creation | After investigation agent session completes |
| Availability | Should run continuously; restart recovers stale patrol rows |

---

## Discovery Sources

| Source | Method | Lookback |
|---|---|---|
| `log_scanner` | Parse JSON log files from `logs/butlers/`, `logs/connectors/`, `logs/uvicorn/` | Configurable (default: 15 min) |
| `session_records` | Query `public.v_qa_recent_failures` SQL view | Configurable lookback |
| `butler_reports` | Drain in-memory buffer of reactive relay findings | N/A (time-bounded by patrol interval) |

All source filtering is tool-based (zero LLM invocations during discovery).

---

## Issue Triage Policy

Findings are deduplicated against (in order):
1. Active `healing_attempts` rows (status: `dispatch_pending`, `investigating`, `pr_open`)
2. Active `qa_dismissals` (not yet expired)
3. Per-fingerprint cooldown window (recent terminal attempts within `cooldown_minutes`)

Novel findings are prioritized: severity ascending (0=critical first), then
occurrence_count descending.

Severity threshold: default 2 (medium). Findings with severity > threshold are
skipped without investigation.

---

## Investigation Dispatch Policy

Each novel finding above the severity threshold triggers:
1. Atomic `create_or_join_attempt()` — atomic novelty claim.
2. Worktree creation with `qa/` prefix.
3. Investigation agent spawn with sandboxed environment.
4. Timeout watchdog (default: 30 minutes).

Investigation agents run with only: `GH_TOKEN`, `PATH`, and build-tool
variables. No butler DB credentials, API keys, or OAuth tokens leak in.

---

## PR Creation Standards

- Labels: `["self-healing", "automated"]`
- GitHub token: retrieved via `CredentialStore.resolve("BUTLERS_QA_GH_TOKEN")`
- Token scope: branch push, PR create/label only — **no merge/approve**
- Anonymization: all event summaries and agent context passed through
  `anonymize()` before inclusion in PRs

---

## Anonymization Requirements

All error event summaries extracted from session records must be anonymized
before storage and PR submission. Raw log lines are never stored in
`qa_findings`. Only computed fingerprints, exception types, call sites, and
sanitized summaries are persisted.

---

## Permissions Model

| Resource | Access |
|---|---|
| `public.qa_patrols` | Read + Write |
| `public.qa_findings` | Read + Write |
| `public.qa_dismissals` | Read + Write |
| `public.healing_attempts` | Read + Write (via core healing package) |
| `public.v_qa_recent_failures` | Read only (sanctioned SQL view) |
| Butler-owned schemas | No direct access |

`cross_butler_access = ["*"]` enables Switchboard routing from any butler to
the QA staffer's `report_finding` tool. This does NOT grant DB-level cross-schema
access.

---

## Failure Modes and Recovery

| Failure | Symptom | Recovery |
|---|---|---|
| Discovery source exception | Source skipped for this patrol cycle; `error_detail` populated | Next patrol retries automatically |
| DB pool exhaustion | Patrol hangs or fails | Daemon restart; stale `running` patrol rows recovered on startup |
| GitHub API rate limiting | PR creation fails; attempt transitions to `failed` | Next patrol cycle for the same fingerprint will retry after cooldown |
| Worktree creation failure | Investigation not dispatched; attempt marked `failed` | Next patrol cycle retries if fingerprint recurs |
| No GitHub token | Investigation completes but transitions to `failed` with `"no_gh_token"` | Provision `BUTLERS_QA_GH_TOKEN` via dashboard /secrets |
| Circuit breaker tripped | All dispatch halted after N consecutive failures | Investigate and fix underlying cause; circuit breaker resets on next success |
| Repeated anonymization failures | PR pipeline halted | Manual review required; operator clears via dashboard |

---

## Dependency Graph

### Depends On

- **PostgreSQL (`butlers.qa` schema):** State store, session log
- **PostgreSQL (`public` schema):** `qa_patrols`, `qa_findings`, `qa_dismissals`, `healing_attempts`, `v_qa_recent_failures`
- **Switchboard:** Registration, liveness, and routing of `report_finding` calls from butlers
- **GitHub API (`BUTLERS_QA_GH_TOKEN`):** PR creation for investigation outcomes

### Depends On QA Staffer

- **All domain butlers:** Relay `report_error` findings via Switchboard `route()` → `report_finding` when QA is available
- **Switchboard:** Routes butler-to-QA `report_finding` calls

---

## Concurrency Model

- Per-staffer semaphore: controls `max_concurrent_investigations` (default: 2)
- Global semaphore: shared with all butler sessions (RFC 0001)
- Investigation agents acquire both semaphores independently of the reporting butler

---

## Observability

Prometheus metrics registered at startup:
- `qa_patrol_total{status}` — patrol outcomes counter
- `qa_findings_total{source_type, dedup_reason}` — findings counter
- `qa_investigations_active` — gauge
- `qa_patrol_duration_seconds` — histogram
- `qa_investigation_duration_seconds{status}` — histogram

OTel spans per patrol cycle:
- `qa.patrol` parent span
- `qa.discover.<source>` child spans
- `qa.triage` child span
- `qa.dispatch` child spans
- `qa.investigation` independent root span per investigation

---

## Escalation

Circuit breaker trips (N consecutive failures):
- Log WARNING
- Dashboard alert via `qa_investigations_active` gauge and patrol status

Repeated anonymization failures:
- PR pipeline halted
- Log ERROR for each failed attempt

If QA Staffer is unreachable:
- Butlers fall back to direct self-healing dispatch (legacy path)
- `qa_fallback_activations_total` counter increments
- Escalate if fallback activations persist over extended periods
