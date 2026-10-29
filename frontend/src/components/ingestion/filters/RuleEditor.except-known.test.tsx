// @vitest-environment jsdom
/**
 * RuleEditor — the per-rule "except people I know" option (bu-q7vx1q.88.3).
 *
 * The checkbox appears only for verdicts that can honor it (skip/block), names where it is
 * honored, persists in the condition payload, and is dropped when the verdict changes away.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

const mockCreateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))
const mockUpdateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('@/hooks/use-ingestion-rules', () => ({
  useCreateIngestionRule: () => ({ mutateAsync: mockCreateMutateAsync, isPending: false }),
  useUpdateIngestionRule: () => ({ mutateAsync: mockUpdateMutateAsync, isPending: false }),
  useTestIngestionRule: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

import type { IngestionRule } from '@/api/types'
import { RuleEditor } from './RuleEditor'

const CHECKBOX = '[data-testid="rule-editor-except-known-input"]'

function setSelectValue(el: HTMLSelectElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

function setInputValue(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('RuleEditor — except people I know', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
    mockCreateMutateAsync.mockClear()
    mockUpdateMutateAsync.mockClear()
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
    document.body.innerHTML = ''
  })

  function save() {
    return act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement
      ).click()
    })
  }

  it('offers the checkbox only for skip, with helper text naming where it is honored', () => {
    act(() => root.render(<RuleEditor mode="create" onClose={() => {}} />))

    expect(container.querySelector(CHECKBOX)).not.toBeNull()
    expect(container.textContent).toContain('Checked on Gmail today')

    act(() =>
      setSelectValue(
        container.querySelector('[data-testid="rule-editor-action"]') as HTMLSelectElement,
        'metadata_only',
      ),
    )
    expect(container.querySelector(CHECKBOX)).toBeNull()
  })

  it('persists the option in the condition payload and drops it when the verdict changes', async () => {
    act(() => root.render(<RuleEditor mode="create" onClose={() => {}} />))
    act(() =>
      setInputValue(
        container.querySelector('[data-testid="rule-editor-condition-domain"]') as HTMLInputElement,
        'bulk.example',
      ),
    )
    act(() => (container.querySelector(CHECKBOX) as HTMLInputElement).click())
    await save()

    const created = (mockCreateMutateAsync.mock.calls[0] as unknown[])[0] as Record<string, unknown>
    expect(created.condition).toMatchObject({ domain: 'bulk.example', except_known_contacts: true })

    act(() =>
      setSelectValue(
        container.querySelector('[data-testid="rule-editor-action"]') as HTMLSelectElement,
        'metadata_only',
      ),
    )
    await save()
    const next = (mockCreateMutateAsync.mock.calls[1] as unknown[])[0] as Record<string, unknown>
    expect(next.condition).not.toHaveProperty('except_known_contacts')
  })

  it('shows a stored option checked when editing and keeps it on save', async () => {
    const rule: IngestionRule = {
      id: 'rule-001',
      scope: 'global',
      rule_type: 'sender_domain',
      condition: { domain: 'bulk.example', match: 'exact', except_known_contacts: true },
      action: 'skip',
      priority: 10,
      enabled: true,
      name: 'Bulk',
      description: null,
      created_by: 'dashboard',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
      deleted_at: null,
    }
    act(() => root.render(<RuleEditor mode="edit" rule={rule} onClose={() => {}} />))

    expect((container.querySelector(CHECKBOX) as HTMLInputElement).checked).toBe(true)
    await save()
    const body = (mockUpdateMutateAsync.mock.calls[0] as unknown[])[0] as {
      body: { condition: Record<string, unknown> }
    }
    expect(body.body.condition.except_known_contacts).toBe(true)
  })
})
