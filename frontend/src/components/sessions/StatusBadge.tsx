// ---------------------------------------------------------------------------
// StatusBadge — shared session-status indicator (dot + label).
//
// One token-reading status badge used by SessionTable, SessionDetailDrawer, and
// SessionDetailPage. Replaces the triplicated hardcoded bg-emerald-600 /
// border-gray-400 markup.
//
// Doctrine (design-language.md §D.3): the success green sits below the 3:1
// contrast floor, so the "Success" text label is mandatory — colour alone never
// carries the status. Failed keeps the destructive Badge (high-contrast).
// Tokens only: --green / --amber via the dot, never raw hex.
// ---------------------------------------------------------------------------

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

export interface StatusBadgeProps {
  /** Session success flag: true = success, false = failed, null = running. */
  success: boolean | null
  className?: string
}

/**
 * Render the session status as a dot + mandatory text label.
 *
 *   success === true   → green dot + "Success"
 *   success === false  → destructive Badge "Failed"
 *   success === null   → amber dot + "Running"
 */
export function StatusBadge({ success, className }: StatusBadgeProps) {
  if (success === false) {
    return (
      <Badge variant="destructive" className={className}>
        Failed
      </Badge>
    )
  }

  const isSuccess = success === true
  const dotColor = isSuccess ? "var(--green)" : "var(--amber)"
  const label = isSuccess ? "Success" : "Running"

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-0.5",
        "text-xs font-medium text-foreground",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="inline-block size-1.5 shrink-0 rounded-full"
        style={{ backgroundColor: dotColor }}
      />
      {label}
    </span>
  )
}
