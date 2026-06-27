/**
 * ColumnsPage — Finder-style cascading column drill at /entities/columns.
 *
 * Each column shows one entity's predicate-grouped neighbours. Clicking a
 * neighbour in column N appends column N+1 showing that neighbour's neighbours.
 * Column 0 defaults to the owner entity when no `?path=` is provided.
 *
 * URL contract:
 *   ?path=<csv>   — comma-separated entity IDs for columns 0..N
 *                   (e.g. ?path=ent-a,ent-b,ent-c means three columns)
 *                   Absent or empty → column 0 = owner entity
 *
 * Keyboard map (view-local; attached to the focused cascade container so the
 * app-wide ⌘K / "/" never get shadowed). All keys operate on the rightmost
 * (active) column's cursor:
 *   ↑ / ↓   move the cursor within the active column
 *   →       deepen — open a new column for the cursored neighbour
 *   ←       pop the rightmost column
 *   Enter   open the cursored neighbour's detail page
 *
 * Data sources:
 *   GET /api/relationship/owner/setup-status       — resolve owner entity_id
 *   GET /api/relationship/entities/{id}/neighbours — each column's neighbours
 *     (rank=weight&per_predicate=6 — ranked truncation with "+N more")
 *
 * No new server endpoint is required (standing option (a)): all cascade is
 * client-side via chained useEntityNeighbours calls.
 *
 * Spec: openspec/changes/entity-v3-lifecycle-and-depth/specs/dashboard-relationship/spec.md
 *       §"Neighbour ranking and truncation (Hop and Columns)", §"Keyboard maps per view"
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";

import { getOwnerSetupStatus } from "@/api/index";
import type { NeighbourEntry } from "@/api/types";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { PredicateGroup } from "@/components/ui/PredicateGroup";
import { Skeleton } from "@/components/ui/skeleton";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityNeighbours } from "@/hooks/use-entities";

/** Top-N neighbours per predicate group; overflow renders as "+N more". */
const PER_PREDICATE = 6;

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

