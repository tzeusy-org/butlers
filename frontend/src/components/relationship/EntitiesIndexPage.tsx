/**
 * EntitiesIndexPage — /entities canonical index for the relationship butler.
 *
 * Replaces the memory butler's EntitiesPage at the /entities route per
 * tasks.md §8.1 (entity-redesign Phase 2).
 *
 * Layout (spec §Requirement: Entity index page):
 *   - SubpageTabs strip (Index active)
 *   - Tabular list in the main column (from 9.1 list+filter API)
 *   - Filter chips: type, state (unidentified / duplicate-candidate / stale),
 *     has=contact
 *   - Curation queue right rail (from 9.5 queue endpoint)
 *
 * Renders inside <Page archetype="overview"> with breadcrumb "Entities".
 *
 * Spec: openspec/changes/relationship-tabs-to-entities/specs/dashboard-relationship/spec.md
 *       §Requirement: Entity index page
 */

import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import type {
  RelationshipEntitySummary,
  RelationshipEntityListParams,
  RelationshipQueueEntry,
} from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import {
  useRelationshipEntities,
  useRelationshipEntityQueue,
} from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const ENTITY_TYPES = [
  "person",
  "organization",
  "location",
  "product",
  "group",
  "other",
] as const;
type EntityType = (typeof ENTITY_TYPES)[number];

const TYPE_LABELS: Record<EntityType, string> = {
  person: "Person",
  organization: "Org",
  location: "Location",
  product: "Product",
  group: "Group",
  other: "Other",
};

const STATE_CHIPS = [
  { value: "unidentified", label: "Unidentified" },
  { value: "duplicate-candidate", label: "Duplicate" },
  { value: "stale", label: "Stale" },
] as const;
type EntityState = (typeof STATE_CHIPS)[number]["value"];

// ---------------------------------------------------------------------------
// Filter chips bar
// ---------------------------------------------------------------------------

interface FilterChipsProps {
  typeFilter: EntityType | null;
  stateFilter: EntityState | null;
  hasContact: boolean;
  onTypeChange: (type: EntityType | null) => void;
  onStateChange: (state: EntityState | null) => void;
  onHasContactChange: (v: boolean) => void;
}

