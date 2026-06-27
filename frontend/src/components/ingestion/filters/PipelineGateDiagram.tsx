/**
 * PipelineGateDiagram — five-gate horizontal flow diagram.
 *
 * Renders: accept → dedupe → tier → route → execute
 * Each gate is a labeled node connected by an arrow.
 *
 * Design: pure CSS/SVG — no graph library. Follows the Dispatch design
 * language: mono uppercase eyebrows, tabular numerals, hairline borders,
 * state colors as foreground/border signals only.
 *
 * The proportional funnel bar below the gate row shows per-gate output
 * relative to the total received. The route gate renders two segments:
 *   - solid foreground = dispatched (routed to a butler)
 *   - amber = preserved-without-dispatch (logged for audit, not routed)
 *   - red = hard drops (the accept gate's filtered count)
 *
 * Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
 *       dashboard-ingestion-dispatch-console/spec.md §"Filters Pipeline"
 * Reference: (ingestion dispatch redesign, graduated) ingestion-filters.jsx §PipelineDiagram
 */

import type { GateCount, GateDefinition } from './gate-state'
import { GATE_DEFS } from './gate-state'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  if (n >= 10_000) return Math.round(n / 1000) + 'k'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n.toLocaleString()
}

// ---------------------------------------------------------------------------
// Gate node
// ---------------------------------------------------------------------------

interface GateNodeProps {
  def: GateDefinition
  count: GateCount
  index: number
}

function GateNode({ def, count, index }: GateNodeProps) {
  const isFirst = index === 0
  const isLast = index === 4
  const hasDrop = count.dropped > 0
  const hasPreserved = count.preserved > 0

  return (
    <div
      className={`flex-1 min-w-0 ${!isFirst ? 'border-l border-border pl-4' : ''} ${!isLast ? 'pr-4' : ''}`}
      data-testid={`gate-node-${def.key}`}
    >
      {/* Eyebrow */}
      <p className="font-mono text-[9.5px] tracking-[0.14em] uppercase text-muted-foreground/70 mb-1.5">
        §{index + 1} · {def.label}
      </p>

      {/* Count + delta */}
      <div className="flex items-baseline gap-2">
        <span
          className="font-mono text-2xl font-medium tracking-[-0.02em] tabular-nums"
          title={count.estimated ? 'Estimated: no per-gate measurement available from this endpoint' : undefined}
          data-testid={count.estimated ? `gate-count-estimated-${def.key}` : undefined}
        >
          {count.estimated ? '~' : ''}{fmt(count.out)}
        </span>
        {hasDrop && (
          <span
            className="font-mono text-[10px] tracking-[0.04em] text-[color:var(--filter-red,oklch(0.62_0.20_25))]"
            data-testid={`gate-drop-${def.key}`}
          >
            −{fmt(count.dropped)}
          </span>
        )}
        {hasPreserved && (
          <span
            className="font-mono text-[10px] tracking-[0.04em] text-[color:var(--filter-amber,oklch(0.72_0.12_70))]"
            data-testid={`gate-preserved-${def.key}`}
          >
            −{fmt(count.preserved)} pres.
          </span>
        )}
        {count.estimated && (
          <span
            className="font-mono text-[9px] tracking-[0.04em] text-muted-foreground/50 self-center"
            data-testid={`gate-estimated-badge-${def.key}`}
            title="Estimated: no per-gate measurement available from this endpoint"
          >
            est.
          </span>
        )}
      </div>

      {/* Gloss */}
      <p className="font-mono text-[9.5px] text-muted-foreground/60 mt-1 leading-tight">
        {def.gloss.slice(0, 60)}…
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Funnel bar
// ---------------------------------------------------------------------------

interface FunnelBarProps {
  counts: GateCount[]
  totalIn: number
}

function FunnelBar({ counts, totalIn }: FunnelBarProps) {
  if (totalIn === 0) {
    return (
      <div
        className="mt-4 h-2 w-full bg-foreground/5"
        data-testid="funnel-bar-unavailable"
      />
    )
  }

  return (
    <div
      className="mt-4 flex h-2 w-full"
      data-testid="funnel-bar"
      role="img"
      aria-label="Proportional pipeline funnel"
    >
      {counts.map((c, i) => {
        const isLast = i === counts.length - 1
        // Width proportional to "in" count of this gate
        const widthPct = (c.in / totalIn) * 100

        // For the route gate we split into dispatched + preserved
        const preservedPct = c.preserved > 0 && c.in > 0
          ? (c.preserved / c.in) * 100
          : 0
        const droppedPct = c.dropped > 0 && c.in > 0
          ? (c.dropped / c.in) * 100
          : 0
        const passPct = 100 - droppedPct - preservedPct

        return (
          <div
            key={c.key}
            className={`relative overflow-hidden ${!isLast ? 'border-r border-background' : ''}`}
            style={{ width: `${widthPct}%` }}
            data-testid={`funnel-segment-${c.key}`}
          >
            {/* Pass-through (solid) */}
            <div
              className="absolute inset-y-0 left-0 bg-foreground/80"
              style={{ width: `${passPct}%` }}
            />
            {/* Preserved-without-dispatch (amber) — route gate only */}
            {preservedPct > 0 && (
              <div
                className="absolute inset-y-0 bg-[color:var(--filter-amber,oklch(0.72_0.12_70))]/60"
                style={{ left: `${passPct}%`, width: `${preservedPct}%` }}
                data-testid="funnel-preserved-segment"
              />
            )}
            {/* Hard drops (red) — accept gate */}
            {droppedPct > 0 && (
              <div
                className="absolute inset-y-0 bg-[color:var(--filter-red,oklch(0.62_0.20_25))]/60"
                style={{ left: `${passPct + preservedPct}%`, width: `${droppedPct}%` }}
                data-testid="funnel-dropped-segment"
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PipelineGateDiagram
// ---------------------------------------------------------------------------

export interface PipelineGateDiagramProps {
  counts: GateCount[]
  /** Whether the backend metrics are available. */
  available: boolean
}

export function PipelineGateDiagram({ counts, available }: PipelineGateDiagramProps) {
  const totalIn = counts[0]?.in ?? 0

  return (
    <div
      className="border-t border-b border-border py-6"
      data-testid="pipeline-gate-diagram"
    >
      {!available && (
        <p className="font-mono text-[10px] tracking-[0.14em] uppercase text-muted-foreground/60 mb-4">
          metrics unavailable · counts are zero
        </p>
      )}

      {/* Gate row */}
      <div className="flex gap-0">
        {GATE_DEFS.map((def, i) => (
          <GateNode
            key={def.key}
            def={def}
            count={counts[i] ?? { key: def.key, in: 0, out: 0, preserved: 0, dropped: 0 }}
            index={i}
          />
        ))}
      </div>

      {/* Proportional funnel bar */}
      <FunnelBar counts={counts} totalIn={totalIn} />

      {/* Axis labels */}
      <div className="mt-2 flex justify-between font-mono text-[9.5px] text-muted-foreground/60">
        <span>received · {fmt(totalIn)}</span>
        <span className="flex items-center gap-4">
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-foreground/80 rounded-sm" />
            dispatched
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-[color:var(--filter-amber,oklch(0.72_0.12_70))]/60 rounded-sm" />
            preserved
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-2 h-2 bg-[color:var(--filter-red,oklch(0.62_0.20_25))]/60 rounded-sm" />
            dropped
          </span>
        </span>
        <span>dispatched · {fmt(counts[counts.length - 1]?.out ?? 0)}</span>
      </div>
    </div>
  )
}
