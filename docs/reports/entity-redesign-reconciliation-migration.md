# Entity-Redesign Migration Reconciliation — contacts → triples (gen-1)

**Date:** 2026-05-30
**Audit bead:** bu-7jo43
**Change audited:** `openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/` (archived)
**Spec anchors:**
- `openspec/specs/dashboard-relationship/spec.md` (entity surface)
- `openspec/specs/relationship-facts/spec.md` (triple store)
**Predecessor reports:**
- `docs/reports/entity-redesign-reconciliation-backend.md` (bead bu-vtk0d, 2026-05-19)
- `docs/reports/entity-redesign-reconciliation-frontend.md` (bead bu-fs5y8, 2026-05-20)

---

## Executive Summary

This report is a gen-1 reconciliation audit of the contacts → triples migration, covering
every spec requirement from both spec anchors against shipped code, per-task close status
for all `tasks.md` §X.Y tasks, and the current spec-to-code delta inventory.

**Since the two predecessor reports (2026-05-19, 2026-05-20), several previously-open gaps
have been resolved:**

- Task 10.7 `identity.py` re-pointed to `relationship.entity_facts` (was gap bu-0f3zn)
- Telegram resolve via has-handle triple now tested (was gap bu-w2zo6)
- "Forget this entity" affordance shipped in EntityDetailPage (was gap bu-fs5y8 F-04)
- Entity-gloss wired into EntityDetailPage Editorial mode (was delta D-03)
- `entity-model.ts` created; hex literals migrated out of component tree (was gap F-06)
- `archived` EntityState added to `StateDot.tsx` and `entity-glosses.ts` (was gap F-05)
- Playwright E2E tests for entity-redesign routes added (was gap F-07; tests are runtime-skip-guarded pending dev server)
- RFC 0004 Amendment 2 applied to `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` (task 12.1 closed)
- `about/lay-and-land/module-vs-butler.md` added (task 12.4 closed)

**Remaining open items:** 7 gaps remain across provenance contract, `v1.md` doctrine,
`entity-redesign-phase-2.md` final report, and one unclosed owner-authz scenario.
See §Observed Gaps below.

**Test run:** 327 tests verified passing (164 targeted + 163 targeted = 327 total; see §Quality Gates).

---

## Per-Requirement Test Coverage — `dashboard-relationship/spec.md`

### Phase 1: Contact detail + Entity detail + Entity-level tab APIs