function FilterChips({
  typeFilter,
  stateFilter,
  hasContact,
  onTypeChange,
  onStateChange,
  onHasContactChange,
}: FilterChipsProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {/* Type filter chips */}
      {ENTITY_TYPES.map((t) => (
        <Button
          key={t}
          variant={typeFilter === t ? "default" : "outline"}
          size="sm"
          onClick={() => onTypeChange(typeFilter === t ? null : t)}
        >
          {TYPE_LABELS[t]}
        </Button>
      ))}

      {/* Separator */}
      <span className="h-5 w-px bg-border mx-1" aria-hidden="true" />

      {/* has=contact chip */}
      <Button
        variant={hasContact ? "default" : "outline"}
        size="sm"
        onClick={() => onHasContactChange(!hasContact)}
      >
        Has contact
      </Button>

      {/* State chips */}
      {STATE_CHIPS.map(({ value, label }) => (
        <Button
          key={value}
          variant={stateFilter === value ? "default" : "outline"}
          size="sm"
          onClick={() => onStateChange(stateFilter === value ? null : value)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Entity table
// ---------------------------------------------------------------------------

function EntityMark({ entityType }: { entityType: string }) {
  // Type indicator glyph per §8.1 spec: P/O/L/X/@/E/G
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

const DUNBAR_TIER_LABELS: Record<number, string> = {
  5: "Support Clique",
  15: "Sympathy Group",
  50: "Good Friends",
  150: "Meaningful",
  500: "Acquaintances",
  1500: "Familiar Faces",
};

interface EntityTableProps {
  entities: RelationshipEntitySummary[];
  isLoading: boolean;
}

function EntityTable({ entities, isLoading }: EntityTableProps) {
  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    );
  }

  if (entities.length === 0) {
    return (
      <EmptyState
        title="No entities found."
        description="Entities appear as the butler builds the knowledge graph."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" data-testid="entity-table">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="pb-2 pr-2 font-medium w-8" aria-label="Type" />
            <th className="pb-2 pr-4 font-medium">Name</th>
            <th className="pb-2 pr-4 font-medium">Tier</th>
            <th className="pb-2 pr-4 font-medium">Last seen</th>
            <th className="pb-2 pr-4 font-medium text-right tabular-nums">Contacts</th>
            <th className="pb-2 font-medium">Aliases</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((entity) => (
            <tr
              key={entity.id}
              className="border-b last:border-0 hover:bg-muted/50"
            >
              <td className="py-2.5 pr-2">
                <EntityMark entityType={entity.entity_type} />
              </td>
              <td className="py-2.5 pr-4">
                <span className="inline-flex items-center gap-2">
                  <Link
                    to={`/entities/${entity.id}`}
                    className="font-medium text-primary hover:underline"
                  >
                    {entity.canonical_name}
                  </Link>
                  {entity.roles?.includes("owner") && (
                    <Badge
                      style={{ backgroundColor: "var(--role-owner)", color: "#fff" }}
                      className="text-xs"
                    >
                      Owner
                    </Badge>
                  )}
                  {entity.metadata?.["unidentified"] === "true" && (
                    <Badge
                      style={{
                        backgroundColor: "var(--state-unidentified)",
                        color: "#fff",
                      }}
                      className="text-xs"
                    >
                      Unidentified
                    </Badge>
                  )}
                </span>
              </td>
              <td className="py-2.5 pr-4">
                {entity.tier != null ? (
                  <Badge variant="outline" className="text-xs tabular-nums">
                    {entity.tier} — {DUNBAR_TIER_LABELS[entity.tier] ?? `Tier ${entity.tier}`}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">—</span>
                )}
              </td>
              <td className="py-2.5 pr-4 text-muted-foreground">
                {entity.last_seen ? (
                  <Time value={entity.last_seen} mode="relative" />
                ) : (
                  <span className="text-xs text-muted-foreground">—</span>
                )}
              </td>
              <td className="py-2.5 pr-4 text-right tabular-nums text-muted-foreground">
                {entity.contact_fact_count}
              </td>
              <td className="py-2.5 text-muted-foreground text-xs">
                {entity.aliases.length > 0 ? entity.aliases.join(", ") : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Curation queue right rail (9.5)
// ---------------------------------------------------------------------------

function QueueRail() {
  const { data, isLoading, isError, error } = useRelationshipEntityQueue({ limit: 20 });

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-5 w-5/6" />
      </div>
    );
  }

  if (isError) {
    const message = error instanceof Error ? error.message : "Failed to load queue.";
    return (
      <p className="text-xs text-destructive" role="alert">
        {message}
      </p>
    );
  }

  const items = data?.items ?? [];

  if (items.length === 0) {
    return (
      <p
        className="text-sm italic text-muted-foreground"
        style={{ fontFamily: "'Source Serif 4', Georgia, serif" }}
        data-testid="queue-rail-empty"
      >
        Nothing waiting.
      </p>
    );
  }

  // Group by bucket
  const unidentified = items.filter((e) => e.bucket === "unidentified");
  const duplicates = items.filter((e) => e.bucket === "duplicate-candidate");
  const stale = items.filter((e) => e.bucket === "stale");

  return (
    <div className="space-y-4" data-testid="queue-rail">
      {unidentified.length > 0 && (
        <QueueSection
          title="Unidentified"
          items={unidentified}
          accentColor="var(--amber)"
        />
      )}
      {duplicates.length > 0 && (
        <QueueSection
          title="Duplicate candidate"
          items={duplicates}
          accentColor="var(--amber)"
        />
      )}
      {stale.length > 0 && (
        <QueueSection
          title="Stale"
          items={stale}
          accentColor="var(--muted-foreground)"
        />
      )}
    </div>
  );
}

function QueueSection({
  title,
  items,
  accentColor,
}: {
  title: string;
  items: RelationshipQueueEntry[];
  accentColor: string;
}) {
  return (
    <div>
      <p
        className="text-xs font-semibold uppercase tracking-wide mb-2"
        style={{ color: accentColor }}
      >
        {title}
      </p>
      <ul className="space-y-1">
        {items.map((entry) => (
          <li key={entry.entity_id} className="text-sm">
            <Link
              to={`/entities/${entry.entity_id}`}
              className="text-primary hover:underline"
            >
              {entry.canonical_name}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

export function EntitiesIndexPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);

  // URL is the source of truth for all filter chips.
  // ?type=person activates the Person chip; any other value or absence deactivates it.
  // ?state=unidentified activates the Unidentified chip; etc.
  // ?has=contact activates the Has contact chip; any other value or absence deactivates it.
  const rawType = searchParams.get("type");
  const typeFilter: EntityType | null =
    rawType !== null && (ENTITY_TYPES as readonly string[]).includes(rawType)
      ? (rawType as EntityType)
      : null;

  const rawState = searchParams.get("state");
  const stateFilter: EntityState | null =
    rawState !== null && STATE_CHIPS.some((c) => c.value === rawState)
      ? (rawState as EntityState)
      : null;

  const hasContact = searchParams.get("has") === "contact";

  const params: RelationshipEntityListParams = {
    entity_type: typeFilter ?? undefined,
    state: stateFilter ?? undefined,
    has: hasContact ? "contact" : undefined,
    limit: PAGE_SIZE,
    offset,
  };

  const { data, isLoading, error } = useRelationshipEntities(params);
  const entities = data?.items ?? [];
  const total = data?.total ?? 0;

  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);
  const hasMore = offset + PAGE_SIZE < total;
  const hasPrev = offset > 0;

  function handleTypeChange(type: EntityType | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (type !== null) {
          next.set("type", type);
        } else {
          next.delete("type");
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  function handleStateChange(state: EntityState | null) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (state !== null) {
          next.set("state", state);
        } else {
          next.delete("state");
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  function handleHasContactChange(v: boolean) {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v) {
          next.set("has", "contact");
        } else {
          next.delete("has");
        }
        return next;
      },
      { replace: false },
    );
    setOffset(0);
  }

  return (
    <Page
      archetype="overview"
      title="Entities"
      description="Browse the relationship graph: people, organizations, and more."
      breadcrumbs={[{ label: "Entities" }]}
      error={error}
    >
      {/* SubpageTabs strip — Index is active */}
      <SubpageTabs />

      {/* Filter chips */}
      <FilterChips
        typeFilter={typeFilter}
        stateFilter={stateFilter}
        hasContact={hasContact}
        onTypeChange={handleTypeChange}
        onStateChange={handleStateChange}
        onHasContactChange={handleHasContactChange}
      />

      {/* Main content + right rail */}
      <div className="flex gap-6">
        {/* Main column — entity table */}
        <div className="min-w-0 flex-1 space-y-4">
          <EntityTable entities={entities} isLoading={isLoading} />

          {/* Pagination */}
          {total > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Showing {rangeStart}&ndash;{rangeEnd} of {total.toLocaleString()}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasPrev}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasMore}
                  onClick={() => setOffset((o) => o + PAGE_SIZE)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </div>

        {/* Right rail — curation queue */}
        <aside
          className="w-64 shrink-0 space-y-4 border-l border-border pl-6"
          aria-label="Curation queue"
        >
          <p className="text-sm font-semibold text-foreground">Queue</p>
          <QueueRail />
        </aside>
      </div>
    </Page>
  );
}
