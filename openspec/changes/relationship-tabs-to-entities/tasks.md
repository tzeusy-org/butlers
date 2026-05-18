## 1. Backend tools — fix gift entity_id resolution (doctrine bug fix)

- [ ] 1.1 Modify `roster/relationship/tools/gifts.py` to call `resolve_contact_entity_id(pool, contact_id)` from `butlers.tools.relationship._entity_resolve` before each `store_fact()` call. Pass the resolved UUID as `entity_id=` (currently `None` at lines 95 and 161). Apply the same fix in `gift_update_status` if it issues additional `store_fact()` calls.
- [ ] 1.2 Add unit tests to `roster/relationship/tests/` covering: gift_add resolves contact→entity correctly, supersession on status change preserves the resolved entity_id, gift on a contact with NULL entity_id triggers the entity-create-and-backfill path, gift_list returns only `validity='active'` facts.
- [ ] 1.3 Verify all three gift tools remain registered in `roster/relationship/modules/tools.py` and their MCP signatures are unchanged.

## 2. Backend tools — fix loan entity_id resolution (doctrine bug fix)

- [ ] 2.1 Modify `roster/relationship/tools/loans.py` to call `resolve_contact_entity_id(pool, contact_id)` before each `store_fact()` call (currently `entity_id=None` at lines 143 and 212). Apply the same fix in `loan_settle` and any other writer paths.
- [ ] 2.2 Add unit tests covering: loan_create resolves contact→entity, supersession on settle preserves entity_id, settled-but-active flag is preserved on read, multi-currency facts are read back accurately.
- [ ] 2.3 Verify the three loan tools remain registered in `roster/relationship/modules/tools.py`.

## 3. Backend API — entity-keyed tab endpoints

- [ ] 3.1 Add new Pydantic models to `roster/relationship/api/models.py`: `EntityNote`, `EntityInteraction`, `EntityGift`, `EntityLoan`, `EntityTimelineItem` (with `kind` discriminator). Field shapes per the `dashboard-relationship` delta spec §"Entity-level tab APIs". Sparse metadata fields MUST render as `null`. Do NOT extend or alias the legacy `Note`/`Interaction`/`Gift`/`Loan`/`ActivityFeedItem` models — they are deleted in task group 6.
- [ ] 3.2 Add `GET /api/butlers/relationship/entities/{id}/notes` to `roster/relationship/api/router.py`. Query: `SELECT FROM facts WHERE entity_id=$1 AND predicate='contact_note' AND validity='active' AND scope='relationship' ORDER BY valid_at DESC LIMIT $2 OFFSET $3`. 404 if entity does not exist in `public.entities`. Pagination defaults from design §D4 (limit=50, max=200).
- [ ] 3.3 Add `GET /api/butlers/relationship/entities/{id}/interactions` with predicate filter `predicate LIKE 'interaction_%'` and the same scoping/pagination rules. Map `type` from the predicate suffix (`interaction_meeting` → `"meeting"`).
- [ ] 3.4 Add `GET /api/butlers/relationship/entities/{id}/gifts` with `predicate = 'gift'`, ordered `created_at DESC`.
- [ ] 3.5 Add `GET /api/butlers/relationship/entities/{id}/loans` with `predicate = 'loan'`, ordered `created_at DESC`.
- [ ] 3.6 Add `GET /api/butlers/relationship/entities/{id}/timeline` with predicate filter `predicate IN ('contact_note','life_event','gift','loan','dunbar_tier_override') OR predicate LIKE 'interaction_%'` and combined sort `valid_at DESC NULLS LAST, created_at DESC`. Each row carries `kind` discriminator.
- [ ] 3.7 Add integration tests under `roster/relationship/tests/` exercising each endpoint against a seeded `facts` fixture, covering all scenarios from the spec delta: notes return ordered, mixed-channel interactions merge, timeline cross-family ordering with `dunbar_tier_override` included, retracted/superseded facts excluded, cross-scope facts excluded, 404 on missing entity, empty=`[]` on no facts, pagination defaults and max enforcement, legacy `activity` predicate excluded from timeline, sparse metadata renders as null.

