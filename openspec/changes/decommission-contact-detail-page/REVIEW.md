# Ratification Review — decommission-contact-detail-page

**Issue:** bu-m8gb6.1
**Reviewer:** Beads Worker (agent/bu-m8gb6.1)
**Date:** 2026-05-24
**Status:** PENDING OPERATOR SIGNOFF

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

**PARTIAL PASS — two non-trivial gaps remain; one set of gaps is addressable as
trivial fixups, but two require operator decision before implementation begins.**

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

### AC3 — Change explicitly distinguishes frontend routes from `/api/butlers/relationship/entities/*` API namespace

**FAIL — not explicitly stated in the change artifacts.**

The proposal.md says: "Preserve `/api/butlers/relationship/entities/*` as the API
namespace; only frontend route prose changes." This statement is present in the *proposal*
but is **not reflected as an explicit normative statement in the delta spec** at
`specs/dashboard-relationship/spec.md`. The delta spec contains no mentions of `api`,
`API`, or "API namespace."

This matters because the existing spec has many requirements naming
`/api/butlers/relationship/entities/*` endpoints (Owner-only auth, entity-level tab
APIs, entity curation queue, Cmd-K Finder, provenance contract, entity activity
aggregator). Ratification AC3 requires the *change* (not just the proposal) to
explicitly call out that `/api/butlers/relationship/entities/*` is unaffected.

**Recommended fix:** Add a brief normative statement at the top of the delta spec (or
in a dedicated note section) clarifying that the `/api/butlers/relationship/entities/*`
API namespace is unchanged by this change; only frontend route prose and navigation
contracts are modified. This is a low-risk clarification that does not change any
requirement semantics.

Whether to block on this or add it now is an **operator decision**.

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

| ID | Severity | Description | Resolution needed |
|----|----------|-------------|-------------------|
| F1 | SEMANTIC | Two requirements in existing spec still name `/butlers/relationship/entities/:id` as the entity detail page: "Entity detail Editorial/Workbench mode toggle" (lines 797–857) and "Dispatch design language token discipline" (line 944–948). Delta does not cover these. | Operator must decide: update in this change or create a follow-on spec-sync bead |
| F2 | SEMANTIC | "Owner identity and credential management via contact detail page" (line 264) still references `/butlers/relationship/contacts/:id` as the primary mechanism; contact detail is being decommissioned. Delta does not replace this requirement. | Operator must decide: update in this change or create follow-on bead |
| F3 | SEMANTIC | "Owner identity setup banner" (line 291) and "Pending identities queue on contacts page" (line 318) reference the `/butlers/relationship/contacts` contacts page path, which is not a registered route. Delta does not update these. | Operator must decide: update in this change or create follow-on bead |
| F4 | MODERATE | AC3: The delta spec contains no explicit statement that the `/api/butlers/relationship/entities/*` API namespace is unaffected. The proposal.md says this, but the spec delta does not. | Recommend adding a one-sentence normative note to the delta spec before implementation |

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

## 5. Operator Signoff Request

This review is blocking on operator signoff before implementation proceeds.

**Questions requiring operator decision:**

1. **F1** — Should the "Entity detail Editorial/Workbench mode toggle" and "Dispatch
   design language token discipline" requirements be updated in this change (by adding
   additional MODIFIED sections to the delta spec) or deferred to the spec-sync bead
   (tasks.md task 1.1)?

2. **F2** — Should "Owner identity and credential management via contact detail page" be
   updated in this change to name the entity-detail contact-channel card or a settings
   surface as the new primary mechanism, or is this deferred?

3. **F3** — Should "Owner identity setup banner" and "Pending identities queue on contacts
   page" be updated in this change to reference `/entities?has=contact` (the entity index),
   or is this deferred?

4. **F4** — Should the delta spec include an explicit normative statement that the
   `/api/butlers/relationship/entities/*` API namespace is unaffected? (Recommended:
   yes — low effort, eliminates implementation ambiguity.)

**Recommended minimum before implementation begins:**

- Add F4 note to delta spec (trivial — one sentence, no requirement semantics change).
- Decide on F1/F2/F3 disposition and either add delta spec sections or create linked
  follow-on beads with explicit dependency on this change closing.

Once the operator makes decisions on F1–F4 and the delta spec is updated accordingly
(or the operator explicitly accepts the gaps with documented follow-on beads), this
change is ready for ratification and implementation can begin.
