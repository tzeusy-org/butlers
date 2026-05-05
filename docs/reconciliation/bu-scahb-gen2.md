# Vertical G (voice and copy pass) — Gen-2 Reconciliation

**Issue:** bu-jj1ts
**Date:** 2026-05-06
**Epic:** bu-scahb — Frontend redesign G: voice and copy pass
**Status:** CLEAN CLOSE — all gen-1 gaps resolved; epic genuinely closed.

---

## Purpose

Gen-1 (bu-scahb.6, PR #1456) found 3 gaps and filed 2 gap beads:

- **bu-65j6j** — Fix `check-no-em-dashes.py` failure: inventory header em-dash + sr-only strings in SourceStateBadgeStrip.tsx
- **bu-77sy5** — G3/G4 residual: 3 non-compliant button labels + inline empty states in MedicationTracker, MeasurementChart, DashboardPage

Both beads are now merged (PR #1457 and PR #1459 respectively). This gen-2 report confirms each gap has been closed in `main`.

---

## Gap Verification

### Gap 1 — `check-no-em-dashes.py` exits 0 (bu-65j6j, PR #1457)

**Gen-1 failure:** Script exited 1 due to 3 em-dashes in `about/lay-and-land/frontend-copy-inventory.md` (header prose + 2 sr-only string mirrors from SourceStateBadgeStrip.tsx).

**Verification:**

```
$ python3 scripts/check-no-em-dashes.py; echo "Exit: $?"
No em-dashes found outside code blocks.
Exit: 0
```

**Status: PASS** — Script exits 0 cleanly.

**Root causes resolved:**
- `SourceStateBadgeStrip.tsx` sr-only strings: em-dashes replaced with colons (`: has recent error`, `: no recent data`). Confirmed at lines 81 and 118.
- Inventory file (`about/lay-and-land/frontend-copy-inventory.md`): header em-dash removed.

---

### Gap 2a — Button labels (bu-77sy5, PR #1459)

**Gen-1 failures:** 3 non-compliant button labels:
- `"Force Patrol Now"` at QaOverviewPage.tsx:682
- `"Sync Now"` at QASettingsCard.tsx:324
- `"Set Value"` at ButlerStateTab.tsx:184 + StateBrowser.tsx:140

**Verification:**

| File | Gen-1 label | Current label | Status |
|---|---|---|---|
| QaOverviewPage.tsx:682 | `"Force Patrol Now"` | `"Run patrol"` | PASS |
| QASettingsCard.tsx:324 | `"Sync Now"` | `"Sync now"` | PASS |
| ButlerStateTab.tsx:184 | `"Set Value"` | `"Set value"` | PASS |
| StateBrowser.tsx:140 | `"Set Value"` | `"Set value"` | PASS |

All four instances corrected to sentence case + owner-direct verb form.

---

### Gap 2b — Inline empty states in components (bu-77sy5, PR #1459)

**Gen-1 failures:**
- `MedicationTracker.tsx` — local `function EmptyState()` did not use shared component
- `MeasurementChart.tsx` — local `function EmptyState()` did not use shared component
- `DashboardPage.tsx:196` — inline `<div><p>No failed notifications...</p></div>`

**Verification:**

| Component | Gen-1 state | Current state | Status |
|---|---|---|---|
| MedicationTracker.tsx | Local inline `function EmptyState()` | `<EmptyStateUI title="No medications found." description="Medications appear here once they are added to your health record." />` | PASS |
| MeasurementChart.tsx | Local inline `function EmptyState()` | `<EmptyStateUI title="No measurements found." description="No data available for this type and date range." />` | PASS |
| DashboardPage.tsx | Inline `<div><p>No failed notifications...</p></div>` | `<EmptyState title="No failed notifications." description="All systems healthy." />` | PASS |

All three refactored to use the shared `<EmptyState>` primitive.

---

### Retained-Inline Verification (intentional exceptions)

Gen-1 noted two components were contextually distinct and should be retained inline. Confirmed justification still holds:

**AggregatePieChart.tsx — chart zero-state**

Inline `<div data-testid="pie-empty-state">` with embedded `<AllCategoriesLegend>` component. The zero-state is visually fused to the chart (shows the category legend even when no data is present). Replacing with shared `<EmptyState>` would require passing the legend as a child or prop, coupling the primitive to a domain-specific component. The copy `"No activity recorded for this window."` follows voice guide (fact statement, no exclamation, no em-dash). **Retained inline: JUSTIFIED.**

**EntityDetailPage.tsx — two scoped inline messages**

1. Line 619: `<p className="text-muted-foreground text-xs py-2">No contacts found.</p>` — this is contact-search result feedback rendered inline within a popover-style linking panel when a user types a search query with no results. It is not a page-level empty state; it is a search-scoped feedback message. The sentence is short, factual, and voice-compliant. **Retained inline: JUSTIFIED.**

2. Lines 1094-1097: `<p className="text-muted-foreground py-8 text-center text-sm">` with context-aware filter text (`"No activity recorded yet."` / `"No {filter} yet."`) — this message adapts to the active timeline filter label. Migrating to shared `<EmptyState>` would either require always rendering a description slot or passing the dynamic copy as a prop, without adding actionability (there is no action a user can take to immediately populate activity). The inline placement is intentional. **Retained inline: JUSTIFIED.**

---

## Regression Scan — New Em-Dashes in User-Facing Copy

Grep of `frontend/src/pages/` and `frontend/src/components/` for em-dash (`—`) excluding code comments:

All hits fall into two categories:
1. **Null-display fallbacks:** `?? "—"` — explicitly permitted by non-negotiable #6 ("typographic convention for missing data, not prohibited prose"). Confirmed in SecretsPage.tsx, ConnectorDetailPage.tsx, QaInvestigationDetailPage.tsx, notification-feed.tsx, SessionDetailDrawer.tsx, tz-format.ts.
2. **Code comments:** Lines beginning with `//` or `*` inside JSDoc/block comments.

**No new user-facing prose em-dashes found. PASS.**

---

## Epic AC Summary

| # | AC | Bead | Final Status |
|---|---|---|---|
| 1 | design-language.md has 'Voice and Copy' section | bu-scahb.7 | PASS |
| 2 | Catalog of user-facing strings exists | bu-scahb.2 | PASS |
| 3 | Every button label follows sentence case + owner-direct verb | bu-scahb.3 + bu-77sy5 | PASS — all 4 residual labels fixed |
| 4 | Every empty-state in src/pages renders via shared `<EmptyState>` | bu-scahb.4 + bu-77sy5 | PASS — 3 components refactored; 2 retained inline with justification |
| 5 | Zero em-dashes in user-facing strings | bu-scahb.5 + bu-65j6j | PASS — sr-only colons; null-display `"—"` permitted |
| 6 (G7) | `check-no-em-dashes.py` exits 0 | bu-scahb.7 + bu-65j6j | PASS — exits 0 confirmed |
| 7 | Em-dash ban as numbered non-negotiable in design-language.md | bu-scahb.7 | PASS |
| 8 | README.md updated to surface Voice and Copy section | bu-scahb.7 | PASS |

---

## Verdict

**The epic bu-scahb is GENUINELY CLOSED.**

All 8 acceptance criteria are satisfied. Both gap beads (bu-65j6j, bu-77sy5) are merged and verified against main. The two intentionally retained inline empty states (AggregatePieChart, EntityDetailPage search/filter messages) have standing justifications that still hold. The em-dash checker passes cleanly.

No follow-up beads required from this gen-2 audit.
