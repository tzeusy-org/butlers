## 1. Database Schema

- [ ] 1.1 Create Alembic migration for `shared.insight_candidates` table (columns: id UUID PK, origin_butler TEXT, priority INTEGER CHECK 1-100, category TEXT, dedup_key TEXT NOT NULL, cooldown_days INTEGER, expires_at TIMESTAMPTZ NOT NULL, message TEXT NOT NULL, channel TEXT, metadata JSONB, created_at TIMESTAMPTZ DEFAULT now(), status TEXT DEFAULT 'pending' CHECK IN ('pending','delivered','expired','filtered'), delivered_at TIMESTAMPTZ; indexes on status+expires_at, dedup_key, origin_butler)
- [ ] 1.2 Create `shared.insight_cooldowns` table in same migration (columns: id UUID PK, dedup_key TEXT NOT NULL, cooldown_until TIMESTAMPTZ NOT NULL, reason TEXT, created_at TIMESTAMPTZ DEFAULT now(); index on dedup_key+cooldown_until)
- [ ] 1.3 Create `shared.insight_engagement` table in same migration (columns: id UUID PK, insight_id UUID FK to insight_candidates, delivered_at TIMESTAMPTZ NOT NULL, engaged BOOLEAN DEFAULT FALSE; index on delivered_at+engaged)
- [ ] 1.4 Create `shared.insight_settings` table in same migration (columns: id INTEGER PK DEFAULT 1, verbosity TEXT DEFAULT 'minimal', custom_budget INTEGER, quiet_start INTEGER CHECK 0-23, quiet_end INTEGER CHECK 0-23, quiet_timezone TEXT, updated_at TIMESTAMPTZ; insert default row with verbosity='minimal')
- [ ] 1.5 Unit tests for migration: tables exist, constraints enforced, default settings row present

## 2. Insight Candidate Model

- [ ] 2.1 Implement `src/butlers/insight.py` with `InsightCandidate` dataclass (priority, category, dedup_key, message, expires_at, cooldown_days, channel, metadata) and `to_mcp_args()` method that returns a dict for the `propose_insight_candidate()` MCP tool call
- [ ] 2.2 Implement client-side validation in dataclass constructor: priority range 1-100, non-empty dedup_key, non-empty message, expires_at in the future
- [ ] 2.3 Unit tests for model: valid construction, priority out of range raises ValueError, empty dedup_key raises ValueError, expired expires_at raises ValueError, to_mcp_args() output format

## 3. Insight Broker Module (Switchboard)

- [ ] 3.1 Create `src/butlers/modules/insight_broker.py` implementing `Module` base class with `register_tools()`, `on_startup()`, `on_shutdown()`
- [ ] 3.2 Implement `propose_insight_candidate()` MCP tool: validate priority range (1-100), validate dedup_key format (3 or 4 colon-separated non-empty segments), validate non-empty message, validate expires_at in the future, check global verbosity (return filtered if off), insert row into `shared.insight_candidates` with origin_butler from calling butler identity, return `{"status": "accepted"|"filtered"|"error", "reason": str}`
- [ ] 3.3 Implement delivery cycle job handler with execution order: quiet hours check, expire old candidates, filter by cooldowns, deduplicate by dedup_key, compute effective budget (with adaptive reduction), select top-B by priority, deliver via notify, record cooldowns, record engagement rows, cleanup old rows
- [ ] 3.4 Implement quiet hours check: read settings, convert current time to user timezone, skip if within quiet window
- [ ] 3.5 Implement candidate expiry: UPDATE status='expired' WHERE status='pending' AND expires_at < now()
- [ ] 3.6 Implement cooldown filtering: exclude candidates whose dedup_key has an active cooldown (cooldown_until > now())
- [ ] 3.7 Implement deduplication: within each dedup_key group, keep only highest priority candidate (tie-break by created_at ASC), mark others status='filtered'
- [ ] 3.8 Implement effective budget computation: read verbosity preset, apply adaptive reduction based on 14-day engagement rate (>=0.5: no change, 0.25-0.5: budget-1, <0.25: 1, 0.0 for 14d: auto-off)
- [ ] 3.9 Implement delivery: compose digest for B>1 or standalone for B=1, call notify(intent='insight'), update candidate status to 'delivered' with delivered_at
- [ ] 3.10 Implement cooldown recording: insert row into insight_cooldowns for each delivered candidate with appropriate cooldown_days (custom or default by priority range)
- [ ] 3.11 Implement engagement row creation: insert one row per delivered candidate into insight_engagement
- [ ] 3.12 Implement cleanup: delete non-pending candidates older than 30 days, delete expired cooldowns older than 30 days, delete engagement rows older than 30 days
- [ ] 3.13 Implement auto-off on total disengagement: detect 14 consecutive days of engagement_rate=0.0, set verbosity to 'off', send final direct notify
- [ ] 3.14 Unit tests for propose_insight_candidate MCP tool: valid submission returns accepted, priority out of range returns error, invalid dedup_key format returns error, empty message returns error, expired expires_at returns error, verbosity=off returns filtered
- [ ] 3.15 Unit tests for delivery cycle: full pipeline with mock notify, budget enforcement, deduplication, cooldown filtering, quiet hours skip, adaptive budget reduction
- [ ] 3.16 Unit tests for edge cases: no candidates, all candidates expired, all candidates cooled down, budget already exhausted today, digest formatting with multiple butlers