## 4. Frontend — EntityDetailView and route

- [ ] 4.1 Create `frontend/src/hooks/use-entities.ts` (or extend if it exists) with `useEntityNotes(entityId)`, `useEntityInteractions(entityId)`, `useEntityGifts(entityId)`, `useEntityLoans(entityId)`, `useEntityTimeline(entityId)` that fetch from the five endpoints in task group 3. Mirror the React-Query patterns used by existing `useContact*` hooks.
- [ ] 4.2 Create `frontend/src/components/relationship/EntityDetailView.tsx` rendering the header card (canonical_name, entity_type, aliases, role badges, optional "Unidentified" badge, "View identity →" link to `/entities/:id`), the linked-contacts section, and the five tabs (Notes / Interactions / Gifts / Loans / Timeline). Empty-state messages per spec scenarios.
  - Note (R2 fitness 2026-05-18): an existing `frontend/src/pages/EntityDetailPage.tsx` already mounts at `/entities/:entityId` per `router.tsx:156`. Task 4.2's `EntityDetailView.tsx` is a NEW component used inside the new entity detail route. The existing `EntityDetailPage.tsx` either (a) becomes a thin wrapper invoking `<EntityDetailView/>`, or (b) is replaced by a new page that renders `EntityDetailView`. Decision deferred to implementation; spec is shape-only.
- [ ] 4.3 Add the page route at `frontend/src/pages/butlers/relationship/entities/[id]` (or framework-equivalent path matching the existing `/butlers/relationship/contacts/:id` route layout) wiring to `EntityDetailView`. If no entity-fetch endpoint exists for the header card, add `GET /api/butlers/relationship/entities/{id}` returning the entity row plus linked contacts; verify before duplicating.
- [ ] 4.4 Modify `frontend/src/components/relationship/ContactDetailView.tsx`: remove the tab block at lines 1194-1218; repoint the `View entity` link at lines 1156-1159 from `/entities/${contact.entity_id}` to `/butlers/relationship/entities/${contact.entity_id}` and promote it to a prominent header element labeled "View entity activity →". Render the warning banner when `contact.entity_id` is null.
- [ ] 4.5 Playwright smoke test on a seeded entity verifying: page loads at the new route, all five tabs render with empty-state on a fresh entity, populated tabs render after seeding facts, timeline includes `dunbar_tier_override` events, contact detail page no longer renders the tab block, and the entity link in the contact header navigates to the correct relationship-scoped page.

## 5. Cruft removal — guard

- [ ] 5.1 Run `rg -n "FROM (notes|interactions|gifts|loans|activity_feed)\b" src/ roster/ tests/ frontend/` and audit every match. Convert any non-test reader to use the new entity-keyed endpoints or fact tools. Delete obsolete test fixtures.
- [ ] 5.2 Run `rg -n "_log_activity|tools/feed|activity_feed" src/ roster/ frontend/src/ tests/` and verify all callers can be removed cleanly. Expected callers (must all be addressed): `tools/notes.py`, `tools/interactions.py`, `tools/gifts.py`, `tools/loans.py`, `tools/dates.py`, `tools/contacts.py`, `tools/contact_info.py`, `tests/test_spo_tools.py`, `tests/test_contact_info.py`. Document any unexpected callers in a follow-up bead before proceeding.
- [ ] 5.3 Run `rg -n "useContactNotes|useContactInteractions|useContactGifts|useContactLoans|useContactFeed" frontend/` and confirm only `use-contacts.ts` and `ContactDetailView.tsx` reference them. The latter is rewritten in task 4.4; the former is edited in task 6.4.

## 6. Cruft removal — execute

