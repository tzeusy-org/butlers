// ---------------------------------------------------------------------------
// ExpandableDetail — keyboard-reachable detail/expand affordance for a
// truncated table cell (bu-x7z84).
//
// The a11y gap this closes (bu-sywxz class 2): dense/repeating table cells that
// clip their content expose the full text ONLY via mouse-hover `title=`. Making
// every clipped cell a focusable tooltip would add dozens of tab stops per table
// (a keyboard-nav regression), so the agreed fix is a per-row detail/expand
// affordance instead: ONE focusable control that discloses the full content.
//
// Contract:
//   - A native `<button>` toggle (Enter AND Space for free, focusable, one tab
//     stop per truncated row — not per cell) with `aria-expanded` and
//     `aria-controls` pointing at the disclosed region.
//   - Collapsed layout is unchanged from a bare truncated cell: the `preview`
//     renders on one line with only a small inline chevron appended, so mouse
//     users' row density is preserved. The toggle is offered ONLY when
//     `expandable` is true (i.e. the content is actually clipped).
//   - The full content (`children`) is revealed in a region below on expand.
//
// This is the shared counterpart to DisclosureRow (whole-row grid disclosure);
// ExpandableDetail is for an in-cell clip where the row itself is not a button.
// ---------------------------------------------------------------------------

import * as React from "react"
import { ChevronDownIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export interface ExpandableDetailProps {
  /**
   * Accessible noun for the toggle, e.g. "message". The button reads
   * "Show full {label}" / "Hide full {label}" and the disclosed region is
   * labelled "Full {label}".
   */
  label: string
  /**
   * The always-visible collapsed preview (typically the existing truncated
   * `<p className="truncate">`). Rendered on one line with the chevron toggle
   * appended so the row keeps its single-line density.
   */
  preview: React.ReactNode
  /**
   * When false, only `preview` renders — no toggle, no region — so an
   * un-clipped cell is byte-for-byte the same as a bare preview. Callers pass
   * the truncation predicate (e.g. `message.length > MAX`).
   */
  expandable: boolean
  /** The full, untruncated detail revealed below on expand. */
  children: React.ReactNode
  /** Optional test id on the toggle button. */
  testId?: string
  className?: string
}

/**
 * Keyboard-reachable expand affordance for a truncated cell. Renders the
 * collapsed `preview` with an inline chevron toggle (only when `expandable`),
 * disclosing `children` (the full content) in an `aria-controls`-linked region.
 *
 * @example
 *   <ExpandableDetail
 *     label="message"
 *     expandable={message.length > 60}
 *     preview={<p className="truncate text-muted-foreground">{truncate(message)}</p>}
 *     testId="notification-detail-toggle"
 *   >
 *     <p className="whitespace-pre-wrap break-words">{message}</p>
 *   </ExpandableDetail>
 */
export function ExpandableDetail({
  label,
  preview,
  expandable,
  children,
  testId,
  className,
}: ExpandableDetailProps) {
  const [open, setOpen] = React.useState(false)
  const regionId = React.useId()

  if (!expandable) {
    return <div className={className}>{preview}</div>
  }

  return (
    <div className={className}>
      <div className="flex items-start gap-1">
        <div className="min-w-0 flex-1">{preview}</div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={regionId}
          aria-label={open ? `Hide full ${label}` : `Show full ${label}`}
          data-testid={testId}
          className="mt-0.5 inline-flex shrink-0 items-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <ChevronDownIcon
            className={cn("size-3.5 transition-transform", open && "rotate-180")}
            aria-hidden="true"
          />
        </button>
      </div>
      {open && (
        <div
          id={regionId}
          role="region"
          aria-label={`Full ${label}`}
          className="mt-1.5"
        >
          {children}
        </div>
      )}
    </div>
  )
}