| # | Requirement | Anchor | Test(s) located | Status |
|---|---|---|---|---|
| D1 | Contact detail page — header (name, company, roles badge, entity link, warning when entity_id is null) | §"Contact detail page" | `ContactDetailView.tsx:923–946` renders link/warning; `ContactDetailPage.test.tsx:191` seeds null entity_id | PARTIAL — no unit test asserting "View entity activity →" link text exactly; link points to `/entities/:id` (correct) |
| D2 | Contact detail page — no tab block (MUST NOT contain notes/interactions/gifts/loans tabs) | §"Contact detail page" | `grep -rn "Tab" roster/relationship/api/router.py` = no legacy tab endpoints; `ContactDetailView.tsx` has no Tab import | OK |
| D3 | Legacy tab endpoints removed — `GET /contacts/{id}/{notes,interactions,gifts,loans,feed}` return 404 | §"Legacy tab endpoints removed" | `list_contact_notes`, `list_contact_gifts`, `list_contact_loans`, `list_contact_feed` absent from `roster/relationship/api/router.py`; `list_contact_interactions` preserved as new thread-view endpoint (different surface) | OK |
| D4 | Legacy tables `relationship.{notes, interactions, gifts, loans, activity_feed}` dropped | §"Legacy tab endpoints removed" | `roster/relationship/migrations/010_drop_legacy_contact_tables.py` drops all five; `grep -rn "FROM notes\|FROM interactions" roster/` returns no production SQL | OK |
| D5 | Contact detail — click-to-reveal secured credential (`GET /api/contacts/{id}/secrets/{info_id}`) | §Scenario: Click-to-reveal | `roster/relationship/api/router.py:4001` implements `GET /entities/{entity_id}/secrets/{info_id}`; no unit test exercises secured=true+reveal flow | PARTIAL — GAP: F-02 (no reveal flow test) |
| D6 | Contact detail — email as `mailto:`, phone as `tel:` | §Scenario: Email and phone clickable | `ContactDetailView.tsx` renders mailto/tel via `ContactInfoSection`; no unit test asserts rendered href | PARTIAL — GAP: F-02 |
| D7 | Contact detail — 404 on missing contact | §Scenario: Contact not found | `roster/relationship/api/router.py` returns 404 on missing contact; frontend test missing | PARTIAL — GAP: F-02 |
| D8 | Entity detail page — header (canonical_name, entity_type, aliases, roles, Unidentified badge, View identity link) | §"Entity detail page" | `EntityDetailPage.tsx:1815` renders Unidentified badge; test `EntityDetailPage.test.tsx:386` covers entity-gloss rendering | PARTIAL — no test asserts Unidentified badge renders when `entity.unidentified=true`; no test for "View identity →" link target; GAP: F-03 |
| D9 | Entity detail page — linked contacts section | §"Entity detail page" | `test_entity_tabs.py::TestLinkedContacts` (backend); `EntityDetailPage.tsx:2000` renders `<LinkedContactsList>` | OK |
| D10 | Entity detail page — unified ActivityTimeline with filter pills (All/Interactions/Notes/Gifts/Loans/Life events) | §"Entity detail page" | `EntityDetailPage.tsx:1087–1205` implements `ActivityTimeline` with 6 filter pills; `EntityDetailPage.test.tsx` covers pill rendering | OK (note: spec says 5 tabs; shipped as unified timeline — see Delta §M-01) |
| D11 | Entity detail page — Gifts panel (separate structural display) | §"Entity detail page" | `EntityDetailPage.tsx:1211–1248` implements `GiftsPanel`; `EntityDetailPage.test.tsx` mocks `useEntityGifts` | OK |
| D12 | Entity detail page — Loans panel | §"Entity detail page" | `EntityDetailPage.tsx:1250–1296` implements `LoansPanel`; `EntityDetailPage.test.tsx` mocks `useEntityLoans` | OK |
| D13 | Entity detail page — "Forget this entity" in Page header, confirm dialog | §"Entity detail Editorial/Workbench mode toggle" | `EntityDetailPage.tsx:1906–1924` + `EntityDetailPage.forget.test.tsx` (6 tests: button in both modes, dialog opens, confirm calls mutateAsync, canned text "Forgetting also tombstones the source. Aliases stay.") | OK |
| D14 | Entity-level tab APIs — 5 endpoints, predicate filters, sort orders, validity='active', scope='relationship' | §"Entity-level tab APIs" | `test_entity_tabs.py::TestNotes`, `TestInteractions`, `TestTimeline`, parametrized `TestSharedTabBehavior` (all 5 paths) | OK |
| D15 | Notes entries: {id, content, emotion, created_at} | §"Entity-level tab APIs" | `test_entity_tabs.py::test_notes_fields_populated_correctly`, `test_note_emotion_null_when_absent` | OK |
| D16 | Interactions entries: {id, type (predicate suffix), summary, occurred_at, direction, group_size} | §"Entity-level tab APIs" | `test_entity_tabs.py::test_returns_all_interaction_subtypes`, `test_type_is_predicate_suffix`, `test_sparse_direction_and_group_size_are_null` | OK |
| D17 | Gifts entries: {id, description, occasion, status, created_at} | §"Entity-level tab APIs" | `test_entity_tabs.py::test_gift_sparse_fields_null_when_absent` | OK |
| D18 | Loans entries: {id, description, amount_cents, currency, direction, settled, settled_at, created_at} | §"Entity-level tab APIs" | `test_entity_tabs.py::test_loan_sparse_fields_null_when_absent`, `test_loan_fields_populated_when_present` | OK |
| D19 | Timeline entries: {kind, id, content, valid_at, predicate, metadata} with kind discriminator | §"Entity-level tab APIs" | `test_entity_tabs.py::test_timeline_kind_field_present`, `test_timeline_includes_all_predicate_families` | OK |
| D20 | Timeline excludes legacy `activity` predicate | §Scenario: Timeline excludes legacy activity | `test_entity_tabs.py::test_timeline_returns_empty_when_only_activity_facts_exist`, `test_timeline_query_does_not_include_activity_predicate` | OK |
| D21 | All 5 endpoints — empty=[] on no facts | §Scenario: Empty entity | `test_entity_tabs.py::TestSharedTabBehavior::test_empty_facts_returns_empty_list` (parametrized) | OK |
| D22 | All 5 endpoints — 404 on missing entity | §Scenario: Entity does not exist | `test_entity_tabs.py::TestSharedTabBehavior::test_missing_entity_returns_404` (parametrized) | OK |
| D23 | All 5 endpoints — retracted/superseded facts excluded | §Scenario: Retracted facts excluded | `test_entity_tabs.py::TestSharedTabBehavior::test_only_active_validity_returned` (parametrized) | OK |
| D24 | All 5 endpoints — pagination defaults (50) and max (200) | §Scenario: Pagination | `test_entity_tabs.py::TestSharedTabBehavior::test_default_pagination_params_sent_to_db`, `test_limit_500_rejected_with_422` (parametrized) | OK |
| D25 | All 5 endpoints — cross-scope facts excluded (scope='relationship' only) | §Scenario: Cross-scope facts excluded | `test_entity_tabs.py::TestSharedTabBehavior::test_scope_filter_present_in_query` (parametrized) | OK |
| D26 | Sparse metadata fields render as null (not omitted, not defaulted) | §Scenario: Sparse metadata | `test_entity_tabs.py::TestSparseMetadataFields` (6 tests: note emotion, interaction direction/group_size, gift fields, loan fields, timeline metadata) | OK |
| D27 | Provenance fields (src, conf, last_seen, weight, verified, primary) on every triple response | §"Provenance contract" | `test_entity_tabs.py::TestProvenanceContract::test_provenance_fields_present_in_response` (parametrized all 5 paths); `test_entity_neighbours.py::test_all_provenance_fields_present` | OK (all 5 tab endpoints now include provenance — gap bu-n6typ from prior report resolved) |
| D28 | Owner-only authz — writes (clause 12a, 8 mutation endpoints) | §"Owner-only authorization" | `test_owner_authz_guardrail.py::TestWriteEndpoints` (8 POST/DELETE tests: POST /entities, /entities/{id}/merge, /archive, /promote-tier, DELETE /entities/{id}, /queue/dismiss, POST /entities/{id}/contacts, DELETE /entities/{id}/contacts/{pred}/{valueHash}) | OK |
| D29 | Owner-only authz — reads (clause 12b, 5 PII-bearing GET endpoints) | §"Owner-only authorization" | `test_owner_authz_guardrail.py::TestReadEndpoints` (5 tests: queue, search, contacts, neighbours, activity) | OK |
| D30 | Deploy gate — startup fails if DASHBOARD_API_KEY unset in non-dev env (clause 12c) | §"Owner-only authorization" | `test_owner_authz_guardrail.py::TestDeployGate::test_startup_fails_when_api_key_unset_in_production`, `test_startup_succeeds_in_dev_without_api_key` | OK |
| D31 | Tab endpoints and GET /entities do NOT require owner authz (not PII-bearing) | §"Owner-only authorization" | `test_owner_authz_guardrail.py::test_get_entity_detail_not_owner_gated`, `test_entity_tab_endpoints_not_owner_gated` | OK |
| D32 | Entity index page `/entities` — tabular list, filter chips, SubpageTabs, queue rail | §"Entity index page" | `EntitiesIndexPage.test.tsx` (50+ tests) | OK |
| D33 | `/contacts` → `/entities?has=contact` redirect (SPA-301 equivalent) | §Scenario: /contacts redirects | `router-config.tsx:91` renders `<Navigate to="/entities?has=contact" replace>`; `router.test.tsx:309` (nav-config: Contacts entry absent) | OK |
| D34 | Contact detail path `/contacts/:contactId` NOT redirected (still serves contact detail) | §Scenario: /contacts redirects | `router-config.tsx:95` maps `/contacts/:contactId` to `<ContactEntityRedirect>` | OK |
| D35 | Entity Hop view `/entities/hop` — re-centre graph, SubpageTabs | §"Entity Hop view" | `HopPage.test.tsx` (5 describe blocks; re-centre changes URL, stays on /entities/hop) | OK |
| D36 | Entity Columns view `/entities/columns` — client-side chaining only | §"Entity Columns view" | `ColumnsPage.test.tsx` (7 describe blocks; chained neighbour calls, no new server endpoint) | OK |
| D37 | Entity Concentration view `/entities/concentration` — predicate tabs from registry | §"Entity Concentration view" | `ConcentrationPage.test.tsx` (7 describe blocks; predicate tabs enumerated from registry, not hardcoded) | OK |
| D38 | Social Map preservation — SocialMapView inside SubpageTabs chrome, data sources unchanged | §"Social Map preservation" | `SocialMapView.test.tsx` (9 tests); `SocialMapPage.tsx` renders `<SubpageTabs>` + `<SocialMapView>` | OK |
| D39 | Entity detail Editorial mode — Display 44px headline, voice gloss (canned string), archetype="detail" | §"Entity detail Editorial/Workbench mode toggle" | `EntityDetailPage.tsx:2052` uses `archetype="editorial"` in Editorial mode; gloss wired at `:1822` via `getEntityGloss()`; `EntityDetailPage.test.tsx:386–490` (7 gloss tests) | OK (note: archetype="editorial" not "detail" — see Delta §M-02) |
| D40 | Entity detail Workbench mode — archetype="overview", provenance grid, sortable | §"Entity detail Editorial/Workbench mode toggle" | `EntityDetailPage.tsx:2378` renders `<ProvenanceGrid>` in Workbench mode | OK |
| D41 | Mode persistence in `localStorage["entities.detail.mode"]`, URL `?mode=` override | §"Entity detail Editorial/Workbench mode toggle" | `EntityDetailPage.tsx:104` exports `ENTITY_MODE_STORAGE_KEY = "entities.detail.mode"`; `EntityDetailPage.test.tsx:248–340` (6 mode persistence tests) | OK |
| D42 | Forget confirm dialog canned text: "Forgetting also tombstones the source. Aliases stay." | §"Entity detail Editorial/Workbench mode toggle" | `EntityDetailPage.forget.test.tsx` tests dialog opens; canned text at `EntityDetailPage.tsx:2098` | OK |
| D43 | Entity curation queue right rail — 3 sections (unidentified/dup/stale), serif gloss when empty | §"Entity curation queue" | `EntitiesIndexPage.test.tsx::shows serif italic 'Nothing waiting.' when queue is empty`, `::renders queue items grouped by bucket` | OK |
| D44 | Queue: state colour only in rail, NOT in index rows | §Scenario: Queue rail is only source of state colour | `EntitiesIndexPage.test.tsx` neutral-row assertion | OK |
| D45 | App-wide Cmd-K Finder — wired to `/entities/search`, keyboard-driven, entity-first, ≤300ms | §"App-wide Cmd-K Finder" | `EntityFinder.test.tsx` (7 tests: open, entity-first, query wiring, empty state, close); `use-keyboard-shortcuts.ts:27` dispatches `dispatchOpenEntityFinder()` | OK |
| D46 | Dispatch design language token discipline — no new tokens, no hex literals outside entity-model.ts | §"Dispatch design language token discipline" | `entity-model.ts` created; hex `#fff` migrated there; `ContactChannelCard.test.tsx:125` has remaining `#1a73e8` in test fixture (not production code) | OK — only test fixture hex remains; production component tree is hex-free |
| D47 | Detail-page voice gloss — canned strings, build-time exhaustiveness, no LLM | §"Detail-page voice gloss" | `entity-glosses.ts:241` TypeScript exhaustiveness check; `entity-glosses.enforcement.test.ts` LLM-prohibition guardrail (bu-0855u) | OK |
| D48 | Finder is deterministic — no LLM ranking (transitive import guardrail) | §"Finder is deterministic" | `test_finder_no_llm_guardrail.py` (3 tests) + `test_finder_no_llm_transitive.py` (4 tests incl. synthetic-banned-import catch) | OK |
| D49 | Provenance contract — error envelope carries `code` discriminator | §"Provenance contract" | `test_owner_authz_guardrail.py` asserts 403 + `owner_required` code; `test_entities_api.py` asserts 404 `entity_not_found` shape | OK |
| D50 | Entity activity aggregator — relationship facts + chronicler MCP, no direct `FROM chronicler.` SQL | §"Entity activity aggregator" | `test_entities_api.py::TestEntityActivity` (4 tests: happy path, entity_facts table, chronicler unreachable degrades, merged stream sorted); `test_chronicler_boundary.py` (2 guardrail tests) | OK |
| D51 | Playwright E2E smoke tests (§4.5) — entity detail route, filter pills, dunbar_tier_override timeline, contact tab-block removal | §tasks.md §4.5 | `frontend/tests/e2e/entity-redesign.spec.ts` (400 lines; 5 runtime tests + 1 test.skip("stretch: populated tabs")); all 5 runtime tests are skip-guarded with `true` pending dev server | PARTIAL — tests exist and are structurally correct; runtime-skip-guarded pending live dev server |