- [ ] 6.1 Delete `roster/relationship/api/router.py` lines 1846-2009 (the five contact-keyed tab endpoints: `list_contact_notes` at 1846, `list_contact_interactions` at 1878, `list_contact_gifts` at 1912, `list_contact_loans` at 1947, `list_contact_feed` at 1984). Use the function names as the source of truth, since exact line numbers will shift as the file is edited.
- [ ] 6.2 Delete the legacy Pydantic models from `roster/relationship/api/models.py`: `Note`, `Interaction`, `Gift`, `Loan`, `ActivityFeedItem`. Verify no other importers remain (per task 5.1 audit).
- [ ] 6.3 Delete `roster/relationship/tools/feed.py` entirely and remove all `_log_activity()` call sites (per the list in task 5.2). The temporal facts are themselves the feed; no separate write is needed.
- [ ] 6.4 Delete frontend hooks `useContactNotes`, `useContactInteractions`, `useContactGifts`, `useContactLoans`, `useContactFeed` from `frontend/src/hooks/use-contacts.ts:64-105`. Leave the rest of the file intact.
- [ ] 6.5 Create new Alembic migration in `roster/relationship/migrations/` (alongside `001_relationship_tables.py`, as a new numbered revision) that performs in order:
  - **Step A — Backfill**: `UPDATE facts SET entity_id = c.entity_id FROM public.contacts c WHERE facts.entity_id IS NULL AND facts.scope='relationship' AND facts.predicate IN ('gift','loan') AND substring(facts.subject from 'contact:([0-9a-f-]+):')::uuid = c.id`. Log row counts of fixed vs. skipped (skipped = subject regex did not match). Abort if skipped > 0 unless an explicit `--force-skip-orphans` flag is set on the migration.
  - **Step B — Sanity check**: `SELECT count(*)` on each of `relationship.notes`, `relationship.interactions`, `relationship.gifts`, `relationship.loans`, `relationship.activity_feed`. If any > 0, abort the migration with a clear message.
  - **Step C — Drop**: `DROP TABLE relationship.notes`, `interactions`, `gifts`, `loans`, `activity_feed`.
  - **`downgrade()`**: recreate the empty tables using the schemas from `001_relationship_tables.py`. Backfill is not reversed (entity_id corrections are correct regardless).
- [ ] 6.6 Delete obsolete tests targeting the removed endpoints/tables (per task 5.2 audit). Re-run the full test suite and fix any cascading failures.

## 7. Quality gates and report

- [ ] 7.1 Run quality gates per CLAUDE.md test execution policy: `uv run ruff check src/ tests/ roster/ conftest.py --output-format concise`, `uv run ruff format --check src/ tests/ roster/ conftest.py -q`, full pytest run with the relationship butler integration tests included. All MUST pass.
- [ ] 7.2 Manually verify on the dev environment: navigate to `/butlers-dev/relationship/entities/<owner-entity-id>`; confirm all five tabs populate with real data (notes, interactions, gifts/loans if any, timeline); navigate to a contact detail page and confirm the tab block is gone and the "View entity activity →" link routes correctly. Check that pre-existing gift/loan facts with previously-NULL entity_ids now appear on the entity tabs after backfill.
- [ ] 7.3 Author the migration outcome report under `docs/reports/relationship-tabs-to-entities.md` capturing: before/after fact counts visible on the dashboard for a representative contact, count of orphan gift/loan facts backfilled, lines of code deleted, tables dropped, spec deltas archived, and any unexpected findings from task 5.1-5.3 grep audits.
- [ ] 7.4 Run `openspec apply` on this change once all tasks are complete and tests pass; archive the change once approved.

---

## Phase 2 extension (2026-05-17) — entity redesign

Added by `/project-direction` Phase 2 for the entity-redesign feature. Tasks 8-12 below
extend the change with the entity-redesign scope per Brief §6b Phase 1 amendments.

## 8. Frontend — new sub-routes and detail-mode toggle

**§8 preamble — global blocker (R3 fitness 2026-05-18):** All §8 tasks are blocked by
`page-primitive-spec-sync` archived. Every page introduced or modified in §8 renders
inside `<Page archetype="...">` (overview / detail), so the primitive contract must
ship first. This is transitive across 8.1-8.11 and is not repeated on each row.

