// @vitest-environment jsdom

import { describe, expect, it, afterEach, beforeEach } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Navigate, MemoryRouter, Route, Routes, useParams } from 'react-router'

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
