# Epic 00: Dispatch Redesign Doctrine + Spec Landing - Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-ve5re.7
Reporter: Beads Worker (automated reconciliation)

---

## 1. Decision Gates

### Gate A: Butler Detail Tier-2 Hero Contract (bu-rx6c2)

**Status:** CLOSED

**Chosen option:** A2 - Absorb Dispatch's status pills and ActionBar buttons into the `<Page>` shell's `actions` slot per `dashboard-butler-management/spec.md:96-99`. Tier-1 header retains title and breadcrumbs; no Tier-2 identity card added. Identity stays in the Overview tab card per `spec.md:166-168`.

**Owner:** uniquosity@gmail.com
**Timestamp:** 2026-05-09

**Close reason:** "Gate A resolved: A2 - absorb Dispatch's status pills + ActionBar buttons into the `<Page>` shell's actions slot per dashboard-butler-management/spec.md:96-99. Tier-1 header retains title + breadcrumbs; no Tier-2 identity card added. Identity stays in the Overview tab card per spec.md:166-168. Decided by user uniquosity@gmail.com on 2026-05-09."

---

### Gate B: Butler Detail Tab Vocabulary (bu-41p8z)

**Status:** CLOSED

**Chosen option:** B2 - Operator/resident mode toggle. Operator mode shows all 10 spec-mandated base tabs (Overview, Sessions, Config, Skills, Schedules, Trigger, MCP, State, CRM, Memory). Resident mode shows the narrow Dispatch vocabulary (Overview, Activity, Logs, Approvals, Spend, Config, Memory). Default is resident; persisted via `localStorage` key `butlers:detail:mode`. Deep-linking to a non-resident tab auto-promotes mode. Conditional tabs (switchboard +Routing Log/Registry, health +Health, general +Collections/Entities, education +Reviews) preserved across both modes.

**Owner:** uniquosity@gmail.com
**Timestamp:** 2026-05-09

**Close reason:** "Gate B resolved: B2 - operator/resident mode toggle. [...] Decided by user uniquosity@gmail.com on 2026-05-09."

---

## 2. Doctrine Edits

All three doctrine edits were committed in PR #1485, merged 2026-05-09 (commit `fed8bb44`). Total diff: 32 lines added across 2 files (within the <100 target).

### Edit A: No Tier-2 Hero on Workspace-Grade Record Pages

**File:** `about/heart-and-soul/design-language.md`
**Anchor:** Near line 233 (within the Settled Direction section, under the composition bullet)

**Summary:** Added a new rule-bullet stating that workspace-grade record pages (butler detail, contact detail, conversation detail) MUST NOT carry a Tier-2 page-level hero block. Status pills and primary actions belong in the `<Page>` actions slot. The rule explicitly cites Gate A A2 resolution from `bu-rx6c2` and prohibits re-litigation by future redesigns.

**Alignment check:** PASS - Correctly encodes A2 (no separate Tier-2; identity stays in Overview tab; actions go to the `<Page>` shell).

### Edit B: Operator vs Resident Mode Distinction

**File:** `about/heart-and-soul/design-language.md`
**Anchor:** Near line 487 (new item 4 in the Settled Direction list, before the Hero metric section)

**Summary:** Added a new rule (item 4) codifying the operator/resident mode distinction: workspace-grade record pages may carry high tab counts when the operator surface needs them. Butler detail preserves all 10 spec-mandated base tabs plus the non-spec Models tab in operator mode. Resident mode is a filtered projection of the operator surface, not a replacement. The rule explicitly cites Gate B B2 resolution from `bu-41p8z`.

**Alignment check:** PASS - Correctly encodes B2 (mode toggle; operator has full 10+ tabs; resident is the default narrow view; deep links and conditional tabs preserve the fuller operator surface).

### Edit C: Spec-Before-Mockup Rule

**File:** `about/craft-and-care/review-and-documentation.md`
**Anchor:** New "## Spec discipline" section (line 29)

**Summary:** Added a new section "Spec discipline" with a one-paragraph rule: any UI mockup proposing a tab list, hero block, or panel set not already present in `openspec/specs/dashboard-*` requires either a cited existing capability or a paired spec change before implementation begins. This rule was motivated by the Dispatch mockup that proposed five fictional tabs (Calendar, Memory, Household butler tabs; Anki Decks panel; `pid` field) without backing spec changes.

**Alignment check:** PASS - Closes the loop that admitted the Dispatch mockup with unsupported surfaces.

---

## 3. OpenSpec Changes

All five changes were individually merged to `main` as separate PRs. All five pass `openspec validate --strict` as of this report.

### E2: redesign-butler-detail-no-hero (PR #1486, merged 2026-05-09, commit `b0a57219`)

