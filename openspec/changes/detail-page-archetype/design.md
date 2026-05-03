## Context

`about/lay-and-land/detail-page-audit.md` scored the seven detail pages and named
`EntityDetailPage` the canonical winner. Its body (lines 1950–2238) maps onto four
tiers: identity hero → primary content → supporting grid → collapsed practical drawer.
`frontend/src/components/ui/page.tsx` ships the `<Page archetype="detail">` shell
that owns the chrome (breadcrumbs, heading, loading, error, empty) and a `max-w-5xl`
container. The `detail` archetype's skeleton (`DetailSkeleton`) is already defined in
that file.

Three spec sections predate the shell and describe their pages without referencing it.
Two of those sections also have route divergence (Contact) or title-quality issues
(Rule, Episode).

This design records the decisions taken before the implementation PRs merge, so future
reconciliation does not have to re-derive intent from code.

## Goals / Non-Goals

**Goals:**

- Define the four-tier detail-page archetype as a normative spec section so implementers
  have a single source of truth for body-slot vocabulary.
- Bring `dashboard-domain-pages` (Fact, Rule, Episode), `dashboard-relationship` (Contact),
  and `dashboard-butler-management` (Butler) into spec-level conformance with the archetype.
- Resolve the Contact page route duplication at the spec layer: pick a canonical path,
  prescribe a redirect, and remove the stale path from requirements.
