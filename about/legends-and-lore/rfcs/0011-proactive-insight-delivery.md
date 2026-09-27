# RFC 0011: Proactive Insight Delivery Protocol

**Status:** Accepted
**Date:** 2026-03-25

## Summary

A three-phase pipeline for delivering proactive insights to the user: butler-side generation, Switchboard-side brokering, and delivery via the existing `notify` contract. Butlers propose structured `insight.v1` candidates through the Switchboard's `propose_insight_candidate()` MCP tool. The insight broker module on the Switchboard validates, deduplicates, ranks, and budget-gates candidates before delivering winners as a digest or standalone message via `notify(intent='insight')`. Anti-spam guarantees are structural — a global daily budget, per-key cooldowns, owner quiet hours, and category-local adaptive shaping are enforced by the broker, not by individual butlers. Butlers never deliver insights directly; they compete for delivery slots.

## Motivation

Butlers hold valuable domain knowledge that goes unused until the user asks. Birthdays approaching, spending anomalies, health measurement gaps, expiring travel documents — all locked behind user-initiated conversations. Proactive surfacing transforms butlers from passive tools into anticipatory assistants.

However, proactive notifications are the most common UX failure in consumer software. The majority of "smart notification" systems devolve into spam within weeks. Each butler independently deciding to notify the user would produce exactly this outcome: five butlers each sending one "helpful" message per day is five interruptions, not one.

The insight delivery protocol solves this by centralizing delivery authority in a single broker. Individual butlers propose candidates but cannot deliver them. The broker enforces a global budget, deduplicates across butler domains, and automatically reduces frequency when the user disengages. The system defaults to minimal noise (1 insight/day) and structurally prevents escalation without explicit user action.

## Design

### Three-Phase Pipeline

The insight pipeline separates concerns into three phases with a staging table (`public.insight_candidates`) as the serialization boundary between generation and delivery.

```
Phase 1: Generation                Phase 2: Brokering              Phase 3: Delivery
┌──────────────────┐               ┌────────────────────┐          ┌─────────────────┐
│ Butler           │  MCP call     │ Switchboard        │          │ notify()        │
│ insight-scan job ├──────────────►│ propose_insight_   │          │ intent='insight'│
│ (daily cron)     │               │ candidate() tool   │          │                 │
│                  │               │                    │          │ Digest (B > 1)  │
│ Relationship ────┤               │ • validate         │  daily   │ or              │
│ Health ──────────┤               │ • verbosity gate   ├─────────►│ Standalone (1)  │
│ Finance ─────────┤               │ • insert to        │  cron    │                 │
│ Travel ──────────┘               │   staging table    │          │ → Messenger     │
                                   │                    │          │ → User channel  │
                                   │ insight-delivery-  │          └─────────────────┘
                                   │ cycle job:         │
                                   │ • expire           │
                                   │ • cooldown filter  │
                                   │ • dedup by key     │
                                   │ • global budget    │
                                   │ • select top-B     │
                                   │ • deliver          │
                                   │ • record cooldowns │
                                   │ • track engagement │
                                   │ • cleanup old rows │
                                   └────────────────────┘
```

**Phase 1 — Generation.** Each butler's `insight-scan` scheduled task runs at its natural cadence (daily, staggered by butler). The job evaluates domain data and calls the Switchboard's `propose_insight_candidate()` MCP tool for each candidate. Butlers use the existing `dispatch_mode='job'` with `job_name='insight-scan'` — no changes to `core-scheduler` are required. If the tool returns `{"status": "filtered"}`, the butler skips remaining candidate generation (early exit on verbosity `off`).

**Phase 2 — Brokering.** The `propose_insight_candidate()` tool on the Switchboard validates the candidate (priority range, dedup key format, non-empty message, future expiry), checks the global verbosity setting, and inserts a row into `public.insight_candidates`. The `insight-delivery-cycle` scheduled task runs once daily (default 8:00 UTC) and orchestrates: expiry, cooldown filtering, deduplication, budget computation, top-B selection, delivery, cooldown recording, engagement tracking, and cleanup. An hourly urgent sub-cycle (see Urgent Sub-Cycle) delivers priority >= 90 candidates between daily runs.

