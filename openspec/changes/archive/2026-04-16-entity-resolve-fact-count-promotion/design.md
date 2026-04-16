## Context

`memory_entity_resolve` is the only public choice point that decides "this name refers to an existing entity" vs. "create a new one." Its current contract assigns scores statically by where the name was found:

| match_type | base score |
|------------|-----------:|
| `role`     |        120 |
| `exact`    |        100 |
| `alias`    |         80 |
| `prefix`   |         50 |
| `fuzzy`    |         20 |

The runtime LLM uses these scores plus the `butler-memory` skill rule "single candidate or top score leads by ≥30 points → use it; otherwise ask" to decide whether to reuse or create. That rule is brittle in exactly one shape: when the only hit is a strong alias match on an entity whose `canonical_name` differs from the input. The LLM sees `score=80, canonical_name="Chloe Wong"` for an input of `"Chloe"`, decides "different person," and creates a duplicate. The case-sensitive partial unique index `uq_entities_canonical_type_live` on `(canonical_name, entity_type)` admits the duplicate because `"Chloe" ≠ "Chloe Wong"`.

The fix has to make a single, ranked exact-tier signal visible to the LLM without losing the ability to flag genuine ambiguity (two real Chloes).

## Goals / Non-Goals

**Goals:**
- Eliminate the "alias-only hit looks weaker than canonical" failure mode by collapsing canonical and alias matches into one case-insensitive exact tier.
- Use connection density (`fact_count`) as the within-tier ranker — a high-traffic existing entity should beat a low-traffic candidate sharing the same name.
- Surface ambiguity as **multiple results at score=100**, not as a calculated gap. The LLM rule becomes "exactly one 100 → reuse; multiple 100s → ask." Easier to encode and harder to misread.
- Keep `entity_resolve` a single MCP round-trip — no chained calls or extra latency on the hot path.

**Non-Goals:**
- Not changing the `uq_entities_canonical_type_live` index to be case-insensitive. That is a separate, larger change with merge-policy implications (tombstoning, history).
- Not back-filling `fact_count` into the entities table or adding triggers. Reads are cheap enough on per-call aggregation.
- Not auto-merging the existing duplicate `Chloe` entities. Merging stays operator-initiated per `about/heart-and-soul/security.md:152-165`.
- Not changing `prefix`, `fuzzy`, or `role` tier semantics.
- Not introducing dunbar-tier signal (relationship butler) into resolve. Cross-schema coupling rejected; `fact_count` is the local proxy.

## Decisions

### D1. Collapse canonical and alias into one case-insensitive exact tier

Both `LOWER(canonical_name) = LOWER($1)` and `EXISTS (SELECT 1 FROM UNNEST(aliases) a WHERE LOWER(a) = LOWER($1))` are treated as the same tier — `name_match = "exact"`. The reasoning: from the calling LLM's perspective, both mean "this name *is* one of this entity's known names." The current asymmetry exists only because canonical names happen to be stored in their own column. Aliases are a first-class part of identity (see `roster/relationship/CLAUDE.md` — entity-first workflow treats them as equivalent to canonical name for resolve).

**Alternatives considered:**
- *Keep tiers separate, just bump alias-base from 80 to 90.* Preserves a soft canonical preference but does nothing for the actual problem (the LLM still sees the differing canonical_name and infers "different person"). Rejected.
- *Add a third "alias-of-canonical-prefix" tier when the input is a token of the canonical name (e.g. "Chloe" is in "Chloe Wong").* Cleverer but adds a third dimension the LLM has to reason about. Rejected for complexity.

### D2. Rank within the exact tier by `fact_count` (active facts only)

`fact_count = COUNT(*) FROM <memory_schema>.facts WHERE (entity_id = e.id OR object_entity_id = e.id) AND validity = 'active' AND invalid_at IS NULL`.

Both subject and object references count — an entity that is talked-*about* as much as it talks-*about* others is equally "real." Restricting to active facts keeps tombstoned/superseded data from inflating the score; this matches the principle in `module-memory/spec.md` that retracted facts no longer count for live behavior.

**Alternatives considered:**
- *Include episodes / interactions / contact_info in the score.* More signal but couples resolve to subsystems memory-butler can't see (interactions live in the relationship butler). Rejected on schema-isolation grounds.
- *Add `last_referenced_at` as a secondary sort.* Useful tiebreak, but the user's explicit instruction was "no tiebreaker — return both at 100." Adopted as-stated.
- *Cache `fact_count` on `public.entities` via trigger.* Lower latency, but adds a write hot-path and a denormalisation invariant to maintain. Rejected for v1; revisit if profiling shows resolve regression.

### D3. Promote tier max → score = 100; ties stay tied

Compute `max_fact_count` across exact-tier candidates. Every candidate whose `fact_count == max_fact_count` returns with `score = 100`. The rest of the exact tier returns with `score = 80` (former alias-base).

This makes ambiguity self-evident:
- One 100 → unambiguous, the LLM reuses.
- Multiple 100s → genuine ambiguity (two equally-referenced same-named entities), LLM must ask.
- All sub-100 → no exact match at all (only prefix/fuzzy), the LLM either picks a prefix candidate or creates fresh.

**Alternatives considered:**
- *Promote only when lead exceeds a margin (e.g. ≥2× or ≥5 absolute).* Preserves more nuance but reintroduces a gap-threshold the LLM has to reason about — the same shape that produced the original bug. Rejected.
- *Always promote the singular max even at lead = 1.* Adopted. Even a thin lead is a real signal; the asymmetry "this entity is more referenced than that one" is exactly what we want to surface. Genuinely tied counts → ties at 100.

