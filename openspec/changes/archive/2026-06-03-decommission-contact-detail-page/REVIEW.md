# Ratification Review — decommission-contact-detail-page

**Issue:** bu-m8gb6.1
**Reviewer:** Beads Worker (agent/bu-m8gb6.1)
**Date:** 2026-05-24 (updated 2026-05-25)
**Status:** RATIFIED — All findings resolved, operator-approved

---

## 1. Strict Validation Result

```
openspec validate decommission-contact-detail-page --strict
Change 'decommission-contact-detail-page' is valid
```

**Result: PASS.**

---

## 2. Acceptance Criteria Verification

### AC1 — `openspec validate decommission-contact-detail-page` passes

**PASS.** Validation completed cleanly with no errors or warnings in strict mode.

---

### AC2 — Contradictory dashboard-relationship route language is resolved

**PASS — all semantic gaps resolved per operator decision to fix F1, F2, F3 in this change.**

The delta spec at `openspec/changes/decommission-contact-detail-page/specs/dashboard-relationship/spec.md`
correctly replaces the three requirements that are primary subjects of this change:

- "Requirement: Contact detail page" — decommissions the standalone page, defines
  the entity-detail contact-channel card at `/entities/:entityId`.
- "Requirement: Contact detail page canonical route is /contacts/:contactId" — changes
  `/contacts/:contactId` from a canonical rendering route to a compatibility redirect.
- "Requirement: Memory entity page links to relationship activity" — replaces the
  requirement that links to `/butlers/relationship/entities/:entityId` as a product
  route, and asserts `/entities/:entityId` as canonical.
- "Requirement: Entity detail page" — re-homes the canonical entity detail page at
  `/entities/:entityId`.

However, the following requirements in the **existing canonical spec**
(`openspec/specs/dashboard-relationship/spec.md`) retain contradictory frontend route
language and are **not covered by any delta in this change**:

**F1 (SEMANTIC GAP — requires operator decision):**
"Requirement: Entity detail Editorial / Workbench mode toggle" (spec.md lines 797–857)
names the entity detail page as `/butlers/relationship/entities/:id` in its requirement
header, in the scenario that seeds Editorial as default, and in the "Dispatch design
language token discipline" requirement (line 948). These requirements describe the
entity detail page as being at `/butlers/relationship/entities/:id`, directly
contradicting the change's assertion that `/entities/:entityId` is the canonical route.
The delta spec does not include a modified version of either of these requirements.

Specifically:
- Requirement header (line 800): "The Entity detail page (`/butlers/relationship/entities/:id` ...)"
- Scenario (line 854): "WHEN a user lands on `/butlers/relationship/entities/<uuid>`..."
- Token discipline (line 948): "...`/butlers/relationship/entities/:id`) SHALL conform..."

These requirements must be updated to name `/entities/:entityId` as the canonical page.
Whether to update them in this change or in a follow-on spec-sync bead is an **operator
decision** — the change's own tasks.md task 1.1 calls for updating the spec so
`/entities/:entityId` is canonical "everywhere," but the delta does not include these
specific requirements.

**F2 (SEMANTIC GAP — requires operator decision):**
"Requirement: Owner identity and credential management via contact detail page" (spec.md
line 264) still reads:

> The contact detail page (`/butlers/relationship/contacts/:id`) SHALL be the primary
> mechanism for configuring owner identity fields and credentials.

This references a path that is deprecated by the contact-canonical-route requirement and
never was canonical per the existing spec. After this change lands, `/contacts/:contactId`
becomes a compatibility redirect to `/entities/:entityId`, so the "primary mechanism"
for credential management must be re-homed. The delta spec does not provide a replacement
requirement for this. Operator must decide whether credential management moves to the
entity detail page, the entity-detail contact-channel card, or a dedicated settings
surface, and whether the spec update belongs in this change or a follow-on.

**F3 (SEMANTIC GAP — requires operator decision):**
"Requirement: Owner identity setup banner" (spec.md line 291) references the contacts
page at `/butlers/relationship/contacts`:

> The dashboard SHALL display a persistent banner on the contacts page
> (`/butlers/relationship/contacts`) when...

