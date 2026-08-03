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
//
// Restore-with-reason-and-undo (JARVIS audit move 6, bu-86c4c.15): the
// StatusBoardCell chip already surfaces the quarantine_reason as its title
// (bu-86c4c.3) and IS the "Restore" button — what was missing was the undo
// half. Restoring a quarantined butler is a real, consequential action (it
// starts running again), so a click doesn't fire the mutation instantly; it
// schedules it RESTORE_UNDO_WINDOW_MS out and offers an "Undo" toast action,
// mirroring the scheduled-decision pattern ApprovalsPage established for its
// a/d/x keyboard verbs (bu-86c4c.14). Nothing reaches the backend unless the
// window elapses without an undo.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { Card, CardContent } from "@/components/ui/card";
import { Page } from "@/components/ui/page";
import { BoardFooter } from "@/components/butlers/BoardFooter";
import { BoardHeader } from "@/components/butlers/BoardHeader";
import { NeedsYouStrip } from "@/components/butlers/NeedsYouStrip";
import { StatusBoardCell } from "@/components/butlers/StatusBoardCell";
import { useButlerStatusBoard } from "@/hooks/use-butler-status-board";
import { useSetEligibility } from "@/hooks/use-general";
import { useRegisterShortcut, type ShortcutBinding } from "@/hooks/use-register-shortcut";
import { useUndoWindow } from "@/hooks/use-undo-window";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Polling interval forwarded to BoardHeader's refresh caption. */
const REFRESH_INTERVAL_MS = 30_000;

/**
 * How long an owner has to undo a restore before it actually fires
 * (matches ApprovalsPage's UNDO_WINDOW_MS convention, bu-86c4c.14).
 */
const RESTORE_UNDO_WINDOW_MS = 5_000;

