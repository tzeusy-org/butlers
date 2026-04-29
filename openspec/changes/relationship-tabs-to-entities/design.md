## Context

The relationship butler's dashboard surface is the most visible drift between doctrine and implementation in the project. The data hierarchy doctrine (`openspec/specs/entity-identity/spec.md` §"Entity-first data model") has been settled since `core_014`: every fact anchors to `public.entities(id)`. The CRUD-to-SPO predicate taxonomy (`openspec/specs/predicate-taxonomy.md` §3.2 and §5.2) names every legacy relationship table and maps it to a fact predicate with a wrapper response shape. The relationship butler's tools layer landed the migration: `roster/relationship/tools/notes.py` and `tools/interactions.py` write facts with proper entity resolution (via `tools/_entity_resolve.py::resolve_contact_entity_id()`); `tools/dunbar.py` reads from `facts` directly.

Three things did not happen, all visible from the dashboard:

1. The dashboard API was never re-pointed at the new tools layer. `roster/relationship/api/router.py:1846-2009` still queries the legacy contact-keyed tables `relationship.{notes, interactions, gifts, loans, activity_feed}` (defined in `roster/relationship/migrations/001_relationship_tables.py`). Those tables receive zero writes.
2. `roster/relationship/tools/gifts.py` and `tools/loans.py` were ported to facts, but with a doctrine-violating bug: both pass `entity_id=None` to `store_fact()` (lines 95, 161 in gifts.py; lines 143, 212 in loans.py). The existing `butler-relationship/spec.md:90-95` requires `entity_id=contact_entity_id` for property-fact tools. The notes/interactions tools call `resolve_contact_entity_id()` first; gifts/loans skip this step. Any entity-keyed query for gifts or loans returns zero rows.
3. `roster/relationship/tools/feed.py::_log_activity()` is called from at least 20 sites across `tools/{notes,interactions,gifts,loans,dates,contacts,contact_info}.py`, writing duplicate facts under `predicate='activity'`. The activity predicate is registry-seeded (per `predicate-taxonomy.md` §6 Phase 2) but is the only predicate the new Timeline tab does not include — the tab is built from primary facts (notes, interactions, gifts, loans, life_events), and `activity` is derivative duplication.

The user-visible symptom: every tab on `/butlers/relationship/contacts/:id` is empty, even though the passive interaction sync job (`openspec/specs/passive-interaction-sync/spec.md`) is creating interaction facts every 4 hours and the user's own `note_create` calls are landing in `facts`.

Stakeholder: single end user (project owner). Maturity: prototype. No external API consumers — only the in-tree Next.js frontend talks to these endpoints.

## Goals / Non-Goals

**Goals**

- Make Notes / Interactions / Gifts / Loans / Timeline tabs render their actual content for any entity, sourced from `facts`.
- Move tab discovery and viewing to the entity detail page; the contact detail page becomes channel/identity-only.
- Bring `tools/gifts.py` and `tools/loans.py` into compliance with the existing `butler-relationship` spec (resolve entity_id before write).
- Backfill orphan gift/loan facts (those with `entity_id IS NULL`) so they surface on the new entity-keyed endpoints.
- Eliminate the five legacy tables, their dead API endpoints, and the `_log_activity()` write path in the same release. No dual-read window. No deprecated predicate writes.
- Correct the `dashboard-relationship` spec to reflect the actual fact metadata schema — stop describing fields that never existed.

**Non-Goals**

- Re-anchoring the typed contact-to-contact `relationships` array to entity edge facts. The current `relationship.relationships` table works, the data is small, and edge-fact migration is a separate epic. Out of scope here. The `relationships` array on the contact detail response is preserved unchanged.
- Adding `contact_task` or `reminder` predicate writers. Those domains are not surfaced by the five tabs in scope.
- Modifying the existing `feed_get` MCP tool's predicate list. The Timeline endpoint is a new dashboard surface and is independent of `feed_get`. Historical `activity` facts (if any) remain queryable via `feed_get` for tools that depend on it; they simply do not appear on the new Timeline tab.
- Removing the `activity` row from the `predicate_registry` seed. Registry entries are advisory metadata, not normative writers; leaving the row is harmless and simplifies rollback.
- Backwards compatibility for the contact-keyed endpoints. Frontend cuts over in lockstep with backend; no consumers exist outside the dashboard.
- Authoring new butler-level requirements. The gift/loan entity-resolution bug fix brings code into compliance with an *existing* requirement (`butler-relationship/spec.md:90-95`). No spec change there.
- Predicate-specific filter UI on tabs (e.g., "show only `interaction_meeting`"). Default lists; filters can be added later if the user needs them.

