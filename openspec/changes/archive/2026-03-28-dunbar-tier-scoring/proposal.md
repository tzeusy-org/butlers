## Why

The relationship butler's reach-out algorithm treats all contacts equally — a flat "30+ days since last interaction" threshold. This ignores how human social capacity actually works. Robin Dunbar's research shows relationships exist in concentric layers of intensity (5/15/50/150/500/1500), each requiring different levels of maintenance. A best friend drifting for two weeks is more urgent than an acquaintance going quiet for three months. The current system can't express this, leading to suggestions that waste the user's limited social energy on low-priority contacts while inner-circle relationships silently fade.

Three overlapping mechanisms exist today (`stay_in_touch_days` column, `relationship-maintenance` schedule, `reconnect-planner` skill) with inconsistent thresholds and no shared model. Unifying them under Dunbar tiers creates a single, principled prioritization framework grounded in the relationship butler's manifesto.

## What Changes

- **Add Dunbar tier as a property of person-entities.** Each person known to the relationship butler gets an implicit tier (5, 15, 50, 150, 500, 1500) derived from interaction frequency and recency. Manual overrides allow the user to pin someone to a tier regardless of computed score.
- **Introduce an implicit decay score per person-entity.** A health score that erodes over time without interaction and refreshes when interactions are logged. The score is computed, not stored — derived from the interaction history already tracked in SPO facts.
- **Replace flat staleness thresholds with tier-aware cadences.** Inner tiers get shorter overdue windows; outer tiers get longer ones. This replaces the hardcoded 30-day cutoff in the `relationship-maintenance` schedule and the ad-hoc thresholds in `reconnect-planner`.
- **Rank weekly reach-out suggestions by tier-weighted urgency.** Combine Dunbar tier, decay score, and contextual signals (upcoming dates, pending gifts, emotional context) into a unified priority ranking.
- **Deprecate `stay_in_touch_days` as the primary cadence mechanism.** Dunbar tier-derived cadences become the default. `stay_in_touch_days` remains as a per-contact manual override for edge cases.

## Capabilities

### New Capabilities
- `dunbar-tier-scoring`: Dunbar layer assignment (implicit from interaction patterns, with manual overrides), decay score computation, tier-aware cadence thresholds, and unified reach-out ranking algorithm.

### Modified Capabilities
- `butler-relationship`: The scheduled task `relationship-maintenance` changes from "30+ days" flat threshold to tier-aware prioritization. The `reconnect-planner` skill adopts Dunbar tiers instead of its ad-hoc tier system. Memory taxonomy adds `dunbar_tier` and `dunbar_tier_override` predicates.

## Impact

- **Database/facts:** New SPO predicates (`dunbar_tier_override`) for manual tier pins. Computed tier and score are derived at query time from existing interaction facts — no new tables required.
- **Tools:** `stay_in_touch_set` tool continues to work but becomes a secondary override. New tools or tool parameters for tier queries and manual tier overrides.
- **Skills:** `reconnect-planner` and `relationship-maintenance` skills updated to use tier-aware ranking instead of flat thresholds.
- **Schedule:** `relationship-maintenance` cron prompt updated to reference Dunbar-aware prioritization.
- **butler.toml:** Schedule prompt text changes (non-breaking).
- **Dashboard — entities page:** Entity list default sort changes from alphabetical to role-priority + Dunbar score. `EntitySummary` API model gains `dunbar_tier` and `dunbar_score` fields. New concentric circles visualization dialog. Memory router gains cross-schema read of relationship interaction facts for score computation.
- **Dashboard — entity API:** `GET /api/memory/entities` response enriched with Dunbar fields. Sort order changes.
