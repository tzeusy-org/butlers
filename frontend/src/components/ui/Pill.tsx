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
//
// bu-86c4c.16 (JARVIS audit move 11): was role="switch" + aria-checked —
// wrong semantics for a filter chip (switch means an independent on/off
// setting; these are toggled selection state, "pressed" not "on"). Fixed to
// the standard ARIA toggle-button pattern (native <button>, aria-pressed);
// the count is folded into the button's own accessible name instead of an
// aria-label on a plain inner <span>, which AT discards.
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
  // Fold the count into the button's accessible name — an aria-label on the
  // inner count <span> is not itself a labelling element, so AT discards it
  // and only ever announces the label text, silently dropping the count.
  // Only auto-computed when the label is plain text and no explicit
  // aria-label was already supplied by the caller (props spreads after this
  // and wins on conflict).
  const computedAriaLabel =
    count !== undefined && typeof children === "string"
      ? `${children}, ${count} ${count === 1 ? "item" : "items"}`
      : undefined

  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={computedAriaLabel}
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
        selected && "text-fg border-fg bg-transparent",
        // Hover (unselected only)
        !selected && "hover:text-fg hover:border-[var(--border-strong,oklch(1_0_0/0.18))]",
        // Focus
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-fg/30",
        // Disabled
        "disabled:pointer-events-none disabled:opacity-40",
        className,
      )}
      {...props}
    >
      {children}
      {count !== undefined && (
        <span
          // Hidden from AT only when the count is already folded into the
          // button's own aria-label above (plain-text children). For a
          // non-text label there's no button-level aria-label to fold into,
          // so this span keeps its own aria-label as a fallback — better an
          // "unlabelled span" pattern than a count no AT ever announces.
          aria-hidden={computedAriaLabel ? "true" : undefined}
          aria-label={computedAriaLabel ? undefined : `${count} ${count === 1 ? "item" : "items"}`}
          className="tabular-nums"
        >
          {count}
        </span>
      )}
    </button>
  )
}