**Phase 3 — Delivery.** Winners are delivered via `notify(intent='insight')` through the existing Switchboard-to-Messenger pipeline. Budget > 1 produces a single digest message; budget = 1 produces a standalone message. The Messenger treats `intent='insight'` as functionally equivalent to `intent='send'` for delivery mechanics, with optional visual differentiation.

### `propose_insight_candidate()` MCP Tool

The insight broker module registers this tool on the Switchboard. It is the sole entry point for candidate submission, enforcing Rule 3 (inter-butler communication is MCP-only through the Switchboard).

**Signature:**

```python
propose_insight_candidate(
    priority: int,          # 1-100, higher = more important
    category: str,          # Domain category (e.g., "birthday", "spending-anomaly")
    dedup_key: str,         # Semantic deduplication key
    message: str,           # Human-readable insight message
    expires_at: datetime,   # Candidate expires if not delivered by this time
    cooldown_days: int | None = None,  # Override default cooldown period
    channel: str | None = None,        # Preferred delivery channel
    metadata: dict | None = None,      # Butler-specific structured data
) -> {"status": "accepted" | "filtered" | "error", "reason": str}
```

**Return values:**

| Status | Meaning | Example `reason` |
|--------|---------|-------------------|
| `accepted` | Candidate inserted into staging table | `"candidate queued for delivery cycle"` |
| `filtered` | Candidate rejected without insertion | `"verbosity is off"` |
| `error` | Validation failure | `"priority must be between 1 and 100"` |

**Validation rules (executed in order, first failure short-circuits):**

1. **Priority range:** `priority` MUST be between 1 and 100 inclusive. Error: `"priority must be between 1 and 100"`.
2. **Dedup key format:** `dedup_key` MUST match the regex `^[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.-]+$` (3-segment) or `^[a-z0-9_-]+:[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.-]+$` (4-segment). Each segment MUST be non-empty. Error: `"dedup_key must match format {category}:{entity}:{time-scope} or {butler}:{category}:{entity}:{time-scope}"`.
3. **Non-empty message:** `message` MUST be non-empty after whitespace trimming. Error: `"message is required and must be non-empty"`.
4. **Future expiry:** `expires_at` MUST be in the future at the time of the call. Error: `"expires_at must be in the future"`.
5. **Verbosity gate:** If global verbosity is `off`, return `{"status": "filtered", "reason": "verbosity is off"}` without insertion.

On successful validation, the tool inserts a row with `origin_butler` set to the calling butler's identity (resolved from the MCP session context), `status='pending'`, and `created_at=now()`.

### `insight.v1` Candidate Schema

The `public.insight_candidates` table is the staging area between generation and delivery.

```sql
CREATE TABLE public.insight_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    origin_butler   TEXT NOT NULL,
    priority        INTEGER NOT NULL CHECK (priority >= 1 AND priority <= 100),
    category        TEXT NOT NULL,
    dedup_key       TEXT NOT NULL,
    cooldown_days   INTEGER,
    expires_at      TIMESTAMPTZ NOT NULL,
    message         TEXT NOT NULL,
    channel         TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'delivered', 'expired', 'filtered')),
    delivered_at    TIMESTAMPTZ
);

CREATE INDEX idx_insight_candidates_delivery
    ON public.insight_candidates (status, expires_at)
    WHERE status = 'pending';

CREATE INDEX idx_insight_candidates_dedup
    ON public.insight_candidates (dedup_key)
    WHERE status = 'pending';

CREATE INDEX idx_insight_candidates_origin
    ON public.insight_candidates (origin_butler);
```

**Field contracts:**

