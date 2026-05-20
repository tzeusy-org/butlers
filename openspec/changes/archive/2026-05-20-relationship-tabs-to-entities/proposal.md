## Why

The contact detail page (`/butlers/relationship/contacts/:id`) renders five tabs — Notes, Interactions, Gifts, Loans, Activity — that are all empty in production despite the underlying data being actively written. The dashboard API queries five contact-keyed legacy tables (`relationship.{notes,interactions,gifts,loans,activity_feed}`), but the relationship butler's tools migrated to bitemporal SPO facts under `predicate-taxonomy.md` Phase 2: notes, interactions, life events, and the activity stream are all stored as rows in `facts`, never the legacy tables. The Dunbar tier engine reads from facts; the dashboard does not. Two doctrine sources (`entity-identity` §"Entity-first data model" and `predicate-taxonomy.md` §3.2) declare facts MUST anchor to entities, not contacts. The `dashboard-relationship` spec is the only artifact still locking tabs to the contact level — it is the drift, and it makes the tabs lie.

A second drift surfaced during reconciliation: `roster/relationship/tools/gifts.py` and `tools/loans.py` already exist as facts-backed writers, but both pass `entity_id=None` to `store_fact()`, violating the existing requirement at `butler-relationship/spec.md:90-95` ("the tool MUST store a fact with… `entity_id=contact_entity_id`"). Notes and interactions resolve `entity_id` correctly via `tools/_entity_resolve.py::resolve_contact_entity_id()`; gifts and loans do not. Any entity-keyed query for gifts or loans returns zero rows even though the tools are running.

## What Changes

- **BREAKING**: Remove the five contact-keyed tab endpoints `GET /api/butlers/relationship/contacts/{id}/{notes,interactions,gifts,loans,feed}`. No deprecation period, no compatibility shims.
- **BREAKING**: DROP TABLE the five legacy schemas `relationship.notes`, `relationship.interactions`, `relationship.gifts`, `relationship.loans`, `relationship.activity_feed`. Verify zero rows before drop. New Alembic migration ships in the same release as the API/UI cutover.
- Add entity-keyed tab endpoints `GET /api/butlers/relationship/entities/{id}/{notes,interactions,gifts,loans,timeline}` reading from `facts` filtered by predicate per `predicate-taxonomy.md` §5.2. All endpoints scope to `validity='active' AND scope='relationship'`. Pagination via `?limit=&offset=`.
- Collapse the Activity tab into a **Timeline** tab — a unified stream of all temporal facts (`interaction_*`, `contact_note`, `life_event`, `gift`, `loan`, `dunbar_tier_override`) ordered by `valid_at DESC`. The standalone `activity` predicate is retired as a write path; `_log_activity()` and `tools/feed.py` are deleted along with all 20+ call sites across `tools/{notes,interactions,gifts,loans,dates,contacts,contact_info}.py`. Existing `activity` facts (if any) become orphan history; they are not surfaced on the new Timeline.
- Add a new entity detail page at `/butlers/relationship/entities/:id` rendering an `EntityDetailView` with the five tabs. Header surfaces `canonical_name`, `entity_type`, `aliases`, role badges, and the list of contacts linked to the entity (with channel summaries).
- Strip the tabbed-content area from the contact detail page (`ContactDetailView.tsx:1194-1218`). The page becomes channel/identity-only: header, contact_info, addresses, important_dates, quick_facts, relationships, labels. The existing `View entity` link at `ContactDetailView.tsx:1156-1159` is repointed from `/entities/{id}` (memory butler identity page) to `/butlers/relationship/entities/{id}` (the new relationship-scoped activity page), and promoted to a prominent header element. The `relationships` array (typed contact-to-contact links) on the contact detail response is preserved unchanged — re-anchoring it to entity edge facts is explicitly out of scope.
- **Fix doctrine bug** in `tools/gifts.py` and `tools/loans.py`: both currently pass `entity_id=None` to `store_fact()`, in violation of `butler-relationship/spec.md:90-95`. Adopt the `tools/notes.py` pattern — call `resolve_contact_entity_id(pool, contact_id)` before the fact write, pass the resolved UUID. Without this fix the new entity-keyed tab APIs return empty for gifts and loans even though tools run.
- **Backfill orphan gift/loan facts**: any existing facts in `facts` with `predicate IN ('gift','loan') AND entity_id IS NULL` are backfilled by reading `subject` (which encodes `contact:{contact_id}:gift|loan:{...}`), resolving the contact's `entity_id`, and updating the fact. Migration includes this step gated by an explicit row count.
- Remove frontend hooks `useContactNotes`, `useContactInteractions`, `useContactGifts`, `useContactLoans`, `useContactFeed` (`use-contacts.ts:64-105`). Replaced by `useEntity*` equivalents.