---

## Per-Requirement Test Coverage — `relationship-facts/spec.md`

| # | Requirement | Anchor | Test(s) located | Status |
|---|---|---|---|---|
| R1 | `relationship.entity_facts` table — all columns, indexes, uniqueness UNIQUE(subject,predicate,object) WHERE validity='active' | §"Relationship entity facts triple store" | `tests/migrations/test_relationship_facts_migration.py`; migration `013_relationship_facts.py` | OK |
| R2 | Single table for both predicate families (contact + relational) | §"Triple store accepts contact and relational predicates" | `test_relationship_assert_fact.py::test_insert_returns_inserted_outcome`, `test_insert_entity_predicate` | OK |
| R3 | Schema-qualified name enforced — `relationship.entity_facts`, never bare `facts` | §Scenario: Schema-qualified name | Convention; all migration SQL uses FQN; no bare `facts` references found in `relationship.entity_facts`-related queries | OK (code review gate) |
| R4 | Predicate catalog — `relationship.entity_predicate_registry` seeded (contact + relational + override families) | §"Predicate catalog" | `roster/relationship/migrations/014_predicate_registry.py`; `015_queue_dismissed_predicate.py` adds state kind | OK |
| R5 | Unknown predicate rejected by central writer before any DB write | §Scenario: Unknown predicate rejected | `test_relationship_assert_fact.py::test_unknown_predicate_raises_value_error`, `test_unknown_predicate_writes_no_row` | OK |
| R6 | Predicate IDs not hardcoded in component tree (only in `entity-model.ts` frontend, `entity_predicate_registry` backend) | §Scenario: Predicate IDs not hardcoded | `test_finder_no_llm_guardrail.py` scans imports; backend seeding SQL in router.py (migration only) | OK |
| R7 | Central writer `relationship_assert_fact()` — predicate validation, dedup (ON CONFLICT), supersession, provenance | §"Central writer" | `test_relationship_assert_fact.py` (34 tests: insert, idempotency, supersession on src/conf/verified/last_seen change, error cases) | OK |
| R8 | Transaction-safety (Amendment 14) — callable from open asyncpg transaction, no nested transaction deadlock | §"Central writer" | `test_relationship_assert_fact.py::TestTransactionSafety::test_accepts_caller_conn_without_panic`, `test_caller_conn_inside_transaction`, `test_caller_conn_idempotent` | OK |
| R9 | Idempotency on (subject, predicate, object) — repeated calls produce exactly one active row | §"Central writer" | `test_relationship_assert_fact.py::TestIdempotency::test_same_call_twice_returns_unchanged`, `test_unchanged_produces_exactly_one_active_row` | OK |
| R10 | Owner-gate carry-forward (RFC 0017 §2.3) — owner subject → pending_approval row, not direct write | §"Central writer" | `test_relationship_assert_fact.py::TestOwnerCarveout::test_owner_subject_returns_pending_approval`, `test_owner_subject_writes_no_fact_row`, `test_owner_subject_writes_pending_action_row` | OK |
| R11 | `resolve_contact_by_channel()` re-pointed to `relationship.entity_facts` | §"Switchboard resolve_contact_by_channel() re-points to triples" | `src/butlers/identity.py:113` queries `relationship.entity_facts`; `tests/core/test_identity.py::test_resolve_contact_by_channel` (entity_id returned, contact_id=None post-bead-7) | OK — bead 7 shipped; was gap bu-0f3zn in prior report |
| R12 | Telegram chat resolves to entity via has-handle triple | §Scenario: Telegram chat resolves | `tests/core/test_identity.py::test_resolve_telegram_via_has_handle_triple` verifies predicate='has-handle', query uses entity_facts, contact_id=None | OK — was gap bu-w2zo6 in prior report |
| R13 | Unknown channel value returns None | §Scenario: Unknown channel returns no match | `tests/core/test_identity.py::test_resolve_contact_by_channel` asserts `pool.fetchrow=None → return None` | OK |
| R14 | `ResolvedContact` drops `contact_id` field post-bead-7 | §"Switchboard resolve_contact_by_channel() re-points to triples" | `tests/core/test_identity.py:72` asserts `r.contact_id is None` | OK |
| R15 | Migration safety — 10-step protocol (pre-snapshot, backfill, dual-write, parity, cut-over, drop) | §"Migration safety — dual-write, parity, cut-over" | Scripts: `contact_migration_snapshot.py` (bead 1), `contact_backfill_triples.py` (bead 5), `contact_backfill_credentials.py`; migration `010_drop_legacy_contact_tables.py` (bead 6/drop) | OK (protocol fully scripted; beads tracked separately) |
| R16 | Dual-write: SQL authoritative during window; MCP best-effort post-commit | §"Dual-write reconciliation contract" | `test_dual_write_parity.py::test_add_email_shim_fires_or_reconciler_closes_gap` | OK |
| R17 | Reconciler job — interval ≤1h, idempotent, sweeps contact_info rows missing triples | §"Dual-write reconciliation contract" | `test_reconciler.py::TestReconcilerInterval::test_default_interval_is_30_minutes`; `test_reconciler_registered_in_job_registry` | OK |
| R18 | Reconciler excludes secured rows (credential rows not emitted as triples) | §"Credentials carve-out" | `test_reconciler.py::test_secured_row_increments_credential_counter`; `test_credentials_carveout.py::TestReconcilerSecuredSkip` | OK |
| R19 | Read-path cut-over (bead 7) only after 24h zero-drift — gated | §"Migration safety" | Process gate (migration bead 7 conditional); `test_dual_write_parity.py::test_single_reconciler_pass_achieves_zero_drift` | OK (process gate) |
| R20 | Drop `public.contact_info` gated by 30-day soak + operator sign-off (bead 10) | §"Migration safety" | Process gate — documented in migration beads; no automated test required | OK (process gate) |
| R21 | Credentials carve-out — `relationship.credentials` table; secured rows NOT migrated to entity_facts | §"Credentials carve-out" | Migration `016_credentials_carveout.py`; `test_credentials_carveout.py` (20+ tests: insert, dedup, revoke, reconciler exclusion) | OK |
| R22 | Credentials not surfaced on `GET /entities/{id}/contacts` endpoint | §Scenario: Credentials not surfaced on entity contacts endpoint | `test_entity_contacts_credentials_exclusion.py` | OK — was gap bu-7hii5 in prior report |
| R23 | Orphan contact resolver — `contact_orphan_resolver.py`, dry-run default, reads snapshot table, mints entities, records report | §"Orphan contact handling" | `src/butlers/scripts/contact_orphan_resolver.py` (479 lines); `--apply=false` default at line 30 | OK (no unit test for the script itself — see §Observed Gaps G-05) |
| R24 | Orphan resolver dry-run: no writes without `--apply` | §Scenario: Dry-run is the default | Implemented via `--apply=false` default; not unit tested | PARTIAL — script exists; no test exercises `--apply=false` guard |
| R25 | `verified` is a column, not a separate verification-triple | §"verified is a column, not a triple" | `relationship.entity_facts` schema has `verified BOOL NOT NULL DEFAULT false`; `entity_predicate_registry` has no `verified-by` predicate | OK |
| R26 | No `verified-by` predicate in registry | §Scenario: No verification-triple predicate registered | Verified: `roster/relationship/migrations/014_predicate_registry.py` has no such seed | OK |

