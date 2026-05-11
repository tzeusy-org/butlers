## Why

Gate B (bu-41p8z) resolved the operator/resident mode conflict with option B2:
operator/resident mode toggle. That decision was specced by
`redesign-detail-page-tab-vocabulary`. That change enumerated seven resident
base tabs (Overview, Activity, Logs, Approvals, Spend, Config, Memory) and
noted butler-specific conditional tabs (health, switchboard, education, etc.)
as mode-independent appendages.

Several domain butlers already carry a bespoke tab in the current codebase:

| Butler       | Tab key       | Label       | Component                             |
|-------------|---------------|-------------|---------------------------------------|
| chronicler  | timelines     | Timelines   | ButlerChroniclerTimelinesTab          |
| education   | reviews       | Reviews     | ButlerEducationReviewsTab             |
| finance     | finances      | Finances    | ButlerFinanceFinancesTab              |
| health      | health        | Health      | ButlerHealthTab (inline)              |
| home        | devices       | Devices     | ButlerHomeDevicesTab                  |
| relationship| contacts      | Contacts    | ButlerRelationshipContactsTab         |
| travel      | trips         | Trips       | ButlerTravelTripsTab                  |

The switchboard carries two operator-only tabs (Routing Log, Registry) that are
explicitly NOT resident bespoke: they predate the resident vocabulary and serve
operator triage, not resident self-service.

The resident vocabulary redesign (`redesign-detail-page-tab-vocabulary`) landed
with only one bespoke-tab rule: "conditional tabs appended after base tabs,
visible in both modes." That rule is necessary but not sufficient. It does not:

- Limit each butler to exactly one bespoke resident-mode tab.
- Fix the insertion point relative to Memory.
- Specify label conventions.
- Specify discovery mechanism.
- Specify loading contract.
- Specify fallback when the butler is paused or quarantined.
- Explicitly opt switchboard out of resident bespoke.

This change fills that gap before per-butler implementation beads under epic
bu-iuol4 author their panel layouts. The visual contract (Panel grid + KPI
quartet rules) is cross-referenced from the sibling change authored by
bu-iuol4.1.

## What Changes

- Add a new requirement to `dashboard-butler-management`: **Bespoke resident
  tab per domain butler**. The requirement enumerates nine rules governing
  optional per-butler tab placement, label, discovery, visual contract, loading,
  fallback, and mode-independence.
- Explicitly prohibit switchboard from carrying a resident bespoke tab; its
  existing operator-only Routing Log and Registry tabs are unchanged.
- Add a scenario to the resident-tab list confirming that bespoke tab labels
  appear alongside resident base tab labels.
- Add a **Per-butler bespoke tab label registry** (bu-iuol4.3): a normative
  table enumerating canonical sentence-case labels for all 11 domain butlers,
  with manifesto-grounded justifications. Includes four new butlers (general,
  lifestyle, messenger, qa) and renames the health butler tab from "Health" to
  "Measurements". Switchboard is explicitly absent.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-butler-management`: Adds nine bespoke-tab rules to the Butler
  detail page resident mode contract, plus a normative per-butler label registry
  for all 11 domain butlers.

## Impact

- **Specs**: One delta spec under `dashboard-butler-management`.
- **Frontend**: `frontend/src/pages/ButlerDetailPage.tsx` and
  `frontend/src/pages/butler-detail-tabs.ts` already implement the bespoke
  pattern hardcoded by butler name. This spec codifies that pattern and
  establishes canonical labels for four new tabs (general/Collections,
  lifestyle/Taste, messenger/Conversations, qa/Investigations) and one rename
  (health: "Health" → "Measurements"). Per-butler panel content is owned by
  implementation beads under bu-iuol4.
- **APIs / database / dependencies**: No API, database, or dependency changes.

## Source References

- Gate B decision bead bu-41p8z.
- `redesign-detail-page-tab-vocabulary`: settled parent change that gates this
  work (modal vocabulary rules, conditional-tab rule).
- `redesign-butler-detail-no-hero`: settled no-hero rule.
- Sibling change bu-iuol4.1 (resident-tab visual contract — Panel grid + KPI
  quartet rules cross-referenced here).
- Epic bu-iuol4: parent epic under which per-butler panel-layout
  implementation beads live.
