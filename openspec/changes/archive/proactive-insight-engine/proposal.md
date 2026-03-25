## Why

Butlers today are predominantly reactive — they respond when spoken to and run maintenance schedules that silently update internal state. The user only benefits from a butler's domain knowledge when they remember to ask. Valuable insights ("your friend's birthday is in 5 days", "spending on dining is 40% above average", "you haven't logged a health measurement in 2 weeks") are locked behind the user initiating a conversation. Surfacing these proactively transforms butlers from passive tools into anticipatory assistants.

However, proactive notifications are the single most common UX failure in consumer software. The overwhelming majority of "smart notification" systems devolve into spam within weeks. This change prioritizes **conservative delivery** — defaulting to less noise, not more — with structural safeguards against notification fatigue baked into the architecture rather than left to individual butlers' discretion.

## What Changes

- **New cross-butler insight coordination layer.** A centralized insight broker (implemented as a Switchboard-adjacent coordination concern) that collects candidate insights from all butlers, deduplicates across domains, ranks by priority, enforces global rate limits, and delivers only the top-N insights per day. Individual butlers never deliver insights directly — they propose candidates.
- **Per-butler insight generation via scheduled jobs.** Each domain butler gains an `insight-scan` scheduled task that evaluates its domain data and produces ranked insight candidates. These are structured proposals, not raw notifications.
- **Global delivery budget with conservative defaults.** A system-wide daily insight budget (default: 3/day) that caps total proactive notifications regardless of how many butlers have candidates. Butlers compete for delivery slots via priority scoring.
- **Cooldown and deduplication engine.** Per-topic cooldown tracking prevents re-nudging about the same insight within a configurable window (default: 7 days). Cross-butler deduplication prevents the same underlying event (e.g., a birthday) from generating insights from both the Relationship and Calendar butlers.
- **Adaptive delivery with graceful degradation.** If the user consistently ignores insights (no interaction within 24h of delivery), the system automatically reduces frequency rather than increasing urgency. Engagement tracking feeds back into delivery budget adjustments.
- **User-adjustable verbosity levels.** Three preset levels (minimal: 1/day, normal: 3/day, verbose: 5/day) plus quiet hours / do-not-disturb integration. Defaults to `minimal`.
- **Insight envelope schema.** A versioned `insight.v1` envelope that carries priority, category, cooldown key, deduplication key, expiry, and the message itself. This is the contract between butler insight generators and the delivery broker.

## Capabilities

### New Capabilities
- `proactive-insight-engine`: The central coordination layer — insight broker, global rate limiting, delivery budget, cooldown tracking, deduplication, adaptive delivery, verbosity settings, quiet hours, and the `insight.v1` envelope schema. This is the core spec for the entire system.
- `insight-delivery`: The delivery pipeline from ranked/filtered insights to actual user-facing notifications via the existing `notify` contract. Covers delivery channel selection, batching (digest mode vs individual), timing, and engagement tracking for adaptive feedback.

### Modified Capabilities
- `core-scheduler`: Adds `insight-scan` as a recognized job dispatch mode alongside `prompt` and `job`, with built-in rate limiting for insight generation frequency.
- `core-notify`: Extends the notify contract with an `insight` intent type that carries insight metadata (priority, cooldown key, dedup key) alongside the message, enabling the Messenger butler to render insights with appropriate visual treatment.
- `butler-relationship`: Adds an `insight-scan` scheduled task that generates relationship-domain insight candidates (upcoming dates, stale contacts, pending gifts, interaction milestones).
- `butler-health`: Adds an `insight-scan` scheduled task that generates health-domain insight candidates (measurement gaps, medication refill timing, symptom trend alerts, streak recognition).
- `butler-finance`: Adds an `insight-scan` scheduled task that generates finance-domain insight candidates (spending anomalies, upcoming bills, budget threshold warnings, subscription renewal alerts).
- `butler-travel`: Adds an `insight-scan` scheduled task that generates travel-domain insight candidates (pre-trip checklists, document expiry, medication/prescription prep, booking confirmations).

## Impact

- **Database:** New `insight_candidates` table in `shared` schema for cross-butler insight staging. New `insight_cooldowns` table tracking per-topic delivery history for cooldown enforcement. New `insight_engagement` table tracking user interaction with delivered insights for adaptive feedback. New `insight_settings` table for user verbosity preferences and quiet hours.
- **Switchboard:** Gains insight broker coordination logic — collects candidates, deduplicates, ranks, enforces budget, dispatches winners to delivery. This is a new coordination concern alongside routing, not a modification to the routing pipeline.
- **Notify contract:** New `intent="insight"` delivery type with additional metadata fields. Non-breaking — existing send/reply/react intents are unchanged.
- **Butler TOML configs:** Each participating butler gains an `[[butler.schedule]]` entry for `insight-scan` with appropriate cadence (daily or twice-daily depending on domain).
- **Skills:** New shared `insight-generator` skill template that individual butler insight-scan prompts can reference for consistent candidate formatting.
- **Dashboard:** Future dashboard visibility into insight history, engagement metrics, and verbosity settings (deferred — not in this change's scope, but the data model supports it).
- **No breaking changes.** The entire system is additive. Butlers without insight-scan tasks continue operating unchanged. The delivery budget defaults to `minimal` (1/day), so even with the system enabled, noise is structurally limited from day one.
