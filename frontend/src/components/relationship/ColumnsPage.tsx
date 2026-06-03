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
 * Data sources:
 *   GET /api/relationship/owner/setup-status       — resolve owner entity_id
 *   GET /api/relationship/entities/{id}/neighbours — each column's neighbours
 *
 * No new server endpoint is required (resolves Phase 1 Open Question 15).
 * All cascade is client-side via chained useEntityNeighbours calls.
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §8.3
 *       specs/dashboard-relationship/spec.md §"Requirement: Entity Columns view"
 */

import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";

import { getOwnerSetupStatus } from "@/api/index";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { PredicateGroup } from "@/components/ui/PredicateGroup";
import { Skeleton } from "@/components/ui/skeleton";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityNeighbours } from "@/hooks/use-entities";

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
  columnIndex: number;
  /** True when this column is the rightmost (currently active) one. */
  isActive: boolean;
  onSelect: (entityId: string, columnIndex: number) => void;
}

function ColumnPanel({ entityId, columnIndex, isActive, onSelect }: ColumnPanelProps) {
  const { data, isLoading, error, refetch } = useEntityNeighbours(entityId);

  const neighbours = data?.neighbours ?? {};
  const predicates = Object.keys(neighbours).sort();

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
      {/* Column header — entity ID */}
      <div className="px-3 py-2 border-b border-border bg-muted/40 shrink-0">
        <p className="text-xs font-mono text-muted-foreground truncate" title={entityId}>
          {entityId}
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
                columnIndex={columnIndex}
                onSelect={(entityId) => onSelect(entityId, columnIndex)}
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
  const columnIds: string[] = pathIds.length > 0 ? pathIds : anchorId ? [anchorId] : [];

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
  function handleSelect(selectedEntityId: string, columnIndex: number) {
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
  }

  /** Clear path — return to owner column-0 default. */
  function handleReset() {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("path");
        return next;
      },
      { replace: false },
    );
  }

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
        /* Cascading columns — horizontal scroll container */
        <div
          className="flex overflow-x-auto border border-border rounded-md min-h-[400px]"
          data-testid="columns-cascade"
          role="region"
          aria-label="Cascading column view"
        >
          {columnIds.map((entityId, index) => (
            <ColumnPanel
              key={`${entityId}-${index}`}
              entityId={entityId}
              columnIndex={index}
              isActive={index === columnIds.length - 1}
              onSelect={handleSelect}
            />
          ))}
        </div>
      )}
    </Page>
  );
}
