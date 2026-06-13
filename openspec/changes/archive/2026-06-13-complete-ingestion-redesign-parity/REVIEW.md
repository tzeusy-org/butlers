# Ratification Review — complete-ingestion-redesign-parity

**Reviewed:** 2026-05-24
**Reviewer:** Agent (bu-y25mj.2)
**Validation:** `openspec validate complete-ingestion-redesign-parity --strict` → **PASS**

---

## Summary

This change defines the binding acceptance contract for the `/ingestion`
visual redesign. It creates one new capability
(`dashboard-ingestion-dispatch-console`) and extends one existing capability
(`dashboard-shell`). The proposal, design, and spec are internally coherent,
well-scoped, and directly derived from the prototype bundle at
`pr/overview/ingestion-redesign/`.

Overall verdict: **ready for operator signoff**. No blocking contradictions
or gaps. Three risks and one naming ambiguity are flagged below, all of which
the operator should consciously accept.

---

## Validation Outcome

```
openspec validate complete-ingestion-redesign-parity --strict
Change 'complete-ingestion-redesign-parity' is valid
```

No structural failures. No formatting or field errors.

---

## Findings

### F1 — [RISK] `/ingestion/history` route exists but the spec says it should not

**Location:** `router-config.tsx` line 148; `IngestionHistoryPage.tsx`.

The current frontend (behind the `INGESTION_DISPATCH_CONSOLE` feature flag)
registers `/ingestion/history` as a first-class sub-route pointing at
`IngestionHistoryPage`. The spec under this change explicitly requires:

> `history` SHALL map to the Timeline route with an equivalent range or saved
> view; it SHALL NOT remain a fourth redesigned tab.

The existing route and page component will need to be removed — or converted to
a redirect to `/ingestion` with an appropriate `?range=` parameter — during
implementation. This is not a spec contradiction; it is an existing
implementation that now has a binding removal requirement. Implementation beads
must track this as a deletion obligation, not just a "new components" task.

**Risk:** If the implementation bead for the frontend foundation (§2) does not
explicitly retire `/ingestion/history`, it will silently persist after the
redesigned surface ships. The spec's scenario "History tab normalizes to
Timeline state" should be made explicit in the bead acceptance criteria.

**Recommendation:** Add a note in tasks.md §2 or §7 that retiring
`IngestionHistoryPage` and converting the `/ingestion/history` route to a
redirect is part of the completion contract.

---

### F2 — [RISK] `?tab=history` redirect goes to `/ingestion/history`, not Timeline

**Location:** `router.tsx` line 70; `IngestionTabRedirect` component.

The legacy `?tab=history` redirect currently navigates to `/ingestion/history`
(the existing sub-route). After the redesign this should redirect to `/ingestion`
with an equivalent range. This is consistent with F1 above — the redirect will
be correct once the `/ingestion/history` route is retired. However, if a
developer implements only the new visual components without retiring the old
route, the spec requirement for History normalization will technically not be
met.

**Risk:** Low — it flows naturally from F1 — but the implementation order
matters. The redirect fix and route retirement must land in the same bead.

---

### F3 — [RISK] `add-connector-oauth-scope-surface` is unratified

**Location:** `proposal.md` §Capabilities: Related; `specs/dashboard-ingestion-dispatch-console/spec.md` §Connector Detail > Scope list scenario.

The connector detail spec requires:
> **WHEN** `connector-oauth-scope-surface` fields are available on the connector
> detail response **THEN** the detail page renders per-scope status…

The `add-connector-oauth-scope-surface` change is still in `openspec/changes/`
(not archived), and its own tasks.md §4 and §6 remain unchecked. The spec
handles this gracefully by using "when available" language and requiring explicit
`unsupported/unavailable` states. This is acceptable, but:

- Implementation must not block connector detail shipping on oauth scope landing.
- The "unsupported/unavailable" states must be implemented from day one, not
  left as TODO/empty.

**Recommendation:** No spec change needed. Confirm this risk acceptance during
operator signoff.

---

### F4 — [GAP] `dashboard-shell` spec route map does not mention ingestion sub-routes

**Location:** `openspec/specs/dashboard-shell/spec.md` §Full Route Map (line 178).

The existing `dashboard-shell` spec lists top-level routes in a scenario block.
Ingestion sub-routes are not listed there. The change's
`specs/dashboard-shell/spec.md` delta adds a new requirement block
("Full Route Map SHALL include the ingestion dispatch console routes")
but the existing scenario block `Scenario: Top-level routes` is not amended to
include `/ingestion/connectors`, `/ingestion/filters`, or the connector detail
pattern.

This is a mild gap: the change's delta adds new language correctly, but an
implementer reading only the base `dashboard-shell` spec would not see the
ingestion sub-routes in the top-level route enumeration. The base spec's
`Scenario: Top-level routes` enumerates routes that all share the shell.
Ingestion sub-routes are children of `/ingestion` and the change spec makes
this first-class — but the base scenario does not enumerate them.