- [ ] 8.1 Add `/entities` route in `frontend/src/router.tsx`; create `EntitiesIndexPage.tsx`
  with tabular list, filter chips, SubpageTabs strip, and right rail. Effort: **L**.
  Blocked by: 9.1, 9.5 (queue endpoint).
- [ ] 8.2 Add `/entities/hop` route + `HopPage.tsx` with re-centre graph fan-out. Effort: **M**.
  Blocked by: 9.2 (neighbours endpoint).
- [ ] 8.3 Add `/entities/columns` route + `ColumnsPage.tsx` client-side cascade via chained
  `/neighbours` calls (no new server endpoint). Effort: **M**. Blocked by: 9.2.
- [ ] 8.4 Add `/entities/concentration` route + `ConcentrationPage.tsx` with predicate tabs
  enumerated from `predicate_registry`. Effort: **M**. Blocked by: 9.3 (concentration endpoint).
- [ ] 8.5 Refactor existing `SocialMapPage.tsx` into `SocialMapView` component so SubpageTabs
  chrome wraps it without duplication. Effort: **S**.
- [ ] 8.6 Add `SubpageTabs` component in `frontend/src/components/relationship/` wrapping
  React Router links (Index/Hop/Columns/Concentration/Social-map). Effort: **S**.
- [ ] 8.7 Extend `EntityDetailView.tsx` (task 4.2) to render Editorial vs Workbench modes,
  with `<Page archetype="detail">` (Editorial) vs `<Page archetype="overview">` (Workbench;
  reuses the already-defined overview archetype because no `workspace` archetype exists
  in any shipped or in-flight spec — see dashboard-relationship/spec.md Editorial /
  Workbench mode toggle requirement) and `localStorage` persistence under
  `entities.detail.mode`. Effort: **L**. Blocked by: `page-primitive-spec-sync` archived,
  `detail-page-archetype` archived (cross-change deps).
- [ ] 8.8 Add `frontend/src/lib/entity-glosses.ts` strict enum `(tier, state, category) →
  string`. Build-time validation: missing combinations fail the build. Effort: **M**.
- [ ] 8.9 Add `frontend/src/components/ui/EntityMark.tsx`, `TierBadge.tsx`, `StateDot.tsx`,
  `KbMono.tsx`, `Pill.tsx` per Brief §2 classification table. Effort: **M**.
- [ ] 8.10 Add `301` redirect rule `/contacts → /entities?has=contact` in
  `frontend/src/router.tsx`; remove Contacts entry from `nav-config.ts`; add
  has=contact filter chip on the Index page. Effort: **S**.
- [ ] 8.11 Add Cmd-K finder component (cmdk 1.1.1 already in deps) wired to
  `/api/butlers/relationship/entities/search`. Entity-first result ordering per
  Phase 1 Open Question 14. Effort: **M**. Blocked by: 9.6.

## 9. Backend — entity API endpoints

- [ ] 9.1 `GET /api/butlers/relationship/entities` (list + filter + pagination over
  `public.entities`, joining `relationship.facts` for `has=contact` filter). Effort: **M**.
  Blocked by: 10.1 (facts table) for `has=contact`, Migration bead 5 (backfill triples
  from `public.contact_info`) so `has=contact` returns non-empty.
- [ ] 9.2 `GET /api/butlers/relationship/entities/{id}/neighbours` (relational triples grouped
  by predicate, both directions). Effort: **M**. Blocked by: 10.1, Migration bead 5
  (backfill triples) so reads return populated neighbours.
- [ ] 9.3 `GET /api/butlers/relationship/entities/concentration?pred=` (weight aggregation +
  rollup). Effort: **M**. Blocked by: 10.1.
- [ ] 9.4 `GET /api/butlers/relationship/entities/{id}/contacts` + `POST` + `DELETE`
  (contact-fact CRUD via `relationship_assert_fact()`). Effort: **M**. Blocked by: 10.3.
- [ ] 9.5 `GET /api/butlers/relationship/entities/queue` (union of unidentified +
  duplicate-candidate + stale; deterministic dup-detection by shared `has-email`/`has-phone`
  values). Effort: **L**. Blocked by: 10.1.
