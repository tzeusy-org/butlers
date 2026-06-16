// ---------------------------------------------------------------------------
// ButlersPage — status-board rewrite (bu-hb7dh.8)
//
// Replaces the alphabetical row-list layout with the 4-column status-board
// grid introduced by the bu-hb7dh epic. All upstream primitives are on main:
//   - Page archetype='status-board' with header/footer slots (PR #1526)
//   - useButlerStatusBoard hook (PR #1528)
//   - StatusBoardCell component (PR #1532)
//   - BoardHeader + BoardFooter chrome (PR #1531)
//
// Patterns preserved from the old page:
//   - Stale-data banner when the query is in error but cached rows exist.
//   - Empty state via the Page primitive's `empty` slot.
//   - Full-page error (no cached data) via Page primitive's `error` prop.
//   - Loading state delegated to the Page primitive skeleton.
//   - onRestore wired to useSetEligibility mutation.
// ---------------------------------------------------------------------------

import { toast } from "sonner";

import { Card, CardContent } from "@/components/ui/card";
import { Page } from "@/components/ui/page";
import { BoardFooter } from "@/components/butlers/BoardFooter";
import { BoardHeader } from "@/components/butlers/BoardHeader";
import { StatusBoardCell } from "@/components/butlers/StatusBoardCell";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useSetEligibility } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Polling interval forwarded to BoardHeader's refresh caption. */
const REFRESH_INTERVAL_MS = 30_000;

// ---------------------------------------------------------------------------
// ButlersPage
// ---------------------------------------------------------------------------

export default function ButlersPage() {
  const { rows, aggregates } = useButlerStatusBoard();
  const setEligibility = useSetEligibility();

  const { isLoading, isError, error, refetch } = aggregates;
  const hasRows = rows.length > 0;

  // Full-page error only when there is no cached data to show.
  const pageError = isError && !hasRows ? error : null;

  // Stale-data banner: last refetch errored but cached rows are still visible.
  // We key off `error != null && hasRows` rather than `isError && hasRows`
  // because the hook sets isError only when there is no cached data; when rows
  // survive from cache the error object is still populated but isError is false.
  const showStaleBanner = error != null && hasRows;

  const pendingRestoreName = setEligibility.isPending ? setEligibility.variables?.name : undefined;

  function handleRestore(name: string) {
    setEligibility.mutate(
      { name, state: "active" },
      {
        onSuccess: () => toast.success(`${name} restored`),
        onError: (err) =>
          toast.error(`Failed to restore ${name}`, {
            description: err instanceof Error ? err.message : undefined,
          }),
      },
    );
  }

  return (
    <Page
      archetype="status-board"
      title="Butlers"
      loading={isLoading}
      error={pageError}
      onRetry={pageError != null ? () => void refetch() : undefined}
      empty={
        !isError && !hasRows && !isLoading
          ? { title: "No butlers found", description: "Check daemon status and try again." }
          : null
      }
      header={<BoardHeader aggregates={aggregates} refreshIntervalMs={REFRESH_INTERVAL_MS} />}
      footer={<BoardFooter aggregates={aggregates} />}
    >
      {/* Stale-data banner — shown above the grid when cached rows exist but the
          last refresh failed. Mirrors the pattern from the old ButlersPage. */}
      {showStaleBanner && (
        <Card>
          <CardContent className="py-4">
            <p className="text-sm text-destructive">
              Showing last known butler status. Refresh failed:{" "}
              {error instanceof Error ? error.message : "Unknown error"}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Status-board grid — 4 columns, each cell links to the butler detail page. */}
      {hasRows && (
        <div
          role="group"
          aria-label="Butler status board"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60"
        >
          {rows.map((row) => (
            <StatusBoardCell
              key={row.name}
              row={row}
              onRestore={handleRestore}
              isRestorePending={pendingRestoreName === row.name}
            />
          ))}
        </div>
      )}
    </Page>
  );
}