**This is not a contradiction** — the delta correctly extends the spec — but it
means the base spec will appear incomplete until the change is applied. This is
normal for an unarchived change.

**Risk:** Very low — by design. Record here for awareness.

---

### F5 — [NAMING/SCOPING] `IngestionSubNav` vs. page-level `TabsTrigger` — ambiguity in transition path

**Location:** `proposal.md` §Impact > Frontend; tasks.md §2.1.

The proposal says to replace the `page-level TabsTrigger ingestion shell with
route-backed IngestionSubNav`. The current `IngestionPage.tsx` uses
`<TabsList>/<TabsTrigger>` for the four-tab layout. The new sub-routes
(`IngestionTimelinePage`, `IngestionConnectorsPage`, etc.) are already partially
implemented but do not have a shared sub-nav.

The spec correctly prohibits `TabsTrigger` as the *route architecture* but does
not prohibit using `TabsTrigger` for intra-page tab switching within a sub-route
(e.g., the drawer's `flame / raw payload / replay history` tabs). This
distinction is clear in the spec but implementation agents should be aware that
the prohibition is route-architecture-scoped, not blanket.

**Recommendation:** No spec change needed. Worth noting in handoff context.

---

### F6 — [COST CONCERN] Per-step token tracking

**Location:** `INGESTION_HANDOFF.md` §8 Open questions; `specs/dashboard-ingestion-dispatch-console/spec.md` §Timeline Ledger.

The handoff explicitly flags: does the butler trace already emit token-counted
spans per step, or must the backend derive per-step tokens proportionally from
step duration? The spec's Timeline Ledger requirement calls for per-session and
per-step token/cost display. If the backend cannot provide native per-step
tracking, proportional derivation will produce visually plausible but technically
inaccurate cost breakdowns.

The spec does not mandate native tracking — it allows derived values — but
implementation must make this derivation explicit (server-side, not
client-side) per the handoff.

**Recommendation:** No spec change needed. This is an implementation-time
decision that should be documented in the butler trace design bead (§3 Timeline
Ledger bead).

---

## Current Implementation State (Context Only)

For operator awareness: the codebase currently has:

- `IngestionPage.tsx` — legacy four-tab page with `TabsTrigger`, still used in prod.
- `IngestionTimelinePage.tsx`, `IngestionConnectorsPage.tsx`, `IngestionFiltersPage.tsx`,
  `IngestionHistoryPage.tsx` — new sub-route wrappers, behind the
  `INGESTION_DISPATCH_CONSOLE` feature flag.
- The flag defaults `false` in prod, `true` in dev.
- The new sub-route pages wrap the *existing* legacy components
  (`TimelineTab`, `ConnectorsListPage`, `FiltersTab`, `BackfillHistoryTab`) — they
  expose the old card/tab visual under new routes.

**This confirms the gap the spec addresses is real.** The existing components do
not implement the Dispatch visual language. They are correctly identified as
targets for replacement.

---

## tasks.md 1.5 Status

Item 1.5 `[ ] Obtain operator signoff on the change before implementation begins`
remains unchecked. This is intentional — operator signoff requires human
acknowledgement and cannot be marked by an agent review.

<!-- review-note: 2026-05-24 agent-review-complete — validation passes, no blocking issues, risks F1/F2/F3 flagged for operator awareness. Operator must explicitly sign off per tasks.md §1.5 before implementation beads are created. -->

---

## Proposed Beads (Operator Consideration)

The operator may wish to file the following beads after signoff:

1. **[P1 task] Retire `/ingestion/history` route and redirect to Timeline** — discovered
   from F1/F2. The existing `IngestionHistoryPage.tsx` and its route registration
   must be removed as part of the frontend foundation bead (§2), not left as a
   follow-up. Consider making this explicit in bead §2 acceptance criteria.

2. **[P2 task] Track per-step token derivation decision** — the backend needs to
   decide whether to track natively or derive proportionally from step duration
   server-side. This affects the Timeline Ledger bead (§3) cost accuracy.

---

## Proposed Operator Signoff Text

> I have reviewed the `complete-ingestion-redesign-parity` OpenSpec change,
> the findings in REVIEW.md, and the spec documents under
> `openspec/changes/complete-ingestion-redesign-parity/specs/`. I accept the
> risks in F1 (history route retirement obligation), F2 (redirect ordering), and
> F3 (oauth scope dependency). Implementation beads for this epic may now be
> created. This constitutes operator signoff for tasks.md §1.5.
>
> Signed: [operator name] — [date]

Once the operator records this signoff (here or in another tracked location),
tasks.md §1.5 may be checked.