And "Requirement: Pending identities queue on contacts page" (spec.md line 318) uses
the same path in a scenario. Neither path is a registered route in the current router.
After this change the contacts index continues to redirect to `/entities?has=contact`
(confirmed in `router-config.tsx` line 91 and delta spec scenario "Contact index still
redirects to entity index filter"). However, the banner and queue requirements still
point to a URL that is not a rendered page. The delta spec does not provide replacement
requirements for these. Operator must decide whether the banner and queue move to
`/entities?has=contact` (the entity index) and whether this update belongs here or in
a follow-on.

---

### AC3 — Change explicitly distinguishes frontend routes from `/api/relationship/entities/*` API namespace

**PASS — resolved per F4 fix.**

A "Scope Note" block has been added at the top of the delta spec
(`openspec/changes/decommission-contact-detail-page/specs/dashboard-relationship/spec.md`)
explicitly stating that this is a FRONTEND-ONLY change and that the
`/api/relationship/entities/*` API namespace is NOT affected.

---

### AC4 — Accepted spec states `/entities/:entityId` is correct and canonical

**PASS.**

The delta spec explicitly and repeatedly asserts `/entities/:entityId` as canonical:

- Delta spec line 6–7: "the canonical entity detail page at `/entities/:entityId`"
- Delta spec line 85–86: "SHALL use `/entities/:entityId` as the canonical entity
  detail route"
- Delta spec line 108: "The frontend SHALL render the canonical entity detail page at
  `/entities/:entityId`"
- design.md Decision D1: "Canonical detail route is `/entities/:entityId`"
- proposal.md: "Canonical route correction: `/entities/:entityId` is the canonical
  entity detail route."

The `RelationshipEntityRedirect` component in `frontend/src/router.tsx` (lines 25–28)
already implements the redirect from `/butlers/relationship/entities/:entityId` to
`/entities/:entityId`, and `router-config.tsx` (line 110) registers `/entities/:entityId`
as the active route rendering `EntityDetailPage`. The spec delta aligns with the
code-level reality.

---

## 3. Findings Summary

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| F1 | SEMANTIC | Two requirements in existing spec still name `/butlers/relationship/entities/:id` as the entity detail page: "Entity detail Editorial/Workbench mode toggle" (lines 797–857) and "Dispatch design language token discipline" (line 944–948). Delta does not cover these. | **RESOLVED** — Both requirements added as MODIFIED blocks in the delta spec. "Editorial/Workbench" updated to use `/entities/:entityId` in body text and scenarios. "Dispatch" updated to list `/entities/:entityId` as the sixth route (replacing `/butlers/relationship/entities/:id`). |
| F2 | SEMANTIC | "Owner identity and credential management via contact detail page" (line 264) still references `/butlers/relationship/contacts/:id` as the primary mechanism; contact detail is being decommissioned. Delta does not replace this requirement. | **RESOLVED** — MODIFIED block added to delta spec re-homing the primary credential management surface to the entity detail contact-channel card at `/entities/:entityId`. Scenarios updated to match. |
| F3 | SEMANTIC | "Owner identity setup banner" (line 291) and "Pending identities queue on contacts page" (line 318) reference the `/butlers/relationship/contacts` contacts page path, which is not a registered route. Delta does not update these. | **RESOLVED** — Both requirements added as MODIFIED blocks in the delta spec. Banner requirement now references `/entities?has=contact` (entity index). Pending identities queue now references `/entities?has=contact`. All scenarios updated. |
| F4 | MODERATE | AC3: The delta spec contains no explicit statement that the `/api/relationship/entities/*` API namespace is unaffected. The proposal.md says this, but the spec delta does not. | **RESOLVED** — "Scope Note" section added at the top of the delta spec explicitly stating this is a FRONTEND-ONLY change and the `/api/relationship/entities/*` API namespace is unchanged. |

---

## 4. Additional Observations

- The router code is already consistent with the change's intent: `router-config.tsx`
  registers `/entities/:entityId` → `EntityDetailPage` (line 110) and
  `/butlers/relationship/entities/:entityId` → `RelationshipEntityRedirect` (line 119).
  No router changes are needed for the canonical route assertion.

- The delta spec's three MODIFIED requirements fully replace the content of the
  corresponding requirements in the canonical spec. The replacement text is
  coherent, well-scoped, and does not introduce new ambiguity within the replaced
  requirements themselves.

- design.md, proposal.md, and tasks.md are internally consistent. The tasks.md task 1.1
  explicitly says "update `openspec/specs/dashboard-relationship/spec.md` via this change
  so `/entities/:entityId` is the canonical entity detail page **everywhere**" — but the
  delta spec does not yet achieve "everywhere" (F1 gap). This is an execution gap, not a
  design contradiction.

---

## 5. Operator Signoff

**Operator decision (2026-05-25):** Fix all 4 findings (F1, F2, F3, F4) inline in
this change before merging. All MODIFIED requirement blocks have been added to the
delta spec. Strict validation passes.

**All acceptance criteria are now PASS. This change is ratified and ready for
implementation.**

Summary of changes made in response to operator decision:
- **F1:** Added MODIFIED blocks for "Entity detail Editorial / Workbench mode toggle"
  and "Dispatch design language token discipline" — both now reference
  `/entities/:entityId` as the canonical route (not `/butlers/relationship/entities/:id`).
- **F2:** Added MODIFIED block for "Owner identity and credential management via contact
  detail page" — re-homed primary credential management surface to the entity detail
  contact-channel card at `/entities/:entityId`.
- **F3:** Added MODIFIED blocks for "Owner identity setup banner" and "Pending identities
  queue on contacts page" — both now reference `/entities?has=contact` (the entity index)
  instead of the unregistered `/butlers/relationship/contacts` route.
- **F4:** Added "Scope Note" at the top of the delta spec explicitly stating this is a
  FRONTEND-ONLY change and the `/api/relationship/entities/*` API namespace is
  unaffected.
