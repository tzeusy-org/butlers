# Selective Email Backfill Strategy

Status: Normative (Target State)  
Last updated: 2026-02-22  
Primary owners: Switchboard + Email Connectors + Dashboard API

Depends on:
- `docs/connectors/interface.md` (section 14, optional backfill polling protocol)
- `docs/connectors/email_ingestion_policy.md` (tiered ingestion rules and retention behavior)
- `docs/roles/switchboard_butler.md` (backfill MCP lifecycle tools + `switchboard.backfill_jobs`)
- Beads `butlers-0bz3.5`, `butlers-0bz3.12`, `butlers-0bz3.13`

## 1. Purpose
This document defines a selective, cost-aware email backfill strategy for historical Gmail ingestion.

The strategy uses MCP-mediated orchestration (Option B):
- Dashboard creates and controls backfill jobs through Switchboard MCP tools.
- Connectors poll Switchboard MCP for pending jobs and report progress through MCP.
- Connectors never write or read runtime backfill state via direct database access.

Goals:
- Recover high-value historical data (finance, health, relationship, travel) without full-archive cost explosion.
- Preserve connector contract boundaries (transport-only adapters, MCP-only coordination).
- Support pause/cancel/resume, cost caps, and auditable lifecycle state.

## 2. Motivation and Cost Model

### 2.1 Why selective backfill is required
Naive archive backfill is expensive and noisy for personal inboxes:
- 10-year archives commonly exceed 50,000 messages.
- Promotions/newsletters/system notifications dominate many inboxes.
- Full LLM classification on all messages produces high token spend and low-value storage churn.

Selective backfill applies category targeting and tiered ingestion rules so only high-value segments receive expensive processing.

### 2.2 Cost model
Let:
- `N_total` = total messages in date scope
- `p_full` = fraction routed to Tier 1/full classification (`0 <= p_full <= 1`)
- `T_full` = average tokens per full-classified message
- `R` = blended $/1M tokens
- `N_full = N_total * p_full`

Estimated classification cost:

`C = (N_full * T_full / 1_000_000) * R`

For naive full backfill, `p_full = 1`.

### 2.3 Worked example: naive vs selective vs on-demand
Assumptions:
- Archive size: `N_total = 50,000` emails
- Avg full classification tokens: `T_full = 10,000`
- Blended pricing: `R = $3.00` per 1M tokens
- Selective tier mix during backfill: `p_full = 0.30` (70% metadata-only or skipped by policy)

Results:
- Naive full backfill:
  - `N_full = 50,000`
  - Tokens = `500,000,000`
  - Cost = `$1,500`
- Selective batch backfill:
  - `N_full = 15,000`
  - Tokens = `150,000,000`
  - Cost = `$450`
  - Savings vs naive = `$1,050` (`70%`)
- On-demand backfill (illustrative):
  - Assume 200 user queries/year, 25 results/query -> `N_full = 5,000`
  - Tokens = `50,000,000`
  - Cost = `$150`
  - Savings vs naive = `$1,350` (`90%`)

This model isolates classification spend. Additional savings from lower storage and reduced fanout are out of scope of this estimate.

## 3. Orchestration Model (Option B: MCP-Mediated)

### 3.1 End-to-end flow
1. Dashboard API calls Switchboard MCP tool `create_backfill_job(...)`.
2. Switchboard writes a job row in `switchboard.backfill_jobs` with `status=pending`.
3. Connector backfill loop (default every 60s) calls `backfill.poll(connector_type, endpoint_identity)`.
4. If a job is assigned, connector traverses source history backward within `[date_from, date_to]`.
5. Connector submits each historical message through the same ingest pipeline as live traffic (`ingest.v1`, same idempotency keys).
6. Connector reports progress in batches via `backfill.progress(...)`.
7. Dashboard history/status views read `switchboard.backfill_jobs` for operator visibility.

Pause/cancel/resume:
- Dashboard uses `backfill.pause`, `backfill.cancel`, `backfill.resume`.
- Connector must treat returned status from `backfill.progress` as authoritative and stop when status is not active.