## Decisions

### D1. Data source: facts table, no intermediate views

Each tab endpoint queries `facts` directly with a predicate filter, scoped to `validity = 'active' AND scope = 'relationship'`. No materialized views, no per-tab tables. Rationale: the predicate taxonomy is the contract; queries that name predicates explicitly are self-documenting; partial indexes already exist (`predicate-taxonomy.md` §4.4). Alternative considered: cache per-entity rollups in a sidecar table — rejected as premature optimization with no measured load problem.

Mapping table (canonical, for implementation):

| Tab | Predicate filter | Sort |
|---|---|---|
| Notes | `predicate = 'contact_note'` | `valid_at DESC` |
| Interactions | `predicate LIKE 'interaction_%'` | `valid_at DESC` |
| Gifts | `predicate = 'gift'` | `created_at DESC` |
| Loans | `predicate = 'loan'` | `created_at DESC` |
| Timeline | `predicate IN ('contact_note','life_event','gift','loan','dunbar_tier_override') OR predicate LIKE 'interaction_%'` | `valid_at DESC NULLS LAST, created_at DESC` |

`dunbar_tier_override` is included on Timeline (per Q1 resolution) so the user can audit when a contact's Dunbar tier was manually pinned. `activity` is **excluded** from Timeline — historical `activity` facts (if any exist post-deletion) remain queryable via `feed_get` but are not surfaced on the new tab. Rationale: `_log_activity()` was always a derivative write of facts that already existed (notes → activity, gift_added → activity, etc.); surfacing both creates duplication.

### D2. Activity tab → Timeline tab (collapse)

The standalone `activity` predicate is retired as a write path. The `_log_activity()` helper and `tools/feed.py` are deleted along with all caller sites — currently across `tools/{notes,interactions,gifts,loans,dates,contacts,contact_info}.py` and a handful of tests. Rationale: each `_log_activity()` call is a duplicate of the primary fact being written. Once notes/interactions/gifts/loans/life_events are facts, the Timeline tab is just a temporal query across them. A new predicate would only re-introduce the duplication.

The `activity` row in `predicate_registry` is left in place. Registry rows are advisory; leaving the row simplifies rollback (no migration churn for a metadata table) and does not cause any code to be invoked.

### D3. Endpoint path: `/api/butlers/relationship/entities/{id}/...`

Mirrors the existing `/api/butlers/relationship/contacts/{id}/...` path naming. Entity is the new identifier; the path makes that explicit. Alternative considered: a top-level `/api/entities/{id}/...` namespace — rejected, entities are conceptually shared but the *queries* are scoped to relationship-domain predicates (`scope='relationship'`) and live in the relationship butler's router.

The relationship butler's API is being extended to expose entity-level read endpoints under its own namespace. This is consistent with `RFC 0007` §"Butler Control Endpoints" — the butler exposes its domain via dashboard endpoints; the entity is the new key, not a new domain.

### D4. Pagination contract: `?limit=&offset=`, default 50, max 200

Matches existing list endpoints in `roster/relationship/api/router.py` (verified at `list_contacts` line 215). Alternative: cursor pagination — overkill for the data sizes (single user, low thousands of facts per active entity).

### D5. Empty-vs-missing entity disambiguation

If `public.entities` has no row with the given UUID → 404. If the entity exists but has zero matching facts → 200 with `[]`. Rationale: matches REST conventions and lets the frontend distinguish "entity gone" from "entity quiet."

### D6. Contact detail page: header-and-identity only

