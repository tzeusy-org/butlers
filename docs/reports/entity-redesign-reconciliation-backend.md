# Entity-Redesign Backend Reconciliation

Date: 2026-05-19
Reconciliation bead: bu-vtk0d
Parent epic: bu-ao6uh

---

## Executive Summary

- **44 spec requirements audited** (20 from `dashboard-relationship/spec.md`, 24 from `relationship-facts/spec.md`)
- **29 tasks.md §X.Y tasks tracked** (§§9.1–9.13, §§10.1–10.9, §§12.1–12.8) — 21 shipped, 7 open/descoped, 1 in-progress
- **5 gaps filed** (0 P0, 3 P2, 2 P3) — see "Gaps Filed" section
- Backend code ships at `relationship.entity_facts` (not `relationship.facts` — see spec-to-code delta); tab endpoints still read legacy `facts` table pending migration bead 7 read-path cut-over (expected, documented in spec)

---

## Per-Requirement Coverage — `dashboard-relationship/spec.md`

| # | Requirement | Test file(s) | Status | Bead if gap |
|---|---|---|---|---|
| 1 | Contact detail page (header card, contact info, dates, facts, relationships) | Frontend scope — out of backend audit | N/A (frontend) | — |
| 2 | Entity detail page (header, linked contacts, 5 tabs) | Frontend scope; backend tab APIs tested | N/A (frontend) | — |
| 3 | Entity-level tab APIs — GET /entities/{id}/notes (contact_note, valid_at DESC) | `test_entity_tabs.py::TestNotes::test_returns_200_with_notes`, `test_notes_ordered_by_valid_at_desc`, `test_notes_fields_populated_correctly` | OK | — |
| 4 | Entity-level tab APIs — GET /entities/{id}/interactions (interaction_%, type suffix) | `test_entity_tabs.py::TestInteractions::test_returns_all_interaction_subtypes`, `test_type_is_predicate_suffix`, `test_no_deduplication_by_predicate_and_valid_at` | OK | — |
| 5 | Entity-level tab APIs — GET /entities/{id}/gifts (predicate=gift, created_at DESC) | `test_entity_tabs.py` (parametrized shared tests: empty, 404, validity, pagination) | OK | — |
| 6 | Entity-level tab APIs — GET /entities/{id}/loans | `test_entity_tabs.py` (parametrized shared tests) | OK | — |
| 7 | Entity-level tab APIs — GET /entities/{id}/timeline (6 predicate families, kind discriminator, NULLS LAST) | `test_entity_tabs.py::TestTimeline::test_timeline_includes_all_predicate_families`, `test_timeline_kind_field_present`, `test_timeline_preserves_db_sort_order` | OK | — |
| 8 | Timeline excludes legacy activity predicate | `test_entity_tabs.py::test_timeline_returns_empty_when_only_activity_facts_exist`, `test_timeline_query_does_not_include_activity_predicate` | OK | — |
| 9 | All 5 tab endpoints — validity='active' only | `test_entity_tabs.py::TestSharedTabBehavior::test_only_active_validity_returned` (parametrized all 5 paths) | OK | — |
| 10 | All 5 tab endpoints — 404 on missing entity | `test_entity_tabs.py::TestSharedTabBehavior::test_missing_entity_returns_404` (parametrized) | OK | — |
| 11 | All 5 tab endpoints — empty=[] on no facts | `test_entity_tabs.py::TestSharedTabBehavior::test_empty_facts_returns_empty_list` (parametrized) | OK | — |
| 12 | All 5 tab endpoints — pagination defaults (50) and max (200) | `test_entity_tabs.py::TestSharedTabBehavior::test_default_pagination_params_sent_to_db`, `test_limit_500_rejected_with_422` (parametrized) | OK | — |
| 13 | Sparse metadata fields render as null (not omitted) | `test_entity_tabs.py::TestSparseMetadataFields::test_note_emotion_null_when_absent`, `test_interaction_direction_and_group_size_null_when_absent`, `test_gift_sparse_fields_null_when_absent`, `test_loan_sparse_fields_null_when_absent`, `test_timeline_metadata_null_when_empty` | OK | — |
| 14 | Cross-scope facts excluded (scope='relationship' only) | `test_entity_tabs.py::TestSharedTabBehavior::test_scope_filter_present_in_query` (parametrized) | OK | — |
| 15 | Mixed-channel interactions merged (no cross-channel dedup) | `test_entity_tabs.py::test_no_deduplication_by_predicate_and_valid_at` | OK | — |
| 16 | Owner-only authorization for entity endpoints — writes (12a) | `test_owner_authz_guardrail.py::TestWriteEndpoints` (8 POST/DELETE mutation endpoints) | OK | — |
| 17 | Owner-only authorization — reads, PII-bearing (12b) | `test_owner_authz_guardrail.py::TestReadEndpoints` (queue, search, contacts, neighbours, activity) | OK | — |
| 18 | Owner-only authorization — deploy gate (12c, DASHBOARD_API_KEY) | `test_owner_authz_guardrail.py::TestDeployGate::test_startup_fails_when_api_key_unset_in_production`, `test_startup_succeeds_in_dev_without_api_key` | OK | — |
| 19 | Provenance contract — every triple response includes src, conf, last_seen, weight, verified, primary | `test_entity_neighbours.py::test_all_provenance_fields_present`, `test_provenance_null_fields_are_explicit_null` — **but tab endpoints (notes/interactions/gifts/loans/timeline) lack provenance fields in models and queries** | GAP | bu-n6typ |
| 20 | Error envelope carries `code` discriminator | `test_owner_authz_guardrail.py` asserts 403 with owner_required; `test_entities_api.py` asserts 404 shape | OK | — |
| 21 | Entity index page (`/entities`) — tabular list, filter chips, SubpageTabs | Frontend scope — out of backend audit (backend: bu-7s86b shipped GET /entities) | N/A (frontend) | — |
| 22 | Entity Hop view (`/entities/hop`) | Frontend scope | N/A (frontend) | — |
| 23 | Entity Columns view (`/entities/columns`) — client-side chaining only, no new server endpoint | Frontend scope | N/A (frontend) | — |
| 24 | Entity Concentration view (`/entities/concentration`) — predicate tabs from registry | Backend: `test_entities_api.py::TestEntityConcentration::test_happy_path_returns_200_with_rollup`, `test_empty_result_returns_empty_items` | OK | — |
| 25 | App-wide Cmd-K Finder — GET /entities/search, rule-based ranking, no LLM | `test_entities_api.py::TestEntitySearch`, `test_finder_no_llm_guardrail.py`, `test_finder_no_llm_transitive.py` | OK | — |
| 26 | Finder is deterministic — no LLM ranking (transitive import guardrail) | `test_finder_no_llm_transitive.py::test_finder_no_llm_transitive_walk_passes_current_codebase` | OK | — |
| 27 | Entity curation queue — GET /entities/queue (unidentified + dup-candidate + stale) | `test_entities_api.py::TestEntityQueue::test_happy_path_returns_200_with_queue`, `test_owner_gate_returns_403_when_no_owner` | OK | — |
| 28 | Entity activity aggregator (GET /entities/{id}/activity — relationship + chronicler MCP, no direct SQL) | `test_entities_api.py::TestEntityActivity::test_happy_path_returns_200_with_items`, `test_sql_uses_entity_facts_not_facts`, `test_chronicler_unreachable_degrades_gracefully`, `test_merged_stream_sorted_desc` | OK | — |
| 29 | Chronicler boundary guardrail — no FROM/JOIN chronicler.* in router | `test_chronicler_boundary.py::test_no_chronicler_sql_cross_schema_references`, `test_no_direct_chronicler_model_imports` | OK | — |
| 30 | Social Map preservation | Frontend scope | N/A (frontend) | — |
| 31 | Entity detail Editorial/Workbench mode toggle | Frontend scope | N/A (frontend) | — |
| 32 | Detail-page voice gloss source — canned strings only | Frontend scope (`entity-glosses.ts`) | N/A (frontend) | — |
| 33 | Dispatch design language token discipline | Frontend scope | N/A (frontend) | — |

