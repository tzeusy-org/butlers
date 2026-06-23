# Proactive Insight Engine

## Purpose
Defines the central coordination layer for proactive user-facing insights across all butlers. Covers the insight candidate schema, global rate limiting via delivery budget, cooldown tracking, cross-butler deduplication, adaptive delivery with graceful degradation, user-adjustable verbosity presets, and quiet hours suppression. This is the core anti-spam architecture — the system defaults to minimal noise and structurally prevents individual butlers from bypassing delivery controls.

## ADDED Requirements

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

### Requirement: Adaptive Delivery with Graceful Degradation
The system SHALL track user engagement with delivered insights and automatically reduce delivery frequency when the user ignores insights. The system SHALL NEVER automatically increase delivery frequency.

#### Scenario: Engagement detection
- **WHEN** an insight is delivered
- **THEN** the system SHALL record a row in `public.insight_engagement` with `insight_id`, `delivered_at`, and `engaged` (BOOLEAN, default FALSE)
- **AND** if the user sends any message to any butler within 60 minutes of `delivered_at`, the `engaged` field SHALL be set to TRUE

#### Scenario: Engagement rate computation
- **WHEN** the delivery cycle computes the engagement rate
- **THEN** it SHALL use a rolling 14-day window: `engagement_rate = count(engaged=TRUE) / count(delivered)` over the last 14 days
- **AND** if no insights were delivered in the last 14 days, the engagement rate SHALL be treated as 1.0 (no penalty)

#### Scenario: Budget reduction on low engagement
- **WHEN** `engagement_rate >= 0.5`
- **THEN** the effective budget SHALL equal the user's configured budget (no reduction)

#### Scenario: Moderate disengagement
- **WHEN** `0.25 <= engagement_rate < 0.5`
- **THEN** the effective budget SHALL be `max(1, configured_budget - 1)`

#### Scenario: Severe disengagement
- **WHEN** `engagement_rate < 0.25`
- **THEN** the effective budget SHALL be 1 regardless of the configured preset

#### Scenario: Total disengagement auto-off
- **WHEN** `engagement_rate == 0.0` for 14 consecutive days (at least 1 insight delivered per day during that period)
- **THEN** the system SHALL auto-downgrade verbosity to `off`
- **AND** SHALL deliver a final notification: "I've paused proactive insights since you haven't found them useful. You can re-enable them anytime."
- **AND** this final notification SHALL be delivered via direct `notify` (not through the insight pipeline)

#### Scenario: No automatic increase
- **WHEN** the user's engagement rate improves after a budget reduction
- **THEN** the effective budget SHALL NOT automatically increase
- **AND** the user MUST explicitly change their verbosity setting to restore the original budget

### Requirement: Quiet Hours Suppression
The user SHALL be able to configure quiet hours during which no insights are delivered. Accumulated candidates are NOT burst-delivered after quiet hours end.

#### Scenario: Quiet hours configuration
- **WHEN** the user configures quiet hours
- **THEN** the setting SHALL be stored in `public.insight_settings` as `quiet_start` (INTEGER, hour 0-23), `quiet_end` (INTEGER, hour 0-23), and `quiet_timezone` (TEXT, IANA timezone)

#### Scenario: Delivery suppression during quiet hours
- **WHEN** the delivery cycle runs and the current time falls within the user's quiet hours (in the user's configured timezone)
- **THEN** the delivery cycle SHALL skip delivery entirely
- **AND** pending candidates SHALL remain for the next non-quiet delivery cycle

#### Scenario: No burst after quiet hours
- **WHEN** the delivery cycle runs after quiet hours have ended
- **AND** candidates accumulated during quiet hours
- **THEN** the daily budget SHALL still apply — at most B insights are delivered
- **AND** candidates that exceed the budget remain pending for the next day (they do not get a "bonus" delivery slot)

#### Scenario: No quiet hours configured
- **WHEN** no quiet hours are configured in `public.insight_settings`
- **THEN** delivery SHALL proceed at the scheduled delivery cycle time without time-based suppression

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
- **THEN** it SHALL execute these steps in order: (1) check quiet hours — if active, skip and return, (2) expire candidates past `expires_at`, (3) filter candidates with active cooldowns, (4) deduplicate by `dedup_key` (keep highest priority), (5) compute effective budget (apply adaptive reduction), (6) select top-B candidates by priority, (7) deliver via `notify` (digest for B>1, standalone for B=1), (8) record cooldowns for delivered candidates, (9) record engagement tracking rows, (10) clean up old rows (candidates older than 30 days, cooldowns older than 30 days past expiry)

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
- **THEN** it SHALL include: `id` (INTEGER, primary key, default 1), `verbosity` (TEXT, default `'minimal'`), `custom_budget` (INTEGER, nullable), `quiet_start` (INTEGER, nullable, hour 0-23), `quiet_end` (INTEGER, nullable, hour 0-23), `quiet_timezone` (TEXT, nullable, IANA timezone), `updated_at` (TIMESTAMPTZ, auto-updated)

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
- **THEN** it SHALL DELETE rows from `public.insight_engagement` where `delivered_at` is older than 30 days

### Requirement: [TARGET-STATE] Switchboard insight reader endpoint

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
- **AND** the change MUST NOT introduce a new grant migration extending SELECT to the health or
  dashboard role

#### Scenario: Status filter defaults to pending

- **WHEN** the dashboard calls `GET /api/switchboard/insights?butler=health` with no `status` parameter
- **THEN** only candidates with `status = 'pending'` MUST be returned

#### Scenario: Butler filter scopes the result

- **WHEN** the reader is called with `butler=health`
- **THEN** candidates whose `origin_butler` is not `health` MUST NOT appear in the result
