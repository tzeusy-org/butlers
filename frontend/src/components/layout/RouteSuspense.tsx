import { Suspense, type ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";

interface RouteSuspenseProps {
  children: ReactNode;
}

/**
 * Stable loading frame for every code-split route.
 *
 * The dashboard shell remains interactive while a route chunk loads; this
 * intentionally avoids route-specific spinners and blank outlet transitions.
 */
export function RouteSuspense({ children }: RouteSuspenseProps) {
  return <Suspense fallback={<RouteSkeleton />}>{children}</Suspense>;
}

function RouteSkeleton() {
  return (
    <div
      aria-busy="true"
      aria-live="polite"
      aria-label="Loading page"
      className="space-y-6 p-6"
      data-testid="route-suspense-skeleton"
      role="status"
    >
      <span className="sr-only">Loading page</span>
      <div aria-hidden="true" className="space-y-2">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-72" />
      </div>
      <div aria-hidden="true" className="grid gap-4 md:grid-cols-2">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
      <Skeleton aria-hidden="true" className="h-56 w-full" />
    </div>
  );
}
