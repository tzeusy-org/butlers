// @vitest-environment jsdom
/**
 * RuleEditor — unit tests for the create/edit verdict authoring flow.
 *
 * Regression focus (bu-4rt0h): the editor MUST emit an action the runtime
 * policy engine actually honors. The old vocabulary (drop/preserve/tier/route)
 * was inert — the evaluator never matched it and the backend rejected it at
 * create with 422. The canonical global-scope vocabulary is:
 *   skip / metadata_only / low_priority_queue / pass_through / route_to:<butler>
 * (see src/butlers/ingestion_policy.py + roster/switchboard/api/models.py).
 *
 * Covered:
 * - default create verdict is a runtime-valid action ('skip'), never 'drop'
 * - the verdict <select> only offers runtime-valid options
 * - selecting "route to butler" reveals a target field and emits route_to:<x>
 * - route_to with an empty target is blocked client-side (no API call)
 * - edit mode round-trips a stored route_to:<butler> action back into the form
 * - DSL test panel sends full envelope shape: headers, mime_parts, raw_key (bu-95ido)
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

// ---------------------------------------------------------------------------
// Mocks — declared before component imports
// ---------------------------------------------------------------------------

const mockCreateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))
const mockUpdateMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))
const mockTestMutateAsync = vi.fn(() => Promise.resolve({ data: {} }))

vi.mock('@/hooks/use-ingestion-rules', () => ({
  useCreateIngestionRule: () => ({
    mutateAsync: mockCreateMutateAsync,
    isPending: false,
  }),
  useUpdateIngestionRule: () => ({
    mutateAsync: mockUpdateMutateAsync,
    isPending: false,
  }),
  useTestIngestionRule: () => ({
    mutateAsync: mockTestMutateAsync,
    isPending: false,
  }),
}))

import type { IngestionRule } from '@/api/types'
import { RuleEditor } from './RuleEditor'

// ---------------------------------------------------------------------------
// DOM helpers
// ---------------------------------------------------------------------------

function makeRoot(): { container: HTMLDivElement; root: Root } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  return { container, root }
}

function cleanup(root: Root, container: HTMLDivElement) {
  act(() => root.unmount())
  container.remove()
  document.body.innerHTML = ''
}

function setInputValue(el: HTMLInputElement | HTMLSelectElement, value: string) {
  const proto =
    el instanceof HTMLSelectElement
      ? HTMLSelectElement.prototype
      : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

function makeRule(overrides: Partial<IngestionRule> = {}): IngestionRule {
  return {
    id: 'rule-001',
    scope: 'global',
    rule_type: 'sender_domain',
    condition: { domain: 'chase.com', match: 'exact' },
    action: 'route_to:finance',
    priority: 10,
    enabled: true,
    name: 'Route Chase to finance',
    description: null,
    created_by: 'dashboard',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    deleted_at: null,
    ...overrides,
  }
}

// The runtime/global verdict vocabulary the evaluator dispatches on. The
// editor's <option> set must be a subset of these (route_to is the prefixed
// special case rendered as a bare value with a target field).
const RUNTIME_VERDICTS = new Set([
  'skip',
  'metadata_only',
  'low_priority_queue',
  'pass_through',
  'route_to',
])

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('RuleEditor — runtime-valid verdict vocabulary (bu-4rt0h)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockCreateMutateAsync.mockClear()
    mockUpdateMutateAsync.mockClear()
  })
  afterEach(() => cleanup(root, container))

  it('offers only runtime-valid verdict options (never the inert drop/preserve/tier/route set)', () => {
    act(() => {
      root.render(
        <RuleEditor mode="create" onClose={() => {}} />,
      )
    })

    const select = container.querySelector(
      '[data-testid="rule-editor-action"]',
    ) as HTMLSelectElement
    expect(select).not.toBeNull()

    const optionValues = Array.from(select.options).map((o) => o.value)
    expect(optionValues.length).toBeGreaterThan(0)
    for (const value of optionValues) {
      expect(
        RUNTIME_VERDICTS.has(value),
        `verdict option "${value}" is not in the runtime vocabulary`,
      ).toBe(true)
    }
    // The old inert verbs must be gone.
    for (const dead of ['drop', 'preserve', 'tier', 'route']) {
      expect(optionValues).not.toContain(dead)
    }
  })

  it('defaults the create verdict to a runtime-valid action (skip), and submits it', async () => {
    act(() => {
      root.render(
        <RuleEditor mode="create" onClose={() => {}} />,
      )
    })

    // Fill the required sender_domain condition field.
    const domain = container.querySelector(
      '[data-testid="rule-editor-condition-domain"]',
    ) as HTMLInputElement
    act(() => setInputValue(domain, 'spam.example.com'))

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement
      ).click()
    })

    expect(mockCreateMutateAsync).toHaveBeenCalledTimes(1)
    const body = (mockCreateMutateAsync.mock.calls[0] as unknown[])[0] as Record<
      string,
      unknown
    >
    expect(body.action).toBe('skip')
    expect(body.scope).toBe('global')
  })

  it('emits route_to:<butler> when the route-to verdict is selected with a target', async () => {
    act(() => {
      root.render(
        <RuleEditor mode="create" onClose={() => {}} />,
      )
    })

    const domain = container.querySelector(
      '[data-testid="rule-editor-condition-domain"]',
    ) as HTMLInputElement
    act(() => setInputValue(domain, 'chase.com'))

    const select = container.querySelector(
      '[data-testid="rule-editor-action"]',
    ) as HTMLSelectElement
    act(() => setInputValue(select, 'route_to'))

    // The target-butler field appears only for route_to.
    const target = container.querySelector(
      '[data-testid="rule-editor-route-target"]',
    ) as HTMLInputElement
    expect(target, 'route target field should appear for route_to').not.toBeNull()
    act(() => setInputValue(target, 'finance'))

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement
      ).click()
    })

    expect(mockCreateMutateAsync).toHaveBeenCalledTimes(1)
    const body = (mockCreateMutateAsync.mock.calls[0] as unknown[])[0] as Record<
      string,
      unknown
    >
    expect(body.action).toBe('route_to:finance')
  })

  it('blocks route_to with an empty target (no API call)', async () => {
    act(() => {
      root.render(
        <RuleEditor mode="create" onClose={() => {}} />,
      )
    })

    const domain = container.querySelector(
      '[data-testid="rule-editor-condition-domain"]',
    ) as HTMLInputElement
    act(() => setInputValue(domain, 'chase.com'))

    const select = container.querySelector(
      '[data-testid="rule-editor-action"]',
    ) as HTMLSelectElement
    act(() => setInputValue(select, 'route_to'))

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement
      ).click()
    })

    expect(mockCreateMutateAsync).not.toHaveBeenCalled()
    expect(container.querySelector('[data-testid="rule-editor-error"]')).not.toBeNull()
  })

  it('round-trips a stored route_to:<butler> action in edit mode', async () => {
    act(() => {
      root.render(
        <RuleEditor mode="edit" rule={makeRule()} onClose={() => {}} />,
      )
    })

    const select = container.querySelector(
      '[data-testid="rule-editor-action"]',
    ) as HTMLSelectElement
    expect(select.value).toBe('route_to')

    const target = container.querySelector(
      '[data-testid="rule-editor-route-target"]',
    ) as HTMLInputElement
    expect(target.value).toBe('finance')

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-save"]') as HTMLButtonElement
      ).click()
    })

    expect(mockUpdateMutateAsync).toHaveBeenCalledTimes(1)
    const arg = (mockUpdateMutateAsync.mock.calls[0] as unknown[])[0] as {
      id: string
      body: Record<string, unknown>
    }
    expect(arg.id).toBe('rule-001')
    expect(arg.body.action).toBe('route_to:finance')
  })
})

// ---------------------------------------------------------------------------
// DSL test panel — full envelope shape (bu-95ido)
// ---------------------------------------------------------------------------

describe('RuleEditor DSL test panel — sends full envelope shape (bu-95ido)', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    ;({ container, root } = makeRoot())
    mockTestMutateAsync.mockClear()
    mockTestMutateAsync.mockResolvedValue({
      data: {
        matched: false,
        decision: null,
        target_butler: null,
        matched_rule_id: null,
        matched_rule_type: null,
        reason: 'no match',
      },
    })
  })
  afterEach(() => cleanup(root, container))

  it('renders header, mime_parts, and raw_key input fields in the DSL panel', () => {
    act(() => {
      root.render(<RuleEditor mode="dsl" onClose={() => {}} />)
    })

    expect(
      container.querySelector('[data-testid="rule-editor-dsl-panel"]'),
      'DSL panel should be visible in dsl mode',
    ).not.toBeNull()

    expect(
      container.querySelector('[data-testid="rule-editor-test-headers"]'),
      'headers textarea missing from DSL panel',
    ).not.toBeNull()

    expect(
      container.querySelector('[data-testid="rule-editor-test-mime-parts"]'),
      'mime_parts field missing from DSL panel',
    ).not.toBeNull()

    expect(
      container.querySelector('[data-testid="rule-editor-test-raw-key"]'),
      'raw_key field missing from DSL panel',
    ).not.toBeNull()
  })

  it('sends headers parsed from "Key: Value" lines in the envelope', async () => {
    act(() => {
      root.render(<RuleEditor mode="dsl" onClose={() => {}} />)
    })

    const headersTextarea = container.querySelector(
      '[data-testid="rule-editor-test-headers"]',
    ) as HTMLTextAreaElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set
      setter?.call(headersTextarea, 'List-Unsubscribe: <mailto:unsub@example.com>\nX-Priority: high')
      headersTextarea.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-test-run"]') as HTMLButtonElement
      ).click()
    })

    expect(mockTestMutateAsync).toHaveBeenCalledTimes(1)
    const call = (mockTestMutateAsync.mock.calls[0] as unknown[])[0] as {
      envelope: Record<string, unknown>
    }
    expect(call.envelope.headers).toEqual({
      'List-Unsubscribe': '<mailto:unsub@example.com>',
      'X-Priority': 'high',
    })
  })

  it('sends mime_parts as a parsed array in the envelope', async () => {
    act(() => {
      root.render(<RuleEditor mode="dsl" onClose={() => {}} />)
    })

    const mimeInput = container.querySelector(
      '[data-testid="rule-editor-test-mime-parts"]',
    ) as HTMLInputElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(mimeInput, 'text/calendar, image/png')
      mimeInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-test-run"]') as HTMLButtonElement
      ).click()
    })

    expect(mockTestMutateAsync).toHaveBeenCalledTimes(1)
    const call = (mockTestMutateAsync.mock.calls[0] as unknown[])[0] as {
      envelope: Record<string, unknown>
    }
    expect(call.envelope.mime_parts).toEqual(['text/calendar', 'image/png'])
  })

  it('sends raw_key in the envelope', async () => {
    act(() => {
      root.render(<RuleEditor mode="dsl" onClose={() => {}} />)
    })

    const rawKeyInput = container.querySelector(
      '[data-testid="rule-editor-test-raw-key"]',
    ) as HTMLInputElement
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      setter?.call(rawKeyInput, 'uid-abc-123')
      rawKeyInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-test-run"]') as HTMLButtonElement
      ).click()
    })

    expect(mockTestMutateAsync).toHaveBeenCalledTimes(1)
    const call = (mockTestMutateAsync.mock.calls[0] as unknown[])[0] as {
      envelope: Record<string, unknown>
    }
    expect(call.envelope.raw_key).toBe('uid-abc-123')
  })

  it('sends all envelope fields together when all are populated', async () => {
    act(() => {
      root.render(<RuleEditor mode="dsl" onClose={() => {}} />)
    })

    const senderInput = container.querySelector(
      '[data-testid="rule-editor-test-sender"]',
    ) as HTMLInputElement
    const channelInput = container.querySelector(
      '[data-testid="rule-editor-test-channel"]',
    ) as HTMLInputElement
    const headersTextarea = container.querySelector(
      '[data-testid="rule-editor-test-headers"]',
    ) as HTMLTextAreaElement
    const mimeInput = container.querySelector(
      '[data-testid="rule-editor-test-mime-parts"]',
    ) as HTMLInputElement
    const rawKeyInput = container.querySelector(
      '[data-testid="rule-editor-test-raw-key"]',
    ) as HTMLInputElement

    act(() => {
      const inputSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
      const textareaSetter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set

      inputSetter?.call(senderInput, 'alerts@example.com')
      senderInput.dispatchEvent(new Event('change', { bubbles: true }))

      inputSetter?.call(channelInput, 'gmail')
      channelInput.dispatchEvent(new Event('change', { bubbles: true }))

      textareaSetter?.call(headersTextarea, 'X-Custom: yes')
      headersTextarea.dispatchEvent(new Event('change', { bubbles: true }))

      inputSetter?.call(mimeInput, 'text/calendar')
      mimeInput.dispatchEvent(new Event('change', { bubbles: true }))

      inputSetter?.call(rawKeyInput, 'uid-xyz')
      rawKeyInput.dispatchEvent(new Event('change', { bubbles: true }))
    })

    await act(async () => {
      ;(
        container.querySelector('[data-testid="rule-editor-test-run"]') as HTMLButtonElement
      ).click()
    })

    expect(mockTestMutateAsync).toHaveBeenCalledTimes(1)
    const call = (mockTestMutateAsync.mock.calls[0] as unknown[])[0] as {
      envelope: Record<string, unknown>
    }
    expect(call.envelope.sender_address).toBe('alerts@example.com')
    expect(call.envelope.source_channel).toBe('gmail')
    expect(call.envelope.headers).toEqual({ 'X-Custom': 'yes' })
    expect(call.envelope.mime_parts).toEqual(['text/calendar'])
    expect(call.envelope.raw_key).toBe('uid-xyz')
  })
})