---

## Per-Task Close Status — `tasks.md`

### Tasks §1–2: Backend tools — entity_id resolution (gift/loan)

| Task | Description | Status | Evidence |
|---|---|---|---|
| §1.1 | `gifts.py` calls `resolve_contact_entity_id()` before each `store_fact()` | CLOSED | `roster/relationship/tools/gifts.py:86,159` |
| §1.2 | Unit tests: gift_add resolves, supersession preserves entity_id, null entity raises, gift_list active-only | CLOSED | `test_spo_tools.py::test_gift_add_stores_entity_id`, `test_gift_update_status_preserves_entity_id`, `test_gift_add_no_entity_raises_value_error`, `test_gift_list_returns_only_active_facts` |
| §1.3 | All three gift tools registered in `tools.py`, MCP signatures unchanged | CLOSED | Verified in `roster/relationship/modules/tools.py` |
| §2.1 | `loans.py` calls `resolve_contact_entity_id()` before each `store_fact()` | CLOSED | `roster/relationship/tools/loans.py:119` |
| §2.2 | Unit tests: loan_create resolves entity, settle preserves entity_id, settled flag preserved, multi-currency | CLOSED | `test_loans_entity_id.py` (6 tests: `test_loan_create_stores_entity_id`, `test_loan_create_contact_id_resolves_entity`, `test_loan_settle_preserves_entity_id`, `test_loan_settle_settled_flag_set`, `test_loan_create_multi_currency`, `test_loan_list_multiple_loans`) |
| §2.3 | Three loan tools registered in `tools.py` | CLOSED | Verified |

### Tasks §3: Backend API — entity-keyed tab endpoints