- [ ] 9.6 `GET /api/butlers/relationship/entities/search` (rule-based ranking per
  `pr/overview/entity-redesign/prompts/07-finder.md §7.5`; NO LLM, NO embedding service —
  enforced by guardrail test). Effort: **M**. Blocked by: 10.1.
- [ ] 9.7 `POST /api/butlers/relationship/entities` (promote unidentified → canonical entity).
  Effort: **S**. Blocked by: 10.3 (writes any contact-fact triples via
  `relationship_assert_fact()`).
- [ ] 9.8 `POST /api/butlers/relationship/entities/{id}/promote-tier` (writes a
  `dunbar_tier_override` triple via `relationship_assert_fact()` per Phase 1 Amendment 6,
  NOT a column write). Effort: **S**. Blocked by: 10.3.
- [ ] 9.9 `POST /api/butlers/relationship/entities/{id}/archive` and `DELETE
  /api/butlers/relationship/entities/{id}` (forget with tombstone). Effort: **S**.
  Blocked by: 10.3 (tombstones write supersession rows via `relationship_assert_fact()`).
- [ ] 9.10 `POST /api/butlers/relationship/entities/{id}/merge` (entity-level merge;
  rewires triples; tombstones source). Effort: **M**. Blocked by: 10.1.
- [ ] 9.11 `POST /api/butlers/relationship/entities/queue/dismiss`. Effort: **S**.
  Blocked by: 10.3 (dismiss writes a state-marker triple via `relationship_assert_fact()`).
- [ ] 9.12 `GET /api/butlers/relationship/entities/{id}/activity` aggregator (relationship
  facts + chronicler `chronicler_list_episodes` MCP call with `entity_id` filter).
  Effort: **M**. Blocked by: 12.5 (chronicler-side `entity_id` filter must exist before
  this aggregator can ship per Brief §6b Amendment 16), 10.1, 10.5.
- [ ] 9.13 Each endpoint in 9.1-9.12 ships with ≥1 integration test under
  `roster/relationship/tests/test_entities_api.py` exercising the happy path, the
  owner-only authz gate (per task 12.8), and at least one error path (404 for unknown
  entity, 400 for malformed params, etc.). Effort: **M**.
  Blocked by: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12, 12.8.

## 10. Backend — data model `relationship.facts`

- [ ] 10.1 Create Alembic migration for `relationship.facts` table per
  `specs/relationship-facts/spec.md` (columns, indexes, uniqueness). Effort: **M**.
- [ ] 10.2 Create `relationship.predicate_registry` table; seed contact + relational +
  override predicate sets. Effort: **S**. Blocked by: 10.1.
- [ ] 10.3 Implement `relationship_assert_fact()` MCP tool (predicate validation, dedup,
  supersession, provenance enforcement). Effort: **M**. Blocked by: 10.1, 10.2.
- [ ] 10.4 Create `relationship.credentials` table (carve-out for `secured=true` rows);
  move credential read path off `public.contact_info`. Effort: **M**.
  Blocked by: 10.1 (credentials live in the same `relationship` schema introduced by
  the facts migration; schema creation must land first).
- [ ] 10.5 Author chronicler-boundary guardrail test
  `roster/relationship/tests/test_chronicler_boundary.py`: scan router source for
  `FROM chronicler.` / `JOIN chronicler.` / `import chronicler.models` — fail if found
  (mirrors `rfcs/0014:178` invariant style). Effort: **S**.
- [ ] 10.6 Author RFC 0004 amendment text at
  `rfc-amendments/0004-amendment-2-contacts-as-triples.md` (done). Apply amendment to
  `about/legends-and-lore/rfcs/0004-identity-and-contact-resolution.md` during archive.
  Effort: **S**.
- [ ] 10.7 Re-point `src/butlers/identity.py:resolve_contact_by_channel()` to query
  `relationship.facts`; update `build_identity_preamble()` to drop `contact_id`. Effort: **M**.
  Blocked by: 10.1, dual-write shim live (see migration beads).
