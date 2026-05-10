import * as React from "react"

import { cn } from "@/lib/utils"

export type RangeValue = "24h" | "7d" | "30d"

const RANGE_OPTIONS: { value: RangeValue; label: string }[] = [
  { value: "24h", label: "24H" },
  { value: "7d", label: "7D" },
  { value: "30d", label: "30D" },
]

export interface RangeToggleProps {
  value: RangeValue
  onChange: (value: RangeValue) => void
  className?: string
}

/**
 * RangeToggle — three-button group for selecting a time range.
 *
 * Active button: bg-foreground text-background
 * Inactive button: bg-transparent text-foreground border-border
 * Labels: mono uppercase 10px tabular-nums
 * Motion contract: transition-colors only (no width/transform animation)
 */
export function RangeToggle({ value, onChange, className }: RangeToggleProps) {
  return (
    <div
      data-slot="range-toggle"
      role="group"
      aria-label="Time range"
      className={cn("inline-flex items-center rounded-md border border-border", className)}
    >
      {RANGE_OPTIONS.map(({ value: optValue, label }) => {
        const isActive = value === optValue
        return (
          <button
            key={optValue}
            type="button"
            aria-pressed={isActive}
            onClick={() => onChange(optValue)}
            className={cn(
              "inline-flex items-center justify-center px-2 py-1",
              "font-mono text-[10px] uppercase tabular-nums",
              "transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1",
              "disabled:pointer-events-none disabled:opacity-50",
              // Active: filled
              isActive && "bg-foreground text-background",
              // Inactive: transparent with border-aware text
              !isActive && "bg-transparent text-foreground hover:bg-muted",
              // Left button: rounded left corners
              optValue === "24h" && "rounded-l-sm",
              // Right button: rounded right corners
              optValue === "30d" && "rounded-r-sm",
            )}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