**Validates:** PASS (`openspec validate redesign-butler-detail-no-hero --strict`)

Resolves the conflict between Dispatch's Tier-2 Hero block on the butler detail page and the existing "No hero slot" rule in `dashboard-butler-management/spec.md:166-168`. Encodes Gate A option A2: the `<Tabs>` block remains the primary page body; Dispatch's status pills and ActionBar buttons migrate to the `<Page>` actions slot rather than a page-level identity tier. The `detail-page-archetype` spec is left untouched (A2 requires no Tier-2 identity-card shape). Proposal cites gate-A bead ID `bu-rx6c2` explicitly.

**Gate citation:** bu-rx6c2 (A2) - PRESENT

### E3: redesign-detail-page-tab-vocabulary (PR #1488, merged 2026-05-09, commit `24f1fe7a`)

**Validates:** PASS (`openspec validate redesign-detail-page-tab-vocabulary --strict`)

Encodes Gate B option B2: operator/resident mode toggle. Resident mode (default) uses the narrow 7-tab Dispatch vocabulary (Overview, Activity, Logs, Approvals, Spend, Config, Memory); operator mode uses the full 10 spec-mandated base tabs. Mode persists in `localStorage` under `butlers:detail:mode`. Deep links to operator-only tabs auto-promote the page to operator mode rather than falling back to Overview. Conditional tabs (switchboard, health, general, education) are preserved across both modes. The non-spec Models tab is treated as operator-only. Proposal cites gate-B bead ID `bu-41p8z` explicitly.

**Gate citation:** bu-41p8z (B2) - PRESENT

### E4: add-butler-process-facts (PR #1487, merged 2026-05-09, commit `ccb54c0a`)

**Validates:** PASS (`openspec validate add-butler-process-facts --strict`)

Rejects the `pid` field proposed by the Dispatch mockup (`pr/overview/butler-detail-data.jsx`) because PIDs are not visible across the Docker container boundary, change on every restart, and carry no actionable information for an operator. Adds four container-introspectable process facts to the Overview tab's process card: `container_name` (derived from `BUTLERS_HOST`), `port` (already on `ButlerSummary`), `registered_duration_seconds` (derived from heartbeat registry `registered_at`/`last_seen_at`), and `config_path` (stable roster-relative path `roster/<name>/butler.toml`). All four fields are derivable from existing infrastructure without database migrations or new endpoints.

**Gate citation:** No gate ID cited (this change is not directly gated by A or B; it addresses a separate cut decision from the epic). No gap: this change is correctly self-contained as an infrastructure cut decision documented in the epic description.

### E5: redesign-detail-tab-overview-card-stack (PR #1492, merged 2026-05-09, commit `2396589f`)

**Validates:** PASS (`openspec validate redesign-detail-tab-overview-card-stack --strict`)

Reorganizes the Butler detail Overview tab into a seven-unit ordered card stack: (1) identity card, (2) process facts card, (3) heartbeat row, (4) module health card, (5) cost card, (6) recent sessions card, (7) eligibility row. Replaces the old recent-notifications feed requirement with a recent-sessions card (no log infrastructure exists). Preserves existing eligibility behaviors (active/quarantined/stale badge semantics, restore mutation, quarantine reason, 24-hour timeline, tooltip labels, 60-second refresh cadence). Every card is pinned to an existing hook or endpoint. Depends on the now-merged `redesign-butler-detail-no-hero` (E2) and `add-butler-process-facts` (E4).

**Gate citation:** Cites sibling OpenSpec changes `redesign-butler-detail-no-hero` and `add-butler-process-facts` (which encode Gate A A2). Indirect gate alignment is correct.

### E6: redesign-butler-list-card-density (PR #1490, merged 2026-05-09, commit `eae29cb2`)

**Validates:** PASS (`openspec validate redesign-butler-list-card-density --strict`)

Adopts the dense Dispatch-style card layout from `pr/overview/butlers-page.jsx:108-198` for the `/butlers` list page. Each card shows: name, status pill, description, port, eligibility chip, and `sessions_24h` as a count or sparkline. Explicitly rejects calendar, memory, and household mock butlers - they are not in the real roster. Preserves existing fleet summary, loading, stale-data error, empty-state, alphabetical sort, and 30-second polling scenarios. The `ButlerSummary` API contract is unchanged: no new fields.

**Gate citation:** No gate ID cited (this change is not directly gated by A or B). Correct: the list page changes are independent of the hero/tab vocabulary decisions.

---

## 4. Cuts Table

The following surfaces from the Dispatch mockup were explicitly rejected and do not appear in any OpenSpec change or implementation epic.

