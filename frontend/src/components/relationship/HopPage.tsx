/**
 * HopPage — re-centre graph explorer at /entities/hop.
 *
 * Renders a predicate-grouped fan-out of neighbours for a chosen anchor
 * entity. Clicking any neighbour selects it for inspection in the right
 * detail pane (inspect-without-committing). Re-centring on a neighbour
 * requires an explicit action: Enter on the keyboard cursor, or the
 * "Go to this entity" button in the detail pane.
 *
 * A clickable breadcrumb trail records the re-centre path (owner › A › B);
 * past segments are links and a reset pill appears at depth > 1.
 *
 * URL contract:
 *   ?center=<entity_id>   — anchor entity UUID (defaults to owner entity if absent)
 *   ?trail=<csv>          — comma-separated entity IDs visited before ?center=
 *                           (the breadcrumb's past segments; absent at depth 0)
 *
 * Keyboard map (view-local; attached to the focused relations pane so the
 * app-wide ⌘K / "/" never get shadowed):
 *   ↑ / ↓   move the relations-pane cursor
 *   Enter   re-centre on the cursored neighbour
 *   Esc     pop the last trail segment (step back one hop)
 *   r       reset the trail to the owner anchor
 *
 * Predicate filter chips:
 *   Shown only when the anchor node has 2+ distinct predicates (high-degree
 *   guard). Each chip toggles visibility of that predicate's neighbour group.
 *   Clearing all active filters restores the full set.
 *
 * Right detail pane:
 *   Clicking a neighbour row selects it. The pane shows the selected
 *   neighbour's entity card and their own relations. An explicit "Go to
 *   this entity" button (and Enter on the cursor) triggers re-centring.
 *
 * Data sources:
 *   GET /api/relationship/owner/setup-status                — resolve owner entity_id
 *   GET /api/relationship/entities/{id}                    — anchor name card
 *   GET /api/relationship/entities/{id}/neighbours         — predicate-grouped fan-out
 *     (rank=weight&per_predicate=6 — ranked truncation with "+N more")
 *
 * Spec: openspec/changes/entity-v3-lifecycle-and-depth/specs/dashboard-relationship/spec.md
 *       §"Neighbour ranking and truncation (Hop and Columns)", §"Keyboard maps per view"
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon, ArrowRightIcon, XIcon } from "lucide-react";

import { getOwnerSetupStatus, getRelationshipEntity } from "@/api/index";
import type { NeighbourEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { EntityMark } from "@/components/ui/EntityMark";
import { Page } from "@/components/ui/page";
import { Pill } from "@/components/ui/Pill";
import { PredicateGroup } from "@/components/ui/PredicateGroup";
import { Skeleton } from "@/components/ui/skeleton";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityNeighbours } from "@/hooks/use-entities";

/** Top-N neighbours per predicate group; overflow renders as "+N more". */
const PER_PREDICATE = 6;

/**
 * Minimum number of distinct predicates before filter chips appear.
 * At 1 predicate there is nothing to filter; chips would just add noise.
 */
const CHIP_MIN_PREDICATES = 2;

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

