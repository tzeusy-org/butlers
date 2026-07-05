import { useSessionDetail } from "@/hooks/use-sessions";
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
  butler: string;
  sessionId: string | null; // null = closed
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
  butler,
  sessionId,
  onClose,
}: SessionDetailDrawerProps) {
  const { data, isLoading, isError } = useSessionDetail(butler, sessionId);
  const session = data?.data ?? null;

  return (
    <Sheet open={sessionId != null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-lg overflow-y-auto">
        {isError || (!isLoading && !session) ? (
          <>
            <SheetHeader>
              <SheetTitle>Session detail</SheetTitle>
              <SheetDescription>
                This session could not be loaded. It may not exist or the butler may be
                unavailable.
              </SheetDescription>
            </SheetHeader>
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
