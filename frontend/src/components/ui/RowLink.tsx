// ---------------------------------------------------------------------------
// RowLink — canonical navigating-row primitive (bu-86c4c.16)
//
// JARVIS audit move 11: a navigating row (status board cell, index row) is a
// real <a>/<Link> whenever possible — the browser gives native semantics,
// middle-click/open-in-new-tab, and screen-reader "link" announcement for
// free. The one exception: when the row must contain a NESTED interactive
// control (e.g. StatusBoardCell's restore <button> for a quarantined
// butler), nesting a <button> inside an <a> is invalid HTML (interactive
// content inside interactive content) — browsers and AT handle it
// inconsistently. RowLink encodes both branches once so every navigating
// row gets the same treatment:
//
//   - default: renders a react-router <Link>
//   - `hasNestedInteractive`: renders a `<div role="link">` with the same
//     aria-label, tabIndex, Enter+Space activation, and focus ring —
//     navigation is handled imperatively via useNavigate() by the consumer
// ---------------------------------------------------------------------------

import * as React from "react"
import { Link, type LinkProps } from "react-router"

import { cn } from "@/lib/utils"

export interface RowLinkProps extends Omit<LinkProps, "onKeyDown"> {
  /**
   * Set when the row contains its own nested interactive control (a button,
   * checkbox, etc.) — switches the root from a real <a> to a
   * `<div role="link">` so the nested control is not invalid-HTML-nested
   * inside an anchor. The consumer is responsible for imperative navigation
   * (e.g. via `useNavigate()`) in `onActivate`.
   */
  hasNestedInteractive?: boolean
  /**
   * Called on click or Enter/Space when `hasNestedInteractive` is set
   * (ignored otherwise — the real <Link> handles its own navigation).
   */
  onActivate?: () => void
}

/**
 * The canonical navigating row. Renders a real `<Link>` by default; switches
 * to an accessible `<div role="link">` fallback when the row must nest its
 * own interactive control.
 *
 * @example
 *   // Plain navigating row
 *   <RowLink to={`/butlers/${name}`} aria-label={ariaLabel} className={cellClass}>
 *     ...cell content...
 *   </RowLink>
 *
 * @example
 *   // Row with a nested restore button
 *   const navigate = useNavigate()
 *   <RowLink
 *     to={routePath}
 *     hasNestedInteractive
 *     onActivate={() => navigate(routePath)}
 *     aria-label={ariaLabel}
 *     className={cellClass}
 *   >
 *     ...cell content, including a nested <button>...
 *   </RowLink>
 */
export const RowLink = React.forwardRef<HTMLAnchorElement | HTMLDivElement, RowLinkProps>(
  function RowLink({ hasNestedInteractive = false, onActivate, className, children, ...props }, ref) {
    if (hasNestedInteractive) {
      // `to` is not a valid DOM attribute — it's consumed only by the <Link>
      // branch below, and dropped here via rest destructuring.
      const { to, ...divProps } = props
      void to
      return (
        <div
          ref={ref as React.Ref<HTMLDivElement>}
          role="link"
          tabIndex={0}
          className={cn(
            "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring focus-visible:ring-inset",
            className,
          )}
          onClick={() => onActivate?.()}
          onKeyDown={(e) => {
            if (e.target !== e.currentTarget) return
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault()
              onActivate?.()
            }
          }}
          {...(divProps as React.HTMLAttributes<HTMLDivElement>)}
        >
          {children}
        </div>
      )
    }

    return (
      <Link ref={ref as React.Ref<HTMLAnchorElement>} className={className} {...props}>
        {children}
      </Link>
    )
  },
)
