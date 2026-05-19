// ---------------------------------------------------------------------------
// Pill — mono toggle pill primitive (bu-ec2wb)
//
// A mono-font, pill-shaped label that can be in a selected/unselected state.
// Used for filter toggles, state chips, and count indicators on the entities
// index page (e.g. "unidentified", "duplicate", "stale" filter chips).
//
// Brief §2: "Mono toggle pill. Use existing frontend/src/components/ui/badge.tsx
//            or add Pill variant." — builds on badge.tsx tokens/shape, adds
//            toggle (selected) semantics and mono font.
// Amendment 9: Reuses existing border, mfg, and fg/bg tokens only.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface PillProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** When true, renders in the active/selected state. */
  selected?: boolean
  /** Count shown after the label (optional). */
  count?: number
  /** Children should be a short label string. */
  children: React.ReactNode
}

/**
 * Mono toggle pill. Renders as a `<button>` for toggle affordance.
 *
 * Selected state: high-contrast (fg text, fg border).
 * Unselected state: muted (mfg text, soft border), hover lifts to fg.
 *
 * @example
 *   <Pill selected={false} onClick={() => setFilter("unidentified")}>
 *     unidentified
 *   </Pill>
 *   <Pill selected count={3}>duplicate</Pill>
 */
export function Pill({ selected = false, count, children, className, ...props }: PillProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={selected}
      className={cn(
        // Shape
        "inline-flex items-center gap-1",
        "h-6 rounded-full px-2.5",
        // Typography — mono, eyebrow-scale
        "font-mono text-[10px] font-medium uppercase tracking-wide leading-none",
        // Border
        "border",
        // Transitions
        "transition-colors",
        // Base (unselected)
        "text-[var(--mfg,oklch(0.708_0_0))] border-[var(--border,oklch(1_0_0/0.10))] bg-transparent",
        // Selected override
        selected && "text-[var(--fg)] border-[var(--fg)] bg-transparent",
        // Hover (unselected only)
        !selected && "hover:text-[var(--fg)] hover:border-[var(--border-strong,oklch(1_0_0/0.18))]",
        // Focus
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30",
        // Disabled
        "disabled:pointer-events-none disabled:opacity-40",
        className,
      )}
      {...props}
    >
      {children}
      {count !== undefined && (
        <span
          aria-label={`${count} ${count === 1 ? "item" : "items"}`}
          className="tabular-nums"
        >
          {count}
        </span>
      )}
    </button>
  )
}
