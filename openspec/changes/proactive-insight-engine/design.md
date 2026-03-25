## Context

Butlers currently have two output modes: reactive responses to routed messages, and scheduled maintenance jobs that update internal state silently. Some scheduled tasks already produce user-facing output (e.g., `upcoming-dates-check` notifies about birthdays, `upcoming-bills-check` alerts on due bills), but these operate independently per butler with no cross-butler coordination, no global rate limiting, no deduplication, and no feedback loop from user engagement.

The existing `notify` contract (`core-notify`) provides the delivery plumbing — butlers can already send messages to the user via Switchboard and Messenger. What is missing is the **intelligence layer above notify**: deciding *what* to say, *when* to say it, and *how much* to say. This change builds that layer.

Key architectural constraints:
- Butlers cannot directly communicate with each other (all coordination goes through Switchboard)
- The `shared` schema is the only cross-butler data surface
- The `notify` contract already handles delivery channel selection and approval gating
- Each butler has its own scheduled task infrastructure via `core-scheduler`
- The user's single Telegram/email channel is a shared scarce resource — every butler competes for attention

## Goals / Non-Goals

**Goals:**
- A centralized insight broker that prevents individual butlers from independently spamming the user
- Structural rate limiting that cannot be bypassed by butler-level configuration
- Cross-butler deduplication (one birthday, one notification — not one from Relationship and one from Calendar)
- Cooldown tracking so dismissed or expired insights are not re-proposed for a configurable window
- Adaptive delivery that automatically reduces frequency when the user ignores insights
- User-adjustable verbosity with a conservative default (`minimal` = 1 insight/day)
- Insight candidates as structured data with priority, category, expiry, and deduplication keys
- Graceful opt-out: setting verbosity to `off` disables all proactive insights system-wide

**Non-Goals:**
- Real-time alerting (urgent alerts like "flight cancelled" remain as direct `notify` calls outside the insight system)
- ML-based personalization of insight ranking (use simple, deterministic priority scoring)
- Per-butler verbosity controls (the budget is global; individual butlers compete for slots)
- Push notification channel (insights go through the existing `notify`/Messenger path)
- Dashboard UI for insight management (data model supports it, but UI is deferred)
- Conversational follow-up on insights (insight is one-way; user can message back normally if they want)

## Decisions

### D1: Insight broker lives in Switchboard, not as a new butler

**Decision:** The insight broker is a coordination concern within the Switchboard butler, implemented as a new module (`module-insight-broker`). It collects candidates from `shared.insight_candidates`, runs deduplication/ranking/budget enforcement, and dispatches winners via the existing `notify` contract.

**Rationale:** Switchboard already coordinates all cross-butler communication. Adding a separate "insight butler" would require another MCP hop and a new daemon to manage. The broker is a lightweight coordination function — collect, rank, filter, deliver — not a domain specialist. It fits naturally as a Switchboard module with a single scheduled task (`insight-delivery-cycle`).

**Alternative considered:** Dedicated insight broker butler. Rejected — adds operational complexity (another daemon, port, schema) for what amounts to a single scheduled job that reads from shared tables and calls `notify`.

### D2: Two-phase pipeline — generation and delivery are separate scheduled tasks, routed through Switchboard MCP

**Decision:** Insight generation and insight delivery run on separate schedules with a staging table in between. Candidate submission flows through a Switchboard MCP tool to comply with Rule 3 (inter-butler communication is MCP-only through the Switchboard):

1. **Generation phase:** Each butler's `insight-scan` scheduled task runs at its natural cadence (e.g., daily at 7:00 UTC for relationship, 7:15 UTC for health). It calls the Switchboard's `propose_insight_candidate()` MCP tool for each candidate, rather than writing directly to `shared.insight_candidates`.
2. **Brokering phase:** The Switchboard's insight-broker module registers the `propose_insight_candidate()` tool, which validates the candidate (priority range, dedup_key format, non-empty message, future expires_at), checks the global verbosity setting (returns `filtered` immediately if `off`), and inserts the row into `shared.insight_candidates`. This serializes all writes through a single entry point.
3. **Delivery phase:** Switchboard's `insight-delivery-cycle` runs once daily (default 8:00 UTC, after all generators have run). It reads all unexpired candidates, applies deduplication and ranking, enforces the daily budget, delivers winners via `notify`, and marks delivered/expired candidates.

