// ---------------------------------------------------------------------------
// DisclosureRow — canonical expand/collapse row primitive (bu-86c4c.16)
//
// JARVIS audit move 11 (cross:accessibility, critical finding on
// TimelineTab.tsx LedgerRow): the densest operator surface in the dashboard
// was a click-handler `<div>` — aria-expanded without a role (ignored or
// misannounced by screen readers), Space did nothing (Enter-only), and the
// expand chevron was read as "black up-pointing triangle" by AT.
//
// DisclosureRow fixes this ONCE, for every disclosure row in the app:
//   - role="button" so AT announces the row as an activatable control
//   - Enter AND Space both toggle (native <button> semantics, replicated
//     manually because a disclosure row's content is a CSS grid of
//     independently-interactive children — a real <button> cannot contain
//     nested buttons/inputs per HTML content-model rules)
//   - aria-expanded (+ optional aria-controls pointing at the disclosed
//     content's id) so the expand/collapse state is announced
//   - a visible focus ring
//   - clicks that originate from a nested interactive element (a checkbox,
//     a nested button) do not toggle — only a click on the row's own
//     surface does, matching the pre-existing click-vs-nested-control
//     convention already used by TimelineTab/StatusBoardCell
//
// Consumers own their own grid/layout (`className`/`style`) — this
// primitive only supplies the interaction contract, not visual structure.
// ---------------------------------------------------------------------------

import * as React from "react"

import { cn } from "@/lib/utils"

export interface DisclosureRowProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "onToggle"> {
  /** Current expanded/collapsed state, reflected as aria-expanded. */
  expanded: boolean
  /** Called on click (row surface only) or on Enter/Space. */
  onToggle: () => void
  /** id of the element this row discloses; wired to aria-controls when present. */
  controlsId?: string
  /** When true, the row is not focusable/activatable (still renders normally). */
  disabled?: boolean
}

/**
 * The canonical expand/collapse row: role="button", Enter+Space activation,
 * aria-expanded (+ aria-controls), and a focus-visible ring layered onto
 * whatever grid/flex layout the consumer supplies via `className`.
 *
 * @example
 *   <DisclosureRow
 *     expanded={isExpanded}
 *     onToggle={() => setExpanded((e) => !e)}
 *     controlsId={`event-drawer-${event.id}`}
 *     className="grid items-center gap-x-3 px-3 py-2"
 *     style={{ gridTemplateColumns: LEDGER_GRID_COLUMNS }}
 *   >
 *     ...row cells...
 *   </DisclosureRow>
 */
export const DisclosureRow = React.forwardRef<HTMLDivElement, DisclosureRowProps>(
  function DisclosureRow(
    { expanded, onToggle, controlsId, disabled = false, className, onClick, onKeyDown, ...props },
    ref,
  ) {
    function handleClick(e: React.MouseEvent<HTMLDivElement>) {
      onClick?.(e)
      if (e.defaultPrevented || disabled) return
      onToggle()
    }

    function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
      onKeyDown?.(e)
      if (e.defaultPrevented || disabled) return
      // Only react when the row itself carries focus — nested interactive
      // children (checkbox, buttons) already handle their own activation.
      if (e.target !== e.currentTarget) return
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault()
        onToggle()
      }
    }

    return (
      <div
        ref={ref}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-expanded={expanded}
        aria-controls={controlsId}
        aria-disabled={disabled || undefined}
        className={cn(
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset",
          disabled ? "cursor-default" : "cursor-pointer",
          className,
        )}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        {...props}
      />
    )
  },
)