- `origin_butler` is set by the broker from the calling butler's MCP session identity. Butlers cannot spoof their origin.
- `priority` follows the standardized range convention (see Priority Scoring below).
- `category` is a freeform domain label (e.g., `birthday`, `spending-anomaly`, `measurement-gap`). It is informational and used for display grouping, not for deduplication.
- `dedup_key` is the semantic deduplication key (see Dedup Key Format below).
- `cooldown_days` overrides the default cooldown period for this candidate's priority range. NULL means use the default.
- `expires_at` is required. Candidates not delivered by this time are marked `expired`.
- `message` is the human-readable insight text delivered to the user.
- `channel` is the preferred delivery channel. NULL means use the owner's primary channel.
- `metadata` is extensible JSONB for butler-specific structured data (for future dashboard rendering).
- `status` transitions are unidirectional: `pending` to `delivered`, `pending` to `expired`, `pending` to `filtered`. Terminal states are immutable.
- `delivered_at` is set when the candidate is delivered. NULL until then.

### Priority Scoring Convention

Butler-generated priorities follow a standardized range convention. The broker does not re-score — it uses the butler-provided priority directly, with `created_at` ascending as tie-breaker (FIFO within same priority).

| Range | Semantics | Examples |
|-------|-----------|----------|
| 90-100 | Time-critical — action needed within 24-48 hours | Birthday tomorrow, bill due today, passport expires in 7 days |
| 70-89 | Actionable soon — action within 7 days | Birthday in 5 days, spending anomaly this week, medication refill in 10 days |
| 50-69 | Informational — summaries, milestones, trends | Monthly summary available, health streak milestone, subscription renewal in 30 days |
| 30-49 | Low-urgency nudges — suggestions, reconnections | Reconnection suggestion, preference pattern detected |
| 1-29 | Background observations — verbose mode only | Interaction milestone, minor trend detected |

### Dedup Key Format and Validation

Dedup keys are the foundation of cross-butler and within-butler deduplication. They encode the semantic identity of an insight — what entity, what domain, and what time scope.

**Format convention:**

- **Cross-butler dedup (3-segment):** `{category}:{entity-identifier}:{time-scope}` — used when multiple butlers may produce insights about the same underlying event. Examples: `birthday:contact-uuid-123:2026`, `bill-due:electric-uuid:2026-04`.
- **Butler-specific dedup (4-segment):** `{butler}:{category}:{entity-identifier}:{time-scope}` — used when insights are butler-specific and should not deduplicate across butlers. Examples: `health:measurement-gap:blood-pressure:2026-w13`, `finance:spending-anomaly:dining:2026-03`.

**Validation regex:**

```
3-segment: ^[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.-]+$
4-segment: ^[a-z0-9_-]+:[a-z0-9_-]+:[a-z0-9_-]+:[a-zA-Z0-9_.-]+$
```

The first two segments (or three for 4-segment keys) use lowercase alphanumeric with hyphens and underscores. The final segment (time-scope) additionally allows dots and uppercase for flexibility in date/UUID formats.

**Dedup resolution:** During the delivery cycle, candidates sharing a `dedup_key` are collapsed to the one with the highest `priority`. Ties are broken by `created_at` ascending (earliest candidate wins). Losers are marked `status='filtered'`.

**Convention enforcement:** Cross-butler dedup requires butlers covering overlapping domains to agree on shared `category` names. For example, both the Relationship and Calendar butlers use `birthday:{contact-entity-id}:{year}` for birthday insights, enabling the broker to collapse duplicates without knowing anything about birthdays.

### Delivery Cycle Execution

The `insight-delivery-cycle` job runs as a daily scheduled task on the Switchboard (default cron: `0 8 * * *`). It executes the following steps in strict order:

**Step 1 — Attention gate.** Evaluate the Owner Attention Policy and the situational context bus (see Quiet Hours and Owner Attention Policy). If either suppresses the cycle, narrow the working set to candidates at or above `URGENT_PRIORITY_THRESHOLD` (90); if there are none, skip the cycle. Sub-threshold candidates stay `pending`: never delivered early, never dropped.

**Step 2 — Expire old candidates.** Mark all candidates with `status='pending'` and `expires_at < now()` as `status='expired'`. Each transition commits together with its content-blind `expired` attention-ledger row; if the ledger write fails, the candidate stays `pending` for a later retry.