| Surface | Rationale | Revisit Trigger |
|---|---|---|
| Calendar butler tab | No `roster/calendar/` exists. Calendar is a chronicler face at `src/butlers/api/routers/calendar_workspace.py:40`, not a standalone butler. Introducing a Calendar tab on the butler detail page would reference a non-existent butler. | A `roster/calendar/` directory with `butler.toml` lands and a standalone calendar butler is proposed. |
| Memory butler tab | No `roster/memory/` exists. Memory is a module (`src/butlers/modules/memory/__init__.py`, 18 tools), not a standalone butler. Deferred to a global `/memory` route over `public.memory_catalog`. | A standalone memory butler is proposed with its own `roster/memory/butler.toml`. |
| Household butler tab | Out of scope. The household domain is partially covered by `roster/home/`. Bundling it into the Dispatch redesign would require a net-new butler RFC. | A separate "new butler" RFC for household is filed and approved as a first-class butler in the roster. |
| Anki "Decks" panel | `roster/education/api/router.py:28-30,142-215` exposes `mind_maps`, `frontier`, `mastery`, `spaced_repetition_pending_reviews`. There are no decks server-side. The Dispatch mockup invents a deck schema; rejected and recast as a "Reviews" tab over existing endpoints in Epic 06 (bu-3cujw). | Recast as "Reviews" tab in Epic 06 (bu-3cujw); this cut is already resolved by the epic. |
| `pid` field | Process IDs are not visible across the Docker container boundary. The dashboard runs in a separate container from `butlers-up`; PIDs change on every restart and carry no actionable operator information. Replaced by `container_name`, `port`, `registered_duration_seconds`, and `config_path` in E4 (`add-butler-process-facts`). | A cross-container process introspection API ships (e.g., a sidecar that exposes `/proc` data over HTTP). |
| Log tail tab | No log shipping infrastructure, log storage, or log API exists. This is a multi-week project of its own. The Logs tab in the Dispatch mockup has no backend support. | A logging spec lands (spec for log ingestion, storage, and retrieval API) and is approved. |

---

## 5. Open Questions and Gaps

### No blockers found

All acceptance criteria for Epic 00 are met:

1. Gate A (bu-rx6c2): CLOSED with option A2. Gate B (bu-41p8z): CLOSED with option B2.
2. All five OpenSpec changes pass `openspec validate --strict`.
3. Three doctrine edits are committed and pushed to `origin/main` (commit `fed8bb44`, PR #1485).
4. This report exists at `pr/dispatch-redesign-epic-00-report.md` with the cuts table.
5. No newly discovered gaps requiring child beads under bu-ve5re.

### Minor note: E4 and E6 do not cite gate IDs in their proposals

This is not a coverage gap - both changes are correctly self-contained and address non-gated cut decisions (the `pid` rejection and the list card density). The gate decisions (A2 and B2) are encoded in E2 and E3 respectively, which are the spec changes that directly encode those gate resolutions. The E4/E6 omissions do not affect correctness or traceability.

### Downstream epics unblocked

The following downstream epics are now unblocked by Epic 00:

- bu-sfeuw (Epic 01): Harden Page detail shell primitives
- bu-bm58r (Epic 02): System runtime summary card
- bu-8bayc (Epic 03): Butler detail tab shell + Gate-B vocabulary
- bu-insd4 (Epic 05): Butler list page denser cards
- bu-3cujw (Epic 06): Education Reviews tab
- bu-dg5qc (Epic 07): Bespoke tab inventory

---

## 6. OpenSpec Validate Output Summary

```
redesign-butler-detail-no-hero:      PASS (openspec validate --strict)
redesign-detail-page-tab-vocabulary: PASS (openspec validate --strict)
add-butler-process-facts:            PASS (openspec validate --strict)
redesign-detail-tab-overview-card-stack: PASS (openspec validate --strict)
redesign-butler-list-card-density:   PASS (openspec validate --strict)
```

---

## 7. Verification Summary

| Check | Status |
|---|---|
| bu-rx6c2 (Gate A) closed | PASS |
| bu-41p8z (Gate B) closed | PASS |
| Doctrine edit A (no Tier-2 hero rule) present in design-language.md | PASS |
| Doctrine edit B (operator/resident mode) present in design-language.md | PASS |
| Doctrine edit C (spec-before-mockup) present in review-and-documentation.md | PASS |
| E2 (redesign-butler-detail-no-hero) validates | PASS |
| E3 (redesign-detail-page-tab-vocabulary) validates | PASS |
| E4 (add-butler-process-facts) validates | PASS |
| E5 (redesign-detail-tab-overview-card-stack) validates | PASS |
| E6 (redesign-butler-list-card-density) validates | PASS |
| Cuts table documents all 6 rejected surfaces | PASS |
| No new gaps requiring child beads | PASS |
| Quality gates (ruff lint + format) | PASS |