/** Parse the ?trail= CSV into an ordered list of entity IDs. */
function parseTrail(raw: string | null): string[] {
  if (!raw) return [];
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Serialise an entity ID list back to a ?trail= CSV. */
function serialiseTrail(ids: string[]): string {
  return ids.join(",");
}

// ---------------------------------------------------------------------------
// Anchor entity card
// ---------------------------------------------------------------------------

interface AnchorCardProps {
  entityId: string;
}

function AnchorCard({ entityId }: AnchorCardProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["relationship-entity", entityId],
    queryFn: () => getRelationshipEntity(entityId),
    enabled: !!entityId,
  });

  if (isLoading) {
    return (
      <Card data-testid="anchor-card-loading">
        <CardHeader>
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-32 mt-1" />
        </CardHeader>
      </Card>
    );
  }

  if (error != null || data == null) {
    return (
      <Card data-testid="anchor-card-error">
        <CardContent className="pt-6">
          <p className="text-sm text-muted-foreground">
            Could not load entity. It may not exist or owner access is required.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="anchor-card">
      <CardHeader>
        <div className="flex items-center gap-2">
          <EntityMark
            name={data.canonical_name}
            entityType={data.entity_type}
            isOwner={data.roles.includes("owner")}
          />
          <CardTitle className="text-xl">{data.canonical_name}</CardTitle>
          {data.roles.includes("owner") && (
            <Badge
              style={{ backgroundColor: "var(--role-owner)" }}
              className="text-xs text-white"
            >
              Owner
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground capitalize">{data.entity_type}</p>
        {data.aliases.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Also known as: {data.aliases.join(", ")}
          </p>
        )}
      </CardHeader>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Breadcrumb trail — clickable past segments + reset pill at depth > 1
// ---------------------------------------------------------------------------

/** A single trail segment; resolves its display name from the entity API. */
function TrailSegmentLabel({ entityId }: { entityId: string }) {
  const { data } = useQuery({
    queryKey: ["relationship-entity", entityId],
    queryFn: () => getRelationshipEntity(entityId),
    enabled: !!entityId,
  });
  return <>{data?.canonical_name ?? entityId}</>;
}

interface HopTrailProps {
  /** Ordered trail of past anchors, oldest first (excludes the current centre). */
  trail: string[];
  /** The current (rightmost) anchor entity ID. */
  currentId: string;
  /** Re-centre on a past trail segment at index `i`, truncating the trail there. */
  onJump: (index: number) => void;
  /** Reset to the owner anchor (clear the trail). */
  onReset: () => void;
}

function HopTrail({ trail, currentId, onJump, onReset }: HopTrailProps) {
  // depth = number of hops taken; > 1 means the reset pill is shown.
  const depth = trail.length;

  return (
    <nav
      aria-label="Hop trail"
      className="flex flex-wrap items-center gap-1 text-sm"
      data-testid="hop-trail"
    >
      {trail.map((id, i) => (
        <span key={`${id}-${i}`} className="flex items-center gap-1">
          <button
            type="button"
            className="text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded font-medium"
            onClick={() => onJump(i)}
            data-testid={`hop-trail-segment-${i}`}
            data-entity-id={id}
          >
            <TrailSegmentLabel entityId={id} />
          </button>
          <span aria-hidden className="text-muted-foreground">
            ›
          </span>
        </span>
      ))}
      {/* Current (rightmost) segment — not a link. */}
      <span
        className="font-medium text-foreground"
        data-testid="hop-trail-current"
        data-entity-id={currentId}
      >
        <TrailSegmentLabel entityId={currentId} />
      </span>

      {depth > 1 && (
        <Pill
          selected={false}
          onClick={onReset}
          className="ml-2"
          data-testid="hop-trail-reset"
        >
          reset
        </Pill>
      )}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Predicate filter chips
// ---------------------------------------------------------------------------

interface PredicateChipsProps {
  /** All predicates present in the current fan-out. */
  predicates: string[];
  /** Currently active (shown) predicates. Empty set = show all. */
  activePredicates: Set<string>;
  /** Toggle a predicate chip on/off. */
  onToggle: (predicate: string) => void;
  /** Clear all active filters (restore full set). */
  onClear: () => void;
}

/**
 * One chip per predicate in the fan-out, shown only when there are 2+ predicates.
 *
 * Semantics:
 * - When *no* chip is selected (activePredicates is empty), all groups are
 *   shown. This is the default/restored state.
 * - Clicking a chip adds it to the active set; only those predicates are shown.
 * - Clicking an active chip removes it; if this empties the set, all groups
 *   are restored.
 * - A "clear" affordance appears when any filter is active.
 */
function PredicateChips({ predicates, activePredicates, onToggle, onClear }: PredicateChipsProps) {
  const hasActive = activePredicates.size > 0;

  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label="Predicate filters"
      data-testid="predicate-chips"
    >
      {predicates.map((p) => (
        <Pill
          key={p}
          selected={activePredicates.has(p)}
          onClick={() => onToggle(p)}
          data-testid={`predicate-chip-${p}`}
          aria-label={`Filter by ${p.replace(/-/g, " ")}`}
        >
          {p.replace(/-/g, " ")}
        </Pill>
      ))}
      {hasActive && (
        <button
          type="button"
          className="inline-flex items-center gap-0.5 text-[10px] font-mono uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
          onClick={onClear}
          data-testid="predicate-chips-clear"
          aria-label="Clear predicate filters"
        >
          <XIcon className="h-3 w-3" aria-hidden />
          clear
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right detail pane — inspect a selected neighbour without re-centring
// ---------------------------------------------------------------------------

interface NeighbourDetailPaneProps {
  /** The entity ID of the selected neighbour. */
  entityId: string;
  /** Called when the user explicitly chooses to re-centre on this entity. */
  onRecentre: (entityId: string) => void;
  /** Called when the pane is dismissed (deselects the neighbour). */
  onDismiss: () => void;
}

/**
 * Right-hand detail pane for inspecting a selected neighbour without
 * committing to a re-centre. Shows the entity's name card and their own
 * relations (neighbours). An explicit "Go to this entity" button triggers
 * re-centring.
 */
function NeighbourDetailPane({ entityId, onRecentre, onDismiss }: NeighbourDetailPaneProps) {
  const { data: entity, isLoading: entityLoading } = useQuery({
    queryKey: ["relationship-entity", entityId],
    queryFn: () => getRelationshipEntity(entityId),
    enabled: !!entityId,
  });

  const { data: neighboursData, isLoading: neighboursLoading } = useEntityNeighbours(entityId, {
    rank: "weight",
    per_predicate: PER_PREDICATE,
  });

  const neighbours = useMemo(() => neighboursData?.neighbours ?? {}, [neighboursData]);
  const predicates = useMemo(() => Object.keys(neighbours).sort(), [neighbours]);

  return (
    <aside
      className="flex flex-col gap-4 min-w-0 border-l pl-4"
      data-testid="neighbour-detail-pane"
      aria-label="Selected neighbour details"
    >
      {/* Pane header */}
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Inspect
        </p>
        <button
          type="button"
          className="text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          onClick={onDismiss}
          aria-label="Close detail pane"
          data-testid="detail-pane-close"
        >
          <XIcon className="h-4 w-4" aria-hidden />
        </button>
      </div>

      {/* Entity name card */}
      {entityLoading ? (
        <div data-testid="detail-pane-entity-loading">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-3 w-24 mt-1" />
        </div>
      ) : entity != null ? (
        <div data-testid="detail-pane-entity">
          <div className="flex items-center gap-2">
            <EntityMark
              name={entity.canonical_name}
              entityType={entity.entity_type}
              isOwner={entity.roles.includes("owner")}
            />
            <span className="font-medium text-sm">{entity.canonical_name}</span>
          </div>
          <p className="text-xs text-muted-foreground capitalize mt-0.5">{entity.entity_type}</p>
        </div>
      ) : null}

      {/* Re-centre button */}
      <Button
        variant="outline"
        size="sm"
        className="gap-1 w-full"
        onClick={() => onRecentre(entityId)}
        data-testid="detail-pane-recentre-btn"
      >
        <ArrowRightIcon className="h-3.5 w-3.5" aria-hidden />
        Go to this entity
      </Button>

      {/* Relations preview */}
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
          Relations
        </p>
        {neighboursLoading ? (
          <div className="space-y-1" data-testid="detail-pane-relations-loading">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
            <Skeleton className="h-4 w-4/6" />
          </div>
        ) : predicates.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="detail-pane-no-relations">
            No relations found.
          </p>
        ) : (
          <div className="space-y-3 overflow-y-auto max-h-96" data-testid="detail-pane-relations">
            {predicates.map((predicate) => {
              const entries = neighbours[predicate];
              const remainder = neighboursData?.remainders?.[predicate];
              return (
                <div key={predicate}>
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                    {predicate.replace(/-/g, " ")}
                  </p>
                  <ul className="space-y-0.5">
                    {entries.map((entry) => (
                      <li key={entry.entity_id} className="text-xs text-foreground/80 px-1">
                        {entry.canonical_name || entry.entity_id}
                      </li>
                    ))}
                  </ul>
                  {remainder != null && remainder > 0 && (
                    <p className="text-xs text-muted-foreground tabular-nums px-1">
                      +{remainder} more
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Neighbour fan-out panel
// ---------------------------------------------------------------------------

interface NeighbourFanOutProps {
  entityId: string;
  /** Called when the user explicitly re-centres on a neighbour. */
  onRecentre: (entityId: string) => void;
  /** Called when a neighbour is selected for inspection (detail pane). */
  onSelect: (entityId: string) => void;
  /** The currently selected neighbour entity ID (for the detail pane). */
  selectedEntityId: string | null;
  /** Pop the trail (step back one hop); no-op at depth 0. */
  onPopTrail: () => void;
  /** Reset the trail to the owner anchor. */
  onResetTrail: () => void;
}

function NeighbourFanOut({
  entityId,
  onRecentre,
  onSelect,
  selectedEntityId,
  onPopTrail,
  onResetTrail,
}: NeighbourFanOutProps) {
  // Ranked truncation: top-N by weight per predicate, with the overflow count
  // surfaced via `remainders` → the "+N more" affordance on each group.
  const { data, isLoading, error, refetch } = useEntityNeighbours(entityId, {
    rank: "weight",
    per_predicate: PER_PREDICATE,
  });

  const neighbours = useMemo(() => data?.neighbours ?? {}, [data]);
  const remainders = data?.remainders ?? {};
  const allPredicates = useMemo(() => Object.keys(neighbours).sort(), [neighbours]);

  // Predicate filter chips — only shown when there are 2+ distinct predicates.
  const [activePredicates, setActivePredicates] = useState<Set<string>>(new Set());

  const handleChipToggle = useCallback((predicate: string) => {
    setActivePredicates((prev) => {
      const next = new Set(prev);
      if (next.has(predicate)) {
        next.delete(predicate);
      } else {
        next.add(predicate);
      }
      return next;
    });
  }, []);

  const handleChipClear = useCallback(() => {
    setActivePredicates(new Set());
  }, []);

  // The predicates actually rendered: filtered set when chips are active, else all.
  const visiblePredicates = useMemo(
    () =>
      activePredicates.size === 0
        ? allPredicates
        : allPredicates.filter((p) => activePredicates.has(p)),
    [allPredicates, activePredicates],
  );

  // Flattened, predicate-ordered list of selectable neighbours for the cursor.
  // Includes only VISIBLE predicates so cursor never lands on hidden rows.
  // The "+N more" rows are inert and intentionally excluded from cursoring.
  const flatEntries = useMemo<NeighbourEntry[]>(
    () => visiblePredicates.flatMap((p) => neighbours[p]),
    [visiblePredicates, neighbours],
  );

  // The raw cursor index is clamped at read-time against the live entry list
  // (deriving it during render avoids a setState-in-effect cascade). The entry
  // set can shrink after a re-centre, so never trust the raw index directly.
  const [rawCursor, setRawCursor] = useState(0);
  const cursor = flatEntries.length === 0 ? 0 : Math.min(rawCursor, flatEntries.length - 1);
  const paneRef = useRef<HTMLDivElement>(null);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLDivElement>) => {
      // View-local only: never consume keys reserved for the app-wide Finder.
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setRawCursor(flatEntries.length === 0 ? 0 : Math.min(cursor + 1, flatEntries.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setRawCursor(Math.max(cursor - 1, 0));
      } else if (e.key === "Enter") {
        const entry = flatEntries[cursor];
        if (entry) {
          e.preventDefault();
          // Enter re-centres on the cursored neighbour (explicit action).
          onRecentre(entry.entity_id);
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        onPopTrail();
      } else if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        onResetTrail();
      }
    },
    [cursor, flatEntries, onRecentre, onPopTrail, onResetTrail],
  );

  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="neighbours-loading">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-3/4" />
      </div>
    );
  }

  if (error != null) {
    return (
      <div data-testid="neighbours-error">
        <EmptyState
          title="Could not load neighbours"
          description="Owner access is required, or the entity may not exist."
          action={
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (allPredicates.length === 0) {
    return (
      <EmptyState
        title="No neighbours yet."
        description="Relational triples for this entity will appear here once the butler builds the knowledge graph."
      />
    );
  }

  // The cursored entity's ID drives the focus ring on its NeighbourRow.
  const cursoredId = flatEntries[cursor]?.entity_id ?? null;

  const showChips = allPredicates.length >= CHIP_MIN_PREDICATES;

  return (
    <div
      ref={paneRef}
      className="space-y-4 focus:outline-none"
      data-testid="neighbours-panel"
      tabIndex={0}
      role="listbox"
      aria-label="Neighbours: use arrow keys to cursor, Enter to re-centre"
      aria-activedescendant={cursoredId ? `hop-neighbour-${cursoredId}` : undefined}
      onKeyDown={handleKeyDown}
    >
      {/* Predicate filter chips (conditional: only when 2+ predicates) */}
      {showChips && (
        <PredicateChips
          predicates={allPredicates}
          activePredicates={activePredicates}
          onToggle={handleChipToggle}
          onClear={handleChipClear}
        />
      )}

      {/* Neighbour groups (filtered by active chips) */}
      <div className="space-y-6">
        {visiblePredicates.map((predicate) => (
          <PredicateGroup
            key={predicate}
            predicate={predicate}
            entries={neighbours[predicate]}
            remainder={remainders[predicate]}
            onSelect={onSelect}
            cursoredEntityId={cursoredId}
            selectedEntityId={selectedEntityId}
            getRowAriaLabel={(entry) =>
              `Inspect entity ${entry.canonical_name || entry.entity_id}`
            }
          />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// HopPage
// ---------------------------------------------------------------------------

export default function HopPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const centerParam = searchParams.get("center");
  const trail = parseTrail(searchParams.get("trail"));

  // Detail pane: the selected neighbour entity ID (null = pane closed).
  const [selectedNeighbourId, setSelectedNeighbourId] = useState<string | null>(null);

  // Resolve owner entity_id when no ?center= is provided
  const { data: ownerStatus, isLoading: ownerLoading } = useQuery({
    queryKey: ["owner-setup-status"],
    queryFn: getOwnerSetupStatus,
    enabled: centerParam == null,
  });

  // Effective anchor: explicit ?center= beats owner fallback
  const centerId = centerParam ?? ownerStatus?.entity_id ?? null;

  // Re-centre: push the current centre onto the trail, then set the new centre.
  const handleRecentre = useCallback(
    (entityId: string) => {
      // Clear the detail pane when re-centring.
      setSelectedNeighbourId(null);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const prevCenter = next.get("center");
          // Build the new trail by appending the centre we are leaving.
          const currentTrail = parseTrail(next.get("trail"));
          if (prevCenter != null && prevCenter !== entityId) {
            const newTrail = [...currentTrail, prevCenter];
            next.set("trail", serialiseTrail(newTrail));
          }
          next.set("center", entityId);
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  // Select a neighbour for inspection in the detail pane (no re-centring).
  const handleSelect = useCallback((entityId: string) => {
    setSelectedNeighbourId((prev) => (prev === entityId ? null : entityId));
  }, []);

  // Dismiss the detail pane.
  const handleDetailPaneDismiss = useCallback(() => {
    setSelectedNeighbourId(null);
  }, []);

  // Jump to a past trail segment: that segment becomes the centre and the trail
  // is truncated before it.
  const handleTrailJump = useCallback(
    (index: number) => {
      setSelectedNeighbourId(null);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          const currentTrail = parseTrail(next.get("trail"));
          const target = currentTrail[index];
          if (target == null) return next;
          const truncated = currentTrail.slice(0, index);
          next.set("center", target);
          if (truncated.length > 0) {
            next.set("trail", serialiseTrail(truncated));
          } else {
            next.delete("trail");
          }
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  // Pop the trail: step back one hop (the last trail segment becomes the centre).
  const handlePopTrail = useCallback(() => {
    setSelectedNeighbourId(null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        const currentTrail = parseTrail(next.get("trail"));
        if (currentTrail.length === 0) return next; // depth 0 → no-op
        const target = currentTrail[currentTrail.length - 1];
        const truncated = currentTrail.slice(0, -1);
        next.set("center", target);
        if (truncated.length > 0) {
          next.set("trail", serialiseTrail(truncated));
        } else {
          next.delete("trail");
        }
        return next;
      },
      { replace: false },
    );
  }, [setSearchParams]);

  // Reset: clear the trail and return to the owner anchor.
  const handleReset = useCallback(() => {
    setSelectedNeighbourId(null);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("center");
        next.delete("trail");
        return next;
      },
      { replace: false },
    );
  }, [setSearchParams]);

  // Loading: waiting for owner resolution when no explicit center
  if (centerParam == null && ownerLoading) {
    return (
      <Page
        archetype="overview"
        title="Hop"
        description="Re-centre on any neighbour to explore the relationship graph."
        breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Hop" }]}
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
      title="Hop"
      description="Re-centre on any neighbour to explore the relationship graph."
      breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Hop" }]}
    >
      {/* SubpageTabs — Hop is active */}
      <SubpageTabs />

      {/* Back-to-owner button when browsing a non-owner anchor */}
      {centerParam != null && (
        <div>
          <Button
            variant="ghost"
            size="sm"
            className="gap-1 text-muted-foreground"
            onClick={handleReset}
            data-testid="clear-center-btn"
          >
            <ArrowLeftIcon className="h-3.5 w-3.5" aria-hidden />
            Reset to owner
          </Button>
        </div>
      )}

      {/* No center and no owner registered */}
      {centerId == null ? (
        <EmptyState
          title="No anchor entity found."
          description="Set up your owner entity or pass ?center=<entity_id> to begin hopping."
          data-testid="hop-no-center"
        />
      ) : (
        <div className="space-y-6">
          {/* Breadcrumb trail — clickable past segments + reset pill */}
          {trail.length > 0 && (
            <HopTrail
              trail={trail}
              currentId={centerId}
              onJump={handleTrailJump}
              onReset={handleReset}
            />
          )}

          {/* Anchor entity card */}
          <AnchorCard entityId={centerId} />

          {/* Neighbour fan-out + optional right detail pane */}
          <div className="flex gap-6">
            {/* Fan-out panel */}
            <Card className="flex-1 min-w-0">
              <CardHeader>
                <CardTitle className="text-base">Neighbours</CardTitle>
              </CardHeader>
              <CardContent>
                <NeighbourFanOut
                  entityId={centerId}
                  onRecentre={handleRecentre}
                  onSelect={handleSelect}
                  selectedEntityId={selectedNeighbourId}
                  onPopTrail={handlePopTrail}
                  onResetTrail={handleReset}
                />
              </CardContent>
            </Card>

            {/* Right detail pane — shown only when a neighbour is selected */}
            {selectedNeighbourId != null && (
              <div className="w-72 shrink-0">
                <NeighbourDetailPane
                  entityId={selectedNeighbourId}
                  onRecentre={handleRecentre}
                  onDismiss={handleDetailPaneDismiss}
                />
              </div>
            )}
          </div>
        </div>
      )}
    </Page>
  );
}