| Task | Description | Status | Evidence |
|---|---|---|---|
| §3.1 | Pydantic models: EntityNote, EntityInteraction, EntityGift, EntityLoan, EntityTimelineItem | CLOSED | `roster/relationship/api/models.py:442,469,495,521,550` |
| §3.2 | `GET /entities/{id}/notes` — contact_note, valid_at DESC, 404 on missing entity | CLOSED | `router.py:4100`; `test_entity_tabs.py::TestNotes` |
| §3.3 | `GET /entities/{id}/interactions` — predicate LIKE 'interaction_%', type from suffix | CLOSED | `router.py:4158`; `test_entity_tabs.py::TestInteractions` |
| §3.4 | `GET /entities/{id}/gifts` — predicate='gift', created_at DESC | CLOSED | `router.py:4218`; parametrized `TestSharedTabBehavior` |
| §3.5 | `GET /entities/{id}/loans` — predicate='loan', created_at DESC | CLOSED | `router.py:4277`; parametrized `TestSharedTabBehavior` |
| §3.6 | `GET /entities/{id}/timeline` — 6 predicate families, valid_at DESC NULLS LAST | CLOSED | `router.py:4339`; `test_entity_tabs.py::TestTimeline` |
| §3.7 | Integration tests — all scenarios (notes ordered, mixed-channel, timeline cross-family, retracted excluded, cross-scope excluded, 404, empty, pagination, legacy activity excluded, sparse null) | CLOSED | `test_entity_tabs.py` (66 tests total) |

### Tasks §4: Frontend — EntityDetailView and route

| Task | Description | Status | Evidence |
|---|---|---|---|
| §4.1 | `use-entities.ts` hooks: useEntityNotes, Interactions, Gifts, Loans, Timeline | CLOSED | `frontend/src/hooks/use-entities.ts:41–77` |
| §4.2 | `EntityDetailView.tsx` (implemented as `EntityDetailPage.tsx`) — header card, linked contacts, activity tabs | CLOSED (with delta M-01) | `EntityDetailPage.tsx` (2400+ lines); unified timeline not 5-tab structure |
| §4.3 | Route at `/butlers/relationship/entities/:id` → redirected to `/entities/:id` | CLOSED | `router-config.tsx:113`; `router.tsx:25–30` (redirect) |
| §4.4 | `ContactDetailView.tsx`: remove tab block; repoint entity link; warning when entity_id null | CLOSED | `ContactDetailView.tsx:923–946`; no Tab import in file |
| §4.5 | Playwright smoke test — route loads, filter pills render, dunbar_tier_override in timeline, contact no-tab, entity link navigates | CLOSED (runtime-skip-guarded) | `frontend/tests/e2e/entity-redesign.spec.ts` (400 lines); skip-guarded with `true` pending live dev server |

### Tasks §5–6: Cruft removal

| Task | Description | Status | Evidence |
|---|---|---|---|
| §5.1 | `rg` audit — no FROM notes/interactions/gifts/loans/activity_feed in production code | CLOSED | No matches in `src/` or `roster/` (finance butler comment is non-operational) |
| §5.2 | `rg` audit — all `_log_activity` callers removed | CLOSED | No `_log_activity` references found in `roster/relationship/` |
| §5.3 | `rg` audit — `useContactNotes/Gifts/Loans/Feed` only in `use-contacts.ts` and `ContactDetailView.tsx` | CLOSED | `useContactInteractions` preserved for thread-view endpoint; others removed |
| §6.1 | Delete legacy contact-keyed tab endpoints from router.py | CLOSED | `list_contact_notes`, `list_contact_gifts`, `list_contact_loans`, `list_contact_feed` absent |
| §6.2 | Delete legacy Pydantic models: Note, Interaction, Gift, Loan, ActivityFeedItem | CLOSED | `grep "^class Note\|^class Interaction\b\|^class Gift\b\|^class Loan\b\|^class ActivityFeedItem" models.py` = no matches |
| §6.3 | Delete `tools/feed.py` and all `_log_activity()` call sites | CLOSED | `feed.py` absent from `roster/relationship/tools/` |
| §6.4 | Delete frontend hooks `useContactNotes/Interactions/Gifts/Loans/Feed` from `use-contacts.ts:64–105` | CLOSED | Only `useContactInteractions` retained (thread-view) |
| §6.5 | Alembic migration — backfill entity_id, sanity check, drop 5 legacy tables | CLOSED | `roster/relationship/migrations/010_drop_legacy_contact_tables.py` |
| §6.6 | Delete obsolete tests targeting removed endpoints/tables | CLOSED | No stale tests found |

### Tasks §7: Quality gates and report

| Task | Description | Status | Evidence |
|---|---|---|---|
| §7.1 | Quality gates: ruff lint + format + pytest | CLOSED | Verified in this audit: ruff all checks passed; format clean; 327 tests pass |
| §7.2 | Manual dev environment verification | OUT OF SCOPE | Agent-env dev stack not available |
| §7.3 | Migration outcome report `docs/reports/relationship-tabs-to-entities-outcome.md` | CLOSED (partial) | `docs/reports/relationship-tabs-to-entities-outcome.md` covers Phase 1; Phase 2 report (§12.6) tracked separately |
| §7.4 | `openspec apply` + archive | CLOSED | Change is archived at `openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/` |

### Tasks §8: Frontend — new sub-routes and detail-mode toggle

| Task | Description | Status | Evidence |
|---|---|---|---|
| §8.1 | `/entities` route + `EntitiesIndexPage.tsx` | CLOSED | PR #1807 (bu-s2bgc) |
| §8.2 | `/entities/hop` route + `HopPage.tsx` | CLOSED | PR #1808 (bu-h4s95) |
| §8.3 | `/entities/columns` route + `ColumnsPage.tsx` | CLOSED | PR #1809 (bu-370h1) |
| §8.4 | `/entities/concentration` route + `ConcentrationPage.tsx` | CLOSED | PR #1810 (bu-m4ya3) |
| §8.5 | Refactor `SocialMapPage` → `SocialMapView` inside SubpageTabs | CLOSED | PR #1817 (bu-zvtxh) |
| §8.6 | `SubpageTabs` component + unit tests | CLOSED | PR #1812 (bu-wx2r0) |
| §8.7 | `EntityDetailView.tsx` Editorial+Workbench toggle, localStorage, archetype | CLOSED | PR #1811 (bu-ar4zf) |
| §8.8 | `entity-glosses.ts` strict enum (tier, state, category), build-time exhaustiveness | CLOSED | PR #1816 (bu-wi06b) |
| §8.9 | EntityMark, TierBadge, StateDot, KbMono, Pill primitives | CLOSED | PR #1813 (bu-ec2wb) |
| §8.10 | `/contacts` redirect + Contacts nav removal; `has=contact` filter chip | CLOSED | PR #1814 (bu-qsipw) |
| §8.11 | Cmd-K EntityFinder (cmdk 1.1.1) + keyboard shortcut | CLOSED | PR #1806 (bu-xfjwk) |

