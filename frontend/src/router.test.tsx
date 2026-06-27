// @vitest-environment jsdom

import { describe, expect, it, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Navigate, MemoryRouter, Route, Routes, useParams, useSearchParams } from 'react-router'
import { navSections } from './components/layout/nav-config'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Local inline of the redirect — same logic as router.tsx RelationshipContactRedirect.
// Tested in isolation so these tests do not depend on RootLayout or all pages.
// ---------------------------------------------------------------------------

function RelationshipContactRedirect() {
  const { id } = useParams()
  return <Navigate to={`/contacts/${id ?? ''}`} replace />
}

function ContactDetailStub() {
  const { contactId } = useParams()
  return (
    <div data-testid="contact-detail-page" data-contact-id={contactId}>
      contact detail
    </div>
  )
}

// ---------------------------------------------------------------------------
// /butlers/relationship/contacts/:id → /contacts/:contactId
// ---------------------------------------------------------------------------

describe('/butlers/relationship/contacts/:id redirect', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  function render(initialPath: string) {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route
              path="/butlers/relationship/contacts/:id"
              element={<RelationshipContactRedirect />}
            />
            <Route path="/contacts/:contactId" element={<ContactDetailStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
  }

  it('navigates to canonical contact page for id=abc-123', () => {
    render('/butlers/relationship/contacts/abc-123')
    const el = container.querySelector('[data-testid="contact-detail-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-contact-id')).toBe('abc-123')
  })

  it('navigates to canonical contact page for a numeric id', () => {
    render('/butlers/relationship/contacts/42')
    const el = container.querySelector('[data-testid="contact-detail-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-contact-id')).toBe('42')
  })

  it('does not render the contact detail page for an unrelated path', () => {
    render('/some/other/path')
    expect(container.querySelector('[data-testid="contact-detail-page"]')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// /contacts → /entities?has=contact redirect (§8.10 entity-redesign)
// ---------------------------------------------------------------------------

// Inline the redirect exactly as implemented in router.tsx so tests are
// isolated from RootLayout and all page components.
function ContactsRedirect() {
  return <Navigate to="/entities?has=contact" replace />
}

function EntitiesIndexStub() {
  const [searchParams] = useSearchParams()
  return (
    <div
      data-testid="entities-index-page"
      data-has={searchParams.get('has') ?? ''}
    >
      entities index
    </div>
  )
}

describe('/contacts → /entities?has=contact redirect', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('redirects /contacts to /entities?has=contact', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/contacts']}>
          <Routes>
            <Route path="/contacts" element={<ContactsRedirect />} />
            <Route path="/entities" element={<EntitiesIndexStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const el = container.querySelector('[data-testid="entities-index-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-has')).toBe('contact')
  })
})

// ---------------------------------------------------------------------------
// /contacts/:contactId → /entities?has=contact compatibility redirect
//
// public.contacts was dropped (core_134) and the per-contact entity resolver
// endpoint no longer exists, so legacy contact bookmarks forward to the entity
// index filter rather than resolving an individual entity.
// ---------------------------------------------------------------------------

function ContactIdRedirect() {
  return <Navigate to="/entities?has=contact" replace />
}

describe('/contacts/:contactId → /entities?has=contact redirect', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('redirects a legacy contact id to the entity index filter', () => {
    act(() => {
      root.render(
        <MemoryRouter initialEntries={['/contacts/contact-001']}>
          <Routes>
            <Route path="/contacts/:contactId" element={<ContactIdRedirect />} />
            <Route path="/entities" element={<EntitiesIndexStub />} />
          </Routes>
        </MemoryRouter>,
      )
    })
    const el = container.querySelector('[data-testid="entities-index-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-has')).toBe('contact')
  })
})

// ---------------------------------------------------------------------------
// /butlers/relationship/entities/:entityId → /entities/:entityId (legacy)
// ---------------------------------------------------------------------------

import { RelationshipEntityRedirect } from './router.tsx'

function EntityDetailStub() {
  const { entityId } = useParams()
  return (
    <div data-testid="entity-detail-page" data-entity-id={entityId}>
      entity detail
    </div>
  )
}

function RelationshipEntityRedirectHarness({ initialPath }: { initialPath: string }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          path="/butlers/relationship/entities/:entityId"
          element={<RelationshipEntityRedirect />}
        />
        <Route path="/entities/:entityId" element={<EntityDetailStub />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('/butlers/relationship/entities/:entityId → /entities/:entityId (legacy)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })

  afterEach(() => {
    act(() => {
      root.unmount()
    })
    container.remove()
    document.body.innerHTML = ''
  })

  it('redirects /butlers/relationship/entities/abc to /entities/abc', () => {
    act(() => {
      root.render(
        <RelationshipEntityRedirectHarness initialPath="/butlers/relationship/entities/abc" />,
      )
    })
    const el = container.querySelector('[data-testid="entity-detail-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-entity-id')).toBe('abc')
  })

  it('redirects with a UUID-style entity id', () => {
    act(() => {
      root.render(
        <RelationshipEntityRedirectHarness initialPath="/butlers/relationship/entities/ent-abc-123-xyz" />,
      )
    })
    const el = container.querySelector('[data-testid="entity-detail-page"]')
    expect(el).not.toBeNull()
    expect(el?.getAttribute('data-entity-id')).toBe('ent-abc-123-xyz')
  })
})

// ---------------------------------------------------------------------------
// nav-config: Contacts entry must not appear (§8.10)
// ---------------------------------------------------------------------------

describe('nav-config', () => {
  it('does not contain a Contacts entry', () => {
    const allItems = navSections.flatMap((section) =>
      section.items.flatMap((item) =>
        item.kind === 'group' ? item.children : [item],
      ),
    )
    const contactsItem = allItems.find(
      (item) => item.label === 'Contacts' || item.path === '/contacts',
    )
    expect(contactsItem).toBeUndefined()
  })

  // The Groups page remains routable (/groups), but is no longer surfaced in
  // the sidebar navigation — see GroupsPage and the relationship CRM quick links.
  it('does not contain a Groups entry', () => {
    const allItems = navSections.flatMap((section) =>
      section.items.flatMap((item) =>
        item.kind === 'group' ? item.children : [item],
      ),
    )
    const groupsItem = allItems.find(
      (item) => item.label === 'Groups' || item.path === '/groups',
    )
    expect(groupsItem).toBeUndefined()
  })
})