## 4. Engagement Detection (Switchboard Integration)

- [ ] 4.1 Add engagement check to Switchboard ingress processing: on each ingress request, query insight_engagement for rows with engaged=FALSE and delivered_at within last 60 minutes, update to engaged=TRUE
- [ ] 4.2 Ensure engagement check is lightweight: use index on (delivered_at, engaged), limit query to avoid performance impact on ingress path
- [ ] 4.3 Unit tests for engagement detection: message within 60 minutes marks engaged=TRUE, message after 60 minutes does not, multiple engagement rows updated in batch

## 5. Notify Contract Extension

- [ ] 5.1 Add `'insight'` to the valid intents list in notify tool validation (alongside send, reply, react)
- [ ] 5.2 Ensure insight intent validates same fields as send intent (message required, request_context optional)
- [ ] 5.3 Ensure insight intent bypasses approval gate for owner-targeted notifications (consistent with send)
- [ ] 5.4 Unit tests for insight intent: accepted by notify tool, message required validation, owner approval bypass

## 6. Switchboard Butler Configuration

- [ ] 6.1 Add `[modules.insight_broker]` to `roster/switchboard/butler.toml`
- [ ] 6.2 Add `insight-delivery-cycle` scheduled task to switchboard `butler.toml` (cron: `0 8 * * *`, dispatch_mode: job, job_name: insight-delivery-cycle)
- [ ] 6.3 Verify module loads on switchboard startup without errors
- [ ] 6.4 Verify `propose_insight_candidate` MCP tool is registered and callable by other butlers via Switchboard

## 7. Relationship Butler Insight Scan

- [ ] 7.1 Implement `insight-scan` job handler in relationship butler roster module; submit each candidate via Switchboard's `propose_insight_candidate()` MCP tool
- [ ] 7.2 Implement upcoming date insight generation: query dates within 7 days, assign priorities (95/80/70), construct dedup_keys with shared birthday/anniversary namespace, call `propose_insight_candidate()` for each
- [ ] 7.3 Implement stale contact insight generation: query contacts overdue by tier-aware cadence, assign priorities (45 for 2x overdue, 35 for 1x overdue), exclude tier 1500 without stay_in_touch_days, call `propose_insight_candidate()` for each
- [ ] 7.4 Implement pending gift insight generation: query gifts with status idea/purchased and associated date within 14 days, priority 60, call `propose_insight_candidate()`
- [ ] 7.5 Implement interaction milestone insight generation: detect 100th interaction, 1-year anniversary of first interaction, priority 30, call `propose_insight_candidate()`
- [ ] 7.6 Implement early exit on verbosity=off: if `propose_insight_candidate()` returns `{"status": "filtered"}`, skip remaining candidate generation
- [ ] 7.7 Add `insight-scan` scheduled task to `roster/relationship/butler.toml` (cron: `0 7 * * *`, dispatch_mode: job, job_name: insight-scan)
- [ ] 7.8 Unit tests for relationship insight scan: candidate generation for each category, priority assignment, dedup_key format, MCP tool call arguments, handling of filtered/error responses, expiry date correctness

## 8. Health Butler Insight Scan

- [ ] 8.1 Implement `insight-scan` job handler in health butler roster module; submit each candidate via Switchboard's `propose_insight_candidate()` MCP tool
- [ ] 8.2 Implement measurement gap insight generation: compute typical cadence from last 10 measurements, generate candidates when gap exceeds 2x cadence, exclude types with <3 entries, call `propose_insight_candidate()` for each
- [ ] 8.3 Implement medication refill insight generation: estimate depletion from logged doses vs prescribed frequency, priorities 90/75/60 by depletion timeline, exclude inactive medications, call `propose_insight_candidate()` for each
- [ ] 8.4 Implement symptom trend insight generation: detect 3+ logs of same symptom in 7 days with severity >= 3, priority 70, call `propose_insight_candidate()`
- [ ] 8.5 Implement health streak recognition: detect streaks at milestone thresholds (7/30/60/90/180/365 days), priority 25, call `propose_insight_candidate()`
- [ ] 8.6 Implement early exit on verbosity=off: if `propose_insight_candidate()` returns `{"status": "filtered"}`, skip remaining candidate generation
- [ ] 8.7 Add `insight-scan` scheduled task to `roster/health/butler.toml` (cron: `0 7 15 * * *`, dispatch_mode: job, job_name: insight-scan)
- [ ] 8.8 Unit tests for health insight scan: candidate generation for each category, cadence computation accuracy, medication depletion estimation, streak detection at milestones, MCP tool call arguments, handling of filtered/error responses