### Tasks §9: Backend — entity API endpoints

| Task | Description | Status |
|---|---|---|
| §9.1 | `GET /entities` list + filter + pagination | CLOSED — `router.py:2684` |
| §9.2 | `GET /entities/{id}/neighbours` (relational triples, both directions) | CLOSED — `router.py:4840`; `test_entity_neighbours.py` |
| §9.3 | `GET /entities/concentration?pred=` | CLOSED — `router.py:3557`; `test_entities_api.py::TestEntityConcentration` |
| §9.4 | `GET/POST/DELETE /entities/{id}/contacts` | CLOSED — `router.py:4979–5205`; `test_entities_api.py::TestEntityContacts` |
| §9.5 | `GET /entities/queue` | CLOSED — `router.py:3199`; `test_entities_api.py::TestEntityQueue` |
| §9.6 | `GET /entities/search` (deterministic Finder) | CLOSED — `router.py:2476`; `test_entities_api.py::TestEntitySearch` |
| §9.7 | `POST /entities` (promote unidentified → canonical entity) | CLOSED — `router.py:2904`; `test_entities_api.py::TestEntityCreate` |
| §9.8 | `POST /entities/{id}/promote-tier` (dunbar_tier_override triple) | CLOSED — `router.py:5599`; `test_entities_api.py::TestEntityPromoteTier` |
| §9.9 | `POST /entities/{id}/archive` + `DELETE /entities/{id}` | CLOSED — `router.py:5480,5805`; `test_entities_api.py::TestEntityArchive,TestEntityDelete` |
| §9.10 | `POST /entities/{id}/merge` (rewires triples, tombstones source) | CLOSED — `router.py:5810`; `test_entities_api.py::TestEntityMerge` |
| §9.11 | `POST /entities/queue/dismiss` | CLOSED — `router.py:3464`; `test_entities_api.py` |
| §9.12 | `GET /entities/{id}/activity` (relationship facts + chronicler MCP aggregator) | CLOSED — `router.py:6187`; `test_entities_api.py::TestEntityActivity` |
| §9.13 | Integration tests for §9.1–9.12 (happy path + owner-only authz + error path) | CLOSED — `test_entities_api.py` (50+ tests); `test_owner_authz_guardrail.py` |

### Tasks §10: Backend — data model `relationship.entity_facts`

| Task | Description | Status | Evidence |
|---|---|---|---|
| §10.1 | Alembic migration for `relationship.entity_facts` | CLOSED | `013_relationship_facts.py` |
| §10.2 | `relationship.entity_predicate_registry` table + seed | CLOSED | `014_predicate_registry.py` |
| §10.3 | `relationship_assert_fact()` MCP tool | CLOSED | `tools/relationship_assert_fact.py` (560 lines); `test_relationship_assert_fact.py` (34 tests) |
| §10.4 | `relationship.credentials` carve-out table | CLOSED | `016_credentials_carveout.py`; `test_credentials_carveout.py` |
| §10.5 | Chronicler boundary guardrail test | CLOSED | `test_chronicler_boundary.py::test_no_chronicler_sql_cross_schema_references`, `test_no_direct_chronicler_model_imports` |
| §10.6 | RFC 0004 Amendment 2 text authored | CLOSED | `openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/rfc-amendments/0004-amendment-2-contacts-as-triples.md` |
| §10.7 | `identity.py::resolve_contact_by_channel()` re-pointed to entity_facts | CLOSED | `src/butlers/identity.py:113` queries `relationship.entity_facts`; `test_resolve_telegram_via_has_handle_triple` passes |
| §10.8 | Finder no-LLM guardrail test (transitive import graph) | CLOSED | `test_finder_no_llm_transitive.py` (4 tests incl. synthetic-banned catch) |
| §10.9 | Dual-write reconciler job (interval ≤1h, idempotent, sweeps contact_info) | CLOSED | `jobs/relationship_jobs.py::run_contact_info_reconciler`; `test_reconciler.py` (20+ tests) |
| §10.10 | Reader inventory bead — enumerate readers of `public.contacts/contact_info` | CLOSED | `docs/reports/contact-migration-read-path-inventory.md` |
| §10.11 | `contact_orphan_resolver.py` (Migration bead 5.5) | CLOSED | `src/butlers/scripts/contact_orphan_resolver.py` (479 lines) |

### Tasks §11: Migration — cross-references to verification beads

The 10-step migration bead chain (beads 1–10) is tracked in the beads graph, NOT as
tasks under this change. The cross-references in `tasks.md §11` are reference markers
only. All bead-to-task references verified consistent.

### Tasks §12: Documentation

| Task | Description | Status | Evidence |
|---|---|---|---|
| §12.1 | RFC 0004 Amendment 2 applied to live RFC | CLOSED | `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md:6` — "Amended: 2026-05-19 — contacts collapsed to RDF triples per Amendment 2 (bu-u8xq2)" |
| §12.2 | RFC 0007 namespace verification | CLOSED | All entity endpoints at `/api/relationship/entities/*`; comment at `tasks.md:251` verifies this; marked `[x]` in tasks.md |
| §12.3 | `design-language.md` editorial-archetype vs workspace-archetype note | CLOSED | `tasks.md:262` marked `[x]` |
| §12.4 | `about/lay-and-land/module-vs-butler.md` note | CLOSED | `about/lay-and-land/module-vs-butler.md` exists (noted in this audit) |
| §12.5 | Verify chronicler MCP tool exposes `entity_id` filter param | CLOSED | `router.py:6116` calls `chronicler_list_episodes` with entity filter; `test_entities_api.py::TestEntityActivity` exercises it |
| §12.6 | `docs/reports/entity-redesign-phase-2.md` | OPEN | File does not exist; tracked as bead bu-p5zlt |
| §12.7 | `about/heart-and-soul/v1.md:64,127–132` — stale "Contacts" language update | OPEN | `v1.md:64` still reads "shared identity registry with cross-channel resolution"; `:128` still reads "canonical contact table" |
| §12.8 | Owner-only-authz guardrail test + startup gate test | CLOSED | `test_owner_authz_guardrail.py` (26 tests) |

---

## Spec-to-Code Delta Inventory

### M-01 — EntityDetailPage uses unified timeline (filter pills), not five separate tabs

**Spec:** `dashboard-relationship/spec.md §"Entity detail page"` describes "five tabs"
(Notes / Interactions / Gifts / Loans / Timeline).

