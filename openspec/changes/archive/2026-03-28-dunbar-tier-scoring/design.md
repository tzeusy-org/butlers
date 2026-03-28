## Context

The relationship butler stores interactions as temporal SPO facts (`predicate='interaction'`, `valid_at=occurred_at`, `scope='relationship'`). Each interaction is linked to a contact via `subject='contact:{contact_id}'` and to an entity via `entity_id`. The memory module already has `confidence` and `decay_rate` fields on facts, but these govern fact validity — not relationship health.

Today, three mechanisms handle reach-out prioritization with inconsistent models:
- `stay_in_touch_days` — per-contact column, explicit cadence, binary overdue check
- `relationship-maintenance` schedule — flat 30-day cutoff, picks 3 contacts
- `reconnect-planner` skill — ad-hoc tier labels (close friend, friend, acquaintance, professional, family) with hardcoded thresholds

The relationship butler's manifesto now defines the Dunbar model as its domain framework: 6 concentric layers with fixed sizes (5/15/50/150/500/1500), implicit placement from interaction patterns, and decay-influenced ranking.

## Goals / Non-Goals

**Goals:**
- Unified scoring model that replaces all three existing mechanisms
- Tier assignment computed from interaction history — no manual setup required
- Tier-aware cadence thresholds that replace the flat 30-day cutoff
- Manual override mechanism for cases where computed tier is wrong
- Weekly suggestions ranked by tier-weighted urgency

**Non-Goals:**
- Alerting or notifications when someone "drops a tier" — decay influences ranking silently
- User-configurable layer sizes — the 5/15/50/150/500/1500 numbers are fixed per manifesto
- ML-based threshold learning — use simple, deterministic formulas
- Removing `stay_in_touch_days` column — it remains as a per-contact override
- Real-time score updates — scores are computed at query time during scheduled tasks

## Decisions

### D1: Compute tier and score at query time, don't store them

**Decision:** Dunbar tier and decay score are derived from interaction facts via a SQL query or view. No new columns or tables.

**Rationale:** Interaction facts already have `valid_at` timestamps. A query can count interactions and compute recency in a single pass. Storing computed tier would create staleness problems — every `interaction_log` call would need to recompute and update the tier. Query-time computation is always fresh.

**Alternative considered:** Materialized view refreshed on a schedule. Rejected because the relationship butler's contact count (hundreds, not millions) makes real-time computation trivial. A materialized view adds operational complexity for no performance gain.

### D2: Decay score formula — exponential decay from last interaction

**Decision:** The decay score for a contact is computed as:

```
score = sum(weight_i * exp(-lambda * days_since_interaction_i))
```

Where:
- Each interaction contributes independently (not just the most recent one)
- `weight_i` = 1.0 for all interactions (simplest model)
- `lambda` = ln(2) / half_life_days, where `half_life_days = 30`
- The sum captures both frequency and recency — frequent recent interactions produce high scores

This produces a score that:
- Decays smoothly over time (no cliff at 30 days)
- Rewards frequency (10 interactions in a month > 1 interaction)
- Is unbounded above but practically ranges 0–20 for active contacts

**Alternative considered:** Simple "days since last interaction." Rejected because it loses frequency information — someone you spoke to once 5 days ago and someone you speak to daily both show "5 days" when the daily contact should score much higher.

### D3: Tier assignment — rank contacts by score, assign by Dunbar layer boundaries

**Decision:** Sort all contacts by decay score descending. Assign tiers by rank position:
- Ranks 1–5: Tier 5 (support clique)
- Ranks 6–15: Tier 15 (sympathy group)
- Ranks 16–50: Tier 50 (good friends)
- Ranks 51–150: Tier 150 (meaningful contacts)
- Ranks 151–500: Tier 500 (acquaintances)
- Ranks 501+: Tier 1500 (recognizable)

Contacts with zero interactions and no manual override are assigned tier 1500.

**Rationale:** This directly implements Dunbar's layer model — the tiers are about cognitive capacity, not absolute thresholds. The top 5 people by engagement ARE the support clique, by definition. This avoids needing to calibrate absolute score thresholds.

**Alternative considered:** Absolute score thresholds (e.g., score > 10 = tier 5). Rejected because thresholds would need tuning per user and would produce unstable tier sizes. A user who talks to everyone daily would have everyone in tier 5. Rank-based assignment enforces the fixed layer sizes.

### D4: Manual tier override — stored as an SPO property fact

**Decision:** A manual override is stored as a fact with `predicate='dunbar_tier_override'`, `content='{tier}'`, `entity_id=contact_entity_id`, `scope='relationship'`. When present, the contact is pinned to that tier regardless of computed score, but still sorted by score within the tier.

**Rationale:** Fits the existing SPO model. Supersession semantics handle updates naturally. No schema migration needed.

### D5: Tier-aware cadence thresholds

**Decision:** Each tier has a default expected contact cadence:

