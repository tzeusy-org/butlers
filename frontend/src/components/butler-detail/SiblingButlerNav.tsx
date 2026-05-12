// ---------------------------------------------------------------------------
// SiblingButlerNav — sibling-butler navigation strip for the butler detail page
// (bu-ja5bt.2)
//
// Renders a horizontal navigation strip listing every butler from useButlers()
// in sessions_24h descending order (name ascending for ties). This component
// is a Tier 1 chrome element only — it lives in the Page header slot, NOT
// between the Page header and the tab rail.
//
// Contract:
//   - role="navigation" aria-label="Navigate to butler"
//   - Each entry is a React Router <Link> with aria-current="page" on the active butler
//   - Keyboard: Tab moves focus in/out; Enter/Space activates the link natively
//   - Skeleton state while useButlers() is loading or errored
//   - Paused/quarantined butlers stay navigable (no aria-disabled)
//   - Query params (?tab=, ?mode=) carried forward from the current URL
//   - Butler hue appears ONLY on <ButlerMark> — never on chrome states
//   - Token-only chrome: no hex, oklch, rgb literals, no inline style
//   - No em-dashes in any JSX string
//
// Doctrine: design-language.md Non-negotiables 1 (token system), 2 (Page is a
// primitive), 6 (no em-dashes). Butler-hue scope restricted to ButlerMark.
// ---------------------------------------------------------------------------

import { useMemo } from "react"
import { Link, useSearchParams } from "react-router"

import { ButlerMark } from "@/components/ui/ButlerMark"
import { Skeleton } from "@/components/ui/skeleton"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SiblingButlerNavProps {
  /** The name of the currently active butler (from URL params). */
  activeButlerName: string
}

// ---------------------------------------------------------------------------
// Activity tone dot
// ---------------------------------------------------------------------------

/**
 * Derive the CSS class for the tone dot on a sibling nav entry.
 * Only neutral chrome tokens are used here. The actual butler hue is
 * restricted to <ButlerMark>.
 */
function toneDotClass(
  activity: "running" | "idle" | "paused" | "awaiting" | "quarantined",
): string {
  switch (activity) {
    case "running":
      return "bg-emerald-500"
    case "idle":
      return "bg-muted-foreground/40"
    case "paused":
      return "bg-destructive"
    case "awaiting":
      return "bg-amber-500"
    case "quarantined":
      return "bg-destructive"
  }
}

// ---------------------------------------------------------------------------
// SiblingButlerNav
// ---------------------------------------------------------------------------

/**
 * Horizontal sibling-butler navigation strip for the butler detail page.
 *
 * Lists every butler from useButlers() in sessions_24h desc (name asc) order.
 * The active butler is marked aria-current="page". Query params (?tab=, ?mode=)
 * are carried across navigation.
 *
 * @example
 *   <SiblingButlerNav activeButlerName="health" />
 */
export function SiblingButlerNav({ activeButlerName }: SiblingButlerNavProps) {
  const { rows, aggregates } = useButlerStatusBoard()
  const [searchParams] = useSearchParams()

  // Carry ?tab= and ?mode= forward to sibling butler pages.
  // Other params are dropped — they are likely butler-specific and would
  // produce unexpected state on the target butler page.
  const carriedParams = useMemo(() => {
    const next = new URLSearchParams()
    const tab = searchParams.get("tab")
    const mode = searchParams.get("mode")
    if (tab) next.set("tab", tab)
    if (mode) next.set("mode", mode)
    const str = next.toString()
    return str ? `?${str}` : ""
  }, [searchParams])

  // ---------------------------------------------------------------------------
  // Skeleton state
  // ---------------------------------------------------------------------------

  if (aggregates.isLoading) {
    return (
      <nav
        role="navigation"
        aria-label="Navigate to butler"
        aria-busy="true"
        className="flex items-center gap-0.5 overflow-x-auto scroll-smooth scrollbar-none py-0.5"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton
            key={i}
            className="h-6 w-20 shrink-0 rounded-sm"
          />
        ))}
      </nav>
    )
  }

  // Error state: also render skeleton shape so the strip never collapses.
  if (aggregates.isError && rows.length === 0) {
    return (
      <nav
        role="navigation"
        aria-label="Navigate to butler"
        className="flex items-center gap-0.5 overflow-x-auto scroll-smooth scrollbar-none py-0.5"
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton
            key={i}
            className="h-6 w-20 shrink-0 rounded-sm opacity-50"
          />
        ))}
      </nav>
    )
  }

  // ---------------------------------------------------------------------------
  // Loaded state
  // ---------------------------------------------------------------------------

  return (
    <nav
      role="navigation"
      aria-label="Navigate to butler"
      className="flex items-center gap-0.5 overflow-x-auto scroll-smooth scrollbar-none py-0.5"
    >
      {rows.map((row) => {
        const isActive = row.name === activeButlerName
        const href = `/butlers/${row.name}${carriedParams}`

        return (
          <Link
            key={row.name}
            to={href}
            aria-current={isActive ? "page" : undefined}
            className={[
              // Base layout
              "flex items-center gap-1.5 px-2 py-1 rounded-sm",
              "shrink-0 min-w-0",
              // Typography
              "text-xs font-medium capitalize",
              // Chrome tokens ONLY — no butler hue here
              isActive
                ? [
                    "text-foreground",
                    "border-b-2 border-foreground",
                    "bg-transparent",
                  ].join(" ")
                : [
                    "text-muted-foreground",
                    "border-b-2 border-transparent",
                    "hover:text-foreground hover:border-border",
                    "focus-visible:text-foreground focus-visible:border-border",
                    "transition-colors duration-[120ms] ease-in-out",
                  ].join(" "),
              // Focus ring for keyboard nav
              "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
            ].join(" ")}
          >
            {/* Butler hue appears ONLY on ButlerMark — not on any other chrome element */}
            <ButlerMark
              name={row.name}
              size={14}
              tone={isActive ? "fill" : "neutral"}
            />

            {/* Butler name (capitalized via CSS class, not JS) */}
            <span className="truncate max-w-[80px]">{row.name}</span>

            {/* Activity tone dot — neutral chrome tokens only */}
            <span
              className={[
                "shrink-0 w-1.5 h-1.5 rounded-full",
                toneDotClass(row.activity),
              ].join(" ")}
              aria-hidden="true"
            />
          </Link>
        )
      })}
    </nav>
  )
}
