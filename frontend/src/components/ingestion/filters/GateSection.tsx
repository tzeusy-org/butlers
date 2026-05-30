/**
 * GateSection — one pipeline gate with its rules.
 *
 * Renders: eyebrow + gate headline + gloss + in/out count + rule rows.
 * If no rules exist, shows the code-resident policy note for gates that
 * have one, or a plain "no rules at this gate" serif italic.
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline" gate sections
 * Reference: pr/overview/ingestion-redesign/ingestion-filters.jsx §GateSection
 */

import type { IngestionRule } from '@/api/types'
import type { GateDefinition, GateCount } from './gate-state'
import { RuleRow } from './RuleRow'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n.toLocaleString()
}

// ---------------------------------------------------------------------------
// GateSection
// ---------------------------------------------------------------------------

export interface GateSectionProps {
  def: GateDefinition
  count: GateCount
  index: number
  rules: IngestionRule[]
  onToggleRule?: (id: string, enabled: boolean) => void
  onEditRule?: (id: string) => void
  onDeleteRule?: (id: string) => void
}

export function GateSection({
  def,
  count,
  index,
  rules,
  onToggleRule,
  onEditRule,
  onDeleteRule,
}: GateSectionProps) {
  const hasDrop = count.dropped > 0
  const hasPreserved = count.preserved > 0

  return (
    <div
      className="mb-10"
      data-testid={`gate-section-${def.key}`}
    >
      {/* Section header */}
      <div
        className="grid gap-5 items-baseline pb-3 border-b border-border"
        style={{ gridTemplateColumns: 'auto 1fr auto' }}
      >
        {/* Left: number + headline */}
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70">
            §{index + 1}
          </span>
          <h2 className="m-0 text-2xl font-medium tracking-[-0.02em] lowercase">
            {def.label}.
          </h2>
        </div>

        {/* Middle: gloss */}
        <p className="font-serif text-[15px] text-muted-foreground leading-snug max-w-[58ch]">
          {def.gloss}
        </p>

        {/* Right: counts */}
        <div className="text-right font-mono text-[10px] text-muted-foreground/70">
          <span>in {fmt(count.in)}</span>
          <span className="mx-1">·</span>
          <span>out {fmt(count.out)}</span>
          {hasDrop && (
            <span className="ml-1.5 text-[color:var(--filter-red,oklch(0.62_0.20_25))]">
              · −{fmt(count.dropped)}
            </span>
          )}
          {hasPreserved && (
            <span className="ml-1.5 text-[color:var(--filter-amber,oklch(0.72_0.12_70))]">
              · −{fmt(count.preserved)} pres.
            </span>
          )}
        </div>
      </div>

      {/* Rules or code-policy */}
      <div className="mt-1">
        {rules.length > 0 ? (
          rules.map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              onToggle={onToggleRule}
              onEdit={onEditRule}
              onDelete={onDeleteRule}
            />
          ))
        ) : (
          <p className="font-serif italic text-sm text-muted-foreground py-5 max-w-[70ch] leading-[1.55]">
            {def.codePolicy ?? 'No rules at this gate. Policy lives in code.'}
          </p>
        )}
      </div>
    </div>
  )
}