| Tier | Cadence | Overdue at |
|------|---------|------------|
| 5    | Weekly  | 14 days    |
| 15   | Biweekly | 21 days  |
| 50   | Monthly | 45 days    |
| 150  | Quarterly | 120 days |
| 500  | Biannually | 270 days |
| 1500 | Never (no proactive suggestions) | — |

A contact's `stay_in_touch_days`, if set, overrides their tier's default cadence.

**Rationale:** Inner tiers need more frequent contact, outer tiers less. Tier 1500 (recognizable) contacts are never suggested proactively — the user must explicitly set `stay_in_touch_days` if they want reminders for someone at that layer.

### D6: Unified ranking formula for weekly suggestions

**Decision:** The weekly `relationship-maintenance` suggestion ranking combines:

```
urgency = (days_overdue / tier_cadence) * tier_weight + context_bonus
```

Where:
- `tier_weight`: 5→5.0, 15→3.0, 50→2.0, 150→1.0, 500→0.5
- `context_bonus`: +2.0 for upcoming date within 14 days, +1.0 for pending gift, +0.5 for positive emotional context in recent notes
- Contacts not yet overdue have `days_overdue = 0` and only rank via context bonus
- Tier 1500 contacts are excluded unless they have `stay_in_touch_days` set

Top N by urgency score are suggested (default N=3, configurable in schedule prompt).

### D7: Implementation as a query helper, not a new tool

**Decision:** The Dunbar scoring logic is implemented as internal Python functions (`compute_dunbar_scores`, `get_tier_ranking`) called by the existing `contacts_overdue` query and the `reconnect-planner` / `relationship-maintenance` skills. No new MCP tools are exposed for tier queries — the LLM doesn't need to reason about tiers directly.

One new MCP tool is added: `dunbar_tier_set(contact_id, tier)` for manual overrides (stores the override fact). The existing `contact_get` response is enriched with `dunbar_tier` and `dunbar_score` fields.

**Rationale:** Tier computation is an internal prioritization mechanism, not something the LLM needs to invoke as a tool. The LLM already calls `contacts_overdue` and gets ranked results — the ranking just gets smarter. Manual override needs a tool because it's a user-facing action.

### D8: Entity API gets Dunbar data via a shared scoring function with cross-schema read

**Decision:** The memory router (`src/butlers/api/routers/memory.py`) already fans out to per-butler schemas to count facts. For Dunbar scoring, it will call the same `compute_dunbar_scores` function used by the relationship butler's MCP tools. This function takes a database pool and executes a single query joining `public.contacts` to `relationship.facts` (interaction predicates). The memory router already has read access to all schemas via its admin pool.

**Rationale:** The memory router is a dashboard read path, not a butler. It already crosses schema boundaries for fact aggregation. Adding one more cross-schema read for interaction-based scoring is consistent with the existing pattern. No new endpoints or inter-butler MCP calls needed.

**Alternative considered:** Having the relationship butler expose a `/dunbar-scores` MCP endpoint that the memory router calls. Rejected because it adds latency (MCP roundtrip) and complexity for what is fundamentally a read query the dashboard already has access to execute.

## Risks / Trade-offs

**[Score instability near tier boundaries]** → A contact at rank 5 and rank 6 may swap tiers frequently as interactions shift. Mitigation: hysteresis — a contact must drop 2 ranks below a boundary to actually move down a tier (e.g., must be rank 8+ to drop from tier 5 to tier 15).

**[Cold start for new users]** → With no interaction history, all contacts start at tier 1500 with score 0. Mitigation: the first time the system runs, it falls back to the existing `stay_in_touch_days` cadences. As interactions are logged, the Dunbar model gradually takes over. The reconnect-planner skill prompt can note "Dunbar tiers are still calibrating" in early weeks.

**[Contacts with no entity_id]** → Some legacy contacts may lack an `entity_id` FK. The score query must LEFT JOIN and handle NULLs gracefully, falling back to the `subject='contact:{id}'` pattern already used by interaction facts.

**[Breaking change to suggestion quality]** → Users accustomed to the flat 30-day model may notice different contacts being suggested. Mitigation: this is the intended behavior. The manifesto explicitly states that inner-circle relationships take priority. No rollback mechanism needed beyond reverting the code.

## Migration Plan

1. Add `dunbar_tier_set` tool and internal scoring functions
2. Update `contacts_overdue` to use tier-aware cadences
3. Update `reconnect-planner` skill to use `compute_dunbar_scores` instead of ad-hoc tier lookup
4. Update `relationship-maintenance` schedule prompt to reference tier-aware ranking
5. Enrich `contact_get` response with computed `dunbar_tier` and `dunbar_score`
6. Add `dunbar_tier` and `dunbar_score` to `EntitySummary` API model; update entity list endpoint sort order
7. Update `EntitiesPage.tsx` to sort by role priority then Dunbar score; add concentric circles dialog
8. No database migration required — uses existing facts table for overrides, existing interaction facts for scoring

**Rollback:** Revert the code. No data changes to undo.

## Open Questions

- **Should tier history be tracked?** Logging tier transitions over time could be interesting for self-reflection ("Alice drifted from your inner circle to good friends over the past 6 months"). Deferred to a future change.