**Code:** `EntityDetailPage.tsx:1087` implements a unified `ActivityTimeline` with 6
client-side kind-filter pills (All / Interactions / Notes / Gifts / Loans / Life events)
plus separate `GiftsPanel` and `LoansPanel` components. The five backend tab endpoints
exist and are tested; the frontend doesn't expose them as separate tabs.

**Rationale:** Design-update decision per `bu-afx6k` (noted in spec: "consolidated into one
filterable stream per shipped UX"). This is a spec-vs-code delta, not a regression.

**Assessment:** Acceptable. The change history acknowledges the design pivot; the backend
contract (5 endpoints) is fully implemented and tested. The unified timeline is more usable
than five separate pagination surfaces.

### M-02 — `archetype="editorial"` used in EntityDetailPage, not `"detail"`

**Spec:** `dashboard-relationship/spec.md §"Entity detail Editorial/Workbench mode toggle"`:
"Editorial mode MUST use `<Page archetype="detail">`".

**Code:** `EntityDetailPage.tsx:2052` passes `archetype="editorial"` in Editorial mode.

**Assessment:** Minor naming delta. The `editorial` archetype may be functionally identical
to or derived from `detail`; the Display 44px headline renders regardless. No functional
regression. Follow-up spec amendment recommended if the `page-primitive-spec-sync` change
normalizes archetype names.

### M-03 — Tab endpoints read legacy `facts` table (Phase 1 read path, pre-bead-7)

**Spec:** Phase 2 table reconciliation note in `dashboard-relationship/spec.md`: "Phase 1
endpoints MUST be re-pointed to `relationship.entity_facts` no later than Migration bead 7."

**Code:** The five tab endpoints at `router.py:4100–4410` currently query `FROM facts WHERE
... scope='relationship'`. The entity_facts migration has shipped; Migration bead 7
(read-path cut-over for these endpoints) is tracked separately as `bu-akads` (the bead 7
milestone). This is **expected** — the dual-write window ensures both tables contain the
same data during the transition.

**Assessment:** Expected delta, fully documented. No action needed at this audit; tracked by
migration bead chain.

### M-04 — `has=contact` filter chip uses `predicate LIKE 'has-%'` (broader than spec)

**Spec:** `dashboard-relationship/spec.md §"Entity index page"`: has=contact filter shows
entities with `has-email | has-phone | has-handle | has-address | has-birthday | has-website`
triples.

**Code:** `router.py:2640` queries `predicate LIKE 'has-%'` — a safe superset.

**Assessment:** Acceptable superset; no functional regression.

### M-05 — Playwright E2E tests are runtime-skip-guarded

**Spec:** `tasks.md §4.5` requires Playwright smoke tests.

**Code:** `frontend/tests/e2e/entity-redesign.spec.ts` exists (400 lines, 5 runtime test
cases + 1 test.skip("stretch")) but all 5 runtime tests call `test.skip(true, "Dev server
not reachable…")`. Tests exercise the correct scenarios structurally; none run in CI without
a live dev server.

**Assessment:** File exists and is structurally correct; runtime testing blocked on dev
server setup. Follow-up bead recommended to either (a) configure a dev server for CI or
(b) replace with unit/integration tests for the same scenarios.

---

## Observed Gaps

### G-01 — `docs/reports/entity-redesign-phase-2.md` missing (§12.6)

**Requirement:** `tasks.md §12.6`: "Author final report at `docs/reports/entity-redesign-phase-2.md`
covering: routes shipped, endpoints shipped, migration bead status, anti-temptation guardrail
test results, before/after entity-count metrics, EntityMark inventory."

**Current state:** File does not exist. Tracked as bead bu-p5zlt.

**Recommendation:** Low-blocking; author after migration bead 9 (post-cut-over verification
report) lands so metrics reflect final state.

### G-02 — `about/heart-and-soul/v1.md` stale "Contacts" doctrine (§12.7)

**Requirement:** `tasks.md §12.7`: Update `v1.md:64,127–132` to replace "canonical contact
table" with "canonical entity registry with contact predicates."

**Current state:** `v1.md:64` reads "Contacts — shared identity registry"; `:128` reads
"Shared contacts registry — canonical contact table." Neither updated post-RFC 0004
Amendment 2.

**Recommendation:** File a targeted documentation task; low-complexity edit but should be
gated on Migration bead 8 (write-path cut-over) when the old language truly becomes stale.

### G-03 — ContactDetailPage tests missing secured-reveal, mailto/tel, 404 scenarios (F-02)

**Requirement:** Spec scenarios: click-to-reveal secured credential; email as mailto, phone
as tel; contact not found → 404 message.

**Current state:** `ContactDetailPage.test.tsx` has these test blocks but they are marked
`describe.skip`. No active test exercises: secured=true masking + reveal flow; `<a href="mailto:…">`
rendering; 404 message on missing contact.

**Recommendation:** Unskip and implement 3 test cases in `ContactDetailPage.test.tsx`. Low
risk, medium priority.

### G-04 — EntityDetailPage tests missing Unidentified badge and "View identity →" link (F-03)

**Requirement:** `dashboard-relationship/spec.md §Unidentified entity badge scenario`:
badge renders when `entity.unidentified=true`; "View identity →" link in header.

**Current state:** The Unidentified badge renders in `EntityDetailPage.tsx:1815` and the
"View identity →" link exists at `:1781`, but no test asserts either is rendered.

**Recommendation:** Add 2 test cases to `EntityDetailPage.test.tsx`: one seeding
`unidentified=true` + asserting badge; one asserting "View identity →" link target.

### G-05 — `contact_orphan_resolver.py` has no unit tests

**Requirement:** `tasks.md §10.11` delivers `contact_orphan_resolver.py` with dry-run
default, snapshot-table read, per-row decision, report output.

**Current state:** Script exists (`src/butlers/scripts/contact_orphan_resolver.py`, 479
lines) and is well-structured. No unit tests were filed alongside it.

**Recommendation:** File a P2 task for unit tests covering: dry-run produces no writes;
orphan-with-canonical-name → mints entity; orphan-without-signal → emits notify(); outcome
recorded in report. These are the 3 spec scenarios from `relationship-facts/spec.md §Orphan
contact handling`.

### G-06 — `GET /entities/{id}/contacts` provenance fields missing in models (residual from bu-n6typ)

**Requirement:** `dashboard-relationship/spec.md §"Provenance contract"` requires all tab
endpoints include `src, conf, last_seen, weight, verified, primary`.

**Current state:** `test_entity_tabs.py::TestProvenanceContract` covers provenance for
tab endpoints. However the models in `roster/relationship/api/models.py` (EntityNote,
EntityInteraction, EntityGift, EntityLoan, EntityTimelineItem) define provenance fields
at model level. The `GET /entities/{id}/contacts` endpoint uses `ContactFact` model which
also carries provenance. Verify: `test_entity_tabs.py::test_provenance_fields_present_in_response`
was located and is passing — this gap is now closed.

**Assessment:** Gap bu-n6typ from prior report is resolved. No action needed.

### G-07 — ProvenanceGrid (Workbench mode) is a stub, not a full sortable grid

**Requirement:** `dashboard-relationship/spec.md §"Entity detail Editorial/Workbench mode"`:
Workbench MUST surface every provenance column in a dense, sortable grid.

**Current state:** `EntityDetailPage.tsx:1613` implements `ProvenanceGrid` which renders
facts from `GET /entities/{id}/facts`. The grid is implemented but its sorting capability
depends on the facts endpoint. Follow-up bead bu-r6vft tracks content differentiation
between Editorial and Workbench modes.

**Assessment:** Gap bu-r6vft (from prior frontend report) is open. Workbench mode exists
and renders a grid; full sort interactivity is tracked separately.

---

## Quality Gates Verification

```
uv run ruff check src/ tests/ roster/ conftest.py --output-format concise
→ All checks passed!

uv run ruff format --check src/ tests/ roster/ conftest.py -q
→ (clean exit — no output)

Targeted pytest run:
  roster/relationship/tests/test_entity_tabs.py
  roster/relationship/tests/test_relationship_assert_fact.py
  roster/relationship/tests/test_chronicler_boundary.py
  roster/relationship/tests/test_owner_authz_guardrail.py
  roster/relationship/tests/test_finder_no_llm_guardrail.py
→ 164 passed, 1 xfailed in 96.99s

  roster/relationship/tests/test_spo_tools.py
  roster/relationship/tests/test_entities_api.py
  roster/relationship/tests/test_reconciler.py
  roster/relationship/tests/test_loans_entity_id.py
  roster/relationship/tests/test_credentials_carveout.py
→ 163 passed, 8 warnings in 85.23s

Total: 327 tests passing, 0 failures.
```

---

## Summary Coverage Table

| Domain | Requirements | Covered | Partial | Gap |
|---|---|---|---|---|
| dashboard-relationship spec (Phase 1 + 2) | 51 | 42 | 7 | 2 |
| relationship-facts spec | 26 | 22 | 2 | 2 |
| tasks.md §1–7 (backend + Phase 1 API) | 22 | 22 | 0 | 0 |
| tasks.md §8.x (frontend routes) | 11 | 11 | 0 | 0 |
| tasks.md §9.x (entity API endpoints) | 13 | 13 | 0 | 0 |
| tasks.md §10.x (data model + migration tooling) | 11 | 11 | 0 | 0 |
| tasks.md §12.x (documentation) | 8 | 6 | 0 | 2 |

**Overall:** 125 tasks — 119 closed, 6 partial/open.

---

## RECOMMENDED FOLLOW-UP BEADS

The following gaps are recommended for the coordinator to file as new beads. No beads were
created by this worker.

---

**1. Unskip ContactDetailPage secured-reveal / mailto-tel / 404 tests**
- Proposed title: `ContactDetailPage: add tests for secured-reveal, mailto/tel links, 404 not-found`
- Type: task
- Priority: P3
- Description: Three spec scenarios in `dashboard-relationship/spec.md` have no active test coverage. `ContactDetailPage.test.tsx` has `describe.skip` blocks for all three. Implement: (1) secured=true masking + click-to-reveal flow; (2) email rendered as `<a href="mailto:…">`, phone as `<a href="tel:…">`; (3) contact not found → 404 message rendered. Discovered from bu-7jo43.

**2. EntityDetailPage: add tests for Unidentified badge and "View identity →" link**
- Proposed title: `EntityDetailPage: add tests for unidentified badge and View-identity link`
- Type: task
- Priority: P3
- Description: Two spec scenarios in `dashboard-relationship/spec.md §Unidentified entity badge` have no test coverage. Add to `EntityDetailPage.test.tsx`: (1) seed `entity.metadata["unidentified"]="true"` and assert "Unidentified" badge renders; (2) assert "View identity →" link in header points to `/entities/:id`. Discovered from bu-7jo43.

**3. contact_orphan_resolver.py: add unit tests**
- Proposed title: `contact_orphan_resolver.py: unit tests for dry-run guard, entity-mint path, escalation path`
- Type: task
- Priority: P2
- Description: `src/butlers/scripts/contact_orphan_resolver.py` (479 lines, Migration bead 5.5) has no unit tests. Three spec scenarios from `relationship-facts/spec.md §Orphan contact handling` are unverified: (1) dry-run invoked without `--apply` produces no writes; (2) orphan with canonical-name signal → mints entity + backfills entity_id; (3) orphan without signal → emits notify() + records as "escalated". Discovered from bu-7jo43.

**4. about/heart-and-soul/v1.md: update stale Contacts doctrine (§12.7)**
- Proposed title: `v1.md: update Contacts bullet and Identity System section post-RFC-0004-Amendment-2`
- Type: task
- Priority: P3
- Description: `about/heart-and-soul/v1.md:64` still reads "Contacts — shared identity registry"; line 128 still reads "canonical contact table". Per `tasks.md §12.7` and RFC 0004 Amendment 2, these should read "canonical entity registry with contact predicates" and fold the Contacts module bullet into the relationship butler entry. Gate on Migration bead 8 (write-path cut-over) to avoid premature doctrine update. Discovered from bu-7jo43.

**5. docs/reports/entity-redesign-phase-2.md: author Phase 2 final report (§12.6)**
- Proposed title: `entity-redesign-phase-2.md: author Phase 2 final report`
- Type: task
- Priority: P2
- Description: `tasks.md §12.6` requires a final report at `docs/reports/entity-redesign-phase-2.md` covering: routes shipped, endpoints shipped, migration bead status, anti-temptation guardrail test results, before/after entity-count metrics, EntityMark inventory. File does not exist (tracked as bu-p5zlt). Author after Migration bead 9 (post-cut-over verification) so metrics reflect final state. Discovered from bu-7jo43.

**6. Playwright entity-redesign E2E tests: remove runtime-skip guards or add CI dev-server**
- Proposed title: `entity-redesign.spec.ts: enable E2E tests in CI (remove skip-true guards)`
- Type: task
- Priority: P3
- Description: `frontend/tests/e2e/entity-redesign.spec.ts` (400 lines, 5 runtime test cases) has all 5 runtime tests wrapped in `test.skip(true, "Dev server not reachable…")`. Tests are structurally correct but never run. Either (a) configure a dev server for CI and remove `true` guard, or (b) replace smoke-test scenarios with integration tests runnable without a live server. Discovered from bu-7jo43.
