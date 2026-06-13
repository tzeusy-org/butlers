# Entity v3 — Spec-to-Code Reconciliation Audit

- **Bead:** bu-6ivjj (gen-1 reconciliation under epic bu-89993)
- **Date:** 2026-06-13
- **Scope:** Backend specs of the OpenSpec change `entity-v3-lifecycle-and-depth`
  reconciled against the implementation landed by the merged sibling beads under
  epic bu-89993.
- **Method:** Read-only audit. Each normative spec requirement was matched to its
  implementing code/test with `file:line` evidence and classified
  `implemented | partial | missing | deviates`.

## Result Summary

| Spec file | Requirements | implemented | partial | missing | deviates |
|---|---|---|---|---|---|
| relationship-entity-lifecycle | 8 | 8 | 0 | 0 | 0 |
| relationship-entity-lookup | 5 | 5 | 0 | 0 | 0 |
| relationship-merge-review | 5 | 5 | 0 | 0 | 0 |
| relationship-facts (delta) | 6 | 6 | 0 | 0 | 0 |
| switchboard-identity (delta) | 1 | 1 | 0 | 0 | 0 |
| module-memory (delta) | 1 | 1 | 0 | 0 | 0 |
| **Total** | **26** | **26** | **0** | **0** | **0** |

**Verdict: full coverage.** Every audited backend requirement is implemented with
matching guardrail/behavioural tests. No untracked gaps were found. One known,
already-tracked spec-prose nit (lookup ranking numbers) is confirmed below and is
*not* re-filed.

---

## relationship-entity-lifecycle

| Requirement | Status | Evidence |
|---|---|---|
| Lifecycle stages are normative | implemented | Spec-level discipline requirement; each entity-v3 endpoint docstring cites its stage (e.g. `roster/relationship/api/router.py:7151` activity binning, `:7438` core-dates; `roster/relationship/tools/relationship_lookup.py` look-up stage). |
| Ingest — entity/fact creation paths | implemented | Connector no-write guardrail `tests/contracts/test_connector_no_fact_writes.py:83` (+ synthetic-violation `:112`, read-only allow `:120`); switchboard temp-contact path preserved (`roster/switchboard/tests/test_switchboard_no_fact_writes.py:218`). |
| Match — deterministic matching only | implemented | No-LLM scans: finder `roster/relationship/tests/test_finder_no_llm_guardrail.py` + `test_finder_no_llm_transitive.py`; compare/merge `roster/relationship/tests/test_merge_review_no_llm.py:93` (synthetic `:105`); lookup resolution is pure SQL `relationship_lookup.py:_resolve_ref` (`:102`). |
| Match — queue bucket derivation | implemented | `router.py:_classify_entity_state` (`:3129`) priority unidentified>duplicate>stale; dismissed-pair suppression SQL `router.py:_dismissed_pair_suppression_sql` (`:3079`) with re-raise on new `{predicate,shared_value}` not in snapshot. |
| Assert — supersession and immutable conf | implemented | Guardrail `roster/relationship/tests/test_conf_immutability_guardrail.py`; DB-layer supersession `test_relationship_assert_fact.py:358,691,702,720`. |
| Look up — three read surfaces, declared store layering | implemented | UI (dashboard endpoints), switchboard `resolve_contact_by_channel`, MCP `relationship_lookup`. Both stores layered identity-before-narrative: `relationship_lookup.py:538-541`; no cross-join (separate `_fetch_identity_facts`/`_fetch_narrative_facts`). |
| Age — read-time staleness derivation | implemented | `roster/relationship/tools/staleness.py` Python `staleness_band` (`:66`) + SQL builders `identity_staleness_band_sql` (`:147`) / `narrative_staleness_band_sql` (`:168`); per-store COALESCE chains + bands 30/180; tests `test_staleness.py`. |
| Canonical fact-store layering binding project-wide | implemented | Writer-side boundary `src/butlers/modules/memory/tools/writing.py:238-248` rejects identity predicates; test `tests/modules/memory/test_tools_writing.py:214`. |

## relationship-entity-lookup

| Requirement | Status | Evidence |
|---|---|---|
| `relationship_lookup` MCP tool contract | implemented | `roster/relationship/tools/relationship_lookup.py:471` — exactly-one-arg validation (`:64`), entity header w/ nullable tier (`:217`), both-store layered facts, recency block (`:412`), resolution block w/ candidates. Tests `tests/relationship/test_relationship_lookup.py:241,362,420`. |
| Lookup is read-only | implemented | Pure SELECTs; tests `test_relationship_lookup.py:468` (no writes) and `:482` (repeated identical reads). |
| In-session-only cost gate | implemented | Schedule-seed scan w/ empty allowlist `test_relationship_lookup.py:537`. |
| Docstring budget (≤300 tokens) | implemented | `test_relationship_lookup.py:514`; constraint-statement test `:520`. |
| Deterministic not-found behavior | implemented | Structured miss `relationship_lookup.py:506-518`; ambiguity `:493-505`; tests `:454,420`; bad-args raise `:224,230`. |