**Step 3 — Cooldown filtering.** Exclude candidates whose `dedup_key` has an active cooldown in `public.insight_cooldowns` (where `cooldown_until > now()`). Filtered candidates remain `pending` — they are not marked as filtered, because the cooldown may expire before the candidate does.

**Step 4 — Deduplication.** Within each `dedup_key` group among remaining candidates, retain only the highest-priority candidate. Break ties by `created_at` ascending. Mark losers as `status='filtered'`.

**Step 5 — Compute budget.** Read the user's verbosity preset from `public.insight_settings`. Map preset to budget (`off`=0, `minimal`=1, `normal`=3, `verbose`=5, or a custom integer 1-10). Engagement never changes the budget (see Adaptive Delivery Shaping). If the budget is 0 (verbosity `off`), mark all remaining pending candidates as `status='filtered'` and return.

**Step 6 — Check already-delivered today.** Count candidates with `delivered_at` within the current calendar day (in the user's configured timezone, or UTC if none configured). Subtract from the effective budget to get the remaining delivery slots. If zero, return without delivery.

**Step 7 — Select top-B.** From remaining candidates after steps 2-6, select the top B by descending `priority` weighted by each candidate's category weight (see Adaptive Delivery Shaping), then ascending `created_at`. Candidates not selected remain `pending` for the next cycle.

**Step 8 — Deliver.** If B > 1, compose a digest message and deliver via a single `notify(intent='insight')` call. If B = 1, deliver the single candidate as a standalone message with butler-origin prefix. On `notify` failure, the candidate's status remains `pending` and is retried next cycle. After 3 consecutive delivery failures for the same candidate, mark it `status='filtered'` with failure metadata. Each delivery records an attention-ledger row: `delivered` for a standalone message, and one `coalesced` row per candidate folded into a digest.

**Step 9 — Record cooldowns.** For each delivered candidate, insert a row into `public.insight_cooldowns` with `cooldown_until = now() + cooldown_days`. If the candidate did not specify `cooldown_days`, use the default for its priority range (see Cooldown Tracking below).

**Step 10 — Record engagement tracking.** For each delivered candidate, insert a row into `public.insight_engagement` with `delivered_at` and `engaged=FALSE`.

**Step 11 — Cleanup.** Delete non-pending candidates from `public.insight_candidates` where `created_at` is older than 30 days. Delete cooldown entries from `public.insight_cooldowns` where `cooldown_until` is older than 30 days in the past. Before deleting engagement entries from `public.insight_engagement` where `delivered_at` is older than 30 days, upsert each affected day's delivered and engaged counts into `public.attention_daily_rollup`.

### Verbosity Presets and Budget

User preferences are stored in `public.insight_settings` (single-row table):

```sql
CREATE TABLE public.insight_settings (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    verbosity       TEXT NOT NULL DEFAULT 'minimal',
    custom_budget   INTEGER CHECK (custom_budget >= 1 AND custom_budget <= 10),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed the default row on migration
INSERT INTO public.insight_settings (id, verbosity) VALUES (1, 'minimal');
```

**Preset mapping:**

| Preset | Daily Budget | Description |
|--------|-------------|-------------|
| `off` | 0 | No proactive insights. Delivery cycle marks all pending as filtered. |
| `minimal` | 1 | Only the highest-priority insight. Default. |
| `normal` | 3 | Top-3 insights, delivered as a digest. |
| `verbose` | 5 | Top-5 insights, delivered as a digest. |
| Custom | 1-10 | User-specified integer via `custom_budget` column. |

If `custom_budget` is non-null, it overrides the preset-derived budget. The `verbosity` column still stores the preset name for display purposes.

When verbosity is `off`, the `propose_insight_candidate()` tool returns `filtered` immediately, allowing butler insight-scan jobs to detect this and skip further candidate generation. Quiet hours are not stored here; see Quiet Hours and Owner Attention Policy.

### Cooldown Tracking

After delivery, the system prevents re-delivery of insights with the same `dedup_key` for a configurable period.

```sql
CREATE TABLE public.insight_cooldowns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dedup_key       TEXT NOT NULL,
    cooldown_until  TIMESTAMPTZ NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_insight_cooldowns_active
    ON public.insight_cooldowns (dedup_key, cooldown_until)
    WHERE cooldown_until > now();
```

**Default cooldown periods by priority range:**

| Priority Range | Default Cooldown | Rationale |
|----------------|-----------------|-----------|
| 90-100 | 1 day | Time-critical insights may need re-notification if the situation persists |
| 70-89 | 7 days | Actionable-soon insights should not repeat within the same week |
| 50-69 | 14 days | Informational insights should not repeat within two weeks |
| 30-49 | 30 days | Low-urgency nudges have a monthly cooldown |
| 1-29 | 30 days | Background observations have a monthly cooldown |

Butlers can override the default by specifying `cooldown_days` on the candidate. The cooldown applies to the `dedup_key`, not the specific candidate — any future candidate with the same key is filtered until the cooldown expires.

### Adaptive Delivery Shaping

The broker tracks engagement to shape which candidates win slots. It never changes the owner's configured budget automatically.

**Engagement tracking table:**

```sql
CREATE TABLE public.insight_engagement (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    insight_id      UUID NOT NULL REFERENCES public.insight_candidates(id),
    delivered_at    TIMESTAMPTZ NOT NULL,
    engaged         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_insight_engagement_window
    ON public.insight_engagement (delivered_at, engaged);
```

Engagement is detected from owner-authored ingress only (see Engagement Tracking Contract).

**Category-local weights.** Each category's last ten attributed deliveries derive a weight of baseline, 0.75, or 0.5, which scales only that category's candidate ordering. A lower Health weight can change Health's rank, never the number of Finance slots available under the configured cap. A later useful verdict or that category's own later engagement can restore its baseline; another category cannot. Explicit useful, not-now, and never feedback (REST and MCP) is owner-attributed, bounded, and reversible at the family boundary.

**Auto-off on total disengagement.** The engagement rate is computed over a rolling 14-day window:

```
engagement_rate = count(engaged=TRUE) / count(*) WHERE delivered_at >= now() - 14 days
```

If no insights were delivered in the window, the rate is treated as 1.0. When the rate is 0.0 for 14 consecutive days (with at least 1 insight delivered per day during that period), the system:

1. Sets `verbosity` to `off` in `public.insight_settings`.
2. Delivers a final notification via direct `notify(intent='send')` (not through the insight pipeline): "I've paused proactive insights since you haven't found them useful. You can re-enable them anytime."

The auto-off check reads raw `insight_engagement` for days still present and falls back to `public.attention_daily_rollup` for days already purged, so the 14-day window cannot be truncated by retention. Only explicit owner action restores delivery after auto-off.

### Digest and Standalone Delivery Format

**Standalone (budget = 1):** The single insight is delivered as a message prefixed with the origin butler name:

```
[Health] You haven't logged blood pressure in 12 days (your usual cadence is weekly)
```

**Digest (budget > 1):** Multiple insights are delivered as a single batched message:

```
Daily Insights (3):

1. [Relationship] Sarah's birthday is in 3 days — you mentioned getting her a book last month
2. [Health] You haven't logged blood pressure in 12 days (your usual cadence is weekly)
3. [Finance] Dining spending this month is 40% above your 3-month average
```

Both formats are delivered via `notify(intent='insight')`. The `metadata` field of the notify envelope includes `insight_count` (int) and `insight_ids` (list of candidate UUIDs) for audit and future dashboard rendering.

### Quiet Hours and Owner Attention Policy

`public.approvals_policy` is the single Owner Attention Policy for routine direct owner-default notification holds, approval-request pushes, the insight broker's regular cycle, and the derived health sleeping context. It is evaluated in its stored IANA timezone as the end-exclusive interval `[quiet_start_hour, quiet_end_hour)`, and every defer or sleep anchor uses the exact configured local end converted to UTC. A never-configured install is seeded to 23:00-08:00 Asia/Singapore; an owner's own configuration is never overwritten. `/api/approvals/policy` writes require a complete hour pair and a recognized IANA timezone. Missing, incomplete, invalid, or unreadable policy data fails open for routine paths; approval pending-action expiry is independent of push timing.

Both `notify()`'s owner-default gate and the delivery cycle also make a deterministic, non-LLM read of `public.user_context` (RFC 0009) for an active `dnd` or `sleeping` signal, which suppresses like quiet hours. Neither quiet hours nor the context bus suppresses anything at or above `URGENT_PRIORITY_THRESHOLD` (90). `notify()` priorities normalize onto the same 1-100 scale via `normalize_priority()` (`high` → 90, `medium` → 50, `low` → 20).

During a suppressed regular cycle, candidates accumulate but are NOT burst-delivered afterward; the daily budget still applies at the next cycle.

### Notify-Path Holds and Coalescing

A direct `notify()` call with no `entity_id`, no explicit `recipient`, intent `send` or `insight`, and priority other than `high` is held durably when quiet hours or the context bus suppress it. The per-butler `delivery_preferences` branch runs first and is unchanged; high-priority, explicit-target, other-intent, and approval-request paths keep their own behavior. The hold inserts the resolved `notify.v1` envelope into the calling butler schema's `deferred_notifications` table. Policy quiet hours anchor `deliver_at` at the configured end; DND or sleeping anchors at the latest active suppressor expiry; if both apply, the later anchor wins. A successful enqueue records a `deferred` ledger row with the row id in `notification_ref`. An enqueue failure records a best-effort `failed` row and returns a retryable error; it never falls through to immediate delivery. Policy or context lookup failures fail open.

The scheduler flush (`_tick_deferred_notification_pass`, `src/butlers/core/scheduler.py`) is the sole later delivery engine. It delivers the stored envelope without re-gating, so a stored `deliver_at` survives later policy changes. It groups due rows by delivery target (channel plus recipient; a `None` recipient is its own group). A target with one due row is delivered verbatim and recorded `delivered`; a target with several is composed into one digest-style message, delivered by one `notify_fn` call, and recorded as one `coalesced` row per notification. A group is marked `delivered` together on success or stays `pending` together on failure, never partially.

### Urgent Sub-Cycle

The Switchboard schedule `insight-urgent-subcycle` (`30 * * * *`) runs `delivery_cycle(urgent_only=True)` with the same production `notify_fn` as the daily job. In this mode selection starts from `priority >= URGENT_PRIORITY_THRESHOLD`, the quiet-hours and context-bus consult is skipped (urgent always bypasses both), the daily budget cap does not apply, and end-of-cycle maintenance (cleanup, auto-off) is left to the daily cycle. Verbosity `off` still applies. Each row's own `status` transition to `delivered` is the double-send guard across both cycles.

### Attention Ledger

`public.attention_ledger` holds one durable row per proactive-egress decision at either choke point (`notify()` and `delivery_cycle()`). Columns: `id`, `occurred_at`, `origin_butler`, `source` (`notify` | `insight`), `channel`, `intent`, `priority_label`, `priority_score` (normalized 1-100), `dedup_key`, `outcome`, `reason`, `notification_ref`, `metadata`. `outcome` is a closed vocabulary: `delivered`, `coalesced`, `deferred`, `suppressed`, `failed`, `expired`. A ledger-write failure is logged at WARNING and never blocks or fails the egress it describes, except where this RFC requires the ledger row to commit with a state transition (candidate expiry).

`public.attention_daily_rollup` holds one row per UTC day (`day`, `owner_ingress_count`, `insights_delivered`, `insights_engaged`, `updated_at`) so the engagement signal survives the 30-day purge.

Readers:

- `GET /api/attention/ledger`: windowed (`since`/`until` on `occurred_at`), filterable (`intent`, `source`, `outcome`, `origin_butler`), paginated raw rows, newest first.
- `GET /api/attention/ledger/summary`: per-`origin_butler` outcome counts over a window that defaults to the last 7 days. Each row carries `suppressed_never_delivered` (`suppressed > 0 AND delivered == 0`), and `flagged_sources` lists those names. The dashboard renders flagged sources as a loud failure banner, distinct from the degraded-source note.
- Both follow the degraded-envelope convention: an unreachable pool yields `source_available=false`; an unmigrated table yields a true empty result.

### `intent='insight'` Notify Extension

The `notify` contract (RFC 0002, core tool) is extended with a fourth delivery intent: `insight`.

**Behavior:**

- `message` is required and must be non-empty (same as `send`).
- `request_context` is optional (same as `send`).
- The Messenger treats `intent='insight'` as functionally equivalent to `intent='send'` for delivery mechanics.
- The Messenger MAY apply visual differentiation (formatting, labels) but this is not required for initial implementation.
- Owner-targeted insight notifications bypass the approval gate (consistent with `intent='send'` behavior).
- The valid intent set becomes: `send`, `reply`, `react`, `insight`.

### Anti-Spam Guarantees

The following guarantees are structural — enforced by the broker's architecture, not by advisory guidelines that individual butlers could ignore.

1. **Single entry point.** Candidates enter only through `propose_insight_candidate()`. Direct writes to `public.insight_candidates` are prohibited by Rule 3 (inter-butler communication is MCP-only through the Switchboard). The broker is the sole writer.

2. **Global budget cap.** The delivery cycle enforces a hard ceiling on daily deliveries. No butler can exceed or circumvent this limit because butlers do not deliver insights — the broker does.

3. **Dedup key validation at entry.** The broker validates dedup key format before insertion. Malformed keys that would silently break deduplication are rejected with an actionable error message.

4. **Cooldown enforcement.** Delivered insights create cooldown entries that the broker checks before every delivery cycle. A butler cannot re-propose the same insight and bypass cooldown because the broker filters by `dedup_key`, not by candidate ID.

5. **Owner-controlled budget.** The global budget changes only by explicit owner action. Disengagement lowers a category's rank weight, never the cap, and a completely ignored system silences itself through auto-off. Only explicit owner action restores delivery after auto-off.

6. **Digest batching.** When budget > 1, insights are delivered as a single message. The user receives one notification, not B notifications.

7. **Candidate expiry.** Every candidate has a required `expires_at`. Candidates that are never delivered are eventually expired and cleaned up. The staging table does not grow without bound.

### Engagement Tracking Contract

Engagement detection is a side effect of the Switchboard's existing ingress processing path (`MessagePipeline.process()`):

1. When the Switchboard accepts an ingress request, it resolves the sender with a read-only `resolve_contact_by_channel()` lookup (`src/butlers/identity.py`), independent of the identity-injection feature flag and without its side effects. Only a sender with the `owner` role proceeds; connector, automated, and unresolved ingress never touch `insight_engagement`.
2. For owner ingress, it increments that day's `owner_ingress_count` in `public.attention_daily_rollup`, then updates `public.insight_engagement` rows with `engaged=FALSE` and `delivered_at` within the last 60 minutes to `engaged=TRUE`.
3. This query uses the `idx_insight_engagement_window` index and is bounded to at most 60 minutes of rows. It does not scan the full table.
4. The engagement check does not delay or block ingress processing. It runs as a lightweight post-acceptance side effect.

The engagement signal is intentionally rough — "any owner message within 60 minutes" is an imprecise proxy for "the user found the insight useful." Over-engineering engagement tracking (click tracking, read receipts, sentiment analysis) would add complexity for marginal accuracy gain. The 14-day rolling window smooths out noise from false positives.

## Integration

- **RFC 0001:** Butler insight-scan tasks use the existing `dispatch_mode='job'` scheduler infrastructure. No changes to the daemon lifecycle are required.
- **RFC 0002:** `propose_insight_candidate` is registered as a module tool on the Switchboard via the `Module.register_tools()` interface. The `notify` core tool is extended with `intent='insight'`. The insight broker implements the `Module` abstract base class.
- **RFC 0003:** Candidate submission flows through the Switchboard as an MCP tool call, consistent with the Switchboard's role as the single coordination point. The broker module runs within the Switchboard daemon alongside routing infrastructure.
- **RFC 0006:** `public.insight_candidates`, `public.insight_cooldowns`, `public.insight_engagement`, and `public.insight_settings` are created in the `public` schema via an Alembic migration, following the existing shared-schema pattern. All butlers can read these tables via their `search_path`; only the Switchboard's broker module writes to them.
- **RFC 0009:** The delivery cycle and `notify()`'s owner-default gate consult the situational context bus for `dnd` or `sleeping` signals alongside quiet hours (see Quiet Hours and Owner Attention Policy).

## Alternatives Considered

**Dedicated insight broker butler.** Rejected — adds operational complexity (another daemon, port, schema, migration chain) for what amounts to a single MCP tool and a daily scheduled job. The broker is a coordination concern that fits naturally as a Switchboard module.

**Butler-direct delivery with advisory rate limits.** Rejected — advisory limits are unenforceable. A butler that "forgets" to check the rate limit can spam the user. Centralizing delivery authority in the broker makes the budget a hard constraint, not a suggestion.

**LLM-based re-ranking of candidates.** Rejected — butler-local priority assignment is sufficient because each butler has domain expertise to judge urgency. The broker's job is cross-domain arbitration via budget, not second-guessing priorities. Simple integer comparison is transparent and debuggable. Deferred to a follow-up if engagement data reveals systematic priority mis-calibration.

**Direct DB writes to public.insight_candidates.** Rejected — this violates Rule 3 (inter-butler communication must go through the Switchboard), bypasses centralized validation, and prevents write serialization. Routing through the MCP tool ensures every candidate submission is validated, logged, and auditable.

**Per-butler delivery budgets in addition to the global budget.** Rejected for initial implementation — adds complexity without clear benefit. A pathological case where one butler dominates every cycle because its domain consistently produces higher-priority candidates may actually reflect genuine priority. Monitor engagement data per origin butler before adding per-butler caps.

**Push notification of engagement events.** Rejected — the pull-based engagement check (query on ingress) is simpler and sufficient. Push would require the Switchboard to maintain subscriber state and handle missed notifications, adding coupling for no practical benefit given the 60-minute detection window.

## Amendments Applied

Each amendment is folded into the Design sections above; this log records when the contract changed.

- **Amendment 1 (2026-07-05) — Attention Ledger, Seeded Quiet Hours, Context-Bus Gating.** Added the attention ledger, seeded owner quiet hours, context-bus gating, and the urgent bypass (see Attention Ledger; Quiet Hours and Owner Attention Policy). Change: `openspec/changes/attention-ledger-broker`.
- **Amendment 2 (2026-07-05) — Same-Window Coalescing + Hourly Urgent Sub-Cycle.** See Notify-Path Holds and Coalescing; Urgent Sub-Cycle. Change: `openspec/changes/attention-ledger-coalescing-urgent-subcycle`.
- **Amendment 3 (2026-07-11) — Attention Ledger Reader + Trust Console Panel.** Added the ledger read API and dashboard panel (see Attention Ledger).
- **Amendment 4 (2026-07-11) — Owner-Gated Engagement Proxy + Daily Attention Rollup.** See Engagement Tracking Contract; Adaptive Delivery Shaping.
- **Amendment 5 (2026-07-19) — Durable Routine Owner-Default Notify Holds.** See Notify-Path Holds and Coalescing. Change: `openspec/changes/park-owner-default-notifications`.
- **Amendment 6 (2026-07-19) — Consolidated Owner Attention Policy.** `public.approvals_policy` became the single quiet-hours authority; `insight_settings` lost its quiet-hour columns (see Quiet Hours and Owner Attention Policy). Change: `openspec/changes/consolidate-owner-attention-policy`.
- **Amendment 7 (2026-09-16) — Reversible Per-Category Insight Shaping.** Replaced the aggregate adaptive-budget reduction with category-local weights and made expiry transactional with its ledger row (see Adaptive Delivery Shaping; Delivery Cycle Execution). Change: `openspec/changes/insight-feedback-expiry`.
