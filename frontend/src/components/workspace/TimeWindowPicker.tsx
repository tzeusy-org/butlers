// ---------------------------------------------------------------------------
// TimeWindowPicker — bu-ig72b.20
//
// Three-preset + custom time-window picker for the Chronicles dashboard.
// State and URL sync live in useTimeWindow (src/hooks/use-time-window.ts).
// ---------------------------------------------------------------------------

import { isValid, parseISO } from "date-fns"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  formatWindowDate,
  type UseTimeWindowResult,
} from "@/hooks/use-time-window"

// Re-export so consumers can import TimeWindow type from this file.
export type { TimeWindow, UseTimeWindowResult } from "@/hooks/use-time-window"

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface TimeWindowPickerProps {
  /** Result of useTimeWindow() hoisted to the parent. */
  window: UseTimeWindowResult
  /** ID for the "from" date input. Defaults to "tw-from". */
  fromInputId?: string
  /** ID for the "to" date input. Defaults to "tw-to". */
  toInputId?: string
}

export function TimeWindowPicker({
  window: tw,
  fromInputId = "tw-from",
  toInputId = "tw-to",
}: TimeWindowPickerProps) {
  return (
    <div
      className="flex flex-wrap items-end gap-3"
      aria-label="Time window picker"
    >
      {/* Preset buttons */}
      <div className="flex gap-1" role="group" aria-label="Preset windows">
        <Button
          variant={tw.preset === "today" ? "default" : "outline"}
          size="sm"
          onClick={() => tw.setPreset("today")}
          aria-pressed={tw.preset === "today"}
        >
          Today
        </Button>
        <Button
          variant={tw.preset === "week" ? "default" : "outline"}
          size="sm"
          onClick={() => tw.setPreset("week")}
          aria-pressed={tw.preset === "week"}
        >
          Last 7 days
        </Button>
      </div>

      {/* Custom date inputs */}
      <div className="flex items-end gap-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor={fromInputId} className="text-xs text-muted-foreground">
            From
          </Label>
          <Input
            id={fromInputId}
            type="date"
            className="h-8 w-36 text-xs"
            value={formatWindowDate(tw.from)}
            max={formatWindowDate(tw.to)}
            onChange={(e) => {
              const parsed = parseISO(e.target.value)
              if (isValid(parsed)) {
                tw.setCustomRange(parsed, tw.to)
              }
            }}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor={toInputId} className="text-xs text-muted-foreground">
            To
          </Label>
          <Input
            id={toInputId}
            type="date"
            className="h-8 w-36 text-xs"
            value={formatWindowDate(tw.to)}
            min={formatWindowDate(tw.from)}
            onChange={(e) => {
              const parsed = parseISO(e.target.value)
              if (isValid(parsed)) {
                tw.setCustomRange(tw.from, parsed)
              }
            }}
          />
        </div>
      </div>
    </div>
  )
}
