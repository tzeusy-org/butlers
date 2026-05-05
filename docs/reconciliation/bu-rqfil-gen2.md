# Vertical B (<DetailPage> consolidation) — Gen-2 Reconciliation Report

**Issue:** bu-wqb69
**Date:** 2026-05-06
**Epic:** bu-rqfil — Frontend redesign B: `<DetailPage>` consolidation
**Based on gen-1 report:** docs/reconciliation/bu-rqfil-gen1.md (bu-rqfil.8)
**Gap beads resolved since gen-1:**
- bu-35rsy — Migrate ContactDetailPage, ButlerDetailPage, EpisodeDetailPage, ConnectorDetailPage onto `<DetailPage>` shell — CLOSED via PR #1407 (merged 2026-05-05)
- bu-r8j9n — Merge PR #1398 and run /opsx:sync for detail-page-archetype — CLOSED via PR #1408 (merged 2026-05-05)

---

## Re-Audit: All 6 Migration Pages

### Grep summary (post-merge, on origin/main)

```
grep -rn "<DetailPage" frontend/src/pages/
  ContactDetailPage.tsx:61:    <DetailPage
  FactDetailPage.tsx:57:    <DetailPage
  RuleDetailPage.tsx:48:    <DetailPage
  EpisodeDetailPage.tsx:43:    <DetailPage
  ButlerDetailPage.tsx:411:    <DetailPage
  ConnectorDetailPage.tsx:168:    <DetailPage

grep -rn 'archetype="detail"' frontend/src/pages/
  EntityDetailPage.tsx:1624:      archetype="detail"  ← intentional: EntityDetailPage is not a migration target
```

**Result:** All 6 migration pages (Fact, Rule, Contact, Butler, Episode, Connector) now use `<DetailPage>` from `components/layout/DetailPage.tsx`. No migration-target page bypasses the wrapper. Gen-1 Gap A is resolved at the structural level.

---

## Per-Page Slot Audit

| Page | `<DetailPage>` | `pulse` | `primary` | `supporting` | `auxiliary` | `practical` | `actions` |
|---|---|---|---|---|---|---|---|
| FactDetailPage | YES | `null` | Card with content/metrics/etc | Confidence + Permanence cards | `null` | `null` | — |
| RuleDetailPage | YES | `null` | Card with content/effectiveness/etc | `null` | `null` | `null` | — |
| ContactDetailPage | YES | PulseStrip (entity-linked only) | ContactDetailView | — | — | — | — |
| ButlerDetailPage | YES | ButlerHeartbeatTile | Tabs block | — | — | — | ChatPanel |
| EpisodeDetailPage | YES | `null` | Content card | Provenance card | — | — | — |
| ConnectorDetailPage | YES | `null` | Status+Counters+Stats+etc | — | Checkpoint Cursor + AlertDialog | — | — |

All pages supply at minimum `pulse` and `primary`. Layout-tier semantics are coherent.

---

## OpenSpec Sync Verification (Gen-1 Gap B)

PR #1408 (bu-r8j9n, merged 2026-05-05) ran `/opsx:sync` and promoted delta specs into the canonical `openspec/specs/` tree. Verification:

| Spec file | Status | Key content |
|---|---|---|
| `openspec/specs/detail-page-archetype/spec.md` | EXISTS (175 lines) | 5 requirements: shell, six-tier body, record-identity title, status pills, action buttons |
| `openspec/specs/dashboard-domain-pages/spec.md` | UPDATED | 3 archetype-conformance requirements added: Fact, Rule, Episode detail pages |
| `openspec/specs/dashboard-relationship/spec.md` | UPDATED | 2 requirements added: canonical `/contacts/:contactId` route + Contact detail archetype conformance |
| `openspec/specs/dashboard-butler-management/spec.md` | UPDATED | 2 requirements added: Butler outer chrome adoption + tab body vocabulary |

Gen-1 Gap B is fully resolved.

---

## Gen-2 Checklist Against Synced Spec

The following ACs are drawn from the now-canonical specs in `openspec/specs/`.

### AC1 — Shell adoption: all 6 migration pages use `<DetailPage>`
**Status: PASS**
All 6 pages import and render `<DetailPage>` from `components/layout/DetailPage.tsx`. Zero migration-target pages use `<Page archetype="detail">` directly.

### AC2 — Fact title = subject field
**Status: PARTIAL GAP**
`FactDetailPage` sets `record={{ title: fact?.content ?? factId ?? "Fact", subtitle: fact.entity_name ? \`${fact.entity_name} · ${fact.predicate}\` : fact.predicate }}`. The shell title is derived from `fact.content` (the object value), NOT `fact.subject`. Provenance content (subject, entity link) is rendered inside the primary card body. The synced spec (`dashboard-domain-pages`) specifies `title = fact.subject`; the current implementation uses `fact.content` instead.