/** Parse the ?path= CSV into an ordered list of entity IDs. */
function parsePath(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Serialise an entity ID list back to a ?path= CSV. */
function serialisePath(ids: string[]): string {
  return ids.join(",");
}

// ---------------------------------------------------------------------------
// ColumnPanel — one column in the cascade
// ---------------------------------------------------------------------------

interface ColumnPanelProps {
  entityId: string;
  /** Human-readable name for the anchor entity, resolved from a parent column's neighbour data. */
  canonicalName?: string;
  columnIndex: number;
  /** True when this column is the rightmost (currently active) one. */
  isActive: boolean;
  onSelect: (entityId: string, columnIndex: number) => void;
  /**
   * The entity_id of the cursored neighbour in the active column (focus ring).
   * Only meaningful when ``isActive``. Reported back via ``onEntriesChange`` so
   * the page can drive the cursor against the live, flattened entry list.
   */
  cursoredEntityId?: string | null;
  /** Reports this column's flattened neighbour list so the page can track canonical names and cursor entries. */
  onEntriesChange?: (entityId: string, entries: NeighbourEntry[]) => void;
}

function ColumnPanel({
  entityId,
  canonicalName,
  columnIndex,
  isActive,
  onSelect,
  cursoredEntityId,
  onEntriesChange,
}: ColumnPanelProps) {
  // Ranked truncation: top-N by weight per predicate; overflow → "+N more".
  const { data, isLoading, error, refetch } = useEntityNeighbours(entityId, {
    rank: "weight",
    per_predicate: PER_PREDICATE,
  });

  const neighbours = useMemo(() => data?.neighbours ?? {}, [data]);
  const remainders = data?.remainders ?? {};
  const predicates = useMemo(() => Object.keys(neighbours).sort(), [neighbours]);

  // Surface this column's flattened entries so the page can:
  //   (a) cursor the active column, and (b) build the canonical-name map.
  const flatEntries = useMemo<NeighbourEntry[]>(
    () => predicates.flatMap((p) => neighbours[p]),
    [predicates, neighbours],
  );
  useEffect(() => {
    onEntriesChange?.(entityId, flatEntries);
  }, [entityId, flatEntries, onEntriesChange]);

  return (
    <div
      className={[
        "flex-shrink-0 w-64 border-r border-border overflow-y-auto",
        "flex flex-col",
        isActive ? "bg-background" : "bg-muted/20",
      ].join(" ")}
      data-testid={`column-panel-${columnIndex}`}
      aria-label={`Column ${columnIndex}: ${entityId}`}
    >
      {/* Column header — canonical name with entity ID as tooltip fallback */}
      <div className="px-3 py-2 border-b border-border bg-muted/40 shrink-0">
        <p className="text-xs font-mono text-muted-foreground truncate" title={entityId}>
          {canonicalName ?? entityId}
        </p>
      </div>

      {/* Column body */}
      <div className="flex-1 overflow-y-auto p-2">
        {isLoading && (
          <div className="space-y-2 pt-2" data-testid={`column-loading-${columnIndex}`}>
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-3/4" />
          </div>
        )}

        {error != null && !isLoading && (
          <div className="pt-2" data-testid={`column-error-${columnIndex}`}>
            <EmptyState
              title="Could not load neighbours"
              description="Owner access may be required."
              action={
                <Button variant="outline" size="sm" onClick={() => void refetch()}>
                  Retry
                </Button>
              }
            />
          </div>
        )}

        {!isLoading && error == null && predicates.length === 0 && (
          <div className="pt-2" data-testid={`column-empty-${columnIndex}`}>
            <EmptyState
              title="No neighbours."
              description="This entity has no relational triples yet."
            />
          </div>
        )}

        {!isLoading && error == null && predicates.length > 0 && (
          <div className="space-y-4" data-testid={`column-neighbours-${columnIndex}`}>
            {predicates.map((predicate) => (
              <PredicateGroup
                key={predicate}
                predicate={predicate}
                entries={neighbours[predicate]}
                remainder={remainders[predicate]}
                columnIndex={columnIndex}
                onSelect={(selectedId) => onSelect(selectedId, columnIndex)}
                cursoredEntityId={isActive ? cursoredEntityId : null}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ColumnsPage
// ---------------------------------------------------------------------------

export default function ColumnsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const pathParam = searchParams.get("path");
  const pathIds = parsePath(pathParam);

  // Resolve owner entity_id when ?path= is absent or empty.
  const { data: ownerStatus, isLoading: ownerLoading } = useQuery({
    queryKey: ["owner-setup-status"],
    queryFn: getOwnerSetupStatus,
    enabled: pathIds.length === 0,
  });

  // Effective first-column ID: first entry of ?path= or owner fallback.
  const anchorId = pathIds[0] ?? ownerStatus?.entity_id ?? null;

  // The cascade is the full list of entity IDs to render as columns.
  // When no ?path= is given and owner is known, we show [anchorId].
  // When ?path= is given, we show pathIds directly.
  const columnIds = useMemo<string[]>(
    () => (pathIds.length > 0 ? pathIds : anchorId ? [anchorId] : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pathIds.join(","), anchorId],
  );

  // ---- Active-column cursor (view-local keyboard navigation) -------------
  // The cursor tracks a row in the rightmost (active) column. ColumnPanel
  // reports that column's flattened neighbour list back up here so the cursor
  // is driven against live data.
  const [rawCursor, setRawCursor] = useState(0);
  const [activeEntries, setActiveEntries] = useState<NeighbourEntry[]>([]);
  const cascadeRef = useRef<HTMLDivElement>(null);

  // ---- Canonical-name registry -----------------------------------------
  // Every column reports its flat entries here so we can look up canonical
  // names for column headers (each column's anchor entity appears as a
  // NeighbourEntry in the previous column's data).
  const [canonicalNames, setCanonicalNames] = useState<Record<string, string>>({});

  const handleEntriesChange = useCallback(
    (anchorEntityId: string, entries: NeighbourEntry[]) => {
      // Update the active column's cursor entries.
      const activeColumnId = columnIds[columnIds.length - 1];
      if (anchorEntityId === activeColumnId) {
        setActiveEntries((prev) => {
          // Avoid an update loop: only commit when the id list actually changed.
          if (
            prev.length === entries.length &&
            prev.every((e, i) => e.entity_id === entries[i].entity_id)
          ) {
            return prev;
          }
          return entries;
        });
      }
      // Build the canonical-name map from every column's neighbour list.
      if (entries.length === 0) return;
      setCanonicalNames((prev) => {
        const patch: Record<string, string> = {};
        for (const e of entries) {
          if (e.canonical_name && !prev[e.entity_id]) {
            patch[e.entity_id] = e.canonical_name;
          }
        }
        return Object.keys(patch).length === 0 ? prev : { ...prev, ...patch };
      });
    },
    [columnIds],
  );

  // ---- Auto-scroll: reveal the newly-opened column ---------------------
  // When the column count grows, scroll the cascade container so the last
  // (active) column is visible. cascadeRef points at the overflow-x-auto div.
  useEffect(() => {
    if (cascadeRef.current) {
      cascadeRef.current.scrollLeft = cascadeRef.current.scrollWidth;
    }
  }, [columnIds.length]);

  // Clamp the cursor to the live entry list at read-time (deriving it during
  // render avoids a setState-in-effect cascade).
  const cursor = activeEntries.length === 0 ? 0 : Math.min(rawCursor, activeEntries.length - 1);

  /**
   * Select an entity from column `columnIndex`, truncating any columns to the
   * right before appending the new selection.
   *
   * Finder-style behaviour: clicking an item in column N replaces columns
   * N+1..end with the newly selected entity's column.
   *
   * When the user is on the owner-fallback view (no ?path= yet), the base is
   * seeded with [anchorId] so the URL captures both the anchor and the new
   * selection.
   */
  const handleSelect = useCallback(
    (selectedEntityId: string, columnIndex: number) => {
      // A new rightmost column becomes active — start its cursor at the top.
      setRawCursor(0);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const currentPath = parsePath(next.get("path"));
          // If no ?path= yet, seed with the resolved anchor (owner fallback).
          const base =
            currentPath.length > 0 ? currentPath : anchorId ? [anchorId] : [];
          // Truncate to the clicked column's position, then append the selection.
          const truncated = base.slice(0, columnIndex + 1);
          // No-op if the clicked column already ends with this entity.
          if (truncated[truncated.length - 1] === selectedEntityId) return next;
          const newPath = [...truncated, selectedEntityId];
          next.set("path", serialisePath(newPath));
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams, anchorId],
  );

  /** Clear path — return to owner column-0 default. */
  const handleReset = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("path");
        return next;
      },
      { replace: false },
    );
  }, [setSearchParams]);

  /** Pop the rightmost column (← key). No-op at a single column. */
  const handlePopRightmost = useCallback(() => {
    // The previous column becomes active again — reset its cursor to the top.
    setRawCursor(0);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        const currentPath = parsePath(next.get("path"));
        // Seed from the rendered cascade when no explicit path yet.
        const base =
          currentPath.length > 0 ? currentPath : anchorId ? [anchorId] : [];
        if (base.length <= 1) return next; // nothing to pop
        const popped = base.slice(0, -1);
        next.set("path", serialisePath(popped));
        return next;
      },
      { replace: false },
    );
  }, [setSearchParams, anchorId]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      // View-local only: never consume keys reserved for the app-wide Finder.
      const activeColumnIndex = columnIds.length - 1;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setRawCursor(
          activeEntries.length === 0 ? 0 : Math.min(cursor + 1, activeEntries.length - 1),
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setRawCursor(Math.max(cursor - 1, 0));
      } else if (e.key === "ArrowRight") {
        const entry = activeEntries[cursor];
        if (entry) {
          e.preventDefault();
          handleSelect(entry.entity_id, activeColumnIndex);
        }
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        handlePopRightmost();
      } else if (e.key === "Enter") {
        const entry = activeEntries[cursor];
        if (entry) {
          e.preventDefault();
          navigate(`/entities/${entry.entity_id}`);
        }
      }
    },
    [activeEntries, cursor, columnIds.length, handleSelect, handlePopRightmost, navigate],
  );

  const cursoredId = activeEntries[cursor]?.entity_id ?? null;

  // Loading: waiting for owner resolution when no explicit path
  if (pathIds.length === 0 && ownerLoading) {
    return (
      <Page
        archetype="overview"
        title="Columns"
        description="Explore relationships in a cascading column view."
        breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Columns" }]}
        loading
      >
        {/* loading state handled by Page primitive */}
        <></>
      </Page>
    );
  }

  return (
    <Page
      archetype="overview"
      title="Columns"
      description="Explore relationships in a cascading column view."
      breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Columns" }]}
    >
      {/* SubpageTabs — Columns is active */}
      <SubpageTabs />

      {/* Reset button when a non-default path is active */}
      {pathParam != null && pathParam !== "" && (
        <div>
          <Button
            variant="ghost"
            size="sm"
            className="gap-1 text-muted-foreground"
            onClick={handleReset}
            data-testid="clear-path-btn"
          >
            Reset to owner
          </Button>
        </div>
      )}

      {/* No anchor resolved (no owner set up, no ?path=) */}
      {columnIds.length === 0 ? (
        <div data-testid="columns-no-anchor">
          <EmptyState
            title="No anchor entity found."
            description="Set up your owner entity or pass ?path=<entity_id> to begin."
          />
        </div>
      ) : (
        /* Cascading columns — horizontal scroll container.
           tabIndex makes the cascade focusable so the view-local keyboard map
           (↑↓ → ← Enter) attaches here and never shadows ⌘K / "/". */
        <div
          ref={cascadeRef}
          className="flex overflow-x-auto border border-border rounded-md min-h-[400px] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          data-testid="columns-cascade"
          role="region"
          aria-label="Cascading column view: use arrow keys to navigate, Enter to open detail"
          tabIndex={0}
          onKeyDown={handleKeyDown}
        >
          {columnIds.map((entityId, index) => (
            <ColumnPanel
              key={`${entityId}-${index}`}
              entityId={entityId}
              canonicalName={canonicalNames[entityId]}
              columnIndex={index}
              isActive={index === columnIds.length - 1}
              onSelect={handleSelect}
              cursoredEntityId={index === columnIds.length - 1 ? cursoredId : null}
              onEntriesChange={handleEntriesChange}
            />
          ))}
        </div>
      )}
    </Page>
  );
}