### 3.2 Connector contract boundary (normative)
Connectors implementing backfill:
- MUST coordinate lifecycle exclusively through Switchboard MCP tools.
- MUST NOT hold persistent database connections for runtime backfill control or progress.
- MUST NOT bypass Switchboard ingest for historical messages.

Operational DB ownership:
- Switchboard owns `switchboard.backfill_jobs`.
- Dashboard may read the table for progress rendering.
- Connector runtime path remains MCP-only.

## 4. Backfill Jobs Data Model
Switchboard MUST provide a durable backfill state table:

```sql
CREATE TABLE switchboard.backfill_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    connector_type TEXT NOT NULL,
    endpoint_identity TEXT NOT NULL,
    target_categories JSONB NOT NULL DEFAULT '[]',
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    rate_limit_per_hour INTEGER NOT NULL DEFAULT 100,
    daily_cost_cap_cents INTEGER NOT NULL DEFAULT 500,
    status TEXT NOT NULL DEFAULT 'pending',
    cursor JSONB,
    rows_processed INTEGER NOT NULL DEFAULT 0,
    rows_skipped INTEGER NOT NULL DEFAULT 0,
    cost_spent_cents INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_backfill_jobs_status
    ON switchboard.backfill_jobs (status);

CREATE INDEX idx_backfill_jobs_connector
    ON switchboard.backfill_jobs (connector_type, endpoint_identity);
```

Allowed `status` values:
- `pending`
- `active`
- `paused`
- `completed`
- `cancelled`
- `cost_capped`
- `error`

State semantics:
- `pending`: queued, unclaimed
- `active`: connector currently processing
- `paused`: operator pause requested
- `completed`: date window exhausted successfully
- `cancelled`: operator cancelled
- `cost_capped`: stopped after cost cap reached
- `error`: stopped due to failure, with `error` populated

## 5. Switchboard MCP Tools (Backfill Lifecycle)
Switchboard MUST expose the following tools.

### 5.1 Dashboard-facing tools
- `create_backfill_job(connector_type, endpoint_identity, target_categories, date_from, date_to, rate_limit_per_hour?, daily_cost_cap_cents?) -> {job_id, status}`
- `backfill.pause(job_id) -> {status}`
- `backfill.cancel(job_id) -> {status}`
- `backfill.resume(job_id) -> {status}`
- `backfill.list(connector_type?, endpoint_identity?, status?) -> [{job summary}]`

### 5.2 Connector-facing tools
- `backfill.poll(connector_type, endpoint_identity) -> {job_id, params, cursor} | null`
- `backfill.progress(job_id, rows_processed, rows_skipped, cost_spent_cents_delta, cursor?, status?, error?) -> {status}`

### 5.3 On-demand retrieval tool
- `email_search_and_ingest(query, max_results?) -> {results_ingested, results_skipped, truncation_applied}`
- This tool is exposed by Switchboard MCP for user-initiated retrieval and invoked by butlers (or dashboard-triggered butler flows), not by connector backfill poll loops.

### 5.4 Tool behavior requirements
- `backfill.poll` returns the oldest `pending` job for that connector identity and transitions it to `active` (setting `started_at` when first activated).
- `backfill.progress` updates counters, `updated_at`, optional cursor, optional error, and returns authoritative status.
- If cumulative `cost_spent_cents >= daily_cost_cap_cents`, Switchboard MUST transition to `cost_capped` and return that status.
- Dashboard controls and connector execution tools must remain role-separated (dashboard does not directly mutate DB, connector does not directly mutate DB).

## 6. Connector Polling and Execution Protocol

### 6.1 Polling loop
Connector loop requirements (when backfill enabled):
- Poll `backfill.poll` no more frequently than every 60 seconds.
- Default polling interval: `CONNECTOR_BACKFILL_POLL_INTERVAL_S=60`.
- Poll runs alongside normal ingest loop and MUST NOT block live ingestion.