## relationship-merge-review

| Requirement | Status | Evidence |
|---|---|---|
| Compare endpoint — structural diff only | implemented | `POST /entities/compare` `router.py:6803`; deterministic `_derive_shared_and_divergent` (`:6641`) shared = identical (predicate,object); divergent = single-cardinality only. |
| No model involvement — guardrail-tested | implemented | `test_merge_review_no_llm.py:93` scans compare/merge/dismiss paths; synthetic spawner caught `:105`. |
| Merge-review audit table | implemented | `relationship.merge_reviews` migration `021_entity_v3_lifecycle.py:179` (no cascade); `_write_merge_review` `router.py:6770`. |
| Single-pair review UX (server half) | implemented | Merge endpoint computes snapshot pre-mutation (`router.py:6355`) and always writes audit row `outcome='merged'` (`:6504`); dismiss endpoint `:6854` writes `outcome='dismissed'`. (FE entry-point wiring is the frontend epic's scope.) |
| (Audit rows survive tombstoning) | implemented | FK without cascade in migration `:183-184`; merge tombstones via `metadata->>'merged_into'` (`router.py:6313`), review row retained. |

## relationship-facts (delta)

| Requirement | Status | Evidence |
|---|---|---|
| `observed_at` and `metadata` columns | implemented | `021_entity_v3_lifecycle.py:120-124` (additive `ADD COLUMN IF NOT EXISTS`, no rewrite); batched idempotent backfill script referenced in migration docstring (`:18`). |
| Central writer stamps `observed_at` | implemented | `relationship_assert_fact` accepts optional `observed_at` default now(); supersession preserves per-row `observed_at` (test `test_relationship_assert_fact.py:640`). |
| `conf` is immutable after write | implemented | Guardrail `test_conf_immutability_guardrail.py` (migrations excluded); supersession-not-mutate DB test `test_relationship_assert_fact.py:691`. |
| Predicate cardinality in the registry | implemented | `021:129-159` adds `cardinality` col + CHECK, seeds `single` for has-birthday + dunbar_tier_override, `multi` otherwise; consumed by `_fetch_single_cardinality_predicates` `router.py:6624`. |
| Supporting tables — entity_view_marks + merge_reviews | implemented | `021:164-193`; UNIQUE entity_id on view_marks, no-cascade FKs on merge_reviews. |
| Read-time staleness available on fact reads | implemented | `staleness.py` SQL builders spliced into reads (`relationship_lookup.py:330,381`; facts drill, compare). |

## switchboard-identity (delta)

| Requirement | Status | Evidence |
|---|---|---|
| Switchboard never asserts entity facts | implemented | `roster/switchboard/tests/test_switchboard_no_fact_writes.py:147` (no fact writes), `:166` (mandated read kept), `:210` (synthetic catches), `:218` (temp-contact flow allowed). |

## module-memory (delta)

| Requirement | Status | Evidence |
|---|---|---|
| Identity-contact data out of scope for memory facts store | implemented | `writing.py:_IDENTITY_REGISTRY_PREDICATES` (`:69`) + `is_identity_registry_predicate` (`:81`); rejection at `:238-248`; test `tests/modules/memory/test_tools_writing.py:214` (incl. hyphen→snake normalization `:206`). |

---

## Confirmed Known/Tracked Items (NOT re-filed)

- **Lookup ranking spec-prose nit (bu-u2le9 / bu-z5uat):** the lookup and search specs'
  prose says "prefix 100 > substring 80 > alias/contact 70 > predicate 30", but both
  the live `GET /entities/search` (`router.py:2467-2470`) and `relationship_lookup`
  (`relationship_lookup.py:50-53`) use **prefix 100 > contact-fact 70 > substring 50 >
  predicate 30**. The implementation is correct against the binding requirement ("same
  ranking as `GET /entities/search`"); only the spec prose numbers are stale. Confirmed,
  not re-filed.
- The frontend-facing items (bu-d9im5 MemoryPage, bu-by2n0-FE / bu-gtnel inspect FE
  adapters, bu-ixtt2 inspect entity_name test, bu-awo8k.8 superseded_by reverse lookup,
  bu-hvrt1 create_temp_contact channel-triple migration) are out of this backend audit's
  scope and remain tracked under their own beads.

## Notes

- The numbered memory-module PRs (#2170 stats, #2172 episodes status, #2175 confirm,
  #2178 retract, #2181 source_episode_id, #2185 importance_min, #2199 inspect register
  rows) extend the memory dashboard/tooling surface; the `module-memory` spec delta in
  this change carries only the single store-boundary requirement, which is fully
  implemented and tested. No additional `module-memory` requirement is in this change.

## Conclusion

The entity-v3 backend implementation **fully matches** all 26 audited spec requirements
modulo the known/tracked items above. No new (untracked) gap beads are warranted from
this audit.
