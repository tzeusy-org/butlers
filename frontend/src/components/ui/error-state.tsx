import * as React from "react";

interface ErrorStateProps {
  title: string;
  description?: string;
  action?: React.ReactNode;
}

/**
 * Canonical error-affordance surface for a page, panel, or table whose data
 * fetch failed — the error-tier sibling of {@link EmptyState} (see
 * `./empty-state.tsx`).
 *
 * A failed fetch is not the same as "nothing here": rendering it through
 * `EmptyState` reads as calm, honest emptiness to both sighted users and
 * screen readers, when the true condition is a degraded source (butlers/
 * CLAUDE.md § Degraded-Mode Response Envelope: "a source that raises or is
 * unreachable must never render as a truthful empty/zero/all-clear result").
 * `ErrorState` renders `role="alert"` so assistive tech announces the
 * failure, and destructive-red text so sighted users see it as an error.
 *
 * This is the block-level counterpart to `<Page>`'s own error card
 * (`components/ui/page.tsx`) and shares its `role="alert"` / destructive
 * vocabulary with `QueryBoundary` / `SourceDegradedNote`
 * (`components/ui/query-boundary.tsx`) — but unlike `QueryBoundary`'s
 * `sourceLabel`-derived canned subject line, `ErrorState` keeps each call
 * site's own specific title and description (e.g. "Owner access is
 * required, or no relational predicates are registered."), which matters
 * when the failure has more than one plausible cause worth naming.
 */
export function ErrorState({ title, description, action }: ErrorStateProps) {
  return (
    <div role="alert" className="flex flex-col items-center justify-center py-16 text-center">
      <h2 className="text-lg font-medium text-destructive">{title}</h2>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-destructive/80">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