- [ ] 10.8 Add Finder no-LLM guardrail test that walks the FULL transitive import graph
  of the `/entities/search` handler (use `importlib.util.find_spec` recursively or
  `modulegraph`). Banned set: `anthropic`, `openai`, `cohere`, `voyageai`, `mistralai`,
  `sentence_transformers`, `pgvector` distance operators (`<->`, `<=>`, `<#>`),
  `requests.post` / `httpx.post` to non-localhost URLs. Allowed set (whitelist):
  `rapidfuzz`, `python-Levenshtein`, plain SQL `ILIKE`, `pg_trgm`. Any reachable module
  containing a banned import fails the test. Per Brief §6b Amendment 15. Effort: **M**.
  Blocked by: 9.6.
- [ ] 10.9 Implement the dual-write reconciler job per Brief §6b Amendment 14
  (`specs/relationship-facts/spec.md` Requirement: Migration safety — dual-write
  reconciliation contract): a periodic worker (interval ≤ 1h) sweeping
  `public.contact_info` for rows missing a matching active triple in `relationship.facts`,
  emitting them via the central writer (`relationship_assert_fact()`), idempotent on
  `(subject, predicate, object)`. Worker lifecycle, retry, and metrics included.
  Effort: **M**. Blocked by: 10.3.
- [ ] 10.10 Reader-inventory bead per Brief §6b Amendment 13: enumerate every reader of
  `public.contacts` / `public.contact_info` (starting set per the brief:
  `src/butlers/identity.py`, `src/butlers/modules/memory/tools/preferences.py`,
  `src/butlers/modules/approvals/{_shared,gate,email_guard}.py`,
  `roster/switchboard/tools/identity/inject.py`,
  `roster/switchboard/tools/routing/route.py`, `roster/home/modules/__init__.py`,
  `roster/relationship/jobs/relationship_jobs.py`); produce a re-pointing plan; file a
  sub-bead per reader. The output of this task becomes Migration bead 4.5 in the
  Amendment 1.1.C bead chain, blocking Migration bead 7 (read-path cut-over). Effort: **M**.
- [ ] 10.11 Implement `src/butlers/scripts/contact_orphan_resolver.py` per Brief §6b
  Amendment 1.1.A.3 update (encoded in `specs/relationship-facts/spec.md` Requirement:
  Orphan contact handling): post-migration Python script, `--apply=false` dry-run default,
  reads from `public.contacts_pre_migration_<YYYYMMDD>` snapshot, mints entities via
  direct SQL OR emits `notify()` to owner per per-row decision, records outcome in
  `docs/reports/contact-migration-orphans-<YYYY-MM-DD>.md`. Sequenced as Migration bead **5.5**
  between backfill (bead 5) and parity tests (bead 6) — see §11 enumeration below.
  Effort: **M**.

## 11. Migration — cross-references to verification beads

The contacts → triples migration is governed by **the 10 verification beads enumerated in
Phase 1 Amendment 1.1.C** (Brief §6b Amendment 1.1). These beads are created and tracked
in the beads graph by `/project-direction` Phase 3; they are NOT tasks under this change.
This section enumerates the cross-references so reviewers can trace coverage:

- **Migration bead 1**: pre-migration snapshot + row-count baseline
  → blocks: 10.3, 9.4, 9.5, 9.6, 10.7
- **Migration bead 2**: write-path inventory
  → blocks: 10.3, 10.7
- **Migration bead 3**: central writer MCP tool (`relationship_assert_fact()`)
  → satisfied by task 10.3
- **Migration bead 4**: dual-write shim per writer
  → blocks: 10.7 (read-path cut-over)
- **Migration bead 4.5**: reader inventory (per Brief §6b Amendment 13)
  → satisfied by task 10.10
  → blocks: Migration bead 7 (read-path cut-over) — every reader enumerated here must be
    re-pointed before cut-over
- **Migration bead 5**: backfill triples from `public.contact_info`
  → blocks: 9.4, 9.5, 9.6 (frontend can read after this), Migration bead 5.5
