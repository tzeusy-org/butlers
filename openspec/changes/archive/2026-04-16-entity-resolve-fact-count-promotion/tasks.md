## Tasks

- [x] T1. Collapse canonical and alias tiers in discovery SQL

**File:** `src/butlers/modules/memory/tools/entities.py:322-370`

Merge Tier 1 (exact canonical_name, line 328) and Tier 2 (exact alias, line 338) into a single tier. The merged tier should match entities where `LOWER(canonical_name) = $1 OR $1 = ANY(SELECT LOWER(a) FROM UNNEST(aliases) AS a)`. Both conditions produce `match_type = 'exact'` and share the same tier number (1). Remove the `UNION ALL` between the old tiers 1 and 2. Renumber old Tier 3 (prefix) to Tier 2.

The prefix tier's exclusion clauses (lines 362-363) already exclude both canonical and alias exact matches, so they remain correct after the merge.

The `DISTINCT ON (id) ... ORDER BY id, tier ASC` dedup (lines 323, 369) continues to work — an entity matching both canonical and alias collapses to one row with the lowest tier number.

**Acceptance:** The discovery SQL has exactly three tiers in its UNION: role (0), exact (1, combined), prefix (2). No row ever returns `match_type = 'alias'`.

- [x] T2. Add `fact_count` via LATERAL join in the discovery SQL

**File:** `src/butlers/modules/memory/tools/entities.py:322-370`

Wrap the existing discovery query in an outer SELECT that adds a `LEFT JOIN LATERAL` to compute `fact_count` per candidate row:

```sql
SELECT d.*, COALESCE(fc.cnt, 0) AS fact_count
FROM (<existing discovery query>) d
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS cnt
    FROM {schema}.facts f
    WHERE (f.entity_id = d.id OR f.object_entity_id = d.id)
      AND f.validity = 'active'
      AND f.invalid_at IS NULL
) fc ON true
```

The `{schema}` placeholder must resolve to the memory butler's schema name. Check how the function currently references the facts table — the `pool` argument connects to the memory schema, so unqualified `facts` should work within the same search_path. Verify by reading the pool setup or the SQL in surrounding functions (e.g. `entity_neighbors`).

**Acceptance:** Each candidate row in `raw_rows` includes `fact_count` (integer). Fuzzy candidates (fetched separately via `_fetch_fuzzy_candidates`) should also get `fact_count` — either by a similar LATERAL in the fuzzy query or by a batch lookup after gathering all candidate IDs. Choose the simpler approach.

- [x] T3. Implement fact-count-based score promotion

**File:** `src/butlers/modules/memory/tools/entities.py:380-425`

After Step 2 (candidate dedup), carry `fact_count` from the row into each candidate dict. Then replace the static `_MATCH_BASE` score assignment (lines 416-425) with:

1. Assign base scores for non-exact tiers as before: `role=120`, `prefix=50`, `fuzzy=20`.
2. For exact-tier candidates: compute `max_fc = max(c["fact_count"] for c in exact_candidates)`. Candidates with `fact_count == max_fc` get `score = 100`. Others get `score = 80`.
3. Handle edge case: if `max_fc == 0` (all exact candidates have zero facts), all exact candidates get `score = 100` (they are tied at the max).

Update the constants block (lines 31-42):
- Remove `_SCORE_EXACT_ALIAS` (no longer used).
- Keep `_SCORE_EXACT_NAME = 100.0` (now means "promoted exact").
- Add `_SCORE_EXACT_DEMOTED = 80.0` (non-max exact tier).

Update `_TIER_RANK` (line 383): remove the `"alias"` key. The remaining keys are `{"role": -1, "exact": 0, "prefix": 1, "fuzzy": 2}`.

