/**
 * FetchingDim -- shared "never-blank list" overlay (JARVIS audit move 10,
 * bu-86c4c.13).
 *
 * Pairs with `placeholderData: (prev) => prev` on a cursor/filter-keyed list
 * query: the query keeps rendering the PREVIOUS page's rows the instant a
 * filter or cursor changes (`isLoading` never goes true again after the
 * first load), and this wrapper dims those stale rows to signal a refetch is
 * in flight — instead of the list blanking to a skeleton/spinner and back.
 *
 * Motion matches Elaboration.tsx's established isFetching treatment: opacity
 * fades via the program-wide `--duration-base` / `--ease-out-quart` tokens.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface FetchingDimProps {
  /** The query's `isFetching`. Only dims when true AND `isLoading` is false
   * (i.e. a background refetch of already-visible data, not the first load). */
  isFetching: boolean;
  className?: string;
  children: ReactNode;
}

export function FetchingDim({ isFetching, className, children }: FetchingDimProps) {
  return (
    <div
      aria-busy={isFetching}
      className={cn(
        "transition-[opacity] duration-base ease-out-quart",
        isFetching && "opacity-60",
        className,
      )}
    >
      {children}
    </div>
  );
}