### D4. Compute `fact_count` in the discovery SQL

The existing `discovery_sql` UNIONs three tiers and returns one row per candidate. Add a `LEFT JOIN LATERAL (SELECT COUNT(*) ...) fc ON true` to project `fact_count` for each candidate row. After the discovery query, the `_TIER_RANK` deduplication keeps fact_count alongside the row. Then in the score-assignment step (currently `_MATCH_BASE` lookup), use `fact_count` to decide promotion.

**Alternatives considered:**
- *Second round-trip: gather candidate IDs, then `SELECT entity_id, COUNT(*) FROM facts WHERE entity_id IN (...) GROUP BY entity_id`.* Two queries, slightly more code. Rejected — `LATERAL` keeps it to one statement and the per-candidate count is bounded (resolve already caps candidate set).
- *Materialised view of (entity_id, fact_count) refreshed on schedule.* Premature; adds operational surface for unproven gain.

### D5. `name_match` field becomes one-of `{role, exact, prefix, fuzzy}` (no `alias`)

The `alias` value is removed from the public return shape. Callers that need to know which name matched can look at the returned `aliases` array and `canonical_name` themselves. This is **breaking** for `roster/relationship/api/router.py:570-604` and `roster/relationship/tools/resolve.py:170` — both must be updated in the same change. Tests in `roster/relationship/tests/test_tools.py` that assert specific scores or `name_match` values must be updated alongside.

### D6. Skill update — ties-at-100 replaces the gap heuristic

`roster/shared/skills/butler-memory/SKILL.md:23-27` currently says:
> Single candidate or top score leads by ≥30 points: Use the top entity_id.
> Multiple candidates, gap <30 points: Ask the user for clarification.

After this change:
> Single candidate at score=100, or exactly one candidate at score=100 ahead of any sub-100 results: use the top entity_id.
> Multiple candidates at score=100: ask the user for clarification — the system has detected a genuine ambiguity.
> No candidates at score=100 (only prefix/fuzzy/sub-tier): treat as zero exact match — fall through to the resolve-or-create protocol's "create a transitory entity" path, OR ask for clarification when the prefix candidate is plausible.

The skill update ships in the same PR as the code change so the runtime contract and the runtime guidance stay in sync.

## Risks / Trade-offs

- **[Risk]** A rarely-mentioned existing entity (low fact_count) shares a name with a frequently-discussed new entity. After import of the new entity, future resolves will rank the new entity at 100 and demote the original, even though both are "real." → **Mitigation:** Both still appear in results — the original is at 80, not hidden. The LLM still has the data to flag the situation if the context_hints suggest it. Long-term, dashboards can highlight "demoted historical entities" for operator review.
- **[Risk]** `fact_count` aggregation adds latency to every resolve call. Today's resolve is one statement; with a `LATERAL COUNT(*)` per candidate row it remains one statement but does an aggregate join. For typical candidate sets (≤10) this is negligible, but very-high-fact entities could push the count cost. → **Mitigation:** The aggregate uses the existing index on `facts(entity_id)`; the candidate set is already capped. Profile in dev; if regression > 50ms p95, swap D4 for the two-query alternative.
- **[Trade-off]** Removing `name_match = "alias"` is a contract break in the public MCP tool surface. → **Mitigation:** All known consumers (relationship butler API, resolve tool, tests) are updated in the same change. The MCP tool docstring is updated. No external consumers exist (the dashboard renders from `score` and `name_match` for display only and will be updated in tasks.md).
- **[Trade-off]** The duplicate `Chloe` left by the buggy session remains until manually merged. → **Mitigation:** Documented in proposal. Operator merge UI exists. Not blocking.
- **[Risk]** `fact_count` only counts memory-layer facts — interactions, calendar events, gifts (relationship butler) do not contribute. An entity rich in relationship data but poor in memory facts could lose ranking. → **Mitigation:** Acceptable for v1; relationship butler also calls `entity_resolve` from the memory butler, so the caller sees the same signal the runtime LLM sees. If this becomes a real failure mode, promote `fact_count` to a `signal_score` that pulls from a denormalised public column written by both subsystems.

## Migration Plan

1. **Code:** Update `entity_resolve` SQL and scoring (`src/butlers/modules/memory/tools/entities.py`). Update relationship downstream consumers in the same commit.
2. **Skill:** Update `roster/shared/skills/butler-memory/SKILL.md` heuristic.
3. **Tests:** Update `tests/` and `roster/relationship/tests/` for new tier semantics; add positive test for tied-100 case and singular-100 lead.
4. **Spec:** Apply the entity-identity delta.
5. **Deploy:** No DB migration. Hot-deploy is safe — old and new resolve return shapes are compatible except for `name_match = "alias"` removal, which only affects the relationship butler's own code, updated in the same PR.
6. **Rollback:** Revert the PR. No state changes to undo.

## Resolved Questions

- Should `fact_count = 0` candidates in the exact tier still be returned, or filtered? **Resolved: return them.** A single zero-fact candidate gets score 100 (it is max in its tier). Multiple zero-fact candidates tie at 100. A genuinely new entity should still be findable.
- Should `episodes.entity_id` references contribute to the rank alongside facts? **Resolved: no — moot.** The `episodes` table does not have an `entity_id` column. Episodes link to entities only through embedded content preambles, not via a queryable FK. `fact_count` counts from the `facts` table only.
- Does `entity_neighbors` need the same fact_count signal in its candidate ranking? **Resolved: out of scope.** Not changing in this PR. May surface as a follow-up bead.