Update `_MATCH_BASE` (lines 416-422): remove `"alias"` entry. The exact-tier score is no longer read from this dict (it's computed via fact_count promotion), but keep the dict for the other tiers.

**Acceptance:** A single exact-tier candidate always gets 100. Two candidates with unequal fact_count: max gets 100, other gets 80. Tied fact_count: both get 100.

- [x] T4. Add `fact_count` to the return shape

**File:** `src/butlers/modules/memory/tools/entities.py:451-461`

Add `"fact_count": c["fact_count"]` to each returned dict. Ensure `fact_count` is an `int`, not a float or None.

Update the MCP tool docstring/description (find via `@mcp_tool` or the `register_tools` method in the memory module) to document the new `fact_count` field.

**Acceptance:** Every result dict includes `fact_count` (int). The `name_match` field never contains `"alias"`.

- [x] T5. Update relationship butler downstream consumers

**Files:**
- `roster/relationship/api/router.py:577-580` — update the docstring comment from `"exact=100, alias=80, prefix=50, fuzzy=20"` to `"exact=100|80 (fact-count promoted), prefix=50, fuzzy=20"`.
- `roster/relationship/tools/resolve.py:160-176` — this function does its own SQL query against `contacts` (not `entity_resolve`), then emits `score=100` for single exact matches. No code change needed here — it doesn't depend on the `"alias"` match type. Verify only.
- `roster/relationship/tools/resolve.py:182-189` — calls `_resolve_via_entity_resolve` which consumes `entity_resolve` results. Verify it doesn't branch on `name_match == "alias"`. If it does, remove that branch.

**Acceptance:** No relationship butler code references `"alias"` as a `name_match` value. The `_suggest_entities` docstring is accurate.

- [x] T6. Update butler-memory shared skill

**File:** `roster/shared/skills/butler-memory/SKILL.md:24-27`

Replace:
```
- **Single candidate or top score leads by ≥30 points:** Use the top `entity_id`. If inferred, confirm to the user transparently.
- **Multiple candidates, gap <30 points:** Ask the user for clarification before storing any facts.
```

With:
```
- **Exactly one candidate at score=100:** Use that `entity_id`. If inferred from an alias or partial name, confirm to the user transparently.
- **Multiple candidates at score=100:** The system detected a genuine ambiguity — ask the user for clarification before storing any facts. Present the tied candidates with their `canonical_name`, `aliases`, and `fact_count` so the user can choose.
- **No candidates at score=100 (only prefix/fuzzy/sub-tier hits):** Treat as no exact match. Fall through to the Resolve-or-Create Protocol below, OR ask for clarification if a prefix candidate looks plausible.
```

**Acceptance:** The skill no longer references a "≥30 points" gap heuristic. The disambiguation rule is based on count of score=100 results.

- [x] T7. Update tests

**File:** `tests/modules/memory/test_tools_entities.py`

Update existing tests:
- `test_exact_match_returns_score_exact_name` (line 214): the mock row must include `fact_count` in the returned data from `pool.fetch`. The assertion `score == _SCORE_EXACT_NAME` remains valid (single exact candidate is always promoted to 100).
- `_entity_mock_row` helper (line 60): add `fact_count: int = 0` parameter, include it in the returned dict.

Add new tests:
- `test_single_exact_alias_match_returns_score_100`: one entity matched via alias only → score=100, name_match="exact".
- `test_two_exact_candidates_unequal_fact_count`: entity A (fact_count=5) and entity B (fact_count=20) both match → B gets 100, A gets 80.
- `test_tied_fact_count_both_score_100`: two exact candidates with equal fact_count → both score=100.
- `test_zero_fact_count_single_candidate_score_100`: one exact candidate with fact_count=0 → score=100.
- `test_fact_count_in_return_shape`: verify `fact_count` key present and is int.
- `test_name_match_never_alias`: create candidates that would have been "alias" tier → verify name_match="exact".
- `test_retracted_facts_excluded_from_count`: **Deferred to integration tests.** The `validity = 'active' AND invalid_at IS NULL` filter is in the SQL LATERAL join — unit mocks cannot exercise SQL filtering. The filter is verified correct by code review (pass 3 check 2). An integration test with a real DB would be the right level for this scenario.

**Acceptance:** All new tests pass. No test references `_SCORE_EXACT_ALIAS` or asserts `name_match == "alias"`.

- [x] T8. Apply the entity-identity delta spec

**File:** `openspec/specs/entity-identity/spec.md`

The delta spec at `openspec/changes/entity-resolve-fact-count-promotion/specs/entity-identity/spec.md` defines ADDED and MODIFIED requirements. Apply them to the main spec:

1. **ADD** the "Entity resolve case-insensitive exact tier with fact-count promotion" requirement block (with all 7 scenarios) to the main spec under the appropriate section.
2. **MODIFY** the "Google account entities excluded from identity resolution" scenario: change `"they SHALL NOT appear in fuzzy name matching or alias resolution"` to `"they SHALL NOT appear in any tier — exact, prefix, or fuzzy"` (dropping the "alias resolution" phrasing since alias is no longer a distinct tier).

**Acceptance:** Main spec contains the new requirement. The Google-account scenario no longer references "alias resolution."

- [x] T9. Update relationship butler tests

**File:** `roster/relationship/tests/test_tools.py` (and any other test files under `roster/relationship/tests/`)

Search for assertions on `name_match == "alias"`, `score == 80` tied to alias semantics, or imports of `_SCORE_EXACT_ALIAS`. Update or remove them. If tests mock `entity_resolve` return values, ensure mocked results include `fact_count` and use `name_match = "exact"` for what were previously alias matches.

**Acceptance:** No relationship test references `"alias"` as a name_match value. All relationship tests pass.

## Ordering

T1 → T2 → T3 → T4 are sequential (each builds on the prior SQL/scoring change).
T5, T6, T8 can proceed in parallel after T4.
T7 and T9 should be written alongside or after their respective code changes (T3-T4 for T7, T5 for T9).

All tasks ship in a single PR.