## Capabilities

### New Capabilities

None. Gift/loan tool contracts are already specified by `butler-relationship` and `predicate-taxonomy.md`; this change is execution and bug-fixing, not new doctrine.

### Modified Capabilities

- `dashboard-relationship`: The Contact detail page requirement loses its tabbed-content section. The Contact detail API requirement loses the contact-keyed sub-resources. New requirements add the entity detail page and entity-level tab APIs. Field shapes are corrected to match `predicate-taxonomy.md` §5.2 wrapper mappings (no more ghost fields like `title`, `body`, `duration_minutes`, `loaned_at`).

## Impact

**Backend (`roster/relationship/`)**
- Modified: `tools/gifts.py` and `tools/loans.py` to call `resolve_contact_entity_id()` and pass resolved `entity_id` to `store_fact()` (bug fix, not new code).
- Modified: `api/router.py` (add 5 entity endpoints, delete 5 contact-keyed endpoints — function names: `list_contact_notes`, `list_contact_interactions`, `list_contact_gifts`, `list_contact_loans`, `list_contact_feed`). The earlier "lines 1846-2009" range from the brief is stale (per R2 fitness pass 2026-05-18 — that range currently holds live owner-info handler code); the function names are the stable identifier.
- Modified: `api/models.py` (new entity-shape Pydantic models; delete `Note`/`Interaction`/`Gift`/`Loan`/`ActivityFeedItem`).
- Deleted: `tools/feed.py` entirely; all 20+ `_log_activity()` call sites across `tools/{notes,interactions,gifts,loans,dates,contacts,contact_info}.py` and tests.
- New Alembic migration in `roster/relationship/migrations/`: backfill orphan gift/loan fact `entity_id` values, then drop the five legacy tables; verify-then-drop pattern.

**Frontend (`frontend/`)**
- New: `src/components/relationship/EntityDetailView.tsx`, hooks `useEntityNotes/Interactions/Gifts/Loans/Timeline` in `src/hooks/use-entities.ts` (or equivalent), route `src/pages/butlers/relationship/entities/[id]`.
- Modified: `src/components/relationship/ContactDetailView.tsx` (remove tab block at lines 1194-1218; repoint and promote the entity link at lines 1156-1159).
- Deleted: contact-scoped tab hooks in `src/hooks/use-contacts.ts:64-105`.

**Database**
- Backfill `facts.entity_id` for orphan gift/loan facts (one-time migration step).
- Drop tables in `relationship` schema: `notes`, `interactions`, `gifts`, `loans`, `activity_feed`. No data migration required (legacy tables are believed empty in prod; verified by gate).
- No changes to `public.entities`, `public.contacts`, `public.contact_info`, or the `facts` table schema.

**API contract**
- Removed: 5 endpoints under `/api/butlers/relationship/contacts/{id}/*`.
- Added: 5 endpoints under `/api/butlers/relationship/entities/{id}/*`.
- No external consumers — only the Butlers dashboard frontend.

**Specs**
- `openspec/specs/dashboard-relationship/spec.md`: modified via delta (this change).
- `openspec/specs/butler-relationship/spec.md`: unchanged. The gift/loan entity-resolution bug is a violation of an existing requirement (`§"Property-fact tools"`); fixing it brings code into spec compliance.
- `openspec/specs/predicate-taxonomy.md`: unchanged.

**Out of scope (deferred)**
- Typed contact-to-contact `relationships` re-anchoring to entity edge facts. Documented in design.md as a known future cleanup; the `relationships` array on the contact detail response is preserved as-is.
- `contact_task` and `reminder` predicate writers — not surfaced by the Notes/Interactions/Gifts/Loans/Timeline tabs.
- Updating the existing `feed_get` MCP tool to remove `activity` from its predicate list. The tool's contract still references `activity` for historical fact retrieval; the new Timeline endpoint is independent of `feed_get` and intentionally excludes the retired predicate.

## Phase 2 extension (2026-05-17) — entity redesign

Added by `/project-direction` Phase 2 for the entity-redesign feature. The brief at
`docs/redesigns/2026-05-17-entity-brief.md` (binding §0 design intent, binding §6b Phase 1
amendments) drives this extension.

- **NEW**: Introduce `relationship.entity_facts` triple store as the canonical RDF registry for
  contact and relational predicates, superseding RFC 0004 §3. Migration follows the
  10-step dual-write / cut-over protocol in `specs/relationship-facts/spec.md`
  Requirement: Migration safety. Zero data loss is mandatory.
- **NEW**: Add five new sub-routes (`/entities`, `/entities/hop`, `/entities/columns`,
  `/entities/concentration`, plus `/entities/social-map` preserved) and the
  Editorial/Workbench detail-mode toggle per `specs/dashboard-relationship/spec.md`
  Phase 2 extension section.