function boardColumnCount(board: HTMLElement): number {
  const template = getComputedStyle(board).gridTemplateColumns.trim();
  // Browsers normally resolve repeat() to pixel tracks, but test/layout
  // environments can expose the authored value instead. Handle both so the
  // vertical cursor follows the responsive board at every breakpoint.
  const repeat = /^repeat\(\s*(\d+)\s*,/.exec(template);
  if (repeat) return Number(repeat[1]);
  if (!template || template === "none") return 4;
  return template.split(/\s+/).filter(Boolean).length || 4;
}

// ---------------------------------------------------------------------------
// ButlersPage
//
// Scheduled-restore survival across unmount (JARVIS audit move 6,
// bu-86c4c.15) used to be a bespoke module-scoped store hand-rolled here --
// consolidated onto the shared useUndoWindow hook (bu-ep4ks.11 / bu-3dp0c),
// which provides the identical MODULE SCOPE semantics (a restore scheduled
// via the quarantine chip survives the page unmounting mid-window; see
// use-undo-window.ts's own module-scope rationale) under the "butler-restore"
// namespace so it never collides with use-approval-decisions.ts's identical
// migration.
// ---------------------------------------------------------------------------

export default function ButlersPage() {
  const { rows, aggregates, needsYou } = useButlerStatusBoard();
  const setEligibility = useSetEligibility();
  const navigate = useNavigate();

  const { isLoading, isError, error, refetch } = aggregates;
  const hasRows = rows.length > 0;
  const hasInitialLoadError = isError && !hasRows;

  // Full-page error only when there is no cached data to show.
  const pageError = hasInitialLoadError ? error : null;

  // Stale-data banner: last refetch errored but cached rows are still visible.
  // We key off `error != null && hasRows` rather than `isError && hasRows`
  // because the hook sets isError only when there is no cached data; when rows
  // survive from cache the error object is still populated but isError is false.
  const showStaleBanner = error != null && hasRows;

  const boardNames = useMemo(() => rows.map((row) => row.name), [rows]);
  const [selectedButlerName, setSelectedButlerName] = useState<string | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);

  const moveBoardCursor = useCallback(
    (delta: number) => {
      if (boardNames.length === 0) return;
      const currentIndex = selectedButlerName ? boardNames.indexOf(selectedButlerName) : -1;
      const nextIndex =
        currentIndex === -1
          ? delta < 0
            ? boardNames.length - 1
            : 0
          : Math.min(Math.max(currentIndex + delta, 0), boardNames.length - 1);
      const nextName = boardNames[nextIndex];
      if (nextName && nextName !== selectedButlerName) setSelectedButlerName(nextName);
    },
    [boardNames, selectedButlerName],
  );

  const moveBoardCursorByRow = useCallback(
    (direction: 1 | -1) => {
      const columns = boardRef.current ? boardColumnCount(boardRef.current) : 4;
      moveBoardCursor(direction * columns);
    },
    [moveBoardCursor],
  );

  const boardShortcuts = useMemo<ShortcutBinding[]>(() => {
    if (boardNames.length === 0) return [];
    return [
      {
        key: "ArrowRight",
        display: ["→"],
        description: "Next butler",
        handler: () => moveBoardCursor(1),
      },
      {
        key: "ArrowLeft",
        display: ["←"],
        description: "Previous butler",
        handler: () => moveBoardCursor(-1),
      },
      {
        key: "ArrowDown",
        display: ["↓"],
        description: "Next board row",
        handler: () => moveBoardCursorByRow(1),
      },
      {
        key: "ArrowUp",
        display: ["↑"],
        description: "Previous board row",
        handler: () => moveBoardCursorByRow(-1),
      },
      ...(selectedButlerName
        ? [
            {
              key: "Enter",
              display: ["Enter"],
              description: "Open selected butler",
              handler: () => navigate(`/butlers/${selectedButlerName}`),
            },
          ]
        : []),
    ];
  }, [boardNames.length, moveBoardCursor, moveBoardCursorByRow, navigate, selectedButlerName]);
  useRegisterShortcut(boardShortcuts);

  const boardCommands = useMemo<PaletteCommand[]>(
    () =>
      rows.map((row) => ({
        id: `open-butler-${row.name}`,
        label: `Open ${row.name}`,
        keywords: ["butler", row.name, row.type],
        perform: () => navigate(`/butlers/${row.name}`),
      })),
    [navigate, rows],
  );
  useRegisterCommands(boardCommands);

  useEffect(() => {
    if (!selectedButlerName) return;
    for (const node of document.querySelectorAll<HTMLElement>("[data-butler-name]")) {
      if (node.getAttribute("data-butler-name") === selectedButlerName) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [selectedButlerName]);

  // Butler names with a restore scheduled but not yet fired -- backed by the
  // shared useUndoWindow module-scoped store (not useState) so a remount
  // mid-window picks up the already-scheduled state instead of double
  // scheduling.
  const restoreUndo = useUndoWindow("butler-restore");

  const networkPendingName = setEligibility.isPending ? setEligibility.variables?.name : undefined;

  function fireRestore(name: string) {
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

  function handleRestore(name: string) {
    const scheduled = restoreUndo.schedule(name, () => fireRestore(name), RESTORE_UNDO_WINDOW_MS);
    if (!scheduled) return; // already scheduled -- ignore repeat clicks

    toast(`Restoring ${name}`, {
      action: { label: "Undo", onClick: () => restoreUndo.cancel(name) },
      duration: RESTORE_UNDO_WINDOW_MS,
    });
  }

  return (
    <Page
      archetype="status-board"
      title="Butlers"
      loading={isLoading}
      error={pageError}
      onRetry={hasInitialLoadError ? () => void refetch() : undefined}
      empty={
        !isError && !hasRows && !isLoading
          ? { title: "No butlers found", description: "Check daemon status and try again." }
          : null
      }
      header={
        hasInitialLoadError
          ? undefined
          : <BoardHeader aggregates={aggregates} refreshIntervalMs={REFRESH_INTERVAL_MS} />
      }
      footer={hasInitialLoadError ? undefined : <BoardFooter aggregates={aggregates} />}
    >
      {/* Needs-you triage strip — leads with every butler that needs the owner
          (offline, quarantined, or overdue against its own cron cadence),
          collapsing to a single calm line when the fleet is fully healthy. */}
      {hasRows && <NeedsYouStrip rows={needsYou} total={aggregates.total} />}

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
          ref={boardRef}
          role="group"
          aria-label="Butler status board"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 border-t border-l border-border/60"
        >
          {rows.map((row) => (
            <StatusBoardCell
              key={row.name}
              row={row}
              onRestore={handleRestore}
              isRestorePending={restoreUndo.isScheduled(row.name) || networkPendingName === row.name}
              isCursorActive={selectedButlerName === row.name}
            />
          ))}
        </div>
      )}
    </Page>
  );
}
