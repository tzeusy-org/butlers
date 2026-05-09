# Gen-1 Reconciliation Report: Epic 07 — Bespoke Tab Audit + Stubs

**Issue:** bu-dg5qc.5
**Date:** 2026-05-10
**Epic:** bu-dg5qc (Epic 07: Bespoke tab audit and conditional stub wiring)

---

## Merged Changes

| Child issue | Commit / PR | Description |
|---|---|---|
| bu-dg5qc.1 | 5b6448bd (cherry-picked) | Bespoke tab inventory: chronicler, finance, general |
| bu-dg5qc.2 | 54a9661f (cherry-picked) | Bespoke tab inventory: health, home, lifestyle, messenger |
| bu-dg5qc.3 | b4ac3c90 (manual merge) | Bespoke tab inventory: qa, relationship, switchboard, travel |
| bu-dg5qc.4 | PR #1506 (merged) | Wire conditional bespoke tab stubs for 5 domain butlers |

---

## Butler Audit Summary (All 12 Real Butlers)

| Butler | Bespoke tab(s) | Decision rationale | Implemented? |
|---|---|---|---|
| chronicler | +Timelines | Unique retrospective timeline model; no base-tab equivalent | Stub (bu-dg5qc.4) |
| education | +Reviews | Course/review rating model; bespoke Reviews panel required | Yes (PR #1505, bu-3cujw.1) |
| finance | +Finances | Personal finance model (transactions, bills, subscriptions) distinct from base Spend tab | Stub (bu-dg5qc.4) |
| general | none | Existing `+Collections` and `+Entities` conditional tabs fully cover the domain | — |
| health | none | Spec-mandated `+Health` conditional tab already IS the bespoke surface | — |
| home | +Devices | Smart-home device inventory, energy, maintenance — no base-tab equivalent | Stub (bu-dg5qc.4) |
| lifestyle | none | No `api/` directory; all domain data in memory module SPO facts | — |
| messenger | none | Infrastructure staffer; no domain content, no API router | — |
| qa | none | Infrastructure staffer; no API router; patrol data in public cross-butler tables | — |
| relationship | +Contacts | Rich personal CRM model (Dunbar tiers, dates, interaction history) | Stub (bu-dg5qc.4) |
| switchboard | none | Two spec-mandated conditional tabs (`+Routing Log`, `+Registry`) are sufficient | — |
| travel | +Trips | Hierarchical trip container model; pre-trip action urgency panel has no base-tab analogy | Stub (bu-dg5qc.4) |

**Total:** 6 butlers with bespoke tabs (5 stubs + 1 implemented). 6 butlers with no bespoke tab warranted.

---

## Implementation Status

### Implemented (full UI)

| Tab | Butler | Component | PR |
|---|---|---|---|
| Reviews | education | `ButlerEducationReviewsTab` (lazy-loaded) | PR #1505 |

### Stubs (conditional tab trigger wired; content placeholder only)

All 5 stubs are in `frontend/src/pages/ButlerDetailPage.tsx`.

| Constant | Tab name | Butler | Trigger guard | Stub message |
|---|---|---|---|---|
| `CHRONICLER_TABS` | `timelines` | chronicler | `showTimelinesTab` | "Timelines coming soon." |
| `FINANCE_TABS` | `finances` | finance | `showFinancesTab` | "Finances coming soon." |
| `HOME_TABS` | `devices` | home | `showDevicesTab` | "Devices coming soon." |
| `RELATIONSHIP_TABS` | `contacts` | relationship | `showContactsTab` | "Contacts coming soon." |
| `TRAVEL_TABS` | `trips` | travel | `showTripsTab` | "Trips coming soon." |

Each stub renders a `TabsContent` element with the correct `value` key. The trigger guards (`showTimelinesTab`, `showFinancesTab`, etc.) are boolean checks on `name === "<butler>"` evaluated inside the component, consistent with the existing `showReviewsTab` pattern.

---

## Follow-Up Beads (Full Bespoke Tab Implementations)

| Bead | Title | Butler | Status |
|---|---|---|---|
| bu-aeg7w | Implement Timelines bespoke tab for chronicler butler | chronicler | in_progress |
| bu-nqepq | Implement Finances bespoke tab for finance butler | finance | open |
| bu-11mug | Implement Devices bespoke tab for home butler | home | open |
| bu-ax5bi | Implement Contacts bespoke tab for relationship butler | relationship | open |
| bu-0eac9 | Implement Trips bespoke tab for travel butler | travel | open |

Each follow-up bead covers the full data-panel implementation: API integration, KPI strip, and indicative panels per the inventory document (`pr/dispatch-redesign-bespoke-inventory.md`).

---

## Spec Coverage

All 12 butlers were inventoried across four audit batches. The audit documents at
`pr/dispatch-redesign-bespoke-inventory.md` record:

- **Tab decision** and **justification** for each butler.
- **Supporting endpoint table** with file:line citations for every backing endpoint.
- **Indicative panel composition** (4-col grid) for all bespoke tabs.
- **Panel sketches** for non-bespoke butlers where an upgrade path exists (health `+Health` tab upgrade, general `+Collections`/`+Entities` tabs).

---

## Gaps

| ID | Description | Severity | Action |
|---|---|---|---|
| GAP-01 | 5 bespoke tabs are stubs only ("coming soon") | Medium | Tracked via follow-up beads bu-aeg7w, bu-nqepq, bu-11mug, bu-ax5bi, bu-0eac9 |
| GAP-02 | lifestyle butler has no `api/router.py` — bespoke tab cannot be backed | Low | Blocked until a lifestyle API surface is added; taste-trend panels sketched in inventory doc |
| GAP-03 | qa butler has no `api/router.py` — patrol/findings data not exposed to dashboard | Low | Future `+QA` bespoke tab sketched in inventory doc; requires new API work |

---

## Ruff Lint

`uv run ruff check src/ tests/ roster/ conftest.py --output-format concise` → **All checks passed**
