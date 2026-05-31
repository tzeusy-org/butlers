## ADDED Requirements

### Requirement: Contact detail page canonical route is /contacts/:contactId

The contact detail page SHALL be served exclusively at the canonical route
`/contacts/:contactId`. The legacy route `/butlers/relationship/contacts/:id` (specified
in the existing "Contact detail page" requirement) is deprecated and MUST NOT be treated
as a normative route.

**Route duplication resolution:**

- **Canonical route:** `/contacts/:contactId` (renders `ContactDetailPage`; matches the
  parameter name already registered at `frontend/src/router.tsx` line 84)
- **Legacy route:** `/butlers/relationship/contacts/:id` — MUST redirect to the canonical
  path via a client-side React Router entry using `<Navigate replace />`, following the
  `RelationshipEntityRedirect` pattern already present at `frontend/src/router.tsx`
  lines 57–64. This is a client-side redirect; for external HTTP-level bookmarks a
  hosting-level redirect would be needed separately (out of scope for this change).
- Any internal navigation link (breadcrumb, "View contact" button, notification link,
  email) that currently targets `/butlers/relationship/contacts/:id` MUST be updated
  to target `/contacts/:contactId`.

**Verification:**

The router at `frontend/src/router.tsx` line 84 already registers `/contacts/:contactId`
as the active route. The legacy path `/butlers/relationship/contacts/:id` is not
registered. This requirement formalizes what the router already does and adds the
redirect requirement for any external links that may have been bookmarked.

#### Scenario: Canonical URL for contact detail

- **WHEN** a user navigates to `/contacts/abc-123-uuid`
- **THEN** the contact detail page MUST render for contact `abc-123-uuid`
- **AND** the URL in the address bar MUST remain `/contacts/abc-123-uuid`

#### Scenario: Legacy URL redirects to canonical

- **WHEN** a user navigates to `/butlers/relationship/contacts/abc-123-uuid`
- **THEN** the client-side router MUST redirect to `/contacts/abc-123-uuid`
- **AND** the contact detail page MUST render for contact `abc-123-uuid`

#### Scenario: Internal contact links use canonical route

- **WHEN** a contact name is rendered as a navigation link anywhere in the dashboard
  (e.g., in the contacts table, in a relationship entry, in a notification)
- **THEN** the link target MUST be `/contacts/{contactId}`, not `/butlers/relationship/contacts/{id}`

---

### Requirement: Contact detail page conforms to the detail-page archetype

The contact detail page at `/contacts/:contactId` SHALL conform to the detail-page archetype
defined in the `detail-page-archetype` spec.

**Changes from the existing requirement (dashboard-relationship §Requirement: Contact
detail page):**

1. **Shell adoption.** The page MUST use `<Page archetype="detail">` as its outer
   shell. The existing breadcrumbs block MUST be passed via the `breadcrumbs` prop.
   The inline three-skeleton loading block and the inline destructive-text error block
   MUST be removed from the page body and delegated to the `loading` and `error` props
   on `<Page>`.

2. **Title.** The `title` prop on `<Page>` MUST be the contact's full name
   (`first_name + " " + last_name`), consistent with the H1 already rendered inside
   `ContactDetailView`. If the contact has a `nickname`, it MUST be appended in
   parentheses: `"Alice Johnson (Allie)"`.

3. **Actions.** The edit and delete buttons currently inside `ContactDetailView`'s
   header (`ContactDetailView.tsx` lines 864–898) MUST be migrated to the `actions`
   prop on `<Page>` so they appear in the page header row. The `ContactDetailView`
   component body retains all other content.

4. **Body layout.** The `<ContactDetailView>` component output (minus the header
   card's edit/delete buttons) becomes the `primary` body slot inside the shell.

5. **Token cleanup status.** The hex-literal color palettes previously at
   `ContactDetailView.tsx` lines 53–62 and 69–77 have already been replaced with
   CSS custom properties (`var(--category-*)` and `var(--role-*)`) as of the migration
   in ce185209 (role badge hex → CSS tokens). No token-cleanup prerequisite remains
   for this migration step. Implementers should verify no new hex literals were
   introduced during the archetype migration.

#### Scenario: Contact detail uses shell loading state

- **WHEN** `GET /api/butlers/relationship/contacts/:id` is in flight
- **THEN** the `<Page>` shell MUST show `DetailSkeleton`
- **AND** no inline `<Skeleton>` blocks MUST be rendered by the page at the page layer

#### Scenario: Contact detail uses shell error state

- **WHEN** the contact fetch fails
- **THEN** the `<Page>` shell MUST render the destructive error card
- **AND** no inline destructive-text block MUST be rendered at the page layer

#### Scenario: Contact detail title shows full name with nickname

- **WHEN** a contact has `first_name = "Alice"`, `last_name = "Johnson"`, and
  `nickname = "Allie"`
- **THEN** the `<h1>` rendered by the shell MUST read "Alice Johnson (Allie)"

#### Scenario: Contact detail title shows full name without nickname

- **WHEN** a contact has `first_name = "Bob"`, `last_name = "Smith"`, and no nickname
- **THEN** the `<h1>` rendered by the shell MUST read "Bob Smith"

#### Scenario: Contact edit and delete actions in page header

- **WHEN** a contact detail page renders a resolved contact
- **THEN** the edit button and the delete button MUST appear in the page header row
  (via the `actions` prop), visible without scrolling
- **AND** they MUST NOT appear only inside the `<ContactDetailView>` card body

#### Scenario: No hex literals for role badge colors

- **WHEN** a contact has a role badge (e.g., "owner") rendered on the detail page
- **THEN** the badge color MUST use a CSS custom property or Tailwind semantic token
- **AND** the badge MUST NOT be styled with an inline `style={{ backgroundColor: "#..." }}`

## Source References

- Non-Negotiable Rule 2 (The Page is a primitive)
- `detail-page-archetype` spec — archetype conformance requirements
- `about/lay-and-land/detail-page-audit.md` §1.2 (ContactDetailPage analysis), §2
  (Token & primitive discipline), §4.3 (Action-bar position), §6.4 (ContactDetailPage
  migration guidance)
- `frontend/src/router.tsx` lines 83–84, 57–64 — canonical route and redirect pattern
- RFC 0003 (Switchboard routing and ingestion) — motivation for canonical identity
  routing