**Rationale:** Decoupling generation from delivery is the key architectural decision that makes rate limiting enforceable. Routing candidate submission through a Switchboard MCP tool (rather than direct DB writes to the shared table) enforces Rule 3: inter-butler communication is MCP-only through the Switchboard. This also provides:
- **Validation at entry point:** The broker validates dedup_key format (`{category}:{entity}:{time-scope}` or `{butler}:{category}:{entity}:{time-scope}`) before insertion, catching format violations immediately rather than silently breaking deduplication.
- **Write serialization:** The Switchboard serializes concurrent candidate submissions, preventing race conditions on dedup_key uniqueness checks.
- **Per-butler rate limiting:** The broker can enforce per-butler submission caps if a pathological generator floods the pipeline (not implemented initially, but the architecture supports it).
- **Explicit coordination:** Every cross-butler data flow is visible in MCP call logs, making the system auditable and debuggable.

**Alternative considered:** Butlers write directly to `shared.insight_candidates` via a shared helper function. Rejected — this violates Rule 3 (inter-butler communication must go through the Switchboard), bypasses the Switchboard's coordination role, and prevents centralized validation and rate limiting at the entry point.

### D3: Conservative default — `minimal` verbosity (1 insight/day)

**Decision:** The system ships with verbosity preset `minimal`, delivering at most 1 insight per day. The three presets are:

| Preset | Daily Budget | Description |
|--------|-------------|-------------|
| `off` | 0 | No proactive insights |
| `minimal` | 1 | Only the highest-priority insight |
| `normal` | 3 | Top-3 insights |
| `verbose` | 5 | Top-5 insights |

The user can also set a custom integer budget (1-10). The `off` preset disables insight-scan tasks system-wide (they still run but skip candidate generation).

**Rationale:** Starting conservative and letting the user dial up is the only defensible approach. A user who receives one useful insight per day will trust the system enough to try `normal`. A user who gets 5 mediocre insights on day one will disable the feature permanently.

### D4: Priority scoring — simple deterministic formula, no ML

**Decision:** Each insight candidate carries a `priority` integer (1-100, higher = more important) set by the generating butler. The broker does not re-score — it uses the butler-provided priority directly, with tie-breaking by `created_at` (oldest first, FIFO within same priority).

Priority ranges by category:
- **90-100:** Time-critical (birthday tomorrow, bill due today, medication refill before trip)
- **70-89:** Actionable soon (birthday in 5 days, spending anomaly this week, measurement gap reaching 2 weeks)
- **50-69:** Informational (monthly summary available, streak milestone, subscription renewal in 30 days)
- **30-49:** Low-urgency nudges (reconnection suggestion, preference pattern detected)
- **1-29:** Background observations (saved for verbose mode only)

**Rationale:** Butler-local priority assignment is sufficient because each butler has the domain expertise to judge urgency within its domain. The broker's job is cross-domain arbitration via budget enforcement, not second-guessing butler priorities. Simple integer comparison is transparent and debuggable.

**Alternative considered:** Broker-side re-ranking using user engagement history per category. Deferred — the adaptive delivery mechanism (D7) handles engagement feedback at the system level rather than per-category.

### D5: Deduplication via semantic keys, not message comparison

**Decision:** Each insight candidate carries a `dedup_key` string (e.g., `relationship:birthday:contact-uuid-123:2026`, `health:measurement-gap:blood-pressure`). The broker deduplicates by `dedup_key` — if multiple candidates share the same key, only the highest-priority one survives. This handles cross-butler overlap (e.g., Relationship and Calendar both noticing a birthday generate candidates with the same `dedup_key` pattern).

**Dedup key conventions:**
- Format: `{butler}:{category}:{entity-identifier}:{time-scope}`
- Cross-butler dedup: butlers covering overlapping domains use a shared `category` namespace. For example, both Relationship and Calendar use `birthday:{contact-entity-id}:{year}` (without the butler prefix) for birthday-related insights, allowing the broker to collapse them.
- Within-butler dedup: the butler prefix distinguishes unrelated insights from different domains.

**Rationale:** Message text comparison is fragile (rephrasing defeats it). Semantic keys give butlers explicit control over what constitutes "the same insight." The shared category namespace for cross-butler overlap is simple and deterministic.

### D6: Cooldown tracking — per-dedup-key with configurable window

**Decision:** After an insight with a given `dedup_key` is delivered (or explicitly dismissed), a cooldown entry is recorded in `shared.insight_cooldowns` with `cooldown_until = now() + cooldown_days`. Any candidate with a matching `dedup_key` is filtered out during the delivery cycle until the cooldown expires.

Default cooldown periods by priority range:
- Priority 90-100: 1 day (time-critical, may need re-notification)
- Priority 70-89: 7 days
- Priority 50-69: 14 days
- Priority 30-49: 30 days
- Priority 1-29: 30 days

Butlers can specify a custom `cooldown_days` on each candidate to override the default. The cooldown resets on delivery — if an insight with the same key is delivered again after the cooldown expires, a new cooldown period begins.

