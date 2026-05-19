/**
 * HopPage — re-centre graph explorer at /entities/hop.
 *
 * Renders a predicate-grouped fan-out of neighbours for a chosen anchor
 * entity. Clicking any neighbour re-centres the view by updating ?center=
 * in the URL and refetching, keeping the user on /entities/hop.
 *
 * URL contract:
 *   ?center=<entity_id>   — anchor entity UUID (defaults to owner entity if absent)
 *
 * Data sources:
 *   GET /api/relationship/owner/setup-status                — resolve owner entity_id
 *   GET /api/relationship/entities/{id}                    — anchor name card
 *   GET /api/relationship/entities/{id}/neighbours         — predicate-grouped fan-out
 *
 * Spec: openspec/changes/relationship-tabs-to-entities tasks.md §8.2
 *       specs/dashboard-relationship/spec.md §"Requirement: Entity Hop view"
 */

import { useCallback } from "react";
import { useSearchParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftIcon, NetworkIcon } from "lucide-react";

import type { NeighbourEntry } from "@/api/types";
import { getOwnerSetupStatus, getRelationshipEntity } from "@/api/index";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityNeighbours } from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// EntityMark glyph (mirrors EntitiesIndexPage inline variant)
// ---------------------------------------------------------------------------

function EntityMark({ entityType }: { entityType: string }) {
  const glyphs: Record<string, string> = {
    person: "P",
    organization: "O",
    location: "L",
    product: "X",
    group: "G",
    email: "@",
    other: "E",
  };
  const glyph = glyphs[entityType] ?? "E";
  return (
    <span
      className="inline-flex items-center justify-center h-5 w-5 rounded text-xs font-mono font-semibold tabular-nums bg-muted text-muted-foreground"
      aria-label={entityType}
    >
      {glyph}
    </span>
  );
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
          <EntityMark entityType={data.entity_type} />
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
// Neighbour row
// ---------------------------------------------------------------------------

interface NeighbourRowProps {
  entry: NeighbourEntry;
  onRecentre: (entityId: string) => void;
}

function NeighbourRow({ entry, onRecentre }: NeighbourRowProps) {
  const entityId = entry.entity_id;
  return (
    <li
      className="flex items-center justify-between py-2 border-b last:border-0 hover:bg-muted/40 px-2 rounded-sm"
      data-testid="neighbour-row"
    >
      <button
        type="button"
        className="flex items-center gap-2 text-left text-sm font-medium text-primary hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
        onClick={() => onRecentre(entityId)}
        aria-label={`Re-centre on entity ${entityId}`}
        data-entity-id={entityId}
      >
        <NetworkIcon className="h-3.5 w-3.5 text-muted-foreground shrink-0" aria-hidden />
        <span>{entityId}</span>
      </button>

      <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0 ml-4">
        {entry.weight != null && (
          <span className="tabular-nums" title="Edge weight">
            w={entry.weight}
          </span>
        )}
        {entry.last_seen != null && (
          <Time value={entry.last_seen} mode="relative" />
        )}
        <Badge variant="outline" className="text-xs">
          {entry.direction === "forward" ? "→" : "←"}
        </Badge>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Predicate group
// ---------------------------------------------------------------------------

interface PredicateGroupProps {
  predicate: string;
  entries: NeighbourEntry[];
  onRecentre: (entityId: string) => void;
}

function PredicateGroup({ predicate, entries, onRecentre }: PredicateGroupProps) {
  const label = predicate.replace(/-/g, " ");
  return (
    <section data-testid={`predicate-group-${predicate}`}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-1 px-2">
        {label}
        <span className="ml-2 font-normal tabular-nums">({entries.length})</span>
      </h3>
      <ul>
        {entries.map((entry) => (
          <NeighbourRow key={entry.entity_id} entry={entry} onRecentre={onRecentre} />
        ))}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Neighbour fan-out panel
// ---------------------------------------------------------------------------

interface NeighbourFanOutProps {
  entityId: string;
  onRecentre: (entityId: string) => void;
}

function NeighbourFanOut({ entityId, onRecentre }: NeighbourFanOutProps) {
  const { data, isLoading, error, refetch } = useEntityNeighbours(entityId);

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

  const neighbours = data?.neighbours ?? {};
  const predicates = Object.keys(neighbours).sort();

  if (predicates.length === 0) {
    return (
      <EmptyState
        title="No neighbours yet."
        description="Relational triples for this entity will appear here once the butler builds the knowledge graph."
      />
    );
  }

  return (
    <div className="space-y-6" data-testid="neighbours-panel">
      {predicates.map((predicate) => (
        <PredicateGroup
          key={predicate}
          predicate={predicate}
          entries={neighbours[predicate]}
          onRecentre={onRecentre}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// HopPage
// ---------------------------------------------------------------------------

export default function HopPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  const centerParam = searchParams.get("center");

  // Resolve owner entity_id when no ?center= is provided
  const { data: ownerStatus, isLoading: ownerLoading } = useQuery({
    queryKey: ["owner-setup-status"],
    queryFn: getOwnerSetupStatus,
    enabled: centerParam == null,
  });

  // Effective anchor: explicit ?center= beats owner fallback
  const centerId = centerParam ?? ownerStatus?.entity_id ?? null;

  const handleRecentre = useCallback(
    (entityId: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("center", entityId);
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

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
            onClick={() =>
              setSearchParams(
                (prev) => {
                  const next = new URLSearchParams(prev);
                  next.delete("center");
                  return next;
                },
                { replace: false },
              )
            }
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
          {/* Anchor entity card */}
          <AnchorCard entityId={centerId} />

          {/* Neighbour fan-out */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Neighbours</CardTitle>
            </CardHeader>
            <CardContent>
              <NeighbourFanOut entityId={centerId} onRecentre={handleRecentre} />
            </CardContent>
          </Card>
        </div>
      )}
    </Page>
  );
}