### 6.2 Job execution behavior
When `backfill.poll` returns a job:
1. Traverse message history backward within `[date_from, date_to]`.
2. For each message, apply tiered ingestion policy from `docs/connectors/email_ingestion_policy.md`.
3. Submit historical events using standard `ingest.v1` contract.
4. Reuse live idempotency key rules from `docs/connectors/interface.md` section 6 (email default: RFC `Message-ID` + mailbox identity; provider message IDs are fallback-only when RFC header is unavailable).
5. Report progress every `N` messages (default `N=50`, configurable via `CONNECTOR_BACKFILL_PROGRESS_INTERVAL`).

Connector stop conditions:
- `backfill.progress` returns `paused`, `cancelled`, or `cost_capped`.
- Operator cancels or pauses from dashboard.
- Job errors irrecoverably (report with `status=error` and details).

Completion behavior:
- Connector reports terminal state with `backfill.progress(..., status="completed")`.
- Connector does not modify live cursor checkpoints based on backfill traversal.

### 6.3 Cursor and resume semantics
- Backfill resume cursor MUST be persisted in `backfill_jobs.cursor` via `backfill.progress`.
- Connector-local live checkpoint files remain for live ingestion only.
- Backfill restarts must resume from server-side job cursor, not local ad hoc files.

## 7. Rate Limiting and Cost Controls

Required controls per active job:
- `rate_limit_per_hour`: connector throttles processed messages/hour.
- `daily_cost_cap_cents`: connector and Switchboard enforce daily spend ceiling.

Normative behavior:
- Connector tracks estimated spend and reports per-batch deltas via `cost_spent_cents_delta` in `backfill.progress`.
- Switchboard computes authoritative cumulative spend in job state.
- When cap is reached, status transitions to `cost_capped` and work stops.
- Daily cap reset occurs at midnight UTC by default; deployments MAY define a different timezone policy but MUST document it.

Operational recommendation:
- Default conservative caps (`100` messages/hour, `500` cents/day) should remain configurable per job from dashboard controls.

## 8. Backfill Modes

### 8.1 Mode A: Selective batch (primary mode)
Selective batch is the default historical strategy.

Recommended category windows:
- Finance: last 7 years (tax/receipt retention horizon)
- Health: all available history
- Relationship (direct correspondence): last 2-3 years
- Travel: last 2 years
- Newsletters/marketing: skip

Execution shape:
- Dashboard may create one job per category or one multi-category job with `target_categories`.
- Overlap is safe due to canonical idempotency keys.

### 8.2 Mode B: On-demand backfill
On-demand retrieval is user-question-driven:
- Example: "When did I last visit Dr. Smith?"
- Butler executes Switchboard MCP tool `email_search_and_ingest(query, max_results)` to request provider search + ingest on demand.
- Results are ingested immediately via Switchboard ingest path.

Normative limits:
- Default and maximum `max_results` SHOULD be 50.
- On-demand mode bypasses `backfill_jobs` queue for immediacy but still honors ingest contract and tier policy where applicable.

### 8.3 Mode C: Background batch (optional phase)
Low-priority continuous historical enrichment:
- Runs only when connector has no urgent live ingestion backlog.
- Uses low rate limits and tight daily cost caps.
- Can be paused/cancelled by same dashboard controls as selective mode.

## 9. Privacy, Consent, and Audit
Backfill is user-controlled and explicit.

Requirements:
- Backfill start requires explicit opt-in confirmation in dashboard UX.
- Dashboard must show estimated cost and expected duration before job creation.
- Users must be able to pause/cancel at any time.
- Lifecycle actions (create, pause, resume, cancel, complete, error, cost cap) MUST be audit logged.
- Backfilled content follows existing data handling and access controls used for live ingestion.

## 10. Retention Alignment
Backfilled messages follow the same tiered retention policy as live messages:
- Tier 1: domain/butler retention rules (for example finance multi-year, health longer-lived).
- Tier 2: metadata retention policy.
- Tier 3: no message-level persistence.

This strategy does not introduce a separate retention regime for historical ingestion.

## 11. Non-Goals
This specification does not:
- Grant connectors direct DB access for operational backfill state.
- Replace the canonical `ingest.v1` envelope.
- Define dashboard page layout details (covered by dashboard specs).
- Mandate background batch in initial rollout (optional phase).