- Verify that ConnectorDetailPage (PR #1397) is within or outside the original
  migration scope and document accordingly.

**Non-Goals:**

- Implementing frontend components. Implementation is in the bu-rqfil epic beads.
- Large-scale changes to the `<Page>` component API. Adding a narrow `status?: React.ReactNode`
  slot to `PageProps` is required by the status-pills requirement (see D2) and is an
  implementation-bead concern, not a design concern. The shell's overall contract and
  all other props remain unchanged.
- Specifying visual design tokens. That is `about/heart-and-soul/design-language.md`.
- Altering the EntityDetailPage spec. Entity is the reference consumer, not a
  migration target.
- Altering ConnectorDetailPage beyond scope verification (see D4).

## Decisions

### D1: "Detail-page archetype" is a new top-level capability spec, not a delta to `dashboard-shell`.

The `<Page>` component lives in the UI layer; the archetype contract is a layout
pattern, not infrastructure. `dashboard-shell` owns routing configuration and navigation
chrome. Adding a multi-page layout contract there would couple two different change
rates.

**Alternative considered:** add an archetype section to `dashboard-shell`. **Rejected**:
the shell spec is already large and stable; the archetype spec is new and will be revised
as more pages migrate. Separate specs for separate concerns.

### D2: Six-tier body vocabulary matches the audit source verbatim.

`detail-page-audit.md` §3.2 names six regions in render order:

1. **Header-hero** — breadcrumbs + H1 + status pills + action buttons
   (owned by `<Page>`, not a body slot).
2. **Hero** — alias chips, role chips, inline-editable fields (optional slot).
3. **Primary** — dominant read surface (required slot).
4. **Supporting** — side-by-side grid (optional slot, default `grid gap-6 sm:grid-cols-2`).
5. **Auxiliaries** — conditional vertical stack; sections hide when empty (optional).
6. **Drawer** — collapsed-by-default fold for settings / secrets (optional slot).

The archetype spec preserves this vocabulary so implementation code and spec
requirements share the same names. ButlerDetailPage's tab body maps to **primary**.

### D3: Contact route — canonical is `/contacts/:contactId`; legacy is a client-side redirect.

The router (`frontend/src/router.tsx` line 84) already registers
`/contacts/:contactId` and renders `ContactDetailPage`. The legacy path
`/butlers/relationship/contacts/:id` appears only in `dashboard-relationship/spec.md`
line 54 and is not registered in the router.

The spec is wrong; the router is right. The delta:
- Removes `/butlers/relationship/contacts/:id` as the normative route from the spec.
- Installs `/contacts/:contactId` as the single canonical route, matching the router
  parameter name already in use.
- Requires that any inbound link to the legacy path be redirected to the canonical path
  via a client-side route entry in React Router (a `<Navigate replace />` component
  following the `RelationshipEntityRedirect` pattern at `frontend/src/router.tsx`
  lines 57–64). This is a client-side redirect, not an HTTP 308. For external bookmarks
  that bypass React Router (i.e., are fetched directly as HTTP requests), a hosting-level
  redirect would be needed separately; that is out of scope for this change.
- Documents both paths in the spec so operators who have bookmarked the legacy path
  know what to expect.

**Alternative considered:** keep both routes live and render the same page at both paths.
**Rejected**: duplicate routes create two sources of truth for canonical breadcrumb
and bookmark behavior, which violates the "explicit over hidden magic" engineering bar.
The redirect is the correct mechanism.

### D4: ConnectorDetailPage is out of scope for this change; document as a sibling migration.

The bead description notes ConnectorDetailPage was not in the original seven and asks
for scope verification. `detail-page-audit.md` §1.7 explicitly inventories and scores
ConnectorDetailPage as one of the seven. It was included in PR #1397 as the
implementer's judgment call (the audit scored it 17/25, ahead of Fact and Rule).

Verdict: **ConnectorDetailPage is within the spirit of the audit** but was not named
in the three target specs (`dashboard-domain-pages`, `dashboard-relationship`,
`dashboard-butler-management`). Its spec home is `connector-base-spec` or a dedicated
`dashboard-connector-detail` spec. This change does not delta those specs; that is a
sibling task.

### D5: Rule and Episode titles must be record-identity, not type-of-record.

`dashboard-domain-pages/spec.md` lines 496–510 (Rule) and 519–533 (Episode) specify:
- Rule: `"Rule" as page title`
- Episode: `"Episode" as page title`

The audit (§3.5) flags this as a doctrine violation: "The H1 must be the thing, not
the type-of-thing." The delta replaces these with:
- Rule: first 80 characters of rule content (truncated with ellipsis if longer).
- Episode: `session_id` if present, otherwise `Episode {id[:8]}`.

Fact already specifies `subject as page title` — no change needed.

### D6: ButlerDetailPage adopts `<Page archetype="detail">` for breadcrumbs and chrome;
tabs remain the `primary` slot unchanged.

The audit (§6.7) calls out butler as "the only detail page that legitimately needs a
workspace shape" but says "Migrating its outer chrome to `<DetailPage>` would be a
clean win." The delta requires:
- `<Page archetype="detail">` wraps the outer chrome.
- `breadcrumbs` prop replaces the inline `<Breadcrumbs>` component.
- The `actions` prop carries `<ChatPanel />`.
- The `<Tabs>` block is the `primary` slot content.
- Inner tab structure is NOT changed by this delta; that is a separate consolidation.

## Risks / Trade-offs

- **Risk:** Route redirect for Contact may break existing deep-links if operators have
  bookmarked `/butlers/relationship/contacts/:id`. **Mitigation:** permanent redirect
  (308) preserves bookmark behavior for all compliant HTTP clients.
- **Trade-off:** ConnectorDetailPage is left to a sibling change. This means the
  `connector-base-spec` remains out of sync with the archetype until that follow-up
  lands. Acceptable: the connector page is already migrated in code (PR #1397);
  only the spec is pending.
- **Trade-off:** Rule and Episode titles change from literal type strings to
  record-identity values. This is a visible behavior change for operators, but it
  aligns with the doctrine and the audit recommendation. The old behavior was a known
  gap, not a deliberate choice.

## Source References

- `about/lay-and-land/detail-page-audit.md` §3.2, §4, §5, §6.7
- `about/heart-and-soul/design-language.md` Non-Negotiable Rule 2, Settled Direction #3
- `frontend/src/components/ui/page.tsx` — `<Page archetype="detail">` implementation
- `frontend/src/router.tsx` lines 83–84, 57–64 — canonical contact route and
  RelationshipEntityRedirect pattern
