/**
 * RuleRow — one rule row in a gate section.
 *
 * Displays: enabled dot · rule name · condition summary · action badge ·
 *           toggle · edit · delete
 *
 * Design: hairline-divided, same density as ConnectorsRoster rows.
 *
 * Delete is a two-step inline confirm (click -> "delete?" / "cancel" ->
 * confirm) rather than a single-click destructive action. No modal — the
 * confirm state lives inline in the same row (bu-4utdw.9).
 *
 * Condition summary renders in exactly ONE place (the when-clause
 * pseudocode below the rule name); a duplicate compact column was removed
 * (bu-4utdw.9).
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline" rule rows
 * Reference: (ingestion dispatch redesign, graduated) ingestion-filters.jsx §RuleRow
 */

import { useState } from 'react'
import type { IngestionRule } from '@/api/types'
import { Switch } from '@/components/ui/switch'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Convert a condition object to a short, readable summary. */
function conditionSummary(condition: Record<string, unknown>): string {
  const entries = Object.entries(condition)
  if (entries.length === 0) return 'always'
  const summary = entries
    .slice(0, 2)
    .map(([k, v]) => (k && v != null ? `${k}: ${String(v)}` : null))
    .filter(Boolean)
    .join(' · ')
  return entries.length > 2 ? `${summary} …` : summary
}

/** Map action string to a color token. */
function actionColor(action: string): string {
  const verb = action.toLowerCase().split(' ')[0]
  if (verb === 'drop') return 'text-[var(--red)]'
  if (verb === 'tier') return 'text-[var(--amber)]'
  if (verb === 'route') return 'text-foreground'
  return 'text-muted-foreground'
}

// ---------------------------------------------------------------------------
// RuleRow
// ---------------------------------------------------------------------------

export interface RuleRowProps {
  rule: IngestionRule
  onToggle?: (id: string, enabled: boolean) => void
  onEdit?: (id: string) => void
  onDelete?: (id: string) => void
}

export function RuleRow({ rule, onToggle, onEdit, onDelete }: RuleRowProps) {
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const colorClass = actionColor(rule.action)
  const dotColor = rule.enabled
    ? 'bg-[var(--green)]'
    : 'bg-muted-foreground/30'

  return (
    <div
      className="grid items-start gap-4 py-4 border-b border-border/50"
      style={{ gridTemplateColumns: '12px 1fr 100px 40px auto' }}
      data-testid={`rule-row-${rule.id}`}
    >
      {/* Enabled dot */}
      <span
        className={`mt-1.5 inline-block w-1.5 h-1.5 rounded-full ${dotColor}`}
        aria-label={rule.enabled ? 'enabled' : 'disabled'}
      />

      {/* Name + condition */}
      <div className="min-w-0">
        <div className="font-medium text-sm leading-tight tracking-[-0.005em] truncate">
          {rule.name ?? rule.id.slice(0, 8)}
        </div>
        {rule.description && (
          <p className="font-serif italic text-[12.5px] text-muted-foreground mt-0.5 leading-snug">
            {rule.description}
          </p>
        )}
        <code className="block mt-1.5 px-2.5 py-1.5 text-[11px] font-mono bg-foreground/[0.025] border border-border/50 leading-relaxed">
          <span className="text-muted-foreground">when </span>
          <span>{conditionSummary(rule.condition)}</span>
          <span className="text-muted-foreground"> → </span>
          <span className={colorClass}>{rule.action}</span>
        </code>
      </div>

      {/* Action badge */}
      <div
        className={`font-mono text-[11px] tracking-[0.04em] self-start pt-0.5 ${colorClass}`}
        data-testid={`rule-action-${rule.id}`}
      >
        {rule.action.split(' ')[0]}
      </div>

      {/* Toggle */}
      <Switch
        checked={rule.enabled}
        onCheckedChange={(checked) => onToggle?.(rule.id, checked)}
        aria-label={`${rule.enabled ? 'Disable' : 'Enable'} rule ${rule.name ?? rule.id}`}
        data-testid={`rule-toggle-${rule.id}`}
        className="self-start mt-0.5"
      />

      {/* Edit + delete */}
      <div className="flex items-center gap-2 self-start pt-0.5">
        <button
          type="button"
          className="font-mono text-[10px] text-muted-foreground hover:text-foreground underline underline-offset-2 decoration-muted-foreground/30"
          onClick={() => onEdit?.(rule.id)}
          aria-label={`Edit rule ${rule.name ?? rule.id}`}
          data-testid={`rule-edit-${rule.id}`}
        >
          edit
        </button>
        {confirmingDelete ? (
          <span className="flex items-center gap-1.5 whitespace-nowrap">
            <button
              type="button"
              className="font-mono text-[10px] text-[var(--red)] underline underline-offset-2"
              onClick={() => {
                onDelete?.(rule.id)
                setConfirmingDelete(false)
              }}
              aria-label={`Confirm delete rule ${rule.name ?? rule.id}`}
              data-testid={`rule-delete-confirm-${rule.id}`}
            >
              delete?
            </button>
            <button
              type="button"
              className="font-mono text-[10px] text-muted-foreground hover:text-foreground"
              onClick={() => setConfirmingDelete(false)}
              aria-label="Cancel delete"
              data-testid={`rule-delete-cancel-${rule.id}`}
            >
              cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="font-mono text-[12px] text-muted-foreground hover:text-[var(--red)]"
            onClick={() => setConfirmingDelete(true)}
            aria-label={`Delete rule ${rule.name ?? rule.id}`}
            data-testid={`rule-delete-${rule.id}`}
          >
            ×
          </button>
        )}
      </div>
    </div>
  )
}