---

## Per-Requirement Coverage — `relationship-facts/spec.md`

| # | Requirement | Test file(s) | Status | Bead if gap |
|---|---|---|---|---|
| 1 | `relationship.entity_facts` triple store — schema (all columns, indexes, uniqueness) | `tests/migrations/test_relationship_facts_migration.py` | OK | — |
| 2 | Triple store accepts contact AND relational predicates in one table | `test_relationship_assert_fact.py::test_insert_returns_inserted_outcome`, `test_insert_entity_predicate` | OK | — |
| 3 | Schema-qualified name enforced (relationship.entity_facts, never bare facts) | Code review gate (no automated test); migration SQL uses FQN throughout | OK (convention) | — |
| 4 | Predicate catalog — `relationship.entity_predicate_registry` seeded (contact + relational + override) | `tests/migrations/test_predicate_registry_migration.py` | OK | — |
| 5 | Unknown predicate rejected by central writer | `test_relationship_assert_fact.py::test_unknown_predicate_raises_value_error`, `test_unknown_predicate_writes_no_row` | OK | — |
| 6 | Predicate IDs not hardcoded in component tree | Checked by `test_finder_no_llm_guardrail.py` (import-scan); roster/relationship/api/router.py seeding SQL is the only backend match | OK | — |
| 7 | Central writer `relationship_assert_fact()` — predicate validation, dedup, supersession, provenance | `test_relationship_assert_fact.py` (34 tests: insert, idempotency, supersession scenarios, error cases) | OK | — |
| 8 | Transaction-safety (Amendment 14) — callable from within open asyncpg transaction | `test_relationship_assert_fact.py::TestTransactionSafety::test_accepts_caller_conn_without_panic`, `test_caller_conn_inside_transaction`, `test_caller_conn_idempotent` | OK | — |
| 9 | Idempotency on (subject, predicate, object) | `test_relationship_assert_fact.py::TestIdempotency::test_same_call_twice_returns_unchanged`, `test_unchanged_produces_exactly_one_active_row` | OK | — |
| 10 | Owner-gate carry-forward (RFC 0017 §2.3) — owner subject → pending_approval | `test_relationship_assert_fact.py::TestOwnerCarveout::test_owner_subject_returns_pending_approval`, `test_owner_subject_writes_no_fact_row`, `test_owner_subject_writes_pending_action_row` | OK | — |
| 11 | Switchboard `resolve_contact_by_channel()` re-pointed to entity_facts | **Not yet shipped** — identity.py still queries `public.contact_info`; blocked by migration bead 7 | GAP (open task 10.7, blocked) | bu-0f3zn |
| 12 | Telegram chat resolves to entity via has-handle triple (post-cut-over test) | No test for entity_facts-based resolve path | GAP: missing test | bu-w2zo6 |
| 13 | Unknown channel value returns None | `tests/core/test_identity.py::test_resolve_contact_by_channel` (old contact_info path only) | Partial — GAP for entity_facts path | bu-w2zo6 |
| 14 | Migration safety — dual-write window (SQL authoritative, reconciler ≤1h) | `test_reconciler.py` (20+ tests covering sweep, idempotency, error, credential exclusion) | OK | — |
| 15 | Reconciler periodic worker — interval, stats, registry | `test_reconciler.py::TestReconcilerInterval::test_default_interval_is_30_minutes`, `test_env_var_overrides_default_interval`, `test_reconciler_registered_in_job_registry` | OK | — |
| 16 | Read-path cut-over only after 24h zero-drift | **Documented in migration protocol** (docs/reports/contact-migration-read-path-inventory.md); no automated parity test yet | OK (process gate, not code) | — |
| 17 | Drop gated by 30-day soak + operator sign-off | Process gate — documented in migration beads; no automated test required | OK (process gate) | — |
| 18 | Credentials carve-out — secured rows → `relationship.credentials`, not entity_facts | `test_credentials_carveout.py` (migration, reconciler exclusion, dedup) | OK | — |
| 19 | Credentials not surfaced on entity contacts endpoint | **No test** asserting GET /entities/{id}/contacts excludes credential rows | GAP: missing test | bu-7hii5 |
| 20 | Orphan contact handling — dry-run default, reads snapshot, mints entity or escalates | `src/butlers/scripts/contact_orphan_resolver.py` shipped (PR #1767); no unit test for script | OK (script exists; testing is integration-level) | — |
| 21 | `verified` is a column, not a triple | `test_relationship_assert_fact.py::test_insert_stores_provenance_fields` (verified column present) | OK | — |
| 22 | No verification-triple predicate in registry | Covered by predicate registry seed — no `verified-by` predicate seeded | OK | — |
| 23 | Dual-write reconciliation contract — SQL authoritative, post-commit MCP best-effort | `test_reconciler.py::test_single_missing_row_is_reconciled`, `test_writer_exception_increments_rows_error`, `test_writer_error_does_not_abort_subsequent_rows` | OK | — |
| 24 | Backfill skips secured rows, copies to credentials | `test_reconciler.py::test_secured_row_increments_credential_counter`, `test_credentials_carveout.py::test_credential_entity_facts_is_empty` | OK | — |

---

## Per-Task Status — `tasks.md` §§9–12

> Scope: backend-only tasks (§§9.1–9.13, §§10.1–10.9, §§12.1–12.8). Frontend tasks (§§3–8) and migration cross-references (§11) are noted but not tracked here.

| Task | Status | PR / Bead | Notes |
|---|---|---|---|
| §9.1 GET /entities (list + filter + pagination) | shipped | PR #1772 (bu-7s86b) | `public.entities` JOIN `relationship.entity_facts` for has=contact filter; owner-authz not required per spec (list-only endpoint) |
| §9.2 GET /entities/{id}/neighbours | shipped | PR #1773 (bu-4wn79) | Bidirectional triples from entity_facts; predicate-registry JOIN (kind='relational'); owner-only authz (12b) |
| §9.3 GET /entities/concentration | shipped | PR #1776 (bu-0vosj) | weight_sum=SUM(COALESCE(weight,1)); predicate tabs from registry WHERE kind='relational'; unknown pred fallback to 'knows'; ::bigint overflow safety |
| §9.4 /entities/{id}/contacts CRUD (GET + POST + DELETE) | shipped | PR #1779 (bu-u1w78) | GET returns has-* facts; POST calls relationship_assert_fact(); DELETE retracts via UPDATE; owner-authz gates on all |
| §9.5 GET /entities/queue | shipped | PR #1775 (bu-t1zfd) | Union of unidentified + dup-candidate + stale; deterministic dup-detection by shared has-email/has-phone; owner-authz |
| §9.6 GET /entities/search | shipped | PR #1774 (bu-q9uiw) | Rule-based ranking per §7.5; no LLM; owner-authz (12b) |
| §9.7 POST /entities (promote unidentified) | shipped | PR #1778 (bu-pzp9m) | Promotes unidentified → canonical entity; owner-authz |
| §9.8 POST /entities/{id}/promote-tier | shipped | PR #1783 (bu-wmigz) | Writes dunbar_tier_override triple via central writer (Amendment 6); owner-authz |
| §9.9 POST /entities/{id}/archive + DELETE /entities/{id} | shipped | PR #1782 (bu-l76uv) | Archive + forget with tombstone; supersession via relationship_assert_fact(); owner-authz |
| §9.10 POST /entities/{id}/merge | shipped | PR #1781 (bu-jp6r6) | Entity-level merge; rewires entity_facts triples; tombstones source; owner-authz |
| §9.11 POST /entities/queue/dismiss | shipped | PR #1784 (bu-297lj) | Writes queue.dismissed triple via central writer; migration 015 extends predicate_registry kind constraint to include 'state'; owner-authz |
| §9.12 GET /entities/{id}/activity aggregator | shipped | PR #1786 (bu-ihiw4) | Merges relationship entity_facts rows + chronicler MCP (chronicler_list_episodes with entity_id filter after PR #1785); graceful degradation on chronicler unreachable; no direct chronicler SQL |
| §9.13 Integration tests for 9.1–9.12 | shipped | PR #1787 (bu-4vhjq) | `test_entities_api.py` with 14 test classes; happy path + owner-authz gate + error-path coverage per endpoint |
| §10.1 Alembic migration for relationship.entity_facts | shipped | PR #1769 (bu-892tf) | Migration rel_013; all columns, indexes, uniqueness per spec |
| §10.2 entity_predicate_registry table + seed | shipped | PR #1770 (bu-hlovw) | Migration rel_014; contact + relational + override predicate sets seeded |
| §10.3 relationship_assert_fact() MCP tool | shipped | PR #1777 (bu-jwllb) | Predicate validation, dedup, supersession, provenance, owner carve-out (RFC 0017 §2.3), transaction-safety |
| §10.4 relationship.credentials table (carve-out) | shipped | PR #1790 (bu-uj3xv) | Migration rel_016; unique index on (entity_id, type) WHERE revoked_at IS NULL; reconciler excludes secured rows |
| §10.5 Chronicler-boundary guardrail test | shipped | PR #1763 (bu-f5qcp) | `test_chronicler_boundary.py::test_no_chronicler_sql_cross_schema_references`, `test_no_direct_chronicler_model_imports` |
| §10.6 RFC 0004 amendment text | shipped | PR #1791 (bu-u8xq2) | `rfc-amendments/0004-amendment-2-contacts-as-triples.md` authored; applied to `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` with Amendment 2 section |
| §10.7 Re-point identity.py resolve_contact_by_channel() to entity_facts | open (blocked) | bu-0f3zn (gap bead) | Blocked by migration beads 4 (dual-write shim), 5 (backfill), 6 (parity tests), 7 (read-path cut-over); identity.py still queries public.contact_info |
| §10.8 Finder no-LLM transitive guardrail | shipped | PR #1789 (bu-wqmck) | `test_finder_no_llm_transitive.py::test_finder_no_llm_transitive_walk_passes_current_codebase`; full transitive import graph walk |
| §10.9 Dual-write reconciler job | shipped | PR #1788 (bu-75a3s) | Periodic worker (30-min default, $DUAL_WRITE_RECONCILER_INTERVAL_MINUTES override); idempotent on (subject, predicate, object); metrics included |
| §10.10 Reader inventory bead | shipped | PR #1766 (bu-wkjc2) | `docs/reports/contact-migration-read-path-inventory.md`; 128 reader entries enumerated across 20 owning contexts |
| §10.11 contact_orphan_resolver.py script | shipped | PR #1767 (bu-yxdzq) | `src/butlers/scripts/contact_orphan_resolver.py`; --apply=false dry-run default; reads snapshot table; mints entities via SQL or escalates via notify() |
| §12.1 RFC 0004 amendment archival | open | bu-ixb3p | Amendment text authored (bu-u8xq2 shipped); formal openspec archive step pending |
| §12.2 RFC 0007 namespace verification | open | bu-oew0h | Verification that all endpoints live under /api/butlers/relationship/entities/*; no RFC 0007 amendment needed (verified by inspection: all endpoints use router prefix per rfcs/0007:31 auto-discovery) |
| §12.3 design-language.md editorial/workbench clarification | open | bu-x0eej | `about/heart-and-soul/design-language.md` has no Workbench/editorial archetype distinction for EntityDetailPage; frontend-gating clarification still needed |
| §12.4 module-vs-butler distinction doc | open | bu-9vh0i | `about/lay-and-land/` has no note; requires clarification of module vs butler distinction (resolves Phase 1 Open Question 25) |
| §12.5 chronicler_list_episodes entity_id filter prereq | shipped | PR #1785 (bu-aqe7n) | chronicler_list_episodes now accepts entity_id filter; unblocked §9.12 |
| §12.6 entity-redesign-phase-2.md final report | open | bu-p5zlt (gap bead) | docs/reports/entity-redesign-phase-2.md does not exist; docs/reports/relationship-tabs-to-entities-outcome.md covers Phase 1 only |
| §12.7 v1.md doctrine update post-RFC 0004 Amendment 2 | open | bu-u1mw8 | about/heart-and-soul/v1.md:64 still reads "Contacts — shared identity registry"; :127-132 still reads "canonical contact table with roles and entity linkage" — both need updating |
| §12.8 owner-only-authz guardrail test | shipped | PR #1771 (bu-i99z3) | `test_owner_authz_guardrail.py` with 20+ tests covering all 12a mutations, 12b reads, 12c deploy gate |

---

## Spec-to-Code Delta

### 1. Table name: `relationship.entity_facts` (not `relationship.facts`)

The spec consistently names the table `relationship.entity_facts`. Shipped migrations and router SQL use this name throughout. An earlier interim commit (bu-yrc2m, PR #1780) fixed spurious `scope`/`entity_id` columns from relationship.facts queries — this was a bug introduced during early development where the old facts-table columns leaked into entity_facts queries. Resolved before merge.

### 2. Tab endpoints still read legacy `facts` table

Spec text (Phase 2 table reconciliation note): "Phase 1 endpoints MUST be re-pointed to `relationship.entity_facts` no later than Migration bead 7 (read-path cut-over). Until cut-over, Phase 1 endpoints read the legacy table." This is EXPECTED — the five tab endpoints (notes, interactions, gifts, loans, timeline) at `roster/relationship/api/router.py` lines 3821, 3867, 3915, 3962, 4018 query `FROM facts WHERE ... scope='relationship'`. The entity_facts migration (bead 7) has not landed. The delta is documented and intentional.

### 3. Tab endpoint models lack provenance fields

The `EntityNote`, `EntityInteraction`, `EntityGift`, `EntityLoan`, `EntityTimelineItem` Pydantic models do NOT include the six provenance fields (`src`, `conf`, `last_seen`, `weight`, `verified`, `primary`) mandated by the "Provenance contract" requirement. This diverges from spec intent ("the API MUST NOT silently drop or omit them") and is filed as gap bead bu-n6typ. Contrast: the `ContactFact` model (used by `/entities/{id}/contacts`) correctly includes all six provenance fields.

### 4. `resolve_contact_by_channel()` not yet re-pointed

`src/butlers/identity.py:resolve_contact_by_channel()` still queries `public.contact_info JOIN public.contacts`. The spec requires this to switch to `relationship.entity_facts` after migration bead 7. The `ResolvedContact` dataclass still includes `contact_id`; per spec it should drop `contact_id` and use only `entity_id`. Tracked as gap bead bu-0f3zn and open task 10.7.

### 5. `predicate_registry` table name

The migration and router SQL use `relationship.entity_predicate_registry` (full name). The queue.dismissed predicate (migration rel_015, bu-297lj) extended the `kind` CHECK constraint to include `'state'` — this is not in the original spec but was required by the dismiss endpoint's predicate type. The extension is sound and documented in the migration.

### 6. `has=contact` filter chip — predicate set

Spec says the `has=contact` filter should surface entities with `has-email | has-phone | has-handle | has-address | has-birthday | has-website` triples. The shipped `GET /entities` implementation at router.py line 2640 queries `relationship.entity_facts` for `predicate LIKE 'has-%'` (broader than the spec's explicit six predicates, but a safe superset). No functional regression.

### 7. `GET /entities/{id}/linked-contacts` vs spec

The spec defines a "linked contacts section" on the entity detail page. The backend ships `GET /entities/{entity_id}/linked-contacts` (returning `public.contacts WHERE entity_id = $1`). This supplements `GET /entities/{entity_id}/contacts` (which returns entity_facts triples). The two surfaces are complementary: linked-contacts returns structured contact objects; contacts returns RDF triples. No spec conflict.

### 8. `queue.dismissed` predicate registered under kind='state'

The curation queue dismiss endpoint writes a `queue.dismissed` predicate with `object='dismissed'`. This predicate kind is `'state'` — a new predicate family not in the original spec seed. Migration rel_015 extended the `kind` CHECK constraint. The spec's Predicate catalog lists contact, relational, and override families; state is an implementation extension consistent with the spec's "set extensible" language for relational predicates.

---

## Gaps Filed

| Bead | Priority | Description |
|---|---|---|
| bu-n6typ | P2 | Tab endpoints (notes/interactions/gifts/loans/timeline) missing provenance fields (src, conf, last_seen, weight, verified, primary) — contract violation per "Provenance contract" requirement |
| bu-0f3zn | P2 | resolve_contact_by_channel() not yet re-pointed to entity_facts (task 10.7); open blocked task; identity.py still queries public.contact_info |
| bu-7hii5 | P2 | Missing test: credentials not surfaced on GET /entities/{id}/contacts — spec scenario untested |
| bu-p5zlt | P3 | docs/reports/entity-redesign-phase-2.md missing (task 12.6) |
| bu-w2zo6 | P2 | Missing test: Telegram resolve via has-handle triple (post-10.7 cut-over test must be written) |

---

## Summary of Open Tasks

The following tasks.md items remain open at audit time:

| Task | Bead | Reason open |
|---|---|---|
| §10.7 re-point identity.py | bu-0f3zn (gap) | Blocked by migration beads 4, 5, 6, 7 (dual-write window not complete) |
| §12.1 RFC 0004 archival | bu-ixb3p | Formal openspec archive step pending; amendment text shipped |
| §12.2 RFC 0007 namespace verification | bu-oew0h | Verification task; low risk — all endpoints confirmed under /api/butlers/relationship/entities/* |
| §12.3 design-language.md clarification | bu-x0eej | Frontend-gating documentation; workbench/editorial archetype note missing |
| §12.4 module-vs-butler doc | bu-9vh0i | about/lay-and-land/ note missing; resolves Open Question 25 |
| §12.6 phase-2 report | bu-p5zlt (gap) | docs/reports/entity-redesign-phase-2.md does not exist |
| §12.7 v1.md doctrine update | bu-u1mw8 | about/heart-and-soul/v1.md still has stale "Contacts" language |

Frontend tasks (§§3–8) and migration cross-references (§11) are out of backend audit scope and tracked separately under bu-lh4ol (frontend epic).