**Severity:** Low — `fact.content` is the most user-visible string (the fact's object value), so the chosen approach is likely more informative. However, the spec contract explicitly names `fact.subject` as the H1 field, and the implementation diverges from it.

### AC3 — Rule title = content summary (not "Rule")
**Status: PARTIAL GAP**
`RuleDetailPage` derives title as `rule?.content ?? ruleId ?? "Rule"`. The title does avoid a generic "Rule" string when content is available, which satisfies the spec's primary intent. However, there is no 80-character truncation: long rule content is passed to the shell H1 untruncated. The synced spec (`dashboard-domain-pages`) specifies the title should be the first 80 chars of content with ellipsis — this truncation logic is absent.

**Severity:** Low — omitting truncation is a cosmetic difference (potentially very long H1 titles). The spec's primary intent (no literal "Rule" title) is met. Truncation is a detail the spec mandates but the implementation omits.

### AC4 — Episode title = session_id (or content-derived fallback)
**Status: PARTIAL GAP**
The synced spec (dashboard-domain-pages §Requirement: Episode detail page conforms to the detail-page archetype, point 2) specifies:
> `title` = `episode.session_id` if non-null; OR `"Episode {episode.id.slice(0,8)}"` if null.

Current `EpisodeDetailPage` instead uses the first non-empty line of `episode.content`, capped at 80 chars. This derives a more descriptive title from the body text but does not match the spec's session_id-first contract.

The gen-1 checklist accepted this as PASS (the title is not literally "Episode") before the spec sync clarified the session_id priority. Post-sync, there is a residual spec/code divergence.

**Severity:** Low — the chosen approach (content-first) is more user-friendly than a bare session_id; the spec's intent was to ban the generic "Episode" string, which is already met. However, the spec contract is now explicit and the implementation diverges.

### AC5 — Contact title = full_name (+ nickname in parentheses)
**Status: PARTIAL GAP**
`ContactDetailPage` uses `contact.full_name` as the title record but does not append the nickname in parentheses when `contact.nickname` is non-null. The spec (`dashboard-relationship` §Scenario: Contact detail title shows full name with nickname) requires `"Alice Johnson (Allie)"` when `nickname = "Allie"`.

**Severity:** Low — visual difference only; no data loss or broken navigation. Affects contacts with a non-null nickname field.

### AC6 — Contact edit/delete buttons in `actions` prop
**Status: GAP**
The spec (`dashboard-relationship` §Contact detail page conforms to the detail-page archetype, point 3) states:
> The edit and delete buttons inside `ContactDetailView.tsx` lines 864–898 MUST be migrated to the `actions` prop on `<Page>`.

Audit: `ContactDetailView.tsx` lines 875–893 still render Pencil and Trash2 icon buttons inside `<CardHeader>`. `ContactDetailPage` does not pass an `actions` prop to `<DetailPage>`. The buttons remain inside the card body, not in the shell's header action slot.

**Severity:** Medium — the buttons work correctly but their position diverges from the archetype contract. The spec explicitly names this as a required action.

### AC7 — Rule status pill (maturity badge) via `status` prop
**Status: PARTIAL GAP**
The spec (`dashboard-domain-pages` §Rule detail page conforms to the detail-page archetype, point 4) requires the Maturity badge to be passed via the `status` prop on `<Page>` so it appears adjacent to the title row. 

Current state: `RuleDetailPage` does not pass a `status` prop to `<DetailPage>`. The Maturity badge is rendered inside the primary card's content section. Additionally, `DetailPage` does not expose a `status` slot in its `DetailPageProps` interface (it would need to be threaded through to the underlying `<Page>` component, which does have `status?: React.ReactNode` — added in bu-5rydz, PR #1403).

**Severity:** Low — the badge is visible and functional. It is not adjacent to the H1 title row, which the spec designates as the canonical position for status pills.

### AC8 — Butler title = butler name (titleized), actions = ChatPanel
**Status: PASS**
`ButlerDetailPage` sets `record={{ title: name, subtitle: description }}` where `name` is the butler name from the record. `ChatPanel` is passed via `actions` prop. Both requirements met.

### AC9 — ConnectorDetailPage spec coverage
**Status: GAP (documentation)**
`ConnectorDetailPage` is now correctly implemented with `<DetailPage>`. However, the three synced dashboard specs (domain-pages, relationship, butler-management) contain no explicit archetype-conformance requirement for `ConnectorDetailPage`. The `detail-page-archetype/spec.md` preamble names Connector as one of 7 adopters, but no spec section explicitly mandates or verifies connector-specific conformance slots.

This was flagged as an open item in `openspec/changes/detail-page-archetype/tasks.md` §5.1: "ConnectorDetailPage spec home — decide whether it lives in `connector-base-spec`, a new `dashboard-connector-detail` spec, or an existing spec."

**Severity:** Low — code is correct; a dedicated conformance requirement for the connector detail page is missing from the spec tree.

### AC10 — PracticalDrawer/PulseStrip location (gen-1 Gap 1 / structural)
**Status: ACKNOWLEDGED (not resolved)**
Both files remain at `components/relationship/PracticalDrawer.tsx` and `components/relationship/PulseStrip.tsx`. The spec AC (`components/detail/` target) was flagged as low-priority in gen-1. No new beads have been filed for the relocation, and it is not blocking the epic. The relationship namespace is coherent given that PulseStrip is a relationship-domain concept.

---

## Gap Summary (Gen-2)

| # | AC | Severity | Finding |
|---|---|---|---|
| Gap A | Fact title = subject field (AC2) | Low | Spec says title = `fact.subject`; code uses `fact.content` (the object value). More descriptive in practice but diverges from spec contract. |
| Gap B | Rule title truncation (AC3) | Low | Spec mandates first 80 chars with ellipsis; code uses `rule.content` untruncated. Literal "Rule" fallback avoided, but truncation is absent. |
| Gap C | Episode title spec contract (AC4) | Low | Spec says `session_id`-first; code uses content-first. Both avoid generic "Episode" string but diverge from explicit spec language. |
| Gap D | Contact nickname in title (AC5) | Low | `full_name` used without appending `(nickname)` when nickname is non-null. |
| Gap E | Contact edit/delete in `actions` prop (AC6) | Medium | Edit/delete buttons remain inside ContactDetailView card body, not migrated to page header `actions` slot. |
| Gap F | Rule maturity badge via `status` prop (AC7) | Low | Maturity badge is inside primary card, not in title-row `status` slot. `DetailPage` also does not expose `status` prop passthrough yet. |
| Gap G | ConnectorDetailPage spec coverage (AC9) | Low | No explicit archetype-conformance requirement for connector detail page in any dashboard spec. |

---

## Epic-Level Verdict

**The primary structural goal of bu-rqfil is achieved:** All 6 migration-target pages (Fact, Rule, Contact, Butler, Episode, Connector) render via `<DetailPage>`. The four-tier slot contract is enforced across all pages. The "no two code paths" doctrine is met at the shell level. The OpenSpec is synced and the detail-page-archetype capability is canonicalized.

**Remaining gaps are polish-tier and spec-precision items.** They do not represent unimplemented features — the pages function correctly. The gaps are:
- Two title-field divergences (Fact uses content not subject; Rule omits 80-char truncation)
- Two title-display refinements (episode session_id priority, contact nickname appendix)
- One action-button placement (contact edit/delete still inside card body)
- One status pill placement (rule maturity badge not on title row)
- One documentation gap (no explicit connector conformance requirement in any spec)

**Recommendation:** The epic bu-rqfil may be closed with the remaining items filed as follow-up beads at their designated priority levels (2–3). The structural consolidation is complete.

---

## Candidates for Closure

- **bu-rqfil** (parent epic) — structural goal achieved; all 6 pages use `<DetailPage>` wrapper; OpenSpec synced
- **bu-wqb69** (this gen-2 bead) — reconciliation complete

**Coordinator action required:** close bu-rqfil and bu-wqb69 after reviewing this report.

---

## New Gap Beads to File (if coordinator agrees)

| Gap | Suggested Title | Type | Priority | Notes |
|---|---|---|---|---|
| Gap A | FactDetailPage: use fact.subject as shell title (per synced spec, not fact.content) | task | 3 | Simple one-liner; may be intentionally content-first — requires product decision |
| Gap B | RuleDetailPage: truncate rule.content to 80 chars with ellipsis in shell title | task | 3 | Simple useMemo addition; prevents overly long H1 on verbose rules |
| Gap C | EpisodeDetailPage: use session_id as title when non-null (per synced spec) | task | 3 | Low friction — simple useMemo change; content-first approach already avoids "Episode" |
| Gap D | ContactDetailPage: append nickname in parentheses to title when present | task | 3 | One-liner in title computation |
| Gap E | ContactDetailPage: migrate edit/delete buttons from ContactDetailView card to `actions` prop | task | 2 | Medium effort — requires props threading from ContactDetailPage → ContactDetailView or lifting state |
| Gap F | RuleDetailPage: pass maturity badge via `status` prop on DetailPage (requires DetailPage to expose `status` slot) | task | 3 | DetailPage needs one new prop; RuleDetailPage then uses it |
| Gap G | Spec: add ConnectorDetailPage archetype-conformance requirement to appropriate dashboard spec | task | 3 | Documentation only; code is already correct |