- **NEW**: App-wide Cmd-K Finder backed by `GET /api/butlers/relationship/entities/search`
  (deterministic rule-based ranking only — no LLM, no embedding service).
- **NEW**: Curation queue right rail at `/entities` backed by
  `GET /api/butlers/relationship/entities/queue`.
- **NEW**: Activity aggregator at `GET /api/butlers/relationship/entities/{id}/activity`
  calling chronicler MCP tools (no direct SQL into `chronicler.*`).
- **NEW**: RFC 0004 Amendment 2 (`rfc-amendments/0004-amendment-2-contacts-as-triples.md`)
  applied during change archive.
- **NEW**: Detail-page voice glosses are canned strings (`frontend/src/lib/entity-glosses.ts`
  strict enum keyed on `(tier, state, category)`). No LLM call per page load.

The Phase 2 extension scope is significantly larger than the original tactical bug fix.

**Change-split decision (2026-05-18, Phase 1 R-pass):** **this change folds the Phase 2
extension; do not split.** Reasons: (a) §§8-12 endpoint surface depends on
`relationship.entity_facts` which is the same backend that §§1-7 gift/loan `entity_id` writes
target, (b) one focused change with two clearly-marked sections is more reviewable than
two cross-referencing changes, (c) parallelism is preserved by per-task `Blocked by:`
declarations and beads-coordinator dispatch, not by change-id separation, (d) the
§1.1 zero-data-loss contract requires the central writer and dual-write shims to land
in coordinated order, (e) single-archive atomicity makes spec drift impossible.

**Deployability gate (binding):** the change archives atomically, but §§1-7 deliverables
(gift/loan entity_id backfill, contact-detail tab strip removal) MUST ship to production
independently of §§8-12 (`relationship.entity_facts` migration + new sub-routes). Concretely:
§6 legacy-table DROP defers until after Migration bead 10 sign-off (per Brief §6b
Amendment 1.1.A.6). The tactical bug fix is not blocked by the larger migration window.

**Cross-reference:** Brief §6b Amendments 11-16 (added 2026-05-18 by Phase 1 R-pass)
extend the binding amendment set. Phase 2 of `/project-direction` must reflect all
sixteen amendments in the spec extension.

### Phase 1 R-pass amendments (2026-05-18)

Added by Phase 1 R-pass (commit `4f9af6c7`) and binding for this Phase 2 extension.
Each amendment is cited at its location in `docs/redesigns/2026-05-17-entity-brief.md`
§6b. The spec extension below reflects all six.

- **Amendment 11 — `v1.md` doctrine update post-RFC 0004 Amendment 2** (Brief §6b
  lines 532-538). Drives tasks.md §12.7: edit `about/heart-and-soul/v1.md:64` and
  `:127-132` at change-archive time to fold the Contacts module bullet into the
  relationship butler entry and replace "canonical contact table" with "canonical
  entity registry with contact predicates."
- **Amendment 12 — Owner-only authorization for entity endpoints** (Brief §6b lines
  540-550). Drives `dashboard-relationship/spec.md` Requirement: Owner-only
  authorization for entity endpoints (12a writes, 12b reads, 12c deploy gate) and
  tasks.md §12.8 (guardrail test).
- **Amendment 13 — Reader inventory companion to Amendment 1.1.B** (Brief §6b lines
  552-566). Drives tasks.md §10.10 (reader-inventory bead becoming Migration bead 4.5,
  blocking Migration bead 7 read-path cut-over).
- **Amendment 14 — Dual-write reconciliation contract** (Brief §6b lines 568-578).
  Drives `relationship-facts/spec.md` Requirement: Migration safety dual-write
  reconciliation contract (SQL-authoritative, periodic reconciler, eventual parity,
  transaction-safe central writer, idempotent on `(subject, predicate, object)`) and
  tasks.md §10.9 (reconciler job implementation).
- **Amendment 15 — Deterministic-Finder enforcement is transitive** (Brief §6b lines
  580-588). Drives tasks.md §10.8 expansion: transitive import-graph scan, enumerated
  banned set (`anthropic`/`openai`/`cohere`/`voyageai`/`mistralai`/`sentence_transformers`/
  pgvector distance ops/non-localhost `requests.post`/`httpx.post`), enumerated allowed
  set (`rapidfuzz`, `python-Levenshtein`, `ILIKE`, `pg_trgm`).
- **Amendment 16 — `chronicler_list_episodes` entity filter is a prereq** (Brief §6b
  lines 590-596). Drives tasks.md §12.5 rewrite (prereq, not follow-up) and §9.12
  blocked-by re-declaration (`Blocked by: 12.5, 10.1, 10.5`).