- **Migration bead 5.5**: orphan contact resolver (per Brief §6b Amendment 1.1.A.3 update)
  → satisfied by task 10.11
  → blocks: Migration bead 6 (parity tests cannot run with unresolved orphans on the
    snapshot table)
- **Migration bead 6**: parity tests
  → blocks: 10.7
- **Migration bead 7**: read-path cut-over (Switchboard, MCP read tools)
  → satisfied by task 10.7
  → includes re-pointing of all readers enumerated in task 10.10 reader-inventory output
- **Migration bead 8**: write-path cut-over (remove dual-write shims)
  → unblocks `public.contact_info` deprecation
- **Migration bead 9**: post-cut-over verification report
  → 30 days after bead 8
- **Migration bead 10**: drop `public.contact_info` (gated)
  → after bead 9 sign-off

Each migration bead carries `[depends on Amendment 1.1 bead: <bead-title>]` style
upstream references in the beads graph; tasks 10.3, 10.7 above carry the corresponding
`blocked-by` references at the task level.

## 12. Documentation

- [ ] 12.1 RFC 0004 amendment (per task 10.6). Effort: **S**.
- [ ] 12.2 RFC 0007 namespace note: confirm all new endpoints live under
  `/api/butlers/relationship/entities/*` per Phase 1 Amendment 2; no RFC 0007 amendment
  needed (existing auto-discovery prefix per `rfcs/0007:31` already covers them). Effort: **XS**.
- [ ] 12.3 Update `about/heart-and-soul/design-language.md` (if needed) to clarify the
  editorial-archetype vs workspace-archetype distinction for EntityDetailPage Editorial
  vs Workbench (per Phase 1 Amendment 7). Effort: **S**.
- [ ] 12.4 Add to `about/lay-and-land/` a note clarifying the module-vs-butler distinction
  (resolves Phase 1 Open Question 25 / Phase D documentation drift). Effort: **S**.
- [ ] 12.5 **Prereq of 9.12** (per Brief §6b Amendment 16): verify the chronicler MCP tool
  surface exposes `chronicler_list_episodes(entity_id=...)`. RFC 0014:255-258 currently
  lists `chronicler_list_episodes` / `chronicler_get_episode` / `chronicler_submit_correction`
  without an `entity_id` filter parameter; the activity aggregator (task 9.12) cannot ship
  without that filter. If the filter is not present, file a chronicler-side spec amendment
  as a blocker on 9.12 (NOT a follow-up bead). If the filter cannot be added in time,
  task 9.12 is descoped to relationship-owned activity rows only (no chronicler join) for
  v1. Effort: **XS**. Blocked by: nothing. Blocks: 9.12.
- [ ] 12.6 Author final report at `docs/reports/entity-redesign-phase-2.md` (extends the
  existing report from task 7.3) covering: routes shipped, endpoints shipped, migration
  bead status, anti-temptation guardrail test results, before/after entity-count metrics,
  EntityMark inventory.
- [ ] 12.7 At change-archive time, edit `about/heart-and-soul/v1.md:64` (the "Contacts —
  shared identity registry with cross-channel resolution" v1 module bullet) and
  `:127-132` (the "Identity System" section text describing "Shared contacts registry —
  canonical contact table with roles and entity linkage") to reflect the post-RFC 0004
  Amendment 2 storage rewrite: replace "canonical contact table" with "canonical entity
  registry with contact predicates" and fold the Contacts module bullet into the
  relationship butler entry. Owner-bootstrap and cross-channel resolution capabilities
  remain — only the implementation language updates. Per Brief §6b Amendment 11. Effort: **XS**.
- [ ] 12.8 Add owner-only-authz guardrail test asserting that every endpoint listed in
  `specs/dashboard-relationship/spec.md` Requirement: Owner-only authorization for entity
  endpoints (clauses 12a and 12b) returns HTTP 403 with `{ code: 'owner_required' }` when
  the caller is not resolved to an owner-role entity. Also add a startup test that asserts
  the daemon refuses to boot in any non-`dev` environment when `DASHBOARD_API_KEY` is unset
  (clause 12c). Per Brief §6b Amendment 12. Effort: **S**.
