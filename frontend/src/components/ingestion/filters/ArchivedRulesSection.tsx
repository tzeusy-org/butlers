/**
 * ArchivedRulesSection — collapsible section for disabled/archived rules.
 *
 * Shows count + expand to view. Restore action available per row.
 * Starts collapsed.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline" archived rules
 * Reference: (ingestion dispatch redesign, graduated) ingestion-filters.jsx §archived section
 */

import { useEffect, useRef, useState } from 'react'
import type { IngestionRule } from '@/api/types'

// ---------------------------------------------------------------------------
// ArchivedRulesSection
// ---------------------------------------------------------------------------

export interface ArchivedRulesSectionProps {
  rules: IngestionRule[]
  linkedRuleId?: string | null
  onLinkedRowReady?: (row: HTMLElement) => void
  onRestore?: (id: string) => void
  restoreError?: string | null
}

export function ArchivedRulesSection({ rules, linkedRuleId, onLinkedRowReady, onRestore, restoreError }: ArchivedRulesSectionProps) {
  const linkedRowRef = useRef<HTMLDivElement>(null)
  const archivedTarget = linkedRuleId && rules.some((rule) => rule.id === linkedRuleId) ? linkedRuleId : null
  const [view, setView] = useState({ target: archivedTarget, expanded: archivedTarget !== null })
  // Adjust only when the target changes. Manual collapse survives rerenders.
  if (view.target !== archivedTarget) {
    setView({ target: archivedTarget, expanded: archivedTarget !== null || view.expanded })
  }
  const expanded = view.target !== archivedTarget && archivedTarget !== null || view.expanded

  useEffect(() => {
    if (expanded && archivedTarget && linkedRowRef.current) onLinkedRowReady?.(linkedRowRef.current)
  }, [expanded, archivedTarget, onLinkedRowReady])

  if (rules.length === 0) return null

  return (
    <div
      className="mt-14"
      data-testid="archived-rules-section"
    >
      {/* Section toggle header */}
      <div className="flex items-baseline gap-3 py-3.5 border-b border-border">
        <span className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground">
          archived
        </span>
        <span
          className="font-mono text-[10px] text-muted-foreground/60"
          data-testid="archived-rules-count"
        >
          {rules.length} disabled rule{rules.length !== 1 ? 's' : ''}
        </span>
        <span className="ml-auto" />
        <button
          type="button"
          className="font-mono text-[10px] text-muted-foreground hover:text-foreground"
          onClick={() => setView((v) => ({ ...v, expanded: !v.expanded }))}
          aria-expanded={expanded}
          data-testid="archived-rules-toggle"
        >
          {expanded ? '↑ collapse' : '↓ expand'}
        </button>
      </div>

      {/* Restore error */}
      {restoreError && (
        <div
          className="mt-2 font-mono text-[11px] text-[var(--red-text)] border border-[var(--red)]/30 px-3 py-2"
          data-testid="archived-rules-restore-error"
        >
          {restoreError}
        </div>
      )}

      {/* Expanded rule list */}
      {expanded && (
        <div data-testid="archived-rules-list">
          {rules.map((rule) => (
            <div
              key={rule.id}
              ref={rule.id === archivedTarget ? linkedRowRef : undefined}
              tabIndex={rule.id === archivedTarget ? -1 : undefined}
              role={rule.id === archivedTarget ? 'group' : undefined}
              aria-label={rule.id === archivedTarget ? `Linked archived rule ${rule.name ?? rule.id}` : undefined}
              data-rule-id={rule.id}
              data-linked-rule={rule.id === archivedTarget ? 'true' : undefined}
              className={`grid gap-3.5 py-3 border-b border-border/50 items-baseline ${rule.id === archivedTarget ? 'border-l-2 border-l-[var(--focus)] pl-3 bg-foreground/[0.03]' : 'opacity-55'}`}
              style={{ gridTemplateColumns: '12px 1fr auto' }}
              data-testid={`archived-rule-row-${rule.id}`}
            >
              {rule.id === archivedTarget && <span className="col-span-full font-mono text-[10px] text-muted-foreground">Linked archived rule</span>}
              {/* Dot */}
              <span className="mt-1.5 inline-block w-1.5 h-1.5 rounded-full bg-muted-foreground/40" />

              {/* Name + note */}
              <div>
                <div className="font-serif italic text-sm text-muted-foreground">
                  {rule.name ?? rule.id.slice(0, 8)}
                </div>
                {rule.description && (
                  <span className="block font-mono text-[10px] text-muted-foreground/60 mt-1">
                    {rule.description}
                  </span>
                )}
              </div>

              {/* Restore */}
              <button
                type="button"
                className="font-mono text-[10px] border border-foreground/20 px-2 py-0.5 hover:bg-foreground/5 transition-colors text-muted-foreground hover:text-foreground"
                onClick={() => onRestore?.(rule.id)}
                aria-label={`Restore rule ${rule.name ?? rule.id}`}
                data-testid={`archived-rule-restore-${rule.id}`}
              >
                restore
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
