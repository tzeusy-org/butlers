/**
 * ArchivedRulesSection — collapsible section for disabled/archived rules.
 *
 * Shows count + expand to view. Restore action available per row.
 * Starts collapsed.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline" archived rules
 * Reference: pr/overview/ingestion-redesign/ingestion-filters.jsx §archived section
 */

import { useState } from 'react'
import type { IngestionRule } from '@/api/types'

// ---------------------------------------------------------------------------
// ArchivedRulesSection
// ---------------------------------------------------------------------------

export interface ArchivedRulesSectionProps {
  rules: IngestionRule[]
  onRestore?: (id: string) => void
  restoreError?: string | null
}

export function ArchivedRulesSection({ rules, onRestore, restoreError }: ArchivedRulesSectionProps) {
  const [expanded, setExpanded] = useState(false)

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
          className="mt-2 font-mono text-[11px] text-[color:var(--filter-red,oklch(0.62_0.20_25))] border border-[color:var(--filter-red,oklch(0.62_0.20_25))]/30 px-3 py-2"
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
              className="grid gap-3.5 py-3 border-b border-border/50 items-baseline opacity-55"
              style={{ gridTemplateColumns: '12px 1fr auto' }}
              data-testid={`archived-rule-row-${rule.id}`}
            >
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
