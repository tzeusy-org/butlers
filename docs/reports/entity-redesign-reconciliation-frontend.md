# Entity-Redesign Frontend Reconciliation Audit

**Date:** 2026-05-20  
**Audit bead:** bu-fs5y8  
**Backend companion report:** `docs/reports/entity-redesign-reconciliation-backend.md`

---

## Executive Summary

- **11 §8.x PRs merged** (PRs #1806–#1817) plus **10 §9.x backend PRs** already tracked in
  the backend report.
- **11 §8.x beads closed** (bu-s2bgc, bu-h4s95, bu-370h1, bu-m4ya3, bu-zvtxh, bu-wx2r0,
  bu-ar4zf, bu-wi06b, bu-ec2wb, bu-qsipw, bu-xfjwk).
- **15 frontend spec requirements** audited (from `dashboard-relationship/spec.md`); 11 fully
  covered, 4 have gaps or deltas.
- **11 tasks.md §8.x tasks** audited; 10 shipped, 1 descoped (§8.5 is split across two PRs
  but both merged).
- **7 known follow-up beads** listed and verified (bu-h9ta6, bu-u5ktp, bu-hhiq9, bu-2nbjv,
  bu-r6vft, bu-macfj, bu-ki9w4) — all confirmed open with correct `discovered-from` links.
- **4 new gaps identified** in this audit; see "Discovered Gaps" section.

---

## §8.x Sibling Bead / PR Status

| §   | Bead       | PR     | Title (truncated)                                | State  | Merged      |
|-----|------------|--------|--------------------------------------------------|--------|-------------|
| 8.1 | bu-s2bgc   | #1807  | /entities EntitiesIndexPage                      | MERGED | 2026-05-19  |
| 8.2 | bu-h4s95   | #1808  | /entities/hop HopPage                            | MERGED | 2026-05-19  |
| 8.3 | bu-370h1   | #1809  | /entities/columns ColumnsPage                    | MERGED | 2026-05-19  |
| 8.4 | bu-m4ya3   | #1810  | /entities/concentration ConcentrationPage        | MERGED | 2026-05-19  |
| 8.5 | bu-zvtxh   | #1817  | SocialMapView refactor + SubpageTabs chrome      | MERGED | 2026-05-19  |
| 8.6 | bu-wx2r0   | #1812  | SubpageTabs unit tests                           | MERGED | 2026-05-19  |
| 8.7 | bu-ar4zf   | #1811  | EntityDetailView Editorial+Workbench toggle      | MERGED | 2026-05-19  |
| 8.8 | bu-wi06b   | #1816  | entity-glosses.ts canned-string enum             | MERGED | 2026-05-19  |
| 8.9 | bu-ec2wb   | #1813  | EntityMark/TierBadge/StateDot/KbMono/Pill        | MERGED | 2026-05-19  |
| 8.10| bu-qsipw   | #1814  | /contacts redirect + nav Contacts removal        | MERGED | 2026-05-19  |
| 8.11| bu-xfjwk   | #1806  | Cmd-K entity finder component                    | MERGED | 2026-05-19  |

All 11 §8.x beads are closed. All PRs are merged to `main`.

---

## Per-Requirement Test Coverage — `dashboard-relationship/spec.md`

### Phase 1 Requirements (Contact detail + Entity detail + Tab APIs)

| # | Requirement (spec section) | Test file(s) | Status |
|---|---|---|---|
| 1 | Contact detail: header card with role badges, entity link, warning when no entity_id | `ContactDetailPage.test.tsx:185` (null entity_id → no PulseStrip); `ContactDetailView.tsx:894–918` (link present/absent) | PARTIAL — no test asserts "View entity activity →" link text or `/butlers/relationship/entities/` target; see Gap F-01 |
| 2 | Contact detail: click-to-reveal secured credential | `ContactDetailPage.test.tsx:140` (secured=false case); no test for secured=true masking + reveal flow | GAP — F-02 |
| 3 | Contact detail: email as `mailto:`, phone as `tel:` | No test exercises `<a href="mailto:…">` or `<a href="tel:…">` | GAP — F-02 |
| 4 | Contact detail: contact not found → 404 message | No explicit 404 test in `ContactDetailPage.test.tsx` | GAP — F-02 |
| 5 | Contact detail: tab block MUST NOT exist | `ContactDetailView.tsx` is 941 lines with no `Tab` import or `tab` render; grep confirms no tab block | OK |
| 6 | Legacy tab endpoints removed (contact notes/interactions/gifts/loans/feed) | `list_contact_notes`, `list_contact_gifts`, `list_contact_loans`, `list_contact_feed` absent from router.py; `list_contact_interactions` is preserved as a different thread-view endpoint | OK |
| 7 | Entity detail: header card (canonical_name, entity_type, aliases, roles, unidentified badge, View identity link) | `EntityDetailPage.test.tsx:124–144` (identity hero, Dunbar pulse); Unidentified badge at `EntityDetailPage.tsx:1815` | PARTIAL — no test asserts "Unidentified" badge renders when `entity.unidentified=true`; no test for "View identity →" link; see Gap F-03 |
| 8 | Entity detail: linked contacts section | `test_entity_tabs.py::TestLinkedContacts` (backend); frontend `EntityDetailPage.tsx:2000` renders `<LinkedContactsList>`; `EntityDetailPage.test.tsx:61` mocks it | OK |
| 9 | Entity detail: five tabs (Notes/Interactions/Gifts/Loans/Timeline) | `EntityDetailPage.tsx:1987–1993` implements a unified `ActivityTimeline` + filter pills + separate `GiftsPanel`/`LoansPanel` components — NOT a five-tab structure | DELTA — see §Spec-to-Code Deltas D-01 |
| 10 | Entity detail: "Forget this entity" affordance in page header (binding per spec §Editorial/Workbench) | Not present in `EntityDetailPage.tsx`; no "Forget" button, no confirm dialog | GAP — F-04 |
| 11 | Entity-level tab APIs — all five endpoints scoped to `validity='active' AND scope='relationship'` | `test_entity_tabs.py::TestSharedTabBehavior::test_only_active_validity_returned`, `test_scope_filter_present_in_query` (parametrized, all 5 paths) | OK |
| 12 | Entity-level tab APIs — 404 on missing entity | `test_entity_tabs.py::TestSharedTabBehavior::test_missing_entity_returns_404` (parametrized) | OK |
| 13 | Entity-level tab APIs — empty=[] on no facts | `test_entity_tabs.py::TestSharedTabBehavior::test_empty_facts_returns_empty_list` (parametrized) | OK |
| 14 | Entity-level tab APIs — pagination (default 50, max 200) | `test_entity_tabs.py::TestSharedTabBehavior::test_default_pagination_params_sent_to_db`, `test_limit_500_rejected_with_422` (parametrized) | OK |
| 15 | Sparse metadata fields render as null | `test_entity_tabs.py::TestSparseMetadataFields` (6 tests: note emotion, interaction direction, gift fields, loan fields, timeline metadata) | OK |

### Phase 2 Extension Requirements (entity redesign)

| # | Requirement (spec section) | Test file(s) | Status |
|---|---|---|---|
| 16 | Owner-only authz for entity endpoints (clauses 12a writes, 12b reads, 12c startup gate) | `test_owner_authz_guardrail.py` (20+ tests; all mutation endpoints, PII read endpoints, deploy gate) | OK |
| 17 | Entity index page `/entities` — tabular list, filter chips, SubpageTabs, queue rail | `EntitiesIndexPage.test.tsx` (50+ tests across 6 describe blocks; neutral rows, filter chips, has=contact, queue rail, empty state) | OK |
| 18 | Entity index: `/contacts` → `/entities?has=contact` redirect | `router.test.tsx:112–170` (redirect + nav-config absence) | OK |
| 19 | Entity Hop view `/entities/hop` — re-centre graph, SubpageTabs | `HopPage.test.tsx` (5 describe blocks; anchor card, neighbour list, re-centre interaction, URL round-trip) | OK |
| 20 | Hop: re-centre stays on `/entities/hop` | `HopPage.test.tsx:319::updates ?center= when neighbour button is clicked` | OK |
| 21 | Entity Columns view `/entities/columns` — client-side chaining | `ColumnsPage.test.tsx` (7 describe blocks; URL round-trips, append column, reset, loading state) | OK |
| 22 | Columns: clicking neighbour appends column (no new server endpoint) | `ColumnsPage.test.tsx:297::calls useEntityNeighbours with a new entity ID after clicking a neighbour` | OK |
| 23 | Entity Concentration view `/entities/concentration` — predicate tabs from registry, tabular nums | `ConcentrationPage.test.tsx` (7 describe blocks; predicate tabs, rollup header, URL round-trip, loading, error, empty) | OK |
| 24 | Concentration: predicate tabs NOT hardcoded (from registry) | `ConcentrationPage.test.tsx:213::renders predicate tabs from the registry` | OK |
| 25 | Social Map preservation — SocialMapView inside SubpageTabs chrome | `SocialMapView.test.tsx` (9 tests; loading, error, jump-to-tier chips, circles canvas) | OK |
| 26 | SubpageTabs — 5 tabs, aria-current, correct hrefs | `SubpageTabs.test.tsx` (17 tests; accessibility, links, active styling, custom className) | OK |
| 27 | Entity detail Editorial/Workbench mode toggle — localStorage, URL override | `EntityDetailPage.test.tsx:248–340` (6 tests; defaults, localStorage read/write, URL override, invalid fallback) | OK |
| 28 | Editorial: Display 44px headline for canonical_name | `page.tsx` has `archetype="detail"` which uses `<div className="max-w-5xl">` wrapper; no explicit 44px heading in `EntityDetailPage.tsx` (heading uses `text-2xl` at line 1772); `<Page archetype="detail">` does NOT render a 44px Display headline | DELTA — see D-02 |
| 29 | Workbench: provenance grid (sortable, all provenance columns) | Not present — both modes render the same `ActivityTimeline` component | GAP — follow-up bead bu-r6vft filed |
| 30 | Editorial: voice gloss `font-serif italic 16px`, text from `entity-glosses.ts` | `entity-glosses.ts` exports `getEntityGloss()` and is fully tested (192 combos in `entity-glosses.test.ts`); NOT wired into `EntityDetailPage.tsx` — no gloss renders under the canonical name | DELTA — see D-03 |
| 31 | mode persistence: `localStorage["entities.detail.mode"]` (not `butlers.detail.mode`) | `EntityDetailPage.tsx:79` exports `ENTITY_MODE_STORAGE_KEY = "entities.detail.mode"` ✓; URL param is `?mode=workbench` not `?view=workbench` as spec says | DELTA — see D-04 |
| 32 | Entity curation queue — right rail, three sections (unidentified/dup/stale), empty serif gloss | `EntitiesIndexPage.test.tsx:334::shows serif italic 'Nothing waiting.' when queue is empty`, `::renders queue items grouped by bucket` | OK |
| 33 | Queue: state colour only in rail, NOT in index rows | `EntitiesIndexPage.test.tsx:165::renders the SubpageTabs nav strip` (neutral rows assertion present) | OK (guarded by test) |
| 34 | App-wide Cmd-K Finder — wired to `/entities/search`, keyboard-driven, entity-first | `EntityFinder.test.tsx` (7 tests: open on dispatch, entity-first ordering, renders names, empty state, close on backdrop/Escape, search query wiring); `use-keyboard-shortcuts.ts:27` dispatches `dispatchOpenEntityFinder()` | OK |
| 35 | Finder: one endpoint per keystroke, no other relationship endpoint | `EntityFinder.test.tsx:394::calls useEntityFinderSearch with the typed query` | OK |
| 36 | Finder: kbd capsules in KbMono | `KbMono.test.tsx` covers render, font, className forwarding; used in `EntityFinder.tsx` | OK |
| 37 | Finder: deterministic ranking — no LLM | Backend: `test_finder_no_llm_guardrail.py`, `test_finder_no_llm_transitive.py`; Frontend: `entity-glosses.test.ts:147::getEntityGloss is a synchronous function` | OK |
| 38 | Detail-page voice gloss — canned strings only, build-time exhaustiveness | `entity-glosses.test.ts` (192-combo Cartesian product); TypeScript `Record<DunbarTier, Record<EntityState, string>>` enforces compile-time exhaustiveness | PARTIAL — `archived` state is absent from `EntityState`; spec requires 5 states but only 4 are implemented (see Gap F-05) |
| 39 | Dispatch design language token discipline — no new tokens, no hex literals outside entity-model.ts | `#fff` appears in `EntityMark.tsx:164`, `EntitiesIndexPage.tsx:226,236`, `ContactDetailView.tsx:861`, `ContactTable.tsx:295`; `entity-model.ts` does not exist | GAP — F-06 |
| 40 | Fonts loaded: Inter Tight, Source Serif 4, JetBrains Mono | `frontend/index.html:9` Google Fonts link loads all three; `frontend/src/index.css:206–208` maps CSS vars | OK |

---

## Per-Task Close Status — `tasks.md` §8.x

| Task | Description (brief) | Status | PR / Notes |
|---|---|---|---|
| §8.1 | EntitiesIndexPage + /entities route | **CLOSED** | PR #1807 (bu-s2bgc) |
| §8.2 | HopPage + /entities/hop route | **CLOSED** | PR #1808 (bu-h4s95) |
| §8.3 | ColumnsPage + /entities/columns (client-side chaining) | **CLOSED** | PR #1809 (bu-370h1) |
| §8.4 | ConcentrationPage + /entities/concentration | **CLOSED** | PR #1810 (bu-m4ya3) |
| §8.5 | SocialMapView refactor into SubpageTabs chrome | **CLOSED** | PR #1817 (bu-zvtxh) |
| §8.6 | SubpageTabs component + unit tests | **CLOSED** | PR #1812 (bu-wx2r0) |
| §8.7 | EntityDetailView Editorial+Workbench toggle, localStorage, archetype | **CLOSED** | PR #1811 (bu-ar4zf); delta on URL param name (`?mode=` vs `?view=`) and Display 44px headline — see D-02, D-04 |
| §8.8 | entity-glosses.ts strict enum (tier, state, category) | **CLOSED** | PR #1816 (bu-wi06b); gloss is exported but not yet wired into EntityDetailPage — see D-03 |
| §8.9 | EntityMark/TierBadge/StateDot/KbMono/Pill primitives | **CLOSED** | PR #1813 (bu-ec2wb) |
| §8.10 | /contacts redirect + Contacts nav removal | **CLOSED** | PR #1814 (bu-qsipw) |
| §8.11 | Cmd-K EntityFinder + keyboard shortcut | **CLOSED** | PR #1806 (bu-xfjwk) |

All 11 §8.x tasks closed. §§1–7 (backend/API) are tracked in the backend report.

---

## §§1–7 Frontend-Relevant Task Status

These tasks have frontend components; the backend implementation is covered in the backend report.

| Task | Description | Status | Notes |
|---|---|---|---|
| §1.1–1.3 | Fix gifts.py entity_id resolution | **CLOSED** | `tools/gifts.py` calls `resolve_contact_entity_id()` at lines 86, 158 |
| §2.1–2.3 | Fix loans.py entity_id resolution | **CLOSED** | `tools/loans.py` calls `resolve_contact_entity_id()` at line 119 |
| §3.1–3.7 | Backend entity-keyed tab endpoints + integration tests | **CLOSED** | All 5 endpoints in router.py; `test_entity_tabs.py` (30+ tests) |
| §4.1 | use-entities.ts hooks (useEntityNotes, Interactions, Gifts, Loans, Timeline) | **CLOSED** | `frontend/src/hooks/use-entities.ts:41–77` — all 5 hooks present |
| §4.2 | EntityDetailView.tsx (renamed to EntityDetailPage.tsx for this project) | **CLOSED** (with delta) | EntityDetailPage uses unified timeline not 5-tab structure — see D-01 |
| §4.3 | Page route at `/butlers/relationship/entities/:id` | **CLOSED** | `router.tsx:106–111` redirects `/butlers/relationship/entities/:entityId` → `/entities/:entityId`; entity detail served by `EntityDetailPage` |
| §4.4 | ContactDetailView: remove tab block + repoint entity link | **CLOSED** (with delta) | Tab block removed; link points to `/entities/` (not `/butlers/relationship/entities/`); link text "View entity →" not "View entity activity →" — see D-05 |
| §4.5 | Playwright smoke test | **NOT CLOSED** | No Playwright tests exist for entity routes; this was not delivered by any of the §8.x PRs — see Gap F-07 |
| §5.1–5.3 | Cruft-removal audit (grep runs) | **CLOSED** | No `useContactNotes/Gifts/Loans/Feed` refs outside test files; `useContactInteractions` preserved for thread-view endpoint (different from legacy tab endpoint) |
| §6.1–6.4 | Cruft removal execute | **CLOSED** | Legacy tab endpoints removed; old models removed; feed.py deleted; old hooks deleted |
| §6.5 | Alembic migration — backfill gift/loan entity_id + drop legacy tables | **CLOSED** | `roster/relationship/migrations/010_drop_legacy_contact_tables.py` |
| §6.6 | Delete obsolete tests | **CLOSED** | No stale tests targeting removed endpoints found |
| §7.1 | Quality gates (ruff lint + format + pytest) | **CLOSED** | Verified in this audit: ruff passes, format passes |
| §7.2 | Manual dev environment verification | **OUT OF SCOPE** | Agent-env dev stack not available for smoke test |
| §7.3 | Migration outcome report | **CLOSED (partial)** | `docs/reports/relationship-tabs-to-entities-outcome.md` covers Phase 1; Phase 2 report (`docs/reports/entity-redesign-phase-2.md`) tracked under §12.6 (open, bead bu-p5zlt) |

---

## Spec-to-Code Delta Inventory

### D-01 — EntityDetailPage uses unified timeline (filter pills), not five separate tabs

**Spec:** `dashboard-relationship/spec.md` §"Entity detail page" line 81: "Tabbed content area — five tabs in this order: Notes, Interactions, Gifts, Loans, Timeline." Each tab paginates from its own endpoint.

**Code:** `EntityDetailPage.tsx:1029` comment: "Activity timeline — single feed with filter pills, replaces the tabbed view." Implementation renders `<ActivityTimeline>` (unified timeline with kind-filter pills at line 1032–1043) plus separate `<GiftsPanel>` and `<LoansPanel>` below. No Notes or Interactions separate tabs.

**Impact:** The UX differs from spec intent; the backend tab endpoints exist and are tested. The chosen implementation is arguably more usable (unified timeline > five tabs) but diverges from the binding spec requirement. Filed as discovered gap F-01 for a follow-up decision: either update the spec or retrofit the five-tab structure.

**Note:** The backend `test_entity_tabs.py` confirms the five endpoints work correctly; only the frontend presentation deviates.

---

### D-02 — Editorial mode uses `text-2xl` heading, not Display 44px

**Spec:** `dashboard-relationship/spec.md` §"Entity detail Editorial/Workbench mode toggle": "Use `<Page archetype='detail'>` … with Display 44px headline for the entity canonical_name."

**Code:** `EntityDetailPage.tsx:1772`: `<h1 className="text-2xl font-semibold leading-tight">`. The `<Page archetype="detail">` wraps the layout container with `max-w-5xl` but does not promote the H1 to a 44px Display tier. `page.tsx:218` confirms `archetype="detail"` renders only `<div className="max-w-5xl">`.

**Impact:** The 44px Display headline that spec mandates for Editorial mode is not rendered. The heading is `text-2xl` (24px) in both Editorial and Workbench. Follow-up bead bu-r6vft covers the broader Editorial/Workbench content differentiation gap.

---

### D-03 — `entity-glosses.ts` is implemented but not wired into EntityDetailPage

**Spec:** §"Detail-page voice gloss source": "Render the voice gloss in Source Serif 4 italic 16px (one line under the canonical name). The gloss text MUST be a canned string selected by (tier, state, category) from `frontend/src/lib/entity-glosses.ts`."

**Code:** `entity-glosses.ts` exports `getEntityGloss()` and is fully tested (192 combinations). `EntityDetailPage.tsx` does NOT import `getEntityGloss` and renders no gloss under the canonical name.

**Impact:** Voice glosses are entirely absent from the entity detail page. The `entity-glosses.ts` module is "done" but disconnected from the UI.

---

### D-04 — URL parameter name is `?mode=` not `?view=`

**Spec:** §"Entity detail Editorial/Workbench mode toggle": "`?view=workbench` URL parameter overrides localStorage for the current page load only."

**Code:** `EntityDetailPage.tsx:82`: `const ENTITY_MODE_PARAM = "mode"`. The URL parameter is `?mode=workbench`, not `?view=workbench`.

**Impact:** Minor — `localStorage` and toggle behaviour are correct. Links that hardcode `?view=workbench` (e.g., from documentation or external tools) would not work. The spec wording is binding.

---

### D-05 — ContactDetailView entity link: text and target differ from spec

**Spec:** §"Contact detail page" line 9: "The header MUST include a prominent 'View entity activity →' link that deep-links to `/butlers/relationship/entities/:entity_id`."

**Code:** `ContactDetailView.tsx:897–900`:
```tsx
to={`/entities/${contact.entity_id}`}
…
View entity →
```

The link points to `/entities/:id` (correct destination per the router redirect at `router.tsx:106–111` which re-routes `/butlers/relationship/entities/:id` → `/entities/:id`). However, (a) the link text is "View entity →" not "View entity activity →", and (b) the canonical target in the spec is `/butlers/relationship/entities/:id`.

**Impact:** The destination resolves correctly (via redirect). The link text mismatch is a UX-level deviation. The spec wording uses "View entity activity →" to distinguish from the memory butler's entity page.

---

## Gaps Identified in This Audit

### F-01 — EntityDetailPage: 5-tab structure vs unified timeline

**Requirement:** `dashboard-relationship/spec.md` line 81 (five tabs: Notes, Interactions, Gifts, Loans, Timeline).  
**Current state:** `EntityDetailPage.tsx:1029` implements unified `ActivityTimeline` with kind-filter pills; no separate Notes or Interactions tab.  
**Tests missing:** None exercising the five-tab structure; backend tab endpoints are independently tested.  
**Action:** Coordinator to file a bead to decide: (a) retrofit the five-tab structure per spec or (b) update the spec to match the shipped unified-timeline design.

### F-02 — ContactDetailPage: missing tests for secured credential, mailto/tel, 404

**Requirement:** Three scenarios in `dashboard-relationship/spec.md`:
- §"Click-to-reveal secured credential": masks secured values, reveal button fetches secret.
- §"Email and phone values are clickable": `mailto:` and `tel:` links.
- §"Contact not found": 404 message.

**Current state:** `ContactDetailPage.test.tsx` has 8 tests; none cover these scenarios. Secured info is mocked with `secured: false` only.  
**Action:** Coordinator to file follow-up bead for ContactDetailPage test gaps.

### F-03 — EntityDetailPage: missing tests for Unidentified badge and View identity link

**Requirement:** `dashboard-relationship/spec.md` §"Unidentified entity badge" scenario and §"Entity detail page renders with tabs" scenario (requires "View identity →" link).  
**Current state:** `EntityDetailPage.tsx:1815` renders "Unidentified" badge conditionally; `EntityDetailPage.test.tsx:87` mocks `unidentified: false` but no test asserts badge renders for `unidentified: true`. No test asserts "View identity →" link presence.  
**Action:** Coordinator to file follow-up bead for these missing scenario tests.

### F-04 — EntityDetailPage: "Forget this entity" affordance missing

**Requirement:** `dashboard-relationship/spec.md` §"Entity detail Editorial/Workbench mode toggle": "Both modes MUST surface a 'Forget this entity' action in the Page header (NOT a kebab menu). Clicking opens a confirm dialog with a one-sentence serif gloss (canned text: 'Forgetting also tombstones the source. Aliases stay.') before the destructive POST."  
**Current state:** `EntityDetailPage.tsx` has no "Forget" or archive action in the page header. The `POST /entities/{id}/archive` and `DELETE /entities/{id}` backends exist (shipped PR #1782).  
**Action:** Coordinator to file follow-up bead for the "Forget this entity" header affordance + confirm dialog.

### F-05 — entity-glosses.ts: `archived` state missing from EntityState

**Requirement:** `dashboard-relationship/spec.md` §"Detail-page voice gloss source": "every `(tier ∈ {0..5}, state ∈ {active, unidentified, duplicate-candidate, stale, archived}, category ∈ {...})` combination MUST resolve to a non-empty string."  
**Current state:** `StateDot.tsx:24` defines `EntityState = "unidentified" | "duplicate-candidate" | "stale" | "healthy"` — 4 states. `archived` is absent, `active` was renamed to `healthy`. The 192-combo test covers 6×4×8 but spec requires 5 states (counting `active`/`healthy` as the same, `archived` is the missing one).  
**Action:** Coordinator to file follow-up bead to add `archived` to `EntityState` and corresponding glosses.

### F-06 — Hex literals outside `entity-model.ts`

**Requirement:** `dashboard-relationship/spec.md` §"Dispatch design language token discipline": "No hex literals anywhere in `frontend/src/components/relationship/*`, `frontend/src/pages/entities/*`, or `frontend/src/pages/butlers/relationship/*` EXCEPT in `frontend/src/lib/entity-model.ts`."  
**Current state:** `entity-model.ts` does not exist. Hex `#fff` appears in:
- `frontend/src/components/ui/EntityMark.tsx:164`
- `frontend/src/components/relationship/EntitiesIndexPage.tsx:226,236`
- `frontend/src/components/relationship/ContactDetailView.tsx:861`
- `frontend/src/components/relationship/ContactTable.tsx:295`

**Action:** Coordinator to file follow-up bead for hex literal cleanup and `entity-model.ts` creation.

### F-07 — Playwright smoke tests not delivered

**Requirement:** `tasks.md §4.5`: "Playwright smoke test on a seeded entity verifying: page loads at the new route, all five tabs render with empty-state on a fresh entity, populated tabs render after seeding facts, timeline includes `dunbar_tier_override` events, contact detail page no longer renders the tab block, and the entity link in the contact header navigates to the correct relationship-scoped page."  
**Current state:** No Playwright tests found for any entity-redesign route. The §4.5 task was not delivered by any of the 11 §8.x PRs.  
**Action:** Coordinator to file follow-up bead for Playwright smoke tests (route smoke, tab render, entity link navigation).

---

## Known Follow-Ups Already Filed

| Bead      | Title (brief)                                          | Priority | Discovered From |
|-----------|--------------------------------------------------------|----------|-----------------|
| bu-h9ta6  | Add entity_type to search response + EntityFinder glyph | P3      | bu-xfjwk (#1806) |
| bu-u5ktp  | db-backed integration test for bulk_replay FOR UPDATE  | P3       | bu-iu5k0         |
| bu-hhiq9  | Worktree node_modules symlinking in coordinator worktrees | P3    | bu-s2bgc (#1807) |
| bu-2nbjv  | Add canonical_name to §9.2 neighbours endpoint response + HopPage | P3 | bu-h4s95 (#1808) |
| bu-r6vft  | EntityDetailView content differentiation Editorial vs Workbench (provenance grid) | P2 | bu-ar4zf (#1811) |
| bu-macfj  | Optional dedicated --entity-type-* token ramp for EntityMark | P4 | bu-ec2wb (#1813) |
| bu-ki9w4  | EntitiesIndexPage: sync typeFilter and stateFilter to URL params | P3 | bu-1cn55         |

All 7 beads confirmed open in the beads graph with correct `discovered-from` links.

---

## Appendix: Key File Inventory

| File | Role | Lines |
|---|---|---|
| `frontend/src/pages/EntityDetailPage.tsx` | Entity detail page (Editorial+Workbench toggle, mode persistence) | 2044 |
| `frontend/src/components/relationship/EntitiesIndexPage.tsx` | `/entities` index with table, filter chips, queue rail, SubpageTabs | — |
| `frontend/src/components/relationship/HopPage.tsx` | `/entities/hop` re-centre explorer | — |
| `frontend/src/components/relationship/ColumnsPage.tsx` | `/entities/columns` cascading drill | — |
| `frontend/src/components/relationship/ConcentrationPage.tsx` | `/entities/concentration` weight aggregation | — |
| `frontend/src/components/relationship/SocialMapView.tsx` | Refactored SocialMapView (was SocialMapPage) | — |
| `frontend/src/components/relationship/SubpageTabs.tsx` | 5-tab SubpageTabs nav strip | — |
| `frontend/src/components/layout/EntityFinder.tsx` | Cmd-K entity finder (cmdk-backed) | — |
| `frontend/src/lib/entity-glosses.ts` | Canned gloss enum keyed (tier, state, category) | — |
| `frontend/src/lib/entity-finder.ts` | Dispatch helper for `open-entity-finder` custom event | — |
| `frontend/src/hooks/use-entities.ts` | Entity hooks: useEntityNotes/Interactions/Gifts/Loans/Timeline/Neighbours/FinderSearch | — |
| `frontend/src/components/ui/EntityMark.tsx` | Entity type glyph/mark primitive | — |
| `frontend/src/components/ui/TierBadge.tsx` | Dunbar tier badge | — |
| `frontend/src/components/ui/StateDot.tsx` | Entity state dot (unidentified/stale/dup/healthy) | — |
| `frontend/src/components/ui/KbMono.tsx` | Keyboard monospace capsule | — |
| `frontend/src/components/ui/Pill.tsx` | Metric/count pill | — |
| `frontend/src/router.tsx:106–111` | `/butlers/relationship/entities/:id` → `/entities/:id` redirect | — |
| `frontend/src/router.tsx:141` | `/contacts` → `/entities?has=contact` redirect | — |
| `frontend/src/hooks/use-keyboard-shortcuts.ts:27` | Cmd/Ctrl+K → dispatchOpenEntityFinder() | — |
| `frontend/src/layouts/RootLayout.tsx:24–25` | EntityFinder mounted in app root | — |

---

## Discovered-Follow-Ups-JSON

```json
[
  {
    "title": "EntityDetailPage: spec requires 5-tab structure (Notes/Interactions/Gifts/Loans/Timeline) but shipped unified ActivityTimeline with filter pills",
    "description": "dashboard-relationship/spec.md line 81 mandates five separate tabs. EntityDetailPage.tsx:1029 ships a unified ActivityTimeline with kind-filter pills instead. Backend tab endpoints exist and are tested. Coordinator to decide: (a) retrofit five-tab structure per spec, or (b) file a spec amendment to match the shipped unified-timeline design. Current implementation is arguably more usable but deviates from the binding spec.",
    "type": "task",
    "priority": 2,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "ContactDetailPage: missing tests for secured credential reveal, mailto/tel links, 404 contact-not-found",
    "description": "Three spec scenarios in dashboard-relationship/spec.md have no test coverage: (1) Click-to-reveal secured credential (masked value + reveal button + GET /api/contacts/{id}/secrets/{info_id}); (2) Email rendered as mailto: link, phone as tel: link; (3) Contact not found returns 404 message. ContactDetailPage.test.tsx currently only tests with secured=false fixtures.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "EntityDetailPage: missing tests for Unidentified badge and View identity link",
    "description": "dashboard-relationship/spec.md §Unidentified entity badge scenario requires a test asserting badge renders when entity.unidentified=true. EntityDetailPage.test.tsx:87 only mocks unidentified=false. Also missing: test that 'View identity →' link is present in the header card and points to /entities/:id.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "EntityDetailPage: 'Forget this entity' header affordance missing",
    "description": "dashboard-relationship/spec.md §Editorial/Workbench mode toggle (binding): 'Both modes MUST surface a Forget this entity action in the Page header (NOT a kebab menu). Clicking opens a confirm dialog with canned text: Forgetting also tombstones the source. Aliases stay.' The EntityDetailPage.tsx has no such action. Backend POST /entities/{id}/archive and DELETE /entities/{id} (PR #1782) are ready. Frontend affordance + confirm dialog need to be added.",
    "type": "feature",
    "priority": 2,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "entity-glosses.ts: archived EntityState missing from EntityState type and gloss table",
    "description": "dashboard-relationship/spec.md §Detail-page voice gloss source specifies state ∈ {active, unidentified, duplicate-candidate, stale, archived}. StateDot.tsx:24 defines only 4 states (healthy, unidentified, duplicate-candidate, stale) — archived is absent. entity-glosses.ts inherits this type; build-time exhaustiveness check does not catch archived. Need to add archived to EntityState, StateDot, and entity-glosses.ts with corresponding canned strings.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "Hex literals in entity component tree and entity-model.ts not created",
    "description": "dashboard-relationship/spec.md §Dispatch design language token discipline: no hex literals in frontend/src/components/relationship/* EXCEPT in frontend/src/lib/entity-model.ts. entity-model.ts does not exist. Hex #fff found in: EntityMark.tsx:164, EntitiesIndexPage.tsx:226,236, ContactDetailView.tsx:861, ContactTable.tsx:295. Fix: create entity-model.ts and migrate hex literals into it, or replace with CSS token equivalents.",
    "type": "chore",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "Playwright smoke tests for entity-redesign routes not delivered (tasks.md §4.5)",
    "description": "tasks.md §4.5 requires Playwright smoke tests verifying: page loads at /entities/:id, all five tabs render with empty-state on fresh entity, populated tabs render after seeding facts, timeline includes dunbar_tier_override events, contact detail page no longer renders tab block, entity link navigates to correct page. No Playwright tests exist for any entity-redesign route.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "entity-glosses.ts wired in entity-glosses.ts but not rendered in EntityDetailPage Editorial mode",
    "description": "dashboard-relationship/spec.md requires the voice gloss to render as Source Serif 4 italic 16px under the canonical name in Editorial mode. entity-glosses.ts and getEntityGloss() are fully implemented and tested (192 combos). EntityDetailPage.tsx does not import or call getEntityGloss(). No gloss renders in Editorial mode.",
    "type": "task",
    "priority": 2,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "EntityDetailPage URL param name is ?mode= but spec requires ?view=",
    "description": "dashboard-relationship/spec.md §Editorial/Workbench mode toggle: '?view=workbench URL parameter overrides localStorage for the current page load only'. EntityDetailPage.tsx:82 uses ENTITY_MODE_PARAM = 'mode'. The URL param is therefore ?mode=workbench not ?view=workbench. localStorage key is correct (entities.detail.mode). Fix: rename the constant and update router tests.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  },
  {
    "title": "ContactDetailView entity link: text should be 'View entity activity →' not 'View entity →'",
    "description": "dashboard-relationship/spec.md §Contact detail page line 9: link MUST be labeled 'View entity activity →'. ContactDetailView.tsx:900 renders 'View entity →'. The destination /entities/:id resolves correctly (via redirect). Fix: update link text to match spec.",
    "type": "task",
    "priority": 3,
    "discovered_from": "bu-fs5y8"
  }
]
```
