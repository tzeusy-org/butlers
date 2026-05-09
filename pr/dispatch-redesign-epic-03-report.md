# Epic 03: Butler Detail Tab Shell + Gate-B Vocabulary — Reconciliation Report (gen-1)

Generated: 2026-05-10
Issue: bu-8bayc.5
Reporter: Beads Worker (automated reconciliation)

---

## 1. Children Summary

### bu-8bayc.1 — Update BASE_TABS to chosen Gate B vocabulary

**Status:** CLOSED (PR #1501 merged 2026-05-09T17:32:10Z)

**Commits:**
- `6e676074` feat(tabs): introduce BASE_TABS_OPERATOR and BASE_TABS_RESIDENT for Gate B B2
- `d0ebe391` fix: clarify tab-config comments to match current operator-default behavior

**Delivered:**
- Exported `BASE_TABS_OPERATOR` (10 spec-mandated base tabs) as a named const from
  `frontend/src/pages/ButlerDetailPage.tsx`.
- Exported `BASE_TABS_RESIDENT` (7-tab Dispatch vocabulary) as a named const.
- Exported `OPERATOR_EXTENSION_TABS = ["models"]` for the non-spec operator-only Models tab.
- `TYPE TabValue` union covers all four tab groups.
- Exported `getAllTabs(butlerName, mode)` and `isValidTab(value, butlerName, mode)` helpers.
- Conditional tab constants `HEALTH_TABS` and `SWITCHBOARD_TABS` preserved verbatim.

**Acceptance coverage:** BASE_TABS constants match Gate B; conditional tabs preserved;
test file updated and passing.

---

### bu-8bayc.2 — Implement operator/resident mode toggle (Gate-B = B2)

**Status:** CLOSED (PR #1502 merged 2026-05-09T17:55:55Z)

**Commits:**
- `4a70188b` feat: operator/resident mode toggle with localStorage persistence
- `b3a07a0e` fix: review follow-ups for operator/resident mode toggle (bu-km64s)

**Delivered:**
- `DetailMode` type (`"operator" | "resident"`).
- `readPersistedMode()` / `persistMode(mode)` helpers using `MODE_STORAGE_KEY`.
- `useState<DetailMode>` initializer reads localStorage on mount, defaults to `"resident"`.
- Deep-link auto-promotion: if `?tab=` names an operator-only tab while stored mode is
  `"resident"`, mode is promoted to `"operator"` and persisted synchronously in the
  state initializer (works during SSR / renderToStaticMarkup).
- `setMode(next)` callback: updates state + localStorage; resets URL to Overview via
  `setSearchParams({}, {replace: true})` if the current tab is invalid in the target mode.
- `ButlerDetailActions` receives `mode` and `onModeChange` props; renders a `<button
  role="switch" aria-checked aria-label>` pill labelled "Operator" or "Resident".
- Mode-conditional `<TabsTrigger>` rendering in the JSX tab list:
  - Operator: Sessions, Skills, Schedules, Trigger, MCP, State, CRM, Models
  - Resident: Activity, Logs, Approvals, Spend
  - Shared (both modes): Overview, Config, Memory
- RTL/static-markup tests for: default resident mode, operator localStorage restore,
  toggle pill attributes (`role="switch"`, `aria-checked`, `aria-label`), localStorage
  read/write paths, and deep-link auto-promotion to operator mode.

**Acceptance coverage:** Mode toggle UI with `role="switch"` present; localStorage
persistence; resident default; operator mode shows all 10 base tabs; tests pass.

---

### bu-8bayc.3 — Migrate dropped tabs to alternate routes (Gate-B = B3)

**Status:** CLOSED (not applicable)
**Close reason:** Gate-B resolved to B2, not B3. This task was explicitly conditional on
B3 being chosen. No work was required.

---

### bu-8bayc.4 — Preserve URL deep-linking and lazy-loading for surviving tabs

**Status:** CLOSED (cherry-pick `759e8905` to main 2026-05-09)

**Commits:**
- `759e8905` test: verify URL deep-linking and lazy-loading for surviving tabs

**Delivered:**
- Tests for all 11 valid operator deep-link keys (`isValidTab` parametric).
- Tests for all 7 valid resident deep-link keys.
- Tests for conditional tab deep-links (health, switchboard routing-log/registry).
- Tests verifying lazy fallback "Loading {tab}..." for skills, schedules, trigger, mcp,
  state, memory when those tabs are the active deep-link in operator mode.
- Tests verifying Overview removes `?tab=` from URL; invalid `?tab=` falls back to
  Overview without mode change.
- Tests verifying `setSearchParams` is NOT called during initial render (no spurious
  history entries).
- Tests for `isValidTab` rejecting null, empty string, and unknown tab names.

**Acceptance coverage:** Deep-linking + lazy-loading acceptance criteria fully covered by
tests.

---

## 2. Gate-B B2 Disposition

**Decision:** B2 — operator/resident mode toggle
**Decided by:** uniquosity@gmail.com on 2026-05-09
**Source bead:** bu-41p8z (closed)

### Default mode

The Gate-B resolution states: "Default is resident; persisted via localStorage."

The OpenSpec change `redesign-detail-page-tab-vocabulary` (design.md §Decision 1)
specifies: "Missing, invalid, or unsupported stored values resolve to `resident`."

**Actual default in code (`ButlerDetailPage.tsx:159-167`):**

```typescript
const MODE_STORAGE_KEY = "butlers.detail.mode";

function readPersistedMode(): DetailMode {
  try {
    const stored = localStorage.getItem(MODE_STORAGE_KEY);
    if (stored === "operator" || stored === "resident") return stored;
  } catch { /* SSR / private browsing */ }
  return "resident";
}
```

The default is `"resident"`. Gate-B resolution and spec are correctly implemented.

### Mode summary

| Mode | Base Tabs | Extension Tabs | Default? |
|------|-----------|----------------|----------|
| Resident | overview, activity, logs, approvals, spend, config, memory (7) | none | **yes** |
| Operator | overview, sessions, config, skills, schedules, trigger, mcp, state, crm, memory (10) | models (1) | no |

---

## 3. Tab Vocabulary Table

### Resident mode tabs (7)

| Key | Label | Type | Lazy | Status |
|-----|-------|------|------|--------|
| `overview` | Overview | base | no | Implemented |
| `activity` | Activity | base | no | Stub ("coming soon") |
| `logs` | Logs | base | no | Stub ("coming soon") |
| `approvals` | Approvals | base | no | Stub ("coming soon") |
| `spend` | Spend | base | no | Stub ("coming soon") |
| `config` | Config | base | no | Implemented (`ButlerConfigTab`) |
| `memory` | Memory | base | yes | Implemented (`ButlerMemoryTab`) |

### Operator mode tabs (10 + 1 extension)

| Key | Label | Type | Lazy | Status |
|-----|-------|------|------|--------|
| `overview` | Overview | base | no | Implemented |
| `sessions` | Sessions | base | no | Implemented (`ButlerSessionsTab`) |
| `config` | Config | base | no | Implemented |
| `skills` | Skills | base | yes | Implemented (`ButlerSkillsTab`) |
| `schedules` | Schedules | base | yes | Implemented (`ButlerSchedulesTab`) |
| `trigger` | Trigger | base | yes | Implemented (`ButlerTriggerTab`) |
| `mcp` | MCP | base | yes | Implemented (`ButlerMcpTab`) |
| `state` | State | base | yes | Implemented (`ButlerStateTab`) |
| `crm` | CRM | base | no | Implemented (`ButlerCrmTab`) |
| `memory` | Memory | base | yes | Implemented (`ButlerMemoryTab`) |
| `models` | Models | extension (non-spec) | yes | Implemented (`ButlerModelOverridesTab`) |

### Conditional tabs (butler-specific, both modes)

| Butler | Key | Label | Status |
|--------|-----|-------|--------|
| health | `health` | Health | Implemented |
| switchboard | `routing-log` | Routing Log | Implemented |
| switchboard | `registry` | Registry | Implemented |
| general | `collections` | Collections | **MISSING — GAP** |
| general | `entities` | Entities | **MISSING — GAP** |
| education | `reviews` | Reviews | **MISSING — GAP** |

---

## 4. Acceptance Coverage Matrix

| Criterion | Source | Status |
|-----------|--------|--------|
| `BASE_TABS_OPERATOR` = 10 spec-mandated tabs | spec.md:55,178 | Covered (bu-8bayc.1, tests) |
| `BASE_TABS_RESIDENT` = 7 Dispatch vocabulary | design.md §2 | Covered (bu-8bayc.1, tests) |
| `OPERATOR_EXTENSION_TABS` = ["models"] (non-spec) | design.md §6 | Covered (bu-8bayc.1, tests) |
| Resident is the default mode (no localStorage) | design.md §1 | Covered (bu-8bayc.2, tests) |
| Mode persisted in localStorage | design.md §1 | Covered (bu-8bayc.2, tests) |
| localStorage key is `butlers.detail.mode` | design.md §1 | Covered (spec updated to match code, bu-4zxxs) |
| Mode toggle pill with `role="switch"` | spec.md:96-99 | Covered (bu-8bayc.2, tests) |
| `aria-checked` reflects mode | proposal.md | Covered (bu-8bayc.2, tests) |
| Operator-only deep link auto-promotes mode | design.md §4 | Covered (bu-8bayc.2, tests) |
| Invalid tab falls back to overview | spec.md:74-78 | Covered (bu-8bayc.4, tests) |
| Deep-link tab keys for both modes valid | spec.md:113-117 | Covered (bu-8bayc.4, tests) |
| Lazy-loading preserved for specified tabs | spec.md:69-72 | Covered (bu-8bayc.4, tests) |
| Tab changes use `replaceState` | spec.md:117 | Covered (bu-8bayc.4, tests) |
| `getAllTabs` helper exported and mode-aware | bu-8bayc.1 AC | Covered (tests) |
| `isValidTab` helper exported and mode-aware | bu-8bayc.1 AC | Covered (tests) |
| Conditional tabs: health (health butler) | spec.md:100-102 | Covered |
| Conditional tabs: routing-log/registry (switchboard) | spec.md:57-67 | Covered |
| Conditional tabs: collections/entities (general) | spec.md:104-106 | **GAP — not implemented** |
| Conditional tabs: reviews (education) | OpenSpec delta §104-109 | **GAP — not implemented** |
| Resident stubs (activity/logs/approvals/spend) | design.md §2 | Partial (stub cards) |

---

## 5. OpenSpec Status

**Change:** `redesign-detail-page-tab-vocabulary`
**Validation:** `openspec validate --strict redesign-detail-page-tab-vocabulary` → PASSES

**Sync state:** The OpenSpec change contains the full B2 vocabulary spec including all
conditional tabs. The implementation satisfies most spec requirements. Two gaps between
spec and code remain (see §6).

**Tasks.md implementation checklist:**

| Task | Status |
|------|--------|
| 2.1 Split base-tab config into resident/operator sets | Done |
| 2.2 Resident default, localStorage persistence | Done |
| 2.3 Accessible operator/resident toggle in actions | Done |
| 2.4 Resident mode: Overview, Activity, Logs, Approvals, Spend, Config, Memory | Done (stubs) |
| 2.5 Operator mode: 10 base tabs | Done |
| 2.6 Models excluded from resident, handled as operator extension | Done |
| 2.7 Conditional tabs: switchboard + health across both modes | Done (partial — general/education missing) |
| 2.8 Auto-promote to operator on operator-only deep-link | Done |
| 2.9 Invalid `?tab=` falls back to Overview without mode switch | Done |
| 2.10 Resident-only tab switches from operator to resident mode | Spec says "WHEN operator mode AND ?tab= is resident-only, switch to resident" — partial: auto-promotion code promotes resident→operator but the reverse (operator→resident on resident-only tab) is not explicitly implemented |

---

## 6. Gaps

### GAP-1: localStorage key mismatch (cosmetic / spec conformance) — RESOLVED

**Resolution (bu-4zxxs):** Spec updated to match the shipped code. The canonical form
is `butlers.detail.mode` (dots). All OpenSpec change documents (`design.md`,
`proposal.md`, `tasks.md`, and the `dashboard-butler-management` spec delta) have been
updated to use the dot form. No code change was needed; the code was already correct.

**Original finding:** Spec used `butlers:detail:mode` (colons) while code used
`butlers.detail.mode` (dots). The dot form was merged and shipped in PR #1502; the spec
was not updated at that time. The decision to canonicalize dots avoids a runtime
migration path for any user who already has the dot-form key stored in their browser.

---

### GAP-2: Conditional tabs for `general` and `education` butlers not implemented

**Severity:** Medium — spec requirements for two named butlers are unmet.

**Spec mandates (dashboard-butler-management/spec.md:104-106 and OpenSpec delta §97-109):**
- `general` butler → "Collections" and "Entities" tabs in both modes
- `education` butler → "Reviews" tab in both modes

**Code state:** `getAllTabs()` only checks `health` and `switchboard`. No `general` or
`education` branch exists. Neither tab key appears in any `TabsTrigger` or `TabsContent`.

**Impact:** A user visiting `/butlers/general` or `/butlers/education` does not see the
conditional tabs. The spec scenario is unfulfilled.

**Note:** Epic 07 (bu-dg5qc) will perform a full bespoke-tab inventory and wire grounded
cases. The general Collections/Entities tabs are the original spec requirement (pre-Epic
03); the education Reviews tab is Epic 06 scope (bu-3cujw). However, the Epic 03 spec
delta explicitly restates both as conditional-tab requirements in both modes.

**Recommended resolution:** File a follow-up bead (or assign to Epic 07) to add the
`general` and `education` conditional-tab branches in `getAllTabs()`, `TabsTrigger`, and
`TabsContent`. This can be deferred to Epic 06/07 delivery but should be tracked.

---

### GAP-3: Resident-only deep-link does not reverse operator→resident promotion (spec §Decision 4)

**Severity:** Low — the forward path (resident→operator for operator-only tabs) is
implemented. The reverse (operator→resident for resident-only tabs) is stated in
`design.md §Decision 4` and the OpenSpec delta scenario "Deep links to resident-only
tabs select resident mode" (spec.md:63-71).

**Current behavior:** If localStorage holds `operator` and `?tab=activity` is present,
`isValidTab("activity", "general", "operator")` returns false (activity is not in
BASE_TABS_OPERATOR), so the page falls back to `"overview"` in operator mode instead
of switching to resident mode.

**Impact:** A user who bookmarks `/butlers/foo?tab=activity` while in resident mode, then
returns in operator mode, lands on Overview instead of the Activity tab. Functionally a
minor UX issue; the tab is reachable via the toggle.

**Recommended resolution:** Extend the mode initializer to check whether `tabParam` is
resident-only (in BASE_TABS_RESIDENT but not BASE_TABS_OPERATOR) and, if so, pull mode
to `"resident"` — mirroring the operator auto-promotion logic.

---

## 7. Summary

Epic 03 core deliverables are complete and merged:

| Item | State |
|------|-------|
| BASE_TABS_OPERATOR / BASE_TABS_RESIDENT constants | Merged (PR #1501) |
| OPERATOR_EXTENSION_TABS (Models) | Merged (PR #1501) |
| getAllTabs / isValidTab mode-aware helpers | Merged (PR #1501) |
| Operator/resident mode toggle with `role="switch"` | Merged (PR #1502) |
| localStorage persistence, default resident | Merged (PR #1502) |
| Deep-link auto-promotion (resident→operator) | Merged (PR #1502) |
| URL deep-linking + lazy-loading tests | Cherry-pick `759e8905` |
| Gate-B B2 disposition documented | This report |
| OpenSpec `redesign-detail-page-tab-vocabulary` validates | Confirmed |

Two gaps remain for follow-up beads:
- **GAP-1** (localStorage key) — RESOLVED by bu-4zxxs: spec updated to canonical `"butlers.detail.mode"` (dots)
- **GAP-2** (missing conditional tabs) — `general` Collections/Entities, `education` Reviews
- **GAP-3** (reverse deep-link promotion) — operator→resident for resident-only tab params
