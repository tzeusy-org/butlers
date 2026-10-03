# Proactive Insight Engine

## Purpose
Defines the central coordination layer for proactive user-facing insights across all butlers. Covers the insight candidate schema, global rate limiting via delivery budget, cooldown tracking, cross-butler deduplication, adaptive delivery with graceful degradation, user-adjustable verbosity presets, and quiet hours suppression. This is the core anti-spam architecture — the system defaults to minimal noise and structurally prevents individual butlers from bypassing delivery controls.

## Requirements

### Requirement: Insight Candidate Schema
Every proactive insight produced by a butler SHALL be represented as a structured candidate row in the `public.insight_candidates` table. Candidates are proposals, not deliveries — they compete for delivery slots during the delivery cycle. Butlers submit candidates via the Switchboard's `propose_insight_candidate()` MCP tool (see Insight Candidate Submission requirement below); they do not write to the table directly.

#### Scenario: Candidate row structure
- **WHEN** the Switchboard's insight broker inserts an insight candidate into `public.insight_candidates`
- **THEN** the row SHALL include: `id` (UUID, primary key), `origin_butler` (TEXT, generating butler name), `priority` (INTEGER, 1-100), `category` (TEXT, domain category), `dedup_key` (TEXT, semantic deduplication key), `cooldown_days` (INTEGER, optional override), `expires_at` (TIMESTAMPTZ, required), `message` (TEXT, human-readable), `channel` (TEXT, optional preferred delivery channel), `metadata` (JSONB, optional butler-specific data), `created_at` (TIMESTAMPTZ, auto-set), `status` (TEXT, default `'pending'`), `delivered_at` (TIMESTAMPTZ, NULL until delivered)

#### Scenario: Valid status transitions
- **WHEN** a candidate's status changes
- **THEN** the only valid transitions SHALL be: `pending` to `delivered`, `pending` to `expired`, `pending` to `filtered`
- **AND** a candidate in `delivered`, `expired`, or `filtered` status SHALL NOT transition to any other status

#### Scenario: Candidate expiry
- **WHEN** a candidate's `expires_at` is in the past at the time of the delivery cycle
- **THEN** the candidate SHALL be marked `status='expired'` and SHALL NOT be considered for delivery

### Requirement: Priority Scoring Convention
Butler-generated insight priorities SHALL follow a standardized range convention to ensure consistent cross-butler ranking.

#### Scenario: Priority range semantics
- **WHEN** a butler assigns a priority to an insight candidate
- **THEN** priorities SHALL follow these ranges: 90-100 for time-critical insights (action needed within 24-48 hours), 70-89 for actionable-soon insights (action within 7 days), 50-69 for informational insights (summaries, milestones, trends), 30-49 for low-urgency nudges (suggestions, reconnections), 1-29 for background observations (verbose mode only)

#### Scenario: Priority boundary validation
- **WHEN** a butler calls `propose_insight_candidate()` with a priority outside 1-100
- **THEN** the tool SHALL return `{"status": "error", "reason": "priority must be between 1 and 100"}` without inserting a row

### Requirement: Deduplication via Semantic Keys
Cross-butler and within-butler deduplication SHALL use semantic `dedup_key` strings rather than message text comparison. Only the highest-priority candidate per `dedup_key` survives the delivery cycle.

#### Scenario: Within-butler deduplication
- **WHEN** the same butler produces two candidates with the same `dedup_key` (e.g., running insight-scan twice before a delivery cycle)
- **THEN** the delivery cycle SHALL retain only the candidate with the higher priority
- **AND** the lower-priority duplicate SHALL be marked `status='filtered'`

#### Scenario: Cross-butler deduplication
- **WHEN** two different butlers produce candidates with the same `dedup_key` (e.g., Relationship and Calendar both producing `birthday:entity-uuid-123:2026`)
- **THEN** the delivery cycle SHALL retain only the candidate with the higher priority
- **AND** ties SHALL be broken by `created_at` ascending (earliest candidate wins)

#### Scenario: Dedup key format convention
- **WHEN** a butler constructs a `dedup_key`
- **THEN** it SHALL follow the format `{category}:{entity-identifier}:{time-scope}` for cross-butler deduplication (no butler prefix — shared namespace)
- **OR** `{butler}:{category}:{entity-identifier}:{time-scope}` for butler-specific insights that should not deduplicate across butlers

#### Scenario: Empty or missing dedup key
- **WHEN** a butler calls `propose_insight_candidate()` without a `dedup_key` or with an empty string
- **THEN** the tool SHALL return `{"status": "error", "reason": "dedup_key is required and must be non-empty"}` without inserting a row

### Requirement: Global Delivery Budget
The system SHALL enforce a global daily delivery budget that caps the total number of insights delivered to the user regardless of how many butlers produce candidates. Individual butlers cannot bypass or increase this budget.

#### Scenario: Budget enforcement during delivery cycle
- **WHEN** the delivery cycle runs and finds N pending candidates after deduplication and cooldown filtering
- **AND** the user's daily budget is B
- **THEN** at most B candidates SHALL be delivered, selected by descending priority with `created_at` ascending as tie-breaker
- **AND** remaining candidates SHALL remain `status='pending'` for the next cycle (not filtered or expired)

