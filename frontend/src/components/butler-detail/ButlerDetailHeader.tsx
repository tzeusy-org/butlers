// ---------------------------------------------------------------------------
// ButlerDetailHeader — header-slot wrapper for the butler detail page.
// (bu-ja5bt.3)
//
// Composes:
//   - Butler identity — name (H1) and description, hue via <ButlerMark>
//
// The header does NOT render ButlerDetailActions; the Page archetype provides
// a separate `actions` slot for that. This component covers ONLY the header
// slot content per the spec.
//
// Contract:
//   - props: butler (active butler name)
//   - Skeleton state while data loads
//   - Error state mirrors loaded dimensions to avoid layout shift
//   - Token-only chrome: no hex, oklch, rgb literals, no inline style
//   - Butler hue appears ONLY on <ButlerMark> — never on other chrome elements
//   - No em-dashes in any JSX string literal
//   - No `pid` anywhere (gate violation)
//
// Doctrine: design-language.md Non-negotiables 1 (token system), 2 (Page is a
// primitive), 6 (no em-dashes). Butler-hue scope restricted to ButlerMark.
// ---------------------------------------------------------------------------

import { ButlerMark } from "@/components/ui/ButlerMark"
import { Skeleton } from "@/components/ui/skeleton"
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board"

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ButlerDetailHeaderProps {
  /** The active butler name (from URL params). */
  butler: string
}

// ---------------------------------------------------------------------------
// ButlerDetailHeader
// ---------------------------------------------------------------------------

/**
 * Header-slot primitive for the butler detail page.
 *
 * Renders the active butler identity block (name + description via ButlerMark
 * hue scope). Intended to be passed as the `header` prop on
 * `<Page archetype="status-board">`.
 *
 * The sibling-butler navigation now lives in the shell PageHeader beside the
 * search/theme controls. The actions slot (ButlerDetailActions) is provided
 * separately by the Page shell; this component does not render it.
 *
 * @example
 *   <ButlerDetailHeader butler="relationship" />
 */
export function ButlerDetailHeader({ butler }: ButlerDetailHeaderProps) {
  const { rows, aggregates } = useButlerStatusBoard()

  // Find the active butler's description from the status board rows.
  // Falls back to null when loading, errored, or not found.
  const activeRow = rows.find((r) => r.name === butler) ?? null
  const description = activeRow?.description ?? null

  // ---------------------------------------------------------------------------
  // Skeleton state
  // ---------------------------------------------------------------------------

  if (aggregates.isLoading) {
    return (
      <div
        data-testid="butler-detail-header"
        className="flex flex-col gap-2 border-b border-border px-7 py-3"
        aria-busy="true"
      >
        {/* Identity skeleton — mirrors loaded identity block height */}
        {/* ButlerMark is h-6 (24px); H1 text-2xl has line-height 2rem (h-8=32px) */}
        <div className="flex items-center gap-2 py-0.5">
          <Skeleton className="h-6 w-6 shrink-0 rounded" />
          <Skeleton className="h-8 w-32 rounded-sm" />
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Error state — mirrors loaded-state dimensions to avoid layout shift
  // ---------------------------------------------------------------------------

  if (aggregates.isError && rows.length === 0) {
    return (
      <div
        data-testid="butler-detail-header"
        className="flex flex-col gap-2 border-b border-border px-7 py-3"
      >
        {/* Identity block preserved at loaded dimensions */}
        <div className="flex items-center gap-2 py-0.5">
          {/* Butler hue appears ONLY on ButlerMark */}
          <ButlerMark name={butler} size={24} tone="fill" />
          <h1 className="text-2xl font-bold tracking-tight capitalize">{butler}</h1>
        </div>
      </div>
    )
  }

  // ---------------------------------------------------------------------------
  // Loaded state
  // ---------------------------------------------------------------------------

  return (
    <div
      data-testid="butler-detail-header"
      className="flex flex-col gap-2 border-b border-border px-7 py-3"
    >
      {/* Identity block: butler hue ONLY on ButlerMark */}
      {/* min-w-0 allows flex children to shrink below intrinsic width, enabling truncation */}
      <div className="flex min-w-0 items-center gap-2 py-0.5">
        {/* Butler hue appears ONLY on this ButlerMark element */}
        <ButlerMark name={butler} size={24} tone="fill" />
        <h1 className="text-2xl font-bold tracking-tight capitalize">{butler}</h1>
        {description ? (
          <span className="text-sm text-muted-foreground font-normal ml-1 truncate">
            {description}
          </span>
        ) : null}
      </div>

    </div>
  )
}