**Rationale:** Fixed cooldown windows are simple and predictable. The priority-tiered defaults mean urgent insights can recur sooner (e.g., "medication refill due" can re-fire the next day if still relevant) while informational nudges stay quiet longer.

### D7: Adaptive delivery — decay on ignore, never escalate

**Decision:** The broker tracks whether the user engaged with each delivered insight (any message to any butler within 1 hour of delivery counts as engagement). An `engagement_rate` is computed as a rolling 14-day window of `engaged / delivered`.

Adaptive behavior:
- `engagement_rate >= 0.5`: No adjustment — user finds insights useful
- `0.25 <= engagement_rate < 0.5`: Reduce effective budget by 1 (floor of 1 if on `minimal`)
- `engagement_rate < 0.25`: Reduce effective budget to 1 regardless of preset
- `engagement_rate == 0.0` for 14 consecutive days: Auto-downgrade to `off` preset and send a final "I've paused proactive insights — you can re-enable them anytime" notification

The system NEVER increases delivery frequency automatically. The user must explicitly change their verbosity setting to get more insights. This is a one-way ratchet down, resettable only by user action.

**Rationale:** This is the core anti-spam mechanism. Most notification systems fail because they interpret silence as "the user didn't see it, send more." This system interprets silence as "the user doesn't want this, send less." The one-way ratchet prevents the classic failure mode where a well-intentioned system gradually becomes noisier.

### D8: Quiet hours — time-based delivery suppression

**Decision:** The user can configure quiet hours as a `(start_hour, end_hour, timezone)` tuple in `shared.insight_settings`. During quiet hours, the delivery cycle is skipped entirely — candidates accumulate and are delivered at the next non-quiet delivery cycle. If the next delivery cycle finds more candidates than the daily budget, it still enforces the budget (quiet hours do not create a "burst" of deferred insights).

Default: No quiet hours configured (delivery at the scheduled time, default 8:00 UTC).

**Rationale:** Simple time window suppression. No complex "snooze" or "deliver later" mechanics. The budget enforcement on the post-quiet-hours cycle prevents the accumulation problem.

### D9: Insight envelope schema — `insight.v1`