Strip the tabbed-content area entirely. Keep header card, contact info, important dates, quick facts, relationships, labels. The existing `View entity` link at `frontend/src/components/relationship/ContactDetailView.tsx:1156-1159` currently points to `/entities/{id}` (the memory butler's identity page). It is **repointed** to `/butlers/relationship/entities/{id}` (the new relationship-scoped activity page) and promoted to a prominent header element labeled "View entity activity →".

The two entity surfaces remain separate by intent: memory-butler identity page (`/entities/:id`) for credentials and identity properties; relationship-butler activity page (`/butlers/relationship/entities/:id`) for the fact streams. Both deep-link to each other via explicit affordances.

Alternative: keep tabs on contact pages as a "filtered" view of the entity's activity for that channel — rejected, channel-filtered activity is rarely what the user wants and complicates the spec for a marginal use case. Alternative: unify into a single entity page — rejected as premature; the two surfaces have different audiences (identity admin vs. relationship browsing).

### D7. Cutover, not migration

Drop the five legacy tables in the same Alembic migration that lands the new endpoints, after a backfill step for orphan gift/loan facts. The migration:

1. Backfills `facts.entity_id` for any rows with `predicate IN ('gift','loan') AND entity_id IS NULL AND scope = 'relationship'`. Subject format is `contact:{contact_id}:gift:{slug}` or `contact:{contact_id}:loan:{uuid}`; parse `contact_id` from subject, look up `entity_id` via `public.contacts.entity_id`, UPDATE the fact. Log row count of fixed facts.
2. Verifies `SELECT count(*) = 0` per legacy table; raises if any are unexpectedly non-empty.
3. `DROP TABLE` for `relationship.{notes, interactions, gifts, loans, activity_feed}`.

The migration's `downgrade()` recreates the empty tables using the schema from `001_relationship_tables.py`. Backfill is non-reversible (UPDATE on existing facts) but harmless on rollback (the entity_id was correct; the legacy tables don't read it).

Rationale: there is no data to migrate (legacy tables are empty), no external consumers, and Core Rule #5 of project-direction explicitly favors removing dead paths over compatibility shims.

### D8. Gift/Loan tool fix pattern

Existing `tools/gifts.py` and `tools/loans.py` pass `entity_id=None`. The fix adopts the `tools/notes.py` pattern verbatim:

```python
from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
...
entity_id = await resolve_contact_entity_id(pool, contact_id)
await store_fact(..., entity_id=entity_id, ...)
```

`resolve_contact_entity_id()` already handles the NULL case by calling `memory_entity_create()` and backfilling `public.contacts.entity_id` (per `predicate-taxonomy.md` §1.4). The fix is mechanical: 4 call sites in gifts.py (lines 95, 161 — likely two more in `gift_update_status`), 4 in loans.py (lines 143, 212 — likely two more in `loan_settle`).

`gift_update_status` and `loan_settle` use supersession by subject key (`contact:{contact_id}:gift:{slug}`). The supersession key change is independent of the entity_id fix; subject keys are unchanged.

### D9. Frontend route placement

Entity detail page at `/butlers/relationship/entities/:id` (no `-dev` suffix; `-dev` is environment-specific and applies only to the live URL prefix, not the spec). The path lives under the relationship butler's frontend tree because the tab data is relationship-scoped. The memory butler's existing entity detail page at `/entities/:id` (per `entity-identity` §"Entity info type registry") remains the *credential and identity* surface for entities; the relationship-scoped page is the *activity* surface. Both can coexist, deep-linked from each other.

## Risks / Trade-offs

