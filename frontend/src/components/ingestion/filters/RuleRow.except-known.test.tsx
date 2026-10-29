// @vitest-environment jsdom
/** RuleRow — the rule list shows the "except people I know" exception (bu-q7vx1q.88.3). */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'

;(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true

import type { IngestionRule } from '@/api/types'
import { RuleRow } from './RuleRow'

function rule(condition: Record<string, unknown>): IngestionRule {
  return {
    id: 'rule-001',
    scope: 'global',
    rule_type: 'sender_domain',
    condition,
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
}

describe('RuleRow — except people I know', () => {
  let container: HTMLDivElement
  let root: Root

  beforeEach(() => {
    container = document.createElement('div')
    document.body.appendChild(container)
    root = createRoot(container)
  })
  afterEach(() => {
    act(() => root.unmount())
    container.remove()
  })

  it('shows the exception on a flagged rule and keeps the flag out of the condition summary', () => {
    act(() =>
      root.render(
        <RuleRow
          rule={rule({ domain: 'bulk.example', match: 'exact', except_known_contacts: true })}
        />,
      ),
    )

    const badge = container.querySelector('[data-testid="rule-except-known-rule-001"]')
    expect(badge?.textContent).toContain('except people I know')
    expect(container.textContent).not.toContain('except_known_contacts')
  })

  it('shows nothing extra on an unflagged rule', () => {
    act(() =>
      root.render(<RuleRow rule={rule({ domain: 'bulk.example', match: 'exact' })} />),
    )

    expect(container.querySelector('[data-testid="rule-except-known-rule-001"]')).toBeNull()
  })
})