**Decision:** The candidate row in `shared.insight_candidates` follows this schema:

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `origin_butler` | TEXT | Butler that generated the candidate |
| `priority` | INTEGER | 1-100, higher = more important |
| `category` | TEXT | Domain category (e.g., `birthday`, `spending-anomaly`, `measurement-gap`) |
| `dedup_key` | TEXT | Semantic deduplication key |
| `cooldown_days` | INTEGER | Optional override for default cooldown period |
| `expires_at` | TIMESTAMPTZ | Candidate expires if not delivered by this time |
| `message` | TEXT | Human-readable insight message |
| `channel` | TEXT | Preferred delivery channel (default: user's primary) |
| `metadata` | JSONB | Butler-specific structured data (for future dashboard rendering) |
| `created_at` | TIMESTAMPTZ | When the candidate was generated |
| `status` | TEXT | `pending`, `delivered`, `expired`, `filtered` |
| `delivered_at` | TIMESTAMPTZ | When delivered (NULL if not yet) |

The `insight.v1` envelope used in the `notify` call wraps the message with insight-specific metadata for the Messenger to render appropriately (e.g., prefixing with an insight label, grouping in a digest).

### D10: Digest mode for `normal` and `verbose` presets

**Decision:** When the daily budget is > 1, insights are delivered as a single batched message (digest) rather than individual notifications. The digest groups insights by butler origin and renders them as a numbered list with priority indicators.

Example digest:
```
Daily Insights (3):

1. [Relationship] Sarah's birthday is in 3 days — you mentioned getting her a book last month
2. [Health] You haven't logged blood pressure in 12 days (your usual cadence is weekly)
3. [Finance] Dining spending this month is 40% above your 3-month average
```

For `minimal` (budget = 1), the single insight is delivered as a standalone message.

**Rationale:** Multiple individual notifications is the definition of spam. A single digest respects the user's attention as a scarce resource. The user reads one message, not three, and can ignore the digest as a unit.

### D11: Insight-scan tasks use existing `job` dispatch mode, not a new dispatch mode

**Decision:** Butler insight-scan tasks use the existing `dispatch_mode='job'` with `job_name='insight-scan'`. The job handler evaluates domain data, produces candidates, and submits each candidate by calling the Switchboard's `propose_insight_candidate()` MCP tool. The verbosity check is performed by the broker on the Switchboard side — if verbosity is `off`, the tool returns `{"status": "filtered", "reason": "verbosity is off"}` and the candidate is not inserted.

**Rationale:** No changes needed to `core-scheduler`. The `job` dispatch mode already supports arbitrary job names with structured arguments. Adding a new dispatch mode would be over-engineering for what is functionally just another scheduled job.

**Revision to proposal:** The proposal listed `core-scheduler` as a modified capability. This is no longer needed — insight-scan tasks use the existing job dispatch infrastructure without modification.

## Risks / Trade-offs

**[Cold start — no insights until butlers have enough data]** The insight generators need domain data to produce meaningful candidates. A new user with no transactions, no health measurements, and no contact interactions will see no insights. Mitigation: this is acceptable and even desirable. Empty insights ("You have no transactions to analyze") would be worse than silence. The system should earn the right to notify by having something worth saying.

**[Cross-butler dedup key coordination requires convention adherence]** Butlers must agree on shared `dedup_key` patterns for overlapping domains (e.g., birthdays). A butler using a non-standard key format breaks deduplication silently. Mitigation: document the key conventions in the spec. The Switchboard's `propose_insight_candidate()` tool validates dedup_key format at the entry point (must match `{category}:{entity}:{time-scope}` or `{butler}:{category}:{entity}:{time-scope}` pattern) and rejects non-conforming keys with an actionable error message.

**[Engagement tracking is a rough proxy]** "Any message within 1 hour of delivery" is an imprecise signal — the user may have messaged about something unrelated. Mitigation: this is intentionally rough. Over-engineering engagement tracking (click tracking, read receipts, sentiment analysis) would add complexity for marginal accuracy. The 14-day rolling window smooths out noise.

**[Staging table accumulation]** If the delivery cycle fails repeatedly, `shared.insight_candidates` will accumulate rows. Mitigation: the `expires_at` column provides natural cleanup. A periodic cleanup job (part of the delivery cycle) marks expired candidates as `status='expired'` and deletes rows older than 30 days.

**[Quiet hours timezone edge cases]** Users traveling across timezones may have quiet hours misconfigured. Mitigation: quiet hours use the user's configured timezone (not UTC), and the delivery cycle converts appropriately. If no timezone is set, quiet hours are not applied.

**[Budget of 1 feels too restrictive as default]** Some users may want more signal from day one. Mitigation: the onboarding flow (or first interaction with a butler's proactive insight) should mention the verbosity setting. The system is conservative by design — it is easier to turn up volume than to regain trust after spam.

## Migration Plan

1. **Schema migration:** Create `shared.insight_candidates`, `shared.insight_cooldowns`, `shared.insight_engagement`, and `shared.insight_settings` tables via Alembic migration
2. **Insight broker module:** Implement `src/butlers/modules/insight_broker.py` as a Switchboard module with the `propose_insight_candidate()` MCP tool and the delivery cycle job
3. **Insight candidate model:** Implement shared `InsightCandidate` dataclass in `src/butlers/insight.py` (importable by all butlers for constructing candidates). No direct DB write helper — butlers submit candidates via the Switchboard MCP tool.
4. **Butler insight-scan jobs:** Add `insight-scan` job handlers to relationship, health, finance, and travel butlers; each calls `propose_insight_candidate()` via Switchboard MCP. Add `[[butler.schedule]]` entries to each butler's `butler.toml`.
5. **Notify extension:** Add `intent="insight"` support to the notify contract for digest rendering
6. **Settings API:** Expose verbosity and quiet hours configuration via a state key (`insight_settings`) readable/writable by any butler
7. **Enable:** Deploy with `minimal` verbosity default. Monitor `shared.insight_candidates` accumulation and engagement rates for 2 weeks before considering default change.

**Rollback:** Remove the `[[butler.schedule]]` entries for `insight-scan` from each butler's TOML. The delivery cycle will find no candidates and do nothing. The tables can remain — they will simply be empty. Full rollback: revert the code and drop the `shared.insight_*` tables.

## Open Questions

- **Should insights support actionable buttons?** A "Dismiss" button could provide explicit negative feedback (stronger signal than ignoring). A "Tell me more" button could trigger a full butler session on the topic. Deferred to a follow-up change — the current design is notification-only.
- **Should the digest include insights from previous days that were deferred by quiet hours or budget limits?** Current design says no — deferred candidates compete fresh in the next cycle and may lose to newer, higher-priority candidates. This could mean some medium-priority insights never get delivered. Acceptable for `minimal`; may need revisiting for `normal`/`verbose`.
- **Should there be a per-butler insight budget in addition to the global budget?** Current design uses only a global budget. A pathological case: one butler dominates every delivery cycle because its domain consistently produces higher-priority candidates. This may be fine (it reflects genuine priority) or may starve quieter domains. Monitor before adding complexity.
