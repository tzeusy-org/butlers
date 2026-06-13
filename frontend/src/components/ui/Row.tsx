// ---------------------------------------------------------------------------
// Row — canonical list-row primitive (bu-ovq7t)
//
// The single-source row layout for every entity view. Per Dispatch design
// language §4a "Lists (the canonical primitive)": a row is a CSS grid of
// optional leading mark / 1fr content / optional trailing meta, separated by
// hairline rules — never a card.
//
//   display: grid;
//   grid-template-columns: <mark> 1fr <meta>;
//   gap: 10–18px;
//   padding: <vertical> 0;
//   border-bottom: 1px solid var(--border);
//
// Affordance (§7): hover applies a 6%/5% tint (via the `interactive` flag);
// focus is a visible 2px outline handled by the consumer's focusable child.
// No transform, no shadow, no card chrome.
//
// This is a layout primitive only — it carries no entity-specific knowledge.
// Consumers compose EntityMark / TierBadge / StateDot / provenance primitives
// into the `mark`, `children`, and `meta` slots.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

/** Row density presets. Maps to the vertical padding scale in §3d. */
export type RowDensity = "scan" | "read"

const DENSITY_PADDING: Record<RowDensity, string> = {
  // Scanned lists (butler-index style): 10px vertical.
  scan: "py-2.5",
  // Read lists (attention style): 18px vertical.
  read: "py-[18px]",
}

export interface RowProps extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Optional leading mark slot (entity mark, status dot, severity glyph,
   * checkbox). Rendered in a fixed-auto-width first column. Omit for a
   * two-column (1fr / meta) row.
   */
  mark?: React.ReactNode
  /**
   * Optional trailing meta slot (time, weight, tags, actions). Rendered in a
   * fixed-auto-width last column, right-aligned. Omit for a leading-only row.
   */
  meta?: React.ReactNode
  /**
   * Row density. `scan` (default, 10px) for quietly scanned index lists;
   * `read` (18px) for attention lists that are actually read.
   */
  density?: RowDensity
  /**
   * When true, applies the canonical row hover tint (§7) and a row cursor.
   * Use for rows that navigate or select on interaction. The clickable target
   * itself should still be a real focusable element (button/link) inside.
   */
  interactive?: boolean
  /**
   * When false, suppresses the bottom hairline (use on the last row of a
   * group when the container already draws an edge). Defaults to true.
   */
  divider?: boolean
  /** The main 1fr content slot. */
  children?: React.ReactNode
}

/**
 * The canonical list row: `[mark] 1fr [meta]` CSS grid, hairline-separated.
 *
 * @example
 *   <Row
 *     mark={<EntityMark name="Alice" entityType="person" />}
 *     meta={<Time value={lastSeen} mode="relative" />}
 *     interactive
 *   >
 *     <span className="font-medium">Alice Johnson</span>
 *   </Row>
 */
export function Row({
  mark,
  meta,
  density = "scan",
  interactive = false,
  divider = true,
  className,
  children,
  ...props
}: RowProps) {
  // Build the grid template from which slots are present so empty columns
  // collapse rather than leaving a gap.
  const columns = [mark != null ? "auto" : null, "1fr", meta != null ? "auto" : null]
    .filter(Boolean)
    .join(" ")

  return (
    <div
      className={cn(
        "grid items-center gap-3",
        DENSITY_PADDING[density],
        divider && "border-b border-border last:border-0",
        interactive && "cursor-pointer hover:bg-foreground/[0.06]",
        className,
      )}
      style={{ gridTemplateColumns: columns }}
      {...props}
    >
      {mark != null && <div className="flex shrink-0 items-center">{mark}</div>}
      <div className="min-w-0">{children}</div>
      {meta != null && (
        <div className="flex shrink-0 items-center justify-end gap-3 text-right">{meta}</div>
      )}
    </div>
  )
}