- **[Risk]** `_log_activity()` is called from more sites than initially audited (verified: 20+ across `tools/{notes,interactions,gifts,loans,dates,contacts,contact_info}.py` and tests). Removing all callers is mechanical but tedious; missing one means a residual write to the deleted module. → **Mitigation**: cruft-removal tasks include a `rg -n "_log_activity\|from.*feed.*import\|tools\.feed"` audit gate that must return zero hits before the migration runs.
- **[Risk]** `frontend/src/hooks/use-contacts.ts` exports more than the five tab hooks we're removing; over-aggressive deletion could break unrelated views. → **Mitigation**: only delete the five named hooks; leave the rest of the file intact.
- **[Risk]** Tests in `roster/relationship/tests/test_spo_tools.py` and `test_contact_info.py` reference `_log_activity` and `activity_feed` directly. → **Mitigation**: cruft-removal task explicitly searches `tests/` and either deletes obsolete tests or rewrites them against the new endpoints.
- **[Risk]** Two entity detail pages (memory butler's identity page, relationship butler's activity page) confuse the user. → **Mitigation**: deep-link both ways with explicit affordances ("View identity →" / "View activity →"); flag for follow-up if usability complaints arise.
- **[Risk]** Orphan gift/loan facts with malformed `subject` strings (not matching `contact:{uuid}:...`) skip backfill and remain invisible. → **Mitigation**: the backfill step logs both the fix count and the skip count; if skips > 0, the migration aborts unless explicitly forced.
- **[Trade-off]** Eliminating `activity_feed` means events that aren't first-class facts (e.g., "contact archived", "contact merged") have no canonical surface. → **Acceptance**: these are dashboard events, not domain facts. If we need a system audit log later it should be its own thing, not a relationship-domain table.
- **[Trade-off]** No cursor pagination means a very long-lived entity (10k+ facts) gets slow `OFFSET` queries. → **Acceptance**: not a current problem; revisit if it becomes one.

## Migration Plan

1. **Fix gift/loan entity_id resolution** (`tools/gifts.py`, `tools/loans.py`). Add tests verifying entity_id is non-null after each write. No DB churn.
2. **Add new entity-keyed API endpoints** alongside the legacy contact-keyed endpoints. Legacy continues to return empty; new endpoints return real data. No DB churn yet.
3. **Land frontend `EntityDetailView` and route**, hooks for entity-scoped tabs. Strip the tab block from `ContactDetailView.tsx`. Repoint and promote the entity link.
4. **Cruft removal in one migration + commit**:
   - Audit gate: `rg -n "FROM (notes|interactions|gifts|loans|activity_feed)\b" src/ roster/ tests/` and `rg -n "_log_activity|tools/feed\.py|activity_feed"` must return zero non-test hits (test hits handled in 4d).
   - Alembic migration: backfill orphan gift/loan facts, verify zero rows in legacy tables, drop them.
   - Code commit: delete legacy router endpoints (`router.py:1846-2009`), legacy Pydantic models, frontend hooks, all `_log_activity()` call sites, `tools/feed.py` itself.
   - Test commit: delete obsolete tests; fix any cascading test failures.
5. **No staged rollout** — the user runs a single instance; cutover is atomic per release.

**Rollback**: `alembic downgrade` recreates the empty legacy tables; revert the API/frontend commit. The new fact-backed write paths remain (notes/interactions are unaffected; gifts/loans now write with entity_id, which is harmless and required by `butler-relationship` spec). The orphan-fact backfill is not reversed (it was a correctness fix, not a behavior change). Recovery time: < 5 minutes if needed.

## Open Questions

- **Q1.** `dunbar_tier_override` events on Timeline — **Resolved**: include. Update D1 mapping table accordingly. The user can audit override history from the same surface as everything else.
- **Q2.** When an entity has multiple contacts (e.g., one Telegram, one email), should interaction tabs deduplicate by `(predicate, valid_at)` if the same interaction was logged via two channels? **Resolved**: no deduplication. Show both rows; let the entity merge engine handle deduplication if needed. Spec includes a scenario asserting this.
- **Q3.** Should gift/loan facts retire automatically on `status='given'` / `settled=true` (no longer shown on the tab) or stay visible? **Resolved**: stay visible with a settled/done badge — historical context is valuable.
- **Q4.** Out-of-scope but flagged: typed contact-to-contact `relationships` array on the contact detail response should eventually re-anchor to entity edge facts (`predicate='knows'`, `'parent_of'`, etc. per `entity-identity` §"Typed relationships between entities via edge facts"). Tracked separately.
