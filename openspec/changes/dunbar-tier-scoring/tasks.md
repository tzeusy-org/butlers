## 1. Core Scoring Engine

- [ ] 1.1 Implement `compute_dunbar_scores(pool) -> list[DunbarScore]` function in `roster/relationship/tools/` that queries all listed contacts' interaction facts and computes exponential decay scores (lambda = ln(2)/30, sum all active interaction facts per contact)
- [ ] 1.2 Implement `get_tier_ranking(scores, overrides) -> list[TierAssignment]` that sorts by score, applies rank-based tier assignment (5/15/50/150/500/1500 boundaries), applies hysteresis (2-rank buffer on downward transitions), and merges manual overrides
- [ ] 1.3 Implement eligibility filtering: exclude contacts with `listed=false`, exclude person-entities without linked contact, return `null` tier/score for ineligible entities
- [ ] 1.4 Define tier constants: cadence map (5→14d, 15→21d, 50→45d, 150→120d, 500→270d, 1500→none), weight map (5→5.0, 15→3.0, 50→2.0, 150→1.0, 500→0.5)
- [ ] 1.5 Unit tests for decay score computation: zero interactions, single interaction, multiple interactions frequency vs recency, exclusion of non-active facts

## 2. Urgency Ranking

- [ ] 2.1 Implement `compute_urgency(tier_assignments, pool) -> list[UrgencyScore]` that computes `(days_overdue / cadence) * tier_weight + context_bonus` per contact, with `stay_in_touch_days` override, tier 1500 exclusion (unless `stay_in_touch_days` set)
- [ ] 2.2 Implement context bonus queries: +2.0 for upcoming date within 14 days, +1.0 for pending gift, +0.5 for positive emotional note context
- [ ] 2.3 Unit tests for urgency: overdue vs not-overdue, tier weight differences, context bonuses, `stay_in_touch_days` override, tier 1500 exclusion

## 3. Manual Tier Override Tool

- [ ] 3.1 Implement `dunbar_tier_set(pool, contact_id, tier)` MCP tool — stores `dunbar_tier_override` property fact via `store_fact`, validates tier in (5,15,50,150,500,1500,null), retract on null
- [ ] 3.2 Register `dunbar_tier_set` in the relationship module tool surface
- [ ] 3.3 Unit tests for override: set, update (supersession), clear (retraction), invalid tier rejection with actionable error message

## 4. Enrich Contact Responses

- [ ] 4.1 Update `contact_get` to include `dunbar_tier`, `dunbar_score`, and `dunbar_tier_override` (boolean) fields by calling scoring functions
- [ ] 4.2 Update `contact_search` to include `dunbar_tier` and `dunbar_score` in each result
- [ ] 4.3 Update `contacts_overdue` to use tier-aware cadences instead of flat `stay_in_touch_days` comparison
- [ ] 4.4 Tests for enriched responses: contact with interactions shows tier/score, contact without interactions shows 1500/0.0, archived contact excluded from ranking

## 5. Update Skills and Schedule

- [ ] 5.1 Update `reconnect-planner` skill (`SKILL.md`) to reference Dunbar tier-weighted urgency ranking instead of ad-hoc tier system with hardcoded thresholds
- [ ] 5.2 Update `relationship-maintenance` skill (`SKILL.md`) to use `compute_urgency` ranking instead of flat 30-day cutoff
- [ ] 5.3 Update `butler.toml` schedule prompt for `relationship-maintenance` to reference tier-aware prioritization

## 6. Entity API — Dunbar Fields and Sort Order

- [ ] 6.1 Add `dunbar_tier: int | None` and `dunbar_score: float | None` fields to `EntitySummary` Pydantic model in `src/butlers/api/models/memory.py`
- [ ] 6.2 Update `GET /api/memory/entities` handler in `src/butlers/api/routers/memory.py` to call `compute_dunbar_scores` (cross-schema read of relationship interaction facts) and populate Dunbar fields for person-entities (null for non-person)
- [ ] 6.3 Update default sort order: person-entities first sorted by role priority (owner > family > other roles > no roles) then Dunbar score descending, non-person entities after sorted by `canonical_name`
- [ ] 6.4 Ensure search results (`?q=`) preserve role+Dunbar sort order
- [ ] 6.5 Add `dunbar_tier` and `dunbar_score` to frontend `EntitySummary` type in `frontend/src/api/types.ts`

## 7. Frontend — Entity List Sort and Display

- [ ] 7.1 Update `EntitiesPage.tsx` to sort entities by role priority then Dunbar score (using API-provided sort order)
- [ ] 7.2 Display `dunbar_tier` badge or indicator on each person-entity row in the entities table
- [ ] 7.3 Handle cold start: when <5 contacts have scores, fall back to role+alphabetical sort gracefully (API returns 1500/0.0, no special frontend logic needed beyond not showing empty tier badges)

## 8. Frontend — Concentric Circles Visualization

- [ ] 8.1 Create `ConcentricCirclesDialog` component with dialog trigger button (bullseye/target icon) in entities page header
- [ ] 8.2 Implement ring layout: owner at center, concentric rings for tiers 5→15→50→150→500→1500, proportional ring sizing
- [ ] 8.3 Implement progressive detail: tiers 5/15 show avatar+name nodes, tier 50 shows initials+hover name, tiers 150+ show count badge with top-5 names and "show all" expansion
- [ ] 8.4 Visual distinction for manual tier overrides (pin icon or border accent on node)
- [ ] 8.5 Ring labels: tier name + count (e.g., "Support Clique (4)"), omit or thin-line for empty tiers
- [ ] 8.6 Click-through: clicking a person node navigates to `/entities/:entityId`
- [ ] 8.7 Cold start empty state: show "Interact with your contacts to see your social map take shape" when <5 contacts have scores, owner still at center, overridden contacts in their rings
- [ ] 8.8 Responsive scaling to dialog dimensions
