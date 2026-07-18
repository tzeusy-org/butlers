import { ApiError } from "@/api/client";
import type { SessionSummary } from "@/api/types";
import { useGlobalSessionDetail } from "@/hooks/use-sessions";
import { StatusBadge } from "@/components/sessions/StatusBadge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { SessionDossier } from "./SessionDossier";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface SessionDetailDrawerProps {
  sessionId: string | null; // null = closed
  /**
   * The selected list row, when one is already available. It seeds the
   * identity/header while the authoritative detail query loads; deep links
   * omit it and retain the normal skeleton-only path.
   */
  seed?: SessionSummary;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function DrawerSkeleton() {
  return (
    <div className="space-y-4 p-4">
      <Skeleton className="h-6 w-48" />
      <div className="space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-4 w-full" />
        ))}
      </div>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// SessionDetailDrawer
// ---------------------------------------------------------------------------

export function SessionDetailDrawer({
  sessionId,
  seed,
  onClose,
}: SessionDetailDrawerProps) {
  // Global (cross-butler) resolution: session ids are globally unique, so a
  // pinned row or deep link resolves without a ?butler= hint (bu-tpudw.2).
  const { data, isLoading, isError, isPlaceholderData, error } = useGlobalSessionDetail(
    sessionId,
    seed,
  );
  const session = data?.data ?? null;

  // The global detail endpoint splits 404 (id genuinely unknown across every
  // reachable pool) from 503 (a butler database was unreachable, so the session
  // may live in a pool we could not query). A 503 must NOT read as "not found"
  // — name the pool-down state distinctly so a transient outage is not
  // mistaken for a deleted/invalid session (CLAUDE.md degraded-envelope
  // convention). The backend's 503 `detail` names the down pool(s).
  const poolDown = isError && error instanceof ApiError && error.status === 503;

  return (
    <Sheet open={sessionId != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        {poolDown ? (
          <>
            <SheetHeader>
              <SheetTitle>Session detail unavailable</SheetTitle>
              <SheetDescription data-testid="session-detail-pool-down" role="alert">
                A butler database is unreachable, so this session could not be
                resolved. It may live in a pool that is currently down.{" "}
                {error instanceof ApiError && error.message ? error.message : null}
              </SheetDescription>
            </SheetHeader>
          </>
        ) : isError || (!isLoading && !session) ? (
          <>
            <SheetHeader>
              <SheetTitle>Session detail</SheetTitle>
              <SheetDescription>
                This session could not be loaded. It may not exist or the butler may be
                unavailable.
              </SheetDescription>
            </SheetHeader>
          </>
        ) : isPlaceholderData && session ? (
          <>
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 text-sm">
                <span className="font-mono truncate">{session.id}</span>
                <StatusBadge success={session.success} />
              </SheetTitle>
              <SheetDescription>
                {session.butler} &mdash; {session.trigger_source}
              </SheetDescription>
            </SheetHeader>
            <div className="px-4 pb-6">
              <p className="mb-3 text-xs text-muted-foreground">
                Loading full session record…
              </p>
              <DrawerSkeleton />
            </div>
          </>
        ) : isLoading || !session ? (
          <>
            <SheetHeader>
              <SheetTitle>Session detail</SheetTitle>
              <SheetDescription>Loading session information...</SheetDescription>
            </SheetHeader>
            <DrawerSkeleton />
          </>
        ) : (
          <>
            {/* Header */}
            <SheetHeader>
              <SheetTitle className="flex items-center gap-2 text-sm">
                <span className="font-mono truncate">{session.id}</span>
                <StatusBadge success={session.success} />
              </SheetTitle>
              <SheetDescription>
                {session.butler} &mdash; {session.trigger_source}
              </SheetDescription>
            </SheetHeader>

            <div className="px-4 pb-6">
              <SessionDossier session={session} />
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
