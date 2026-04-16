## Why

Today `memory_entity_resolve` ranks an exact canonical-name match (score 100) above an exact alias match (score 80), independent of how strongly each candidate is connected to the rest of memory. In a real session (`b6915262-7109-410e-94eb-8bc3b9a74ba9`), resolving `"Chloe"` returned the existing entity `Chloe Wong` only as a score-80 alias hit. The runtime LLM read the lower score plus the differing canonical name as "this is a different person" and called `memory_entity_create("Chloe", "person")` — which the case-sensitive `uq_entities_canonical_type_live` index admitted as a duplicate. The result is two live `person` entities for the same Chloe, only one of which carries the accumulated relationship history.

This collides with shared doctrine. `about/heart-and-soul/v1.md:99-102` names the canonical contact/entity registry as a v1 invariant, and `about/heart-and-soul/security.md:152-165` requires that contact merging stay a deliberate (manual or LLM-assisted) decision — not a side-effect of a weak score. Resolution must use the signal we already have — how many active facts already reference each candidate — to make the right entity obvious to the calling LLM.

## What Changes

- Collapse `memory_entity_resolve`'s `exact` (canonical-name) and `alias` tiers into a single **case-insensitive exact-match tier**. Canonical-name match and alias match are equivalent inputs to ranking; neither is intrinsically preferred.
- Within the collapsed exact tier, rank candidates by `fact_count`, defined as the count of rows in the memory `facts` table where `entity_id = e.id OR object_entity_id = e.id`, restricted to `validity = 'active' AND invalid_at IS NULL`.
- Promote every candidate whose `fact_count` equals the tier's maximum to `score = 100`. Non-maximum exact-tier candidates retain their pre-promotion base score (80). Ties at the top all surface at 100 so the runtime LLM sees the ambiguity and disambiguates.
- Lower tiers (`prefix`, `fuzzy`, `role`) are unchanged.
- Include `fact_count` in each result record so the dashboard and runtime LLM can see the basis for the ranking.
- **BREAKING** for callers parsing the `name_match` field: alias-only and canonical-only matches now both report `name_match = "exact"`. Callers that branched on `"alias"` vs `"exact"` must instead inspect `aliases` / `canonical_name` themselves.
- Update the `butler-memory` shared skill (`roster/shared/skills/butler-memory/SKILL.md`) to remove the "score gap ≥ 30" disambiguation heuristic, since the new design uses **ties at 100** as the explicit ambiguity signal. Single-100 = reuse; multiple-100 = ask the user.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `entity-identity`: adds a new requirement defining the case-insensitive exact-match tier and the fact-count-based score promotion contract for `memory_entity_resolve`. Also rewords the existing "Google account entities excluded from identity resolution" scenario to drop the "alias resolution" phrasing — alias is no longer a distinct tier.

## Impact

- **Code (memory butler):** `src/butlers/modules/memory/tools/entities.py` — `entity_resolve` SQL and scoring logic. The discovery query gains a `fact_count` aggregate (or a follow-up batch lookup keyed by candidate id). Tier merging and base-score assignment are restructured. `_TIER_RANK` and `_MATCH_BASE` constants change.
- **Code (relationship butler downstream consumers):**
  - `roster/relationship/api/router.py:570-604` (`_suggest_entities`) — its docstring at line 578 ("exact=100, alias=80, prefix=50, fuzzy=20") and any score-arithmetic callers must be reconciled with the collapsed tier.
  - `roster/relationship/tools/resolve.py:170` — emits `score=100` for single-exact contact matches and integrates with `_resolve_via_entity_resolve`; verify nothing depends on alias scoring at 80.
  - `roster/relationship/tests/test_tools.py` — assertions like `score >= 100` margin checks need re-grounding against the new tied-100 semantics.
- **API surface:** `memory_entity_resolve` MCP tool return shape gains `fact_count`. `name_match` value `"alias"` is removed from the exact tier.
- **Skills:** `roster/shared/skills/butler-memory/SKILL.md:23-27` — replace the `≥30 points` disambiguation gap with the ties-at-100 rule.
- **Specs:**
  - `openspec/specs/entity-identity/spec.md` — add new requirement (resolve scoring contract); modify the Google-account exclusion scenario at line 388-392 to drop "alias resolution" wording.
  - `openspec/specs/module-memory/spec.md` — no change. Live `module-memory` spec only covers correction-driven retraction; it does not contain tier-scoring requirements today.
- **No DB schema changes.** `fact_count` is computed on read; no new column, no new index proposed in this change.
- **No migration.** The duplicate `Chloe` entity created in session `b6915262-...` is left as-is and will be cleaned up by an operator merge after this change ships.
- **RFC impact:** none. `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` covers contact/channel resolution at the switchboard layer and does not specify memory-layer entity-resolve scoring tiers.
- **Coordinate with in-flight changes:** `openspec/changes/add-degenerate-session-guardrails/` and `openspec/changes/predicate-registry-enforcement/` both touch `module-memory`; this change does not, so no merge order constraint.
