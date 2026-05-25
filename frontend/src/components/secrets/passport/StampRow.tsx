// ---------------------------------------------------------------------------
// StampRow — audit event row (bu-qo3sf)
//
// Renders a single audit stamp: glyph + date/time + action + actor +
// optional serif note. Used in the Audit section of each credential page.
//
// butler-secrets §Evidence-Over-Value Affordance Contract §6:
//   "last 10 AuditEvent rows: 1-char mono glyph + date/time + action +
//   actor + serif note"
//
// Layout (grid columns):
//   [glyph 16px] [mono date/time] [mono action] [mono actor] [voice note (optional)]
//
// The serif note is a verbatim message tail — never LLM-elaborated.
// ---------------------------------------------------------------------------

import * as React from "react"

import { Mono } from "@/components/ui/Mono"
import { Voice } from "@/components/ui/Voice"
import { cn } from "@/lib/utils"

import { StampGlyph } from "./StampGlyph"
import type { AuditAction } from "./StampGlyph"

export interface StampRowProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Audit action determines the glyph and glyph colour. */
  action: AuditAction
  /** Pre-formatted date/time string (e.g. "14:21 today", "3 May"). */
  datetime: string
  /**
   * Human-readable actor identifier (e.g. "owner", "butler:health",
   * "oauth:google").
   */
  actor: string
  /**
   * Optional verbatim serif note (e.g. provider error message).
   * Rendered in Source Serif 4 italic — never LLM-generated.
   */
  note?: string
}

/**
 * Single audit event row.
 *
 * @example
 *   <StampRow action="verified" datetime="14:21 today" actor="owner" />
 *   <StampRow
 *     action="failed"
 *     datetime="09:03 today"
 *     actor="butler:health"
 *     note="Token expired: 401 Unauthorized"
 *   />
 */
export function StampRow({
  action,
  datetime,
  actor,
  note,
  className,
  ...props
}: StampRowProps) {
  return (
    <div
      className={cn(
        "flex items-baseline gap-3 py-1.5",
        "border-b border-[var(--border-soft,oklch(1_0_0/0.06))] last:border-b-0",
        className,
      )}
      {...props}
    >
      <StampGlyph action={action} />
      <Mono muted className="shrink-0">
        {datetime}
      </Mono>
      <Mono className="shrink-0">{action}</Mono>
      <Mono muted className="shrink-0">
        {actor}
      </Mono>
      {note && (
        <Voice as="span" variant="italic" className="flex-1 min-w-0 text-[13px] leading-[1.4]">
          {note}
        </Voice>
      )}
    </div>
  )
}