#### Scenario: Budget already exhausted
- **WHEN** the delivery cycle runs and B insights have already been delivered today (based on `delivered_at` within the current calendar day in the user's configured timezone)
- **THEN** no additional insights SHALL be delivered
- **AND** pending candidates SHALL remain for the next day's cycle

#### Scenario: Zero budget (verbosity off)
- **WHEN** the user's verbosity is set to `off` (budget = 0)
- **THEN** the delivery cycle SHALL mark all pending candidates as `status='filtered'` with no delivery
- **AND** butler insight-scan jobs SHALL skip candidate generation entirely (early return)

### Requirement: Verbosity Presets
The user SHALL be able to control insight delivery volume via named presets stored in `public.insight_settings`. The system SHALL default to the most conservative preset.

#### Scenario: Available presets
- **WHEN** the user queries or sets their verbosity level
- **THEN** the valid presets SHALL be: `off` (budget 0), `minimal` (budget 1), `normal` (budget 3), `verbose` (budget 5)
- **AND** a custom integer budget (1-10) SHALL also be accepted

#### Scenario: Default verbosity
- **WHEN** no verbosity setting exists in `public.insight_settings`
- **THEN** the system SHALL default to `minimal` (budget 1)

#### Scenario: Verbosity change via state tool
- **WHEN** a butler runtime instance calls `state_set(key='insight_verbosity', value='normal')` or the user requests a verbosity change through any butler
- **THEN** the setting SHALL be persisted in `public.insight_settings` and take effect at the next delivery cycle

### Requirement: Cooldown Tracking
After an insight is delivered or explicitly dismissed, the system SHALL prevent re-delivery of insights with the same `dedup_key` for a configurable cooldown period.

#### Scenario: Cooldown after delivery
- **WHEN** an insight with `dedup_key='birthday:uuid-123:2026'` is delivered
- **THEN** a cooldown entry SHALL be recorded in `public.insight_cooldowns` with `dedup_key`, `cooldown_until` = `now() + cooldown_days`, and `reason='delivered'`
- **AND** any future candidate with the same `dedup_key` SHALL be filtered out during the delivery cycle until `cooldown_until` has passed

#### Scenario: Default cooldown periods by priority range
- **WHEN** a candidate does not specify a custom `cooldown_days`
- **THEN** the default cooldown SHALL be: priority 90-100 = 1 day, priority 70-89 = 7 days, priority 50-69 = 14 days, priority 30-49 = 30 days, priority 1-29 = 30 days

#### Scenario: Custom cooldown override
- **WHEN** a candidate specifies `cooldown_days=3`
- **THEN** the cooldown period SHALL be 3 days regardless of the candidate's priority range

#### Scenario: Cooldown expiry
- **WHEN** a cooldown entry's `cooldown_until` is in the past
- **THEN** new candidates with that `dedup_key` SHALL be eligible for delivery
- **AND** expired cooldown entries SHALL be cleaned up periodically (retained for audit for 30 days)

#### Scenario: Redelivery after cooldown expiry
- **WHEN** a candidate with a `dedup_key` is delivered again after its prior cooldown entry has expired but has not yet been cleaned up
- **THEN** the existing `public.insight_cooldowns` row for that `dedup_key` SHALL be updated in place (`cooldown_until`, `reason`, `created_at` refreshed to the new delivery) rather than erroring on the `dedup_key` primary key
- **AND** marking the candidate delivered, recording its cooldown, and recording its engagement row SHALL commit as one atomic unit, so a failure partway through never leaves a candidate marked `'delivered'` without its cooldown/engagement bookkeeping

### Requirement: Adaptive Delivery with Graceful Degradation
The system SHALL keep the owner's configured global delivery cap intact while
shaping candidate ordering with per-category engagement weights. A category's
disengagement SHALL never reduce another category's available delivery capacity.
The system SHALL retain total-disengagement auto-off when every delivery has
remained unengaged across the existing fourteen-day safety window.

#### Scenario: Engagement detection
- **WHEN** an insight is delivered
- **THEN** the system SHALL record a row in `public.insight_engagement` with
  `insight_id`, `delivered_at`, `engaged` (BOOLEAN, default FALSE), `category`,
  and `origin_butler`
- **AND** if the OWNER sends any message to any butler within 60 minutes of
  `delivered_at`, the `engaged` field SHALL be set to TRUE
- **AND** ingress from a connector, an automated source, or any non-owner
  (including unresolved/unknown) sender SHALL NOT count toward engagement

#### Scenario: Engagement rate computation
- **WHEN** the delivery cycle ranks eligible candidates
- **THEN** it SHALL derive each category's engagement signal from its last ten
  attributed deliveries
- **AND** a category with no attributed deliveries SHALL retain baseline weight
- **AND** no aggregate engagement rate SHALL reduce the configured global cap

#### Scenario: Budget reduction on low engagement
- **WHEN** a category's engagement rate is at least 0.5
- **THEN** that category's weight SHALL remain at baseline
- **AND** the effective global budget SHALL equal the owner's configured budget

#### Scenario: Moderate disengagement
- **WHEN** a category's engagement rate is at least 0.25 and below 0.5
- **THEN** that category's weight SHALL be reduced to 0.75
- **AND** other categories and the configured global budget SHALL remain unchanged

#### Scenario: Severe disengagement
- **WHEN** a category's engagement rate is below 0.25
- **THEN** that category's weight SHALL be reduced to 0.5
- **AND** other categories and the configured global budget SHALL remain unchanged

#### Scenario: Total disengagement auto-off
- **WHEN** every insight delivered on each of 14 consecutive days remains
  unengaged (at least 1 insight delivered per day)
- **THEN** the system SHALL auto-downgrade verbosity to `off`
- **AND** SHALL deliver a final notification: "I've paused proactive insights
  since you haven't found them useful. You can re-enable them anytime."
- **AND** this final notification SHALL be delivered via direct `notify` (not
  through the insight pipeline)
- **AND** for any day in the 14-day window no longer present in
  `public.insight_engagement` (purged), the day's delivered/engaged totals SHALL
  be read from `public.attention_daily_rollup`

#### Scenario: No automatic increase
- **WHEN** a category's engagement evidence improves after its weight was reduced
- **THEN** only that category's later attributed deliveries or an explicit useful
  verdict MAY restore its baseline weight
- **AND** no other category's weight or the configured global budget SHALL change

### Requirement: Quiet Hours Suppression
The system SHALL use the global Owner Attention Policy in
`public.approvals_policy` to configure quiet hours during which routine insights
are not delivered. The policy is evaluated in its IANA timezone as the
end-exclusive interval `[quiet_start_hour, quiet_end_hour)`. Accumulated
candidates are NOT burst-delivered after quiet hours end.

`public.insight_settings` SHALL retain only insight verbosity and budget
controls; it SHALL NOT be a second quiet-hours authority. Missing, incomplete,
invalid, or unreadable policy data SHALL fail open for the regular delivery
cycle after logging the diagnostic condition.

#### Scenario: Canonical quiet-hours configuration
- **WHEN** the owner configures quiet hours through the Owner Attention Policy
- **THEN** the setting is stored in `public.approvals_policy` as
  `quiet_start_hour` (INTEGER, hour 0-23), `quiet_end_hour` (INTEGER, hour
  0-23), and `timezone` (TEXT, IANA timezone)
- **AND** no broker-private quiet-hours setting is read at runtime

#### Scenario: Delivery suppression during quiet hours
- **WHEN** the regular delivery cycle runs and the current time falls within
  the Owner Attention Policy interval
- **THEN** the delivery cycle SHALL skip routine delivery entirely
- **AND** pending candidates SHALL remain for the next non-quiet delivery cycle

#### Scenario: Exact quiet end resumes routine delivery
- **WHEN** the regular delivery cycle runs at exactly `quiet_end_hour` in the
  policy timezone
- **THEN** the policy does not suppress routine candidates on that boundary

#### Scenario: No burst after quiet hours
- **WHEN** the delivery cycle runs after quiet hours have ended
- **AND** candidates accumulated during quiet hours
- **THEN** the daily budget SHALL still apply — at most B insights are delivered
- **AND** candidates that exceed the budget remain pending for the next day
  (they do not get a "bonus" delivery slot)

#### Scenario: No usable policy configured
- **WHEN** `public.approvals_policy` has no complete usable quiet window
- **THEN** delivery SHALL proceed at the scheduled delivery cycle time without
  time-based suppression

### Requirement: Priority-Urgent Bypass of Quiet Hours and the Context Bus
The delivery cycle SHALL allow a candidate whose `priority` is at or above
`URGENT_PRIORITY_THRESHOLD` (90 — RFC 0011's "time-critical" floor) to bypass
both the global Owner Attention Policy and a context-bus `dnd`/`sleeping`
signal. When at least one such candidate is pending during what would otherwise
be a fully-suppressed cycle, the delivery cycle proceeds for urgent candidates
only; candidates below the threshold remain `status='pending'`, untouched, for
a later non-suppressed cycle.

#### Scenario: Urgent candidate delivered during quiet hours, routine candidate untouched
- **WHEN** the delivery cycle runs during active Owner Attention Policy quiet
  hours
- **AND** one pending candidate has `priority=95` and another has `priority=70`
- **THEN** the `priority=95` candidate is delivered (or included in a digest)
- **AND** the `priority=70` candidate's status remains `'pending'` — it is
  neither delivered nor marked `filtered`/`expired` by this cycle

#### Scenario: Fully suppressed cycle when no candidate is urgent
- **WHEN** the delivery cycle runs during active Owner Attention Policy quiet
  hours (or an active context-bus `dnd`/`sleeping` signal)
- **AND** every pending candidate has `priority < 90`
- **THEN** the cycle returns `skipped=True` and delivers nothing
- **AND** one `public.attention_ledger` row is written with `outcome="suppressed"`
  and the triggering `reason`

#### Scenario: Expiry runs regardless of suppression
- **WHEN** the delivery cycle would otherwise be fully suppressed (Owner
  Attention Policy or context bus, no urgent candidate)
- **THEN** the expiry step (marking `expires_at`-past candidates as `expired`)
  still runs unconditionally before the suppression check

### Requirement: Context-Bus Gating of the Delivery Cycle
The delivery cycle SHALL consult the situational context bus
(`public.user_context`) for an active `dnd`, `meeting`, `sleeping`, or
`traveling` signal, deterministically, as an additional suppression input
alongside the global Owner Attention Policy. When more than one such signal
is active, precedence is `dnd`, then `meeting`, then `sleeping`, then
`traveling` — the first of these, in that order, with an active
non-max-held instance wins and is reported as the suppression signal.

Each signal type has its own max-hold TTL bounding how long that signal
alone may suppress routine delivery, independent of the signal's own (often
much longer) context-bus expiry: `dnd` 4 hours, `meeting` 2 hours, `sleeping`
10 hours, `traveling` 6 hours. A signal whose `set_at` is older than its
max-hold TTL, relative to the delivery cycle's `now`, no longer suppresses
delivery even while it otherwise remains active on the context bus — this
exists because `traveling` may legitimately stay active for up to 30 days
(per the context-bus module's own TTL clamp), and routine insights must not
silently queue for the length of a trip.

#### Scenario: dnd signal suppresses when no quiet hours are configured
- **WHEN** `public.approvals_policy.quiet_start_hour`/`quiet_end_hour` are NULL
  (quiet hours not configured or not active)
- **AND** `public.user_context` has an active `dnd` signal within its max-hold
  TTL
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed exactly as if quiet hours were active, with
  `reason="context_bus:dnd"`

#### Scenario: meeting or traveling signal suppresses like dnd/sleeping
- **WHEN** `public.user_context` has an active `meeting` or `traveling`
  signal within its max-hold TTL, and no higher-precedence signal is active
- **AND** no pending candidate is priority>=90
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` (or
  `"context_bus:traveling"`), exactly as `dnd`/`sleeping` suppress today

#### Scenario: A signal beyond its max-hold TTL no longer suppresses
- **WHEN** `public.user_context` has an active `traveling` signal whose
  `set_at` is more than 6 hours before the delivery cycle's `now`
- **AND** no other suppressing signal is active
- **THEN** the cycle is NOT suppressed by that signal — the context-bus
  consult returns no suppression from it, even though the signal itself
  remains active (not yet expired) on the context bus

#### Scenario: A lower-precedence active signal still suppresses when a higher one has expired its hold
- **WHEN** `public.user_context` has an active `dnd` signal beyond its 4-hour
  max-hold TTL, and an active `meeting` signal within its 2-hour max-hold TTL
- **THEN** the cycle is suppressed with `reason="context_bus:meeting"` — the
  suppression check does not stop at the first (expired-hold) signal in
  precedence order, it falls through to the next eligible one

### Requirement: Owner Attention Policy Is the Only Quiet-Hours Authority
`public.insight_settings` SHALL not contain `quiet_start`, `quiet_end`, or
`quiet_timezone` columns. A guarded core migration SHALL preserve a complete,
valid legacy insight window only when `public.approvals_policy` is incomplete;
a complete canonical Owner Attention Policy wins conflicts.

#### Scenario: Legacy insight fields are removed after guarded migration
- **WHEN** the owner-attention consolidation migration completes
- **THEN** `public.insight_settings` has no quiet-hours fields
- **AND** the broker uses only `public.approvals_policy` at runtime

### Requirement: Attention Ledger Recording of Delivered/Coalesced/Failed Candidates
Every candidate the delivery cycle attempts to deliver SHALL be recorded to `public.attention_ledger`. A single-candidate delivery is recorded with `outcome="delivered"`; when multiple candidates are folded into one digest message (`deliver_count > 1`), each candidate in the digest is recorded with `outcome="coalesced"` — distinguishing "sent alone" from "sent as part of a composed batch" for later dashboard/audit use.

When the `notify_fn` dispatch fails (an error return or an exception), each selected candidate SHALL instead be recorded with `outcome="failed"` and a machine-readable `reason` (`"delivery_error:<detail>"` for an error return, `"unexpected_error:<ExceptionType>"` for an exception), so an insight-delivery outage is provable at the insight choke point instead of reading identically to a benign quiet-hours hold. `failed` MUST NOT be conflated with `deferred`/`suppressed` (benign, chosen holds) — this mirrors the `notify()` boundary's failed-vs-deferred distinction (see `core-notify` spec §"Attention Ledger Recording at the notify() Boundary"). Ledger recording is best-effort/fail-open: a ledger-write failure MUST NOT abort the `delivery_attempt_count`/3-strikes `filtered` bookkeeping it describes.

#### Scenario: Standalone delivery recorded as delivered
- **WHEN** the delivery cycle selects exactly one candidate and delivers it
- **THEN** one `public.attention_ledger` row is written with `outcome="delivered"`, `dedup_key` set to the candidate's `dedup_key`, and `notification_ref` set to the candidate's id

#### Scenario: Digest delivery recorded as coalesced, one row per candidate
- **WHEN** the delivery cycle selects 3 candidates and delivers them as one digest message
- **THEN** 3 `public.attention_ledger` rows are written, each with `outcome="coalesced"` and its own candidate's `dedup_key`/id

#### Scenario: Failed delivery recorded as failed, one row per candidate
- **WHEN** the `notify_fn` call for the selected candidates returns `status="error"` (or raises)
- **THEN** one `public.attention_ledger` row is written per selected candidate with `outcome="failed"`, `source="insight"`, the candidate's `dedup_key`/id, and a `reason` of `"delivery_error:<detail>"` (error return) or `"unexpected_error:<ExceptionType>"` (exception)
- **AND** the existing `delivery_attempt_count` bump and 3-strikes `filtered` transition remain unchanged

#### Scenario: 3-strikes give-up encoded in the failed row's metadata, not a separate row
- **WHEN** a selected candidate's delivery fails for the 3rd time and is marked `filtered`
- **THEN** its `outcome="failed"` ledger row carries `metadata.terminally_filtered=true` and `metadata.retryable=false` — no distinct `suppressed`/`abandoned` row is written, so the terminal give-up is provable via a metadata filter without conflating it with a benign chosen hold
- **AND** a candidate whose failure count is still below 3 records `metadata.retryable=true` (the next cycle retries it)

### Requirement: Hourly Urgent Sub-Cycle
`delivery_cycle()` SHALL accept an `urgent_only` mode used by a dedicated hourly schedule (distinct from the existing daily schedule), so a candidate at or above `URGENT_PRIORITY_THRESHOLD` (90) is delivered within the hour rather than waiting for the next daily cycle.

In this mode:
- candidate selection is narrowed to `priority >= URGENT_PRIORITY_THRESHOLD`
  from the start — routine (sub-threshold) candidates are never selected,
  filtered, deduplicated, or otherwise touched by this cycle;
- the quiet-hours/context-bus consult is skipped outright (not merely
  bypassed after being computed) — an urgent candidate is never suppressed by
  either, so querying them is unnecessary;
- the daily adaptive budget cap does not apply — every eligible urgent
  candidate is delivered (or folded into one digest) this cycle, not just the
  top-B;
- end-of-cycle maintenance (`cleanup_old_rows`, disengagement auto-off) is
  skipped — these are daily-cadence concerns the regular cycle already
  covers once a day.

An explicit `verbosity=off` configuration SHALL still suppress delivery in
`urgent_only` mode exactly as it does in the regular cycle — this is a hard
user opt-out, not a time-based deferral the urgent bypass is meant to
override.

#### Scenario: Urgent candidates delivered hourly, routine candidates untouched
- **WHEN** the hourly urgent sub-cycle runs with one candidate at
  `priority=95` and another at `priority=70` both pending
- **THEN** the `priority=95` candidate is delivered
- **AND** the `priority=70` candidate's status remains `'pending'`,
  untouched — it is neither delivered, filtered, nor deduplicated by this
  cycle

#### Scenario: No daily budget cap in urgent_only mode
- **WHEN** the hourly urgent sub-cycle runs with 3 eligible urgent candidates
  pending and the configured daily verbosity budget is 1
- **THEN** all 3 are delivered this cycle, composed into one digest message
  (not capped to 1 by the daily budget)

#### Scenario: Quiet hours and the context bus are bypassed without being queried
- **WHEN** the hourly urgent sub-cycle runs during active quiet hours with an
  eligible urgent candidate pending
- **THEN** the candidate is delivered
- **AND** the context-bus consult is never invoked (the urgent_only path
  narrows to urgent candidates first and skips both suppression checks
  entirely, rather than computing and then ignoring them)

#### Scenario: verbosity=off still suppresses urgent candidates
- **WHEN** `insight_settings.verbosity = 'off'`
- **AND** the hourly urgent sub-cycle runs with an urgent candidate pending
- **THEN** the cycle is skipped and the candidate is marked `filtered`,
  exactly as the regular daily cycle already behaves under `verbosity=off`

#### Scenario: A candidate the urgent sub-cycle already delivered is never re-sent
- **WHEN** the hourly urgent sub-cycle delivers a `priority=95` candidate
- **AND** the next daily cycle runs afterward
- **THEN** the daily cycle's `pending`-status fetch does not include that
  candidate — it was already transitioned to `status='delivered'` by the
  urgent sub-cycle, which is the same row-status guard against double-send
  the daily cycle already relies on for its own deliveries

### Requirement: Insight Candidate Submission via Switchboard MCP
Butlers SHALL submit insight candidates exclusively through the Switchboard's `propose_insight_candidate()` MCP tool. Direct writes to `public.insight_candidates` are prohibited (Rule 3: inter-butler communication is MCP-only through the Switchboard).

#### Scenario: propose_insight_candidate tool signature
- **WHEN** the insight broker module registers its MCP tools
- **THEN** it SHALL expose `propose_insight_candidate(priority: int, category: str, dedup_key: str, message: str, expires_at: datetime, cooldown_days: int | None = None, channel: str | None = None, metadata: dict | None = None) -> {"status": "accepted" | "filtered" | "error", "reason": str}`

#### Scenario: Successful candidate submission
- **WHEN** a butler calls `propose_insight_candidate()` with valid parameters
- **THEN** the tool SHALL insert a row into `public.insight_candidates` with `origin_butler` set to the calling butler's identity, `status='pending'`, and `created_at=now()`
- **AND** it SHALL return `{"status": "accepted", "reason": "candidate queued for delivery cycle"}`

#### Scenario: Verbosity off rejection
- **WHEN** a butler calls `propose_insight_candidate()` and the global verbosity is `off`
- **THEN** the tool SHALL return `{"status": "filtered", "reason": "verbosity is off"}` without inserting a row

#### Scenario: Dedup key format validation
- **WHEN** a butler calls `propose_insight_candidate()` with a `dedup_key`
- **THEN** the tool SHALL validate that the key matches the format `{segment}:{segment}:{segment}` or `{segment}:{segment}:{segment}:{segment}` (colon-separated, 3 or 4 segments, no empty segments)
- **AND** if the format is invalid, the tool SHALL return `{"status": "error", "reason": "dedup_key must match format {category}:{entity}:{time-scope} or {butler}:{category}:{entity}:{time-scope}"}`

#### Scenario: Missing required fields
- **WHEN** a butler calls `propose_insight_candidate()` with an empty `message` or missing `expires_at`
- **THEN** the tool SHALL return `{"status": "error", "reason": "..."}` with a descriptive message, without inserting a row

#### Scenario: Expired expires_at rejection
- **WHEN** a butler calls `propose_insight_candidate()` with `expires_at` in the past
- **THEN** the tool SHALL return `{"status": "error", "reason": "expires_at must be in the future"}` without inserting a row

### Requirement: Insight Broker Module on Switchboard
The insight broker SHALL be implemented as a Switchboard module (`module-insight-broker`) with the `propose_insight_candidate()` MCP tool and a scheduled task that orchestrates the delivery cycle.

#### Scenario: Module registration
- **WHEN** the Switchboard butler starts with `[modules.insight_broker]` in its `butler.toml`
- **THEN** the insight broker module SHALL register the `propose_insight_candidate` MCP tool and the `insight-delivery-cycle` job

#### Scenario: Delivery cycle execution order
- **WHEN** the `insight-delivery-cycle` job runs
- **THEN** it SHALL execute these steps in order: (1) check the Owner
  Attention Policy — if active, skip and return, (2) expire candidates past
  `expires_at`, (3) filter candidates with active cooldowns, (4) deduplicate
  by `dedup_key` (keep highest priority), (5) compute effective budget (apply
  adaptive reduction), (6) select top-B candidates by priority, (7) deliver
  via `notify` (digest for B>1, standalone for B=1), (8) record cooldowns for
  delivered candidates, (9) record engagement tracking rows, (10) clean up old
  rows (candidates older than 30 days, cooldowns older than 30 days past expiry)

#### Scenario: Delivery cycle scheduling
- **WHEN** the Switchboard butler's `butler.toml` configures the `insight-delivery-cycle`
- **THEN** it SHALL run as a daily scheduled task with a configurable cron (default `0 8 * * *` — 8:00 UTC)

### Requirement: Insight Candidate Model
A shared Python dataclass SHALL be available for all butlers to construct well-formed insight candidates for submission via the Switchboard MCP tool.

#### Scenario: InsightCandidate dataclass
- **WHEN** a butler's insight-scan job handler needs to construct a candidate
- **THEN** it SHALL use the `InsightCandidate` dataclass with fields: `priority` (int), `category` (str), `dedup_key` (str), `message` (str), `expires_at` (datetime), `cooldown_days` (int | None), `channel` (str | None), `metadata` (dict | None)
- **AND** the dataclass SHALL provide a `to_mcp_args()` method that returns a dict suitable for passing to the `propose_insight_candidate()` MCP tool call

#### Scenario: Client-side validation
- **WHEN** an `InsightCandidate` is constructed with `priority=0` or `priority=150`
- **THEN** the constructor SHALL raise a `ValueError` with message "priority must be between 1 and 100"
- **AND** this is a convenience validation — the Switchboard tool also validates server-side

### Requirement: Insight Settings Table
User insight preferences SHALL be stored in a dedicated `public.insight_settings` table with a single-row design (one settings record per installation).

#### Scenario: Settings schema
- **WHEN** the `public.insight_settings` table is created
- **THEN** it SHALL include: `id` (INTEGER, primary key, default 1),
  `verbosity` (TEXT, default `'minimal'`), `custom_budget` (INTEGER,
  nullable), and `updated_at` (TIMESTAMPTZ, auto-updated)

#### Scenario: Default row
- **WHEN** the insight system initializes and no settings row exists
- **THEN** a default row SHALL be inserted with `verbosity='minimal'` and all optional fields NULL

### Requirement: Candidate Cleanup
The system SHALL periodically clean up old insight data to prevent unbounded table growth.

#### Scenario: Candidate row cleanup
- **WHEN** the delivery cycle runs its cleanup step
- **THEN** it SHALL DELETE rows from `public.insight_candidates` where `status` is NOT `'pending'` AND `created_at` is older than 30 days

#### Scenario: Cooldown row cleanup
- **WHEN** the delivery cycle runs its cleanup step
- **THEN** it SHALL DELETE rows from `public.insight_cooldowns` where `cooldown_until` is older than 30 days in the past

#### Scenario: Engagement row cleanup
- **WHEN** the delivery cycle runs its cleanup step
- **THEN** it SHALL first upsert each affected day's delivered/engaged counts into `public.attention_daily_rollup` (see Requirement: Attention Daily Rollup)
- **AND** it SHALL DELETE rows from `public.insight_engagement` where `delivered_at` is older than 30 days

### Requirement: Attention Daily Rollup
The system SHALL persist a durable daily rollup of owner-engagement signal in `public.attention_daily_rollup`, so the disengagement ratchet's history survives the 30-day `insight_engagement` purge and cannot be poisoned by non-owner ingress.

#### Scenario: Rollup schema
- **WHEN** the `public.attention_daily_rollup` table is created
- **THEN** it SHALL include: `day` (DATE, primary key), `owner_ingress_count` (INTEGER, default 0), `insights_delivered` (INTEGER, default 0), `insights_engaged` (INTEGER, default 0), `updated_at` (TIMESTAMPTZ, auto-updated)

#### Scenario: Owner ingress recorded daily
- **WHEN** the Switchboard's engagement gate resolves an ingress sender to the owner
- **THEN** it SHALL upsert the current UTC day's `owner_ingress_count` in `public.attention_daily_rollup`, incrementing it by 1
- **AND** a rollup-write failure SHALL NOT block ingress routing

#### Scenario: Insight delivery rolled up before purge
- **WHEN** the delivery cycle's cleanup step purges `public.insight_engagement` rows older than 30 days
- **THEN** it SHALL first upsert each affected day's delivered/engaged counts into `public.attention_daily_rollup`

### Requirement: Switchboard insight reader endpoint

The Switchboard SHALL expose a read-only insight reader at `GET /api/switchboard/insights` so dashboard surfaces
can render pending insight candidates without each butler needing read access to the cross-butler
`public.insight_candidates` table. The reader is hosted on the **Switchboard** because the insight
broker (Switchboard) role is the only butler role that already holds SELECT on
`public.insight_candidates`. Per `core_010_insight_tables.py`, `butler_switchboard_rw` is granted full
DML (INSERT/UPDATE/DELETE — hence SELECT) on the table, whereas every other butler role (including
`butler_health_rw`) is granted **INSERT only** and has **no SELECT**. There is no blanket "all butlers
may SELECT all public tables" rule — `database-security` grants butler roles SELECT only on public
tables *outside* the write-authorization matrix, and `public.insight_candidates` is *inside* that
matrix. Hosting the reader on the Switchboard therefore requires **no grant migration** and preserves
schema isolation: a non-Switchboard butler does not gain direct SELECT through a new grant.

The reader SHALL accept a `butler` query parameter that filters by `origin_butler`, a `status`
parameter (default `pending`), and a `limit`. It returns the candidate rows the requesting surface is
allowed to see.

#### Scenario: Read pending health candidates

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health&status=pending`
- **THEN** the Switchboard MUST return insight candidates where `origin_butler = 'health'` and
  `status = 'pending'`
- **AND** each returned item MUST include `id`, `category`, `priority`, `message`, `metadata`,
  `created_at`, `status`, and `expires_at`

#### Scenario: Reader is hosted on the role that already holds SELECT

- **WHEN** the insight reader queries `public.insight_candidates`
- **THEN** it MUST run under the Switchboard (insight broker) role, which already holds access to
  that table
- **AND** no grant migration MUST extend SELECT on that table to the health or dashboard role

#### Scenario: Status filter defaults to pending

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health` with no `status` parameter
- **THEN** only candidates with `status = 'pending'` MUST be returned

#### Scenario: Butler filter scopes the result

- **WHEN** the reader is called with `butler=health`
- **THEN** candidates whose `origin_butler` is not `health` MUST NOT appear in the result

### Requirement: Broker Catch-Up Cycle at Suppression End
When the delivery cycle fully suppresses a routine (non-urgent) cycle —
no candidate at or above `URGENT_PRIORITY_THRESHOLD` pending, and the cycle
returns `skipped=True` with an `outcome="suppressed"` attention-ledger row —
it SHALL reconcile a deterministic one-shot scheduled task that re-invokes
the delivery cycle at the suppression's own computed end instant, instead of
relying solely on the next regularly scheduled cron tick. The suppression
end SHALL be computed as: the Owner Attention Policy's end-exclusive quiet
window boundary when the active suppression is `quiet_hours`; or the
suppressing context-bus signal's `set_at` plus that signal's own max-hold TTL
(per "Context-Bus Gating of the Delivery Cycle") when the active suppression
is a context-bus signal. Reconciliation SHALL be idempotent, keyed by a
single deterministic task identity so a suppressed cycle re-run before the
catch-up fires reschedules the existing task to a materially different
target rather than duplicating it, and best-effort/fail-open: a scheduling
failure SHALL NOT abort or alter the suppressed cycle's return.

A cycle that delivers at least one urgent candidate this tick (the
Priority-Urgent Bypass) was never fully suppressed and SHALL NOT reconcile a
catch-up task. A `daily_hold_mode` cycle that bypasses suppression via the
hard fallback deadline delivers this tick and SHALL NOT reconcile a catch-up
task either. A `daily_hold_mode` cycle that defers on a travel day
(`reason="travel_day_defer"`) IS a fully suppressed skip and SHALL reconcile
a catch-up task for `traveling`'s own max-hold end, exactly like any other
suppressed skip.

#### Scenario: Quiet-hours suppression schedules a catch-up at the policy's end boundary
- **WHEN** the delivery cycle is fully suppressed by the Owner Attention
  Policy quiet-hours window, with no urgent candidate pending
- **THEN** a one-shot catch-up task is reconciled for the exact instant the
  quiet window ends in the policy's configured timezone

#### Scenario: A context-bus signal schedules a catch-up at its max-hold end
- **WHEN** the delivery cycle is fully suppressed by an active context-bus
  signal (`dnd`, `meeting`, `sleeping`, or `traveling`), with no urgent
  candidate pending
- **THEN** a one-shot catch-up task is reconciled for that signal's `set_at`
  plus its own max-hold TTL

#### Scenario: A travel-day defer also schedules a catch-up
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is
  `traveling`, and no urgent candidate is pending
- **THEN** the cycle still defers with `reason="travel_day_defer"` (per
  "Hold-Until-First-Active Daily Digest Cadence") AND a one-shot catch-up
  task is reconciled for `traveling`'s max-hold end, even though the hard
  fallback deadline never force-delivers this cycle

#### Scenario: An urgent-bypass cycle does not schedule a catch-up
- **WHEN** the delivery cycle delivers at least one urgent (priority >=
  `URGENT_PRIORITY_THRESHOLD`) candidate this tick, whether or not a
  suppression signal is also active
- **THEN** no catch-up task is reconciled — the cycle was not fully
  suppressed

#### Scenario: Repeated suppression before the catch-up fires reschedules rather than duplicates
- **WHEN** a suppressed cycle reconciles a catch-up task for one computed end
  instant, and a later suppressed cycle (before that task has fired)
  computes a materially different end instant for the same or a different
  active suppression
- **THEN** the existing catch-up task is rescheduled to the new instant
  rather than a second task being created

#### Scenario: Scheduling failure does not abort the suppressed cycle
- **WHEN** reconciling the catch-up task raises an error (e.g. the scheduler
  is unavailable)
- **THEN** the delivery cycle still returns `skipped=True` with its
  suppressed-outcome ledger row intact, exactly as if catch-up reconciliation
  had not been attempted

### Requirement: Bounded Explicit Insight Feedback
The broker SHALL expose useful, not-now, and never owner-feedback verbs without
adding another global verbosity control. Feedback attribution SHALL be derived by
the server and the persisted evidence SHALL remain content-blind.

#### Scenario: Useful reverses a family hold
- **WHEN** the owner marks an insight useful after snoozing or muting its family
- **THEN** the family cooldown SHALL be removed and its category weight SHALL be eligible to return to baseline

#### Scenario: Not-now is bounded
- **WHEN** the owner marks an insight not-now
- **THEN** a future `snooze_until` SHALL be required and the family SHALL resume after that instant

#### Scenario: Never remains reversible
- **WHEN** the owner marks an insight never
- **THEN** the family SHALL receive an indefinite cooldown
- **AND** a later useful verdict SHALL reverse it

#### Scenario: Bounded doors share one behavior
- **WHEN** feedback is invoked through Switchboard MCP, REST, delivered-message action metadata, or the dashboard insight row
- **THEN** every door SHALL call the same useful, not-now, or never behavior
- **AND** no door SHALL accept a caller-asserted actor

### Requirement: Per-Category Reversible Attention Shaping
The broker SHALL shape candidate ordering with per-category engagement weights
inside the existing global delivery cap. It SHALL publish a content-blind reason
for every reduced category weight.

#### Scenario: Uniform engagement preserves the prior budget
- **WHEN** every eligible category has the same engagement history
- **THEN** category weights SHALL be equal
- **AND** the effective global budget and candidate count SHALL equal the previous global-budget behavior

#### Scenario: One category does not quiet another
- **WHEN** nine of the last ten Health insights were ignored and another category remained engaged
- **THEN** Health SHALL receive a lower weight without reducing the other category's weight
- **AND** its reason SHALL read `hearing less from Health: 9 of last 10 ignored`

### Requirement: Expired-Unseen Attention Truth
Every pending candidate that expires unseen SHALL produce exactly one attention
ledger row with `outcome=expired` and a closed `blocked_by` reason from `budget`,
`cooldown`, `held_by`, or `dedup`.

#### Scenario: Expiry is visible per origin
- **WHEN** a pending candidate expires before delivery
- **THEN** one content-blind ledger row SHALL reference that candidate
- **AND** `GET /api/attention/ledger/summary` SHALL count it as `expired_unseen` for the originating butler

#### Scenario: Equal priority prefers the perishable candidate
- **WHEN** two eligible candidates have equal weighted priority and only one expires before the next regular cycle
- **THEN** the perishable candidate SHALL rank first

### Requirement: Candidate Cooldown Is Not the Only Standing-State Record

Owner condition ledger reconciliation (`owner-condition-ledger` capability) SHALL NOT change `insight_candidates`' cooldown/dedup/verbosity/budget
semantics, which remain the sole delivery-gating mechanism defined elsewhere
in this specification. A producer MAY additionally reconcile a category it
submits candidates for into the owner condition ledger as a state side
effect alongside candidate submission.

#### Scenario: Owner condition reconciliation does not alter candidate delivery

- **WHEN** a producer reconciles a category into the owner condition ledger
  on the same scheduled run it submits an insight candidate for that
  category
- **THEN** the candidate's dedup key, cooldown, expiry, and priority
  evaluation proceed exactly as they would without the reconciliation call
- **AND** a reconciliation failure never blocks, delays, or alters candidate
  submission for that run

### Requirement: Correlated-Candidate Clustering in Digest Formatting
When the delivery cycle composes a multi-candidate digest, it SHALL group
candidates that share a non-null `metadata.entity_id`, or whose event time
windows overlap (`metadata.event_window: {start, end}` as ISO 8601
timestamps, or `metadata.event_date` as an ISO date normalized to a half-open
full UTC-day window `[00:00, next 00:00)`), into one labeled sub-group within
the digest message. An explicit `event_window` is authoritative: `event_date`
is considered only when `event_window` is absent. An explicit `event_window`
SHALL have positive duration (`end > start`); malformed, partial, empty,
wrong-type, or reversed windows resolve no correlation data. Event-window
overlap uses half-open `[start, end)` semantics, so adjacent boundaries alone
do not correlate. Grouping is
transitive: if candidate A links to B and B links to C, all three render as
one group even if A and C share neither an entity nor an overlapping window
directly. This grouping is deterministic and computed without any LLM call.
A candidate with neither field, or with malformed values for either field,
resolves no correlation data and renders as its own singleton entry, in
exactly the same textual form as digest formatting produced before this
requirement existed.

#### Scenario: Candidates sharing an entity render as one correlated group
- **WHEN** the digest includes two candidates whose `metadata.entity_id`
  values are equal and non-null
- **THEN** the digest renders those two candidates under one
  `Correlated (2):` sub-entry instead of two separate flat bullets

#### Scenario: Candidates with overlapping event windows render as one correlated group
- **WHEN** the digest includes two candidates whose `metadata.event_window`
  (or `metadata.event_date`) time ranges overlap
- **THEN** the digest renders those two candidates under one correlated
  sub-entry

#### Scenario: Adjacent UTC event dates remain separate
- **WHEN** the digest includes one candidate with `metadata.event_date`
  `"2026-08-04"` and another with `metadata.event_date` `"2026-08-05"`
- **THEN** the digest renders them as separate entries because their normalized
  half-open UTC-day windows meet at a boundary but do not overlap

#### Scenario: Empty event window fails open to singleton
- **WHEN** a candidate has an explicit `metadata.event_window` whose `start`
  and `end` are equal, alongside a valid window that covers that instant
- **THEN** the empty window resolves no correlation data and both candidates
  render as separate singleton entries

#### Scenario: Invalid explicit event window does not fall back to event date
- **WHEN** a candidate carries a partial, empty, wrong-type, zero-length, or
  reversed explicit `metadata.event_window` together with a valid
  `metadata.event_date` that would overlap another candidate's valid window
- **THEN** the explicit window resolves no correlation data, the event date is
  not used as a fallback, and both candidates render as separate singleton
  entries

#### Scenario: No correlation metadata preserves prior flat-list formatting
- **WHEN** none of the digest's candidates carry `metadata.entity_id`,
  `metadata.event_window`, or `metadata.event_date`
- **THEN** the digest renders as a flat numbered list of
  `[Butler] message` lines, byte-identical in structure to digest formatting
  before this requirement existed

#### Scenario: Malformed correlation metadata fails open to singleton
- **WHEN** a candidate's `metadata.event_window` or `metadata.event_date`
  cannot be parsed as a valid date/timestamp
- **THEN** that candidate is treated as having no correlation data for
  clustering purposes — it does not raise, and does not silently link to an
  unrelated candidate

### Requirement: Held-By Signal Telemetry on Suppressed Ledger Rows
The delivery cycle SHALL record a `held_by` key in the `metadata` of any
`public.attention_ledger` row it writes with `outcome="suppressed"`, naming
the specific suppression signal — the context-bus signal type (`"dnd"`,
`"meeting"`, `"sleeping"`, or `"traveling"`) or the literal string
`"quiet_hours"` — so the specific hold is queryable as structured data
without parsing the free-text `reason` field.

#### Scenario: Suppressed ledger row names the holding signal
- **WHEN** the delivery cycle is suppressed by an active `meeting` signal
- **THEN** the resulting `outcome="suppressed"` ledger row has
  `reason="context_bus:meeting"` and `metadata={"held_by": "meeting"}`

#### Scenario: Quiet-hours suppression names quiet_hours as the holding signal
- **WHEN** the delivery cycle is suppressed by the Owner Attention Policy
  quiet-hours window (not the context bus)
- **THEN** the resulting `outcome="suppressed"` ledger row has
  `reason="quiet_hours"` and `metadata={"held_by": "quiet_hours"}`

### Requirement: Best-Effort LLM Synthesis for Correlated Clusters
The delivery cycle SHALL attempt a best-effort one-sentence LLM synthesis for
each correlated cluster (see "Correlated-Candidate Clustering in Digest
Formatting") with more than one member when composing a multi-candidate
digest, rendering the synthesis inline with the cluster's `Correlated (N):`
label on success. Synthesis SHALL use only the "cheap"
model-catalog complexity tier resolved for the delivering butler via the
direct-API runtime lane (`runtime_type="api"`); when that tier resolves to
any other runtime, is unavailable, is over its token quota, times out, or
returns a blank response, synthesis SHALL fail open and the cluster renders
with its pre-existing plain bullet-list formatting. Synthesis SHALL NOT
introduce a new delivery budget knob — call volume is bounded by the
existing per-day candidate budget alone (at most one call per multi-candidate
cluster within an already-budgeted selection).

#### Scenario: Successful synthesis is rendered inline with the cluster label
- **WHEN** a correlated cluster resolves a non-blank one-sentence LLM
  synthesis
- **THEN** the digest renders `Correlated (N): <sentence>` for that cluster,
  followed by its member bullets unchanged

#### Scenario: Synthesis fails open to the plain cluster label
- **WHEN** the cheap tier resolves to a runtime other than the direct-API
  lane, is unavailable, is over quota, times out, or returns a blank response
- **THEN** the cluster renders as `Correlated (N):` with no inline sentence,
  identical to digest formatting before this requirement existed

#### Scenario: No new budget knob is introduced
- **WHEN** cluster synthesis is attempted
- **THEN** the number of synthesis calls in a cycle never exceeds the number
  of multi-candidate clusters within that cycle's already-computed
  `effective_budget` selection — no separate LLM-call budget setting exists

### Requirement: Hold-Until-First-Active Daily Digest Cadence
The daily (non-urgent) delivery cycle SHALL support a `daily_hold_mode` in
which a fully suppressed cycle with no pending urgent (priority >=
`URGENT_PRIORITY_THRESHOLD`) candidate does not unconditionally skip until
the next scheduled daily cron slot. Instead:
- if the active suppressing signal is `traveling`, the cycle SHALL defer the
  routine digest entirely for that tick (`reason="travel_day_defer"`),
  regardless of how long `traveling` has been active, and SHALL NOT
  force-deliver it via the hard fallback deadline below;
- otherwise (an active `dnd`, `meeting`, `sleeping`, or quiet-hours
  suppression), the cycle SHALL bypass suppression for the full routine
  pending set once the delivery cycle's `now` reaches the hard fallback
  deadline (11:00 UTC), so a held digest is never silently skipped for an
  entire day.

`daily_hold_mode` has no effect on a cycle that is not suppressed, nor on the
`urgent_only` hourly sub-cycle (which already bypasses this suppression
consult unconditionally per RFC 0011 Amendment 1).

#### Scenario: A travel day defers the routine digest without a deadline override
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is
  `traveling`, and no urgent candidate is pending
- **AND** the delivery cycle's `now` is past the hard fallback deadline
- **THEN** the cycle is still skipped with `reason="travel_day_defer"` — the
  hard fallback deadline does not force delivery on a travel day

#### Scenario: The hard fallback deadline force-delivers a held routine digest
- **WHEN** `daily_hold_mode=True`, the active suppressing signal is `dnd`,
  `meeting`, `sleeping`, or quiet-hours, no urgent candidate is pending, and
  the delivery cycle's `now` has reached the hard fallback deadline
- **THEN** the cycle proceeds with the full routine pending candidate set as
  if unsuppressed

#### Scenario: Before the hard fallback deadline, a held digest keeps waiting
- **WHEN** `daily_hold_mode=True`, a non-traveling suppressing signal is
  active, no urgent candidate is pending, and `now` has not yet reached the
  hard fallback deadline
- **THEN** the cycle is skipped with the original suppression reason,
  identical to `daily_hold_mode=False` behavior

### Requirement: Premise-Bound Candidates
A candidate MAY carry a typed `premise` naming the fact its message asserts: `{"kind": "owner_condition", "source", "fingerprint"}` (the condition episode is still active) or `{"kind": "probe", "butler", "probe", "args"}` (a registered, deterministic, zero-LLM probe answers true). `propose_insight_candidate` and its MCP tool SHALL accept `premise`, validate it before any write, and persist it on `public.insight_candidates.premise`. A candidate with no premise SHALL behave exactly as before this requirement.

#### Scenario: A malformed premise is rejected before any write
- **WHEN** a candidate is proposed with an unknown `kind`, a missing required key, or non-object probe `args`
- **THEN** the call returns `status="error"` and no row is inserted

#### Scenario: A premise that is false at send is withdrawn, never sent
- **WHEN** a delivery cycle runs and a pending candidate's premise evaluates false (for example the bill it names is now paid)
- **THEN** before suppression, cooldown, dedup and budget are applied the candidate SHALL move `pending -> withdrawn`, the notify function SHALL NOT be called for it, and one attention-ledger row with `outcome="withdrawn"` and `reason="premise_false"` SHALL be recorded
- **AND** a replayed cycle SHALL NOT withdraw or record it again

#### Scenario: An unknown premise is delivered stamped, never claimed as rechecked
- **WHEN** a probe errors, is not registered, or finds no evidence row
- **THEN** the candidate is delivered with its text suffixed `(as of <created_at UTC>)` and SHALL NOT be described as rechecked
- **AND** the stored `insight_candidates.message` is unchanged

### Requirement: In-Place Amendment of Delivered Insights
When a delivered candidate's `owner_condition` premise resolves, the delivered message SHALL be corrected without a new notification. The owner-condition ledger's `reconcile_snapshot` `post_write` hook SHALL enqueue one `public.insight_amendments` row per delivered candidate whose premise names the resolved condition, inside the reconcile transaction, idempotent on `(candidate_id, premise fingerprint + resolved_at)`. The delivery cycle SHALL apply pending amendments before any early return, independent of quiet hours, verbosity and budget.

#### Scenario: A standalone Telegram delivery is edited in place
- **WHEN** a pending amendment's candidate was delivered standalone with a stored chat id and provider message id
- **THEN** the cycle edits that message to a struck-through original plus `Resolved HH:MM UTC`, marks the amendment `applied`, and records one `outcome="amended"` ledger row

#### Scenario: An uneditable delivery folds into the next digest
- **WHEN** the delivery was a digest line, was sent over email, has no provider reference, the edit is rejected (Telegram 400), or three transport attempts fail
- **THEN** the amendment becomes `fold` and the next delivery that happens anyway SHALL carry a `Since last digest: Resolved ...` line, after which it is `folded`
- **AND** a correction SHALL NEVER be sent as its own standalone message

#### Scenario: Repeated reconciles stay single
- **WHEN** the same resolved condition is reconciled again
- **THEN** exactly one amendment exists for the candidate and episode, and a second cycle performs no second edit

