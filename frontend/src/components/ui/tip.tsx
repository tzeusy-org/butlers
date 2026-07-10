import * as React from "react"

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

type TipProps = Omit<
  React.ComponentProps<typeof TooltipContent>,
  "children"
> & {
  /**
   * Tooltip body. When nullish or an empty string, the child renders untouched
   * with no wrapper — this mirrors the `title={cond ? text : undefined}` idiom
   * the native attribute used, so a conditionally-absent tooltip stays absent
   * (and adds no radix machinery).
   */
  content: React.ReactNode
  /**
   * The trigger element. It MUST be a single element that forwards refs and
   * props — an interactive element (`<a>`, `<button>`) or a non-interactive
   * element made focusable with `tabIndex={0}`. It becomes the tooltip trigger
   * via `asChild`, so the element itself is the trigger and no extra
   * interactive node is nested inside it (keeps axe's nested-interactive rule
   * clean).
   */
  children: React.ReactElement
  /** Radix open delay. Defaults to 0 to match the immediacy of a `title=`. */
  delayDuration?: number
}

/**
 * `Tip` — the focusable, screen-reader-announced replacement for a load-bearing
 * `title=` attribute (bu-sywxz, following the bu-f310e/#3021 Plex-halo exemplar).
 *
 * A native `title` tooltip never surfaces on keyboard focus and is
 * inconsistently announced by assistive tech. `Tip` wraps the same content in a
 * radix Tooltip that opens on hover AND keyboard focus and is SR-announced,
 * while `asChild` keeps the underlying element as the sole interactive node.
 *
 * Each `Tip` carries its own `TooltipProvider`, so callers do not need a
 * provider ancestor. When converting a whole cluster of adjacent triggers,
 * prefer a single shared `TooltipProvider` around the group and raw
 * `Tooltip`/`TooltipTrigger`/`TooltipContent` (as the exemplar did) to avoid
 * redundant providers.
 */
export function Tip({
  content,
  children,
  delayDuration = 0,
  ...contentProps
}: TipProps) {
  if (content == null || content === "") {
    return children
  }
  return (
    <TooltipProvider delayDuration={delayDuration}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent {...contentProps}>{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