## 9. Finance Butler Insight Scan

- [ ] 9.1 Implement `insight-scan` job handler in finance butler roster module; submit each candidate via Switchboard's `propose_insight_candidate()` MCP tool
- [ ] 9.2 Implement spending anomaly insight generation: compare current month category totals vs 3-month rolling average, generate candidates when >30% above, exclude categories with <3 months history, call `propose_insight_candidate()` for each
- [ ] 9.3 Implement upcoming bill insight generation: query unpaid bills due within 3 days, priorities 92/75 by urgency, call `propose_insight_candidate()` for each
- [ ] 9.4 Implement budget threshold insight generation: detect 80%/90% budget utilization, priorities 70/50, call `propose_insight_candidate()`
- [ ] 9.5 Implement subscription renewal insight generation: query annual subscriptions renewing within 14 days, priorities 75/55, exclude monthly subscriptions, call `propose_insight_candidate()` for each
- [ ] 9.6 Implement early exit on verbosity=off: if `propose_insight_candidate()` returns `{"status": "filtered"}`, skip remaining candidate generation
- [ ] 9.7 Add `insight-scan` scheduled task to `roster/finance/butler.toml` (cron: `0 7 30 * * *`, dispatch_mode: job, job_name: insight-scan)
- [ ] 9.8 Unit tests for finance insight scan: spending anomaly detection with percentage thresholds, bill urgency priorities, subscription filtering (annual only), MCP tool call arguments, handling of filtered/error responses

## 10. Travel Butler Insight Scan

- [ ] 10.1 Implement `insight-scan` job handler in travel butler roster module; submit each candidate via Switchboard's `propose_insight_candidate()` MCP tool
- [ ] 10.2 Implement pre-trip preparation insight generation: query planned trips departing within 7 days, priorities 92/78/65, exclude completed/cancelled trips, call `propose_insight_candidate()` for each
- [ ] 10.3 Implement document expiry insight generation: query documents expiring within 90 days, priorities 85/65/45, cooldown_days 3/7/14 by urgency, call `propose_insight_candidate()` for each
- [ ] 10.4 Implement medication prep for travel insight generation: check for active medications + trips >3 days departing within 14 days, priorities 75/55, call `propose_insight_candidate()`
- [ ] 10.5 Implement early exit on verbosity=off: if `propose_insight_candidate()` returns `{"status": "filtered"}`, skip remaining candidate generation
- [ ] 10.6 Add `insight-scan` scheduled task to `roster/travel/butler.toml` (cron: `0 7 45 * * *`, dispatch_mode: job, job_name: insight-scan)
- [ ] 10.7 Unit tests for travel insight scan: pre-trip candidate generation, document expiry at each threshold, medication prep only for trips >3 days, exclusion of past/completed trips, MCP tool call arguments, handling of filtered/error responses

## 11. Integration Testing

- [ ] 11.1 Integration test: end-to-end flow from insight-scan calling `propose_insight_candidate()` via Switchboard MCP through delivery cycle to notify call, verifying budget enforcement
- [ ] 11.2 Integration test: cross-butler deduplication — relationship and calendar butlers submit same birthday dedup_key via `propose_insight_candidate()`, only highest priority survives delivery cycle
- [ ] 11.3 Integration test: cooldown enforcement — deliver an insight, verify same dedup_key is filtered in next cycle
- [ ] 11.4 Integration test: adaptive delivery — simulate low engagement rate, verify budget reduction
- [ ] 11.5 Integration test: quiet hours — set quiet hours, verify delivery cycle skips during quiet window
- [ ] 11.6 Integration test: verbosity=off — set off, verify `propose_insight_candidate()` returns filtered status, delivery cycle has no candidates to process
- [ ] 11.7 Integration test: digest formatting — submit 3 candidates across butlers via `propose_insight_candidate()`, verify single digest message with correct format
- [ ] 11.8 Integration test: dedup_key format validation — submit candidates with invalid dedup_key formats via `propose_insight_candidate()`, verify error responses with actionable messages
