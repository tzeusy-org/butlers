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
  onRestore?: (id: string) => void
  restoreError?: string | null
  highlightedRuleId?: string | null
  onTargetReady?: (row: HTMLDivElement) => void
}

export function ArchivedRulesSection({ rules, onRestore, restoreError, highlightedRuleId, onTargetReady }: ArchivedRulesSectionProps) {
  const [expanded, setExpanded] = useState(Boolean(highlightedRuleId))
  const [previousTarget, setPreviousTarget] = useState(highlightedRuleId)
  const targetRef = useRef<HTMLDivElement>(null)

  // Derive expansion during the target transition so the row mounts before
  // the focus effect. Manual collapse remains available after navigation.
  if (previousTarget !== highlightedRuleId) {
    setPreviousTarget(highlightedRuleId)
    if (highlightedRuleId) setExpanded(true)
  }

  useEffect(() => {
    if (expanded && highlightedRuleId && targetRef.current) onTargetReady?.(targetRef.current)
  }, [expanded, highlightedRuleId, onTargetReady])

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
          onClick={() => setExpanded((v) => !v)}
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
              ref={rule.id === highlightedRuleId ? targetRef : undefined}
              role="group"
              aria-label={`Archived rule ${rule.name ?? rule.id}`}
              aria-current={rule.id === highlightedRuleId ? 'true' : undefined}
              tabIndex={-1}
              className={`grid gap-3.5 py-3 border-b border-border/50 items-baseline ${rule.id === highlightedRuleId ? 'bg-foreground/[0.05] border-l-2 border-l-focus pl-3' : 'opacity-55'}`}
              style={{ gridTemplateColumns: '12px 1fr auto' }}
              data-testid={`archived-rule-row-${rule.id}`}
            >
              {/* Dot */}
              <span className="mt-1.5 inline-block w-1.5 h-1.5 rounded-full bg-muted-foreground/40" />

              {/* Name + note */}
              <div>
                <div className="font-serif italic text-sm text-muted-foreground">
                  {rule.name ?? rule.id.slice(0, 8)}
                  {rule.id === highlightedRuleId && <span className="ml-2 font-mono text-[10px]">linked rule</span>}
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
