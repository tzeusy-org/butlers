// @vitest-environment jsdom
/**
 * Tests for IngestionSubNav — shared sub-navigation for ingestion routes.
 *
 * Covers:
 * - Renders Timeline, Connectors, Filters links
 * - Highlights the active route correctly (Timeline at /ingestion, Connectors at /ingestion/connectors, Filters at /ingestion/filters)
 * - Timeline link uses end-matching (does not stay active on /ingestion/connectors)
 * - Accessible nav landmark with label
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Routes, Route } from 'react-router'
import { IngestionSubNav } from './IngestionSubNav'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

describe('IngestionSubNav', () => {
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
              path="*"
              element={<IngestionSubNav />}
            />
          </Routes>
        </MemoryRouter>,
      )
    })
  }

  it('renders Timeline, Connectors, and Filters navigation links', () => {
    render('/ingestion')
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    expect(nav).not.toBeNull()
    const links = Array.from(nav!.querySelectorAll('a')).map((a) => a.textContent?.trim())
    expect(links).toContain('Timeline')
    expect(links).toContain('Connectors')
    expect(links).toContain('Filters')
  })

  it('does not render a History link (history is Timeline in the redesign)', () => {
    render('/ingestion')
    const nav = container.querySelector('nav[aria-label="Ingestion views"]')
    const links = Array.from(nav!.querySelectorAll('a')).map((a) => a.textContent?.trim())
    expect(links).not.toContain('History')
  })

  it('marks Timeline as active at /ingestion', () => {
    render('/ingestion')
    const links = container.querySelectorAll('a')
    const timeline = Array.from(links).find((a) => a.textContent?.trim() === 'Timeline')
    const connectors = Array.from(links).find((a) => a.textContent?.trim() === 'Connectors')
    expect(timeline?.getAttribute('aria-current')).toBe('page')
    expect(connectors?.getAttribute('aria-current')).toBeFalsy()
  })

  it('marks Connectors as active at /ingestion/connectors', () => {
    render('/ingestion/connectors')
    const links = container.querySelectorAll('a')
    const timeline = Array.from(links).find((a) => a.textContent?.trim() === 'Timeline')
    const connectors = Array.from(links).find((a) => a.textContent?.trim() === 'Connectors')
    // Timeline uses end-matching and should NOT be active on sub-routes
    expect(timeline?.getAttribute('aria-current')).toBeFalsy()
    expect(connectors?.getAttribute('aria-current')).toBe('page')
  })

  it('marks Filters as active at /ingestion/filters', () => {
    render('/ingestion/filters')
    const links = container.querySelectorAll('a')
    const filters = Array.from(links).find((a) => a.textContent?.trim() === 'Filters')
    const timeline = Array.from(links).find((a) => a.textContent?.trim() === 'Timeline')
    expect(filters?.getAttribute('aria-current')).toBe('page')
    expect(timeline?.getAttribute('aria-current')).toBeFalsy()
  })

  it('Timeline link is NOT active at /ingestion/connectors (end=true)', () => {
    render('/ingestion/connectors')
    const links = container.querySelectorAll('a')
    const timeline = Array.from(links).find((a) => a.textContent?.trim() === 'Timeline')
    // With end=true, /ingestion is not active when on /ingestion/connectors
    expect(timeline?.getAttribute('aria-current')).toBeFalsy()
  })

  it('has an accessible nav landmark with aria-label', () => {
    render('/ingestion')
    const nav = container.querySelector('nav')
    expect(nav?.getAttribute('aria-label')).toBe('Ingestion views')
  })
})
