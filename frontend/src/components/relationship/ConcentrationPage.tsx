/**
 * ConcentrationPage — weight-aggregation balance-sheet at /entities/concentration.
 *
 * Renders a ranked table of entities by edge-weight for a selected relational
 * predicate. The predicate is selected via URL state (?predicate=<id>), making
 * every view deep-linkable. Tabs across the top enumerate all relational
 * predicates from the registry (returned inline by the concentration endpoint).
 *
 * URL contract:
 *   ?predicate=<predicate_id>   — active predicate (defaults to 'knows' server-side
 *                                  when absent; the tab strip reflects the active
 *                                  predicate returned by the server)
 *
 * Data sources:
 *   GET /api/relationship/entities/concentration?pred=<predicate>
 *     — returns items[], rollup{}, predicate_tabs[], predicate, total
 *
 * Spec: openspec/changes/archive/2026-05-20-relationship-tabs-to-entities/tasks.md §8.4
 *       specs/dashboard-relationship/spec.md §"Concentration"
 */

import { useCallback } from "react";
import { useSearchParams } from "react-router";

import type { ConcentrationEntry, PredicateTab } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityConcentration } from "@/hooks/use-entities";

// ---------------------------------------------------------------------------
// Predicate tab strip (horizontal, one per relational predicate in registry)
// ---------------------------------------------------------------------------

interface PredicateTabStripProps {
  tabs: PredicateTab[];
  activePredicate: string;
  onSelect: (predicate: string) => void;
}

function PredicateTabStrip({ tabs, activePredicate, onSelect }: PredicateTabStripProps) {
  if (tabs.length === 0) {
    return null;
  }

  return (
    <nav
      aria-label="Predicate filter"
      className="flex flex-wrap gap-1 border-b border-border pb-0"
      data-testid="predicate-tab-strip"
    >
      {tabs.map(({ predicate, label }) => {
        const isActive = predicate === activePredicate;
        return (
          <button
            key={predicate}
            type="button"
            onClick={() => onSelect(predicate)}
            aria-pressed={isActive}
            data-predicate={predicate}
            className={[
              "px-3 py-2 text-sm font-medium transition-colors",
              "hover:text-foreground",
              isActive
                ? "border-b-2 border-foreground text-foreground -mb-px"
                : "text-muted-foreground border-b-2 border-transparent -mb-px",
            ].join(" ")}
          >
            {label}
          </button>
        );
      })}
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Rollup header — total weight and top-3 share
// ---------------------------------------------------------------------------

interface RollupHeaderProps {
  total: number;
  top3Share: number | null;
  predicate: string;
}

function RollupHeader({ total, top3Share, predicate }: RollupHeaderProps) {
  return (
    <div
      className="flex items-center gap-4 text-sm text-muted-foreground"
      data-testid="rollup-header"
    >
      <span>
        Predicate:{" "}
        <span className="font-mono text-foreground font-medium">{predicate}</span>
      </span>
      <span>
        Total weight:{" "}
        <span className="tabular-nums text-foreground font-medium">{total}</span>
      </span>
      {top3Share != null && (
        <span>
          Top-3 share:{" "}
          <span className="tabular-nums text-foreground font-medium">
            {(top3Share * 100).toFixed(1)}%
          </span>
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Concentration row
// ---------------------------------------------------------------------------

interface ConcentrationRowProps {
  entry: ConcentrationEntry;
  rank: number;
}

function ConcentrationRow({ entry, rank }: ConcentrationRowProps) {
  const sharePercent =
    entry.share != null ? `${(entry.share * 100).toFixed(1)}%` : "—";

  return (
    <li
      className="flex items-center gap-3 py-2 border-b last:border-0 hover:bg-muted/40 px-2 rounded-sm"
      data-testid="concentration-row"
      data-entity-id={entry.entity_id}
    >
      {/* Rank badge */}
      <span
        className="inline-flex items-center justify-center h-5 w-6 shrink-0 rounded text-xs font-mono tabular-nums bg-muted text-muted-foreground"
        aria-label={`Rank ${rank}`}
      >
        {rank}
      </span>

      {/* Name */}
      <span className="flex-1 text-sm font-medium truncate" title={entry.canonical_name}>
        {entry.canonical_name}
      </span>

      {/* Metrics */}
      <div className="flex items-center gap-3 text-xs text-muted-foreground shrink-0">
        <span
          className="tabular-nums"
          title={`Weight sum: ${entry.weight_sum}`}
          data-testid="weight-sum"
        >
          w={entry.weight_sum}
        </span>

        <span
          className="tabular-nums"
          title={`Fact count: ${entry.fact_count}`}
          data-testid="fact-count"
        >
          ×{entry.fact_count}
        </span>

        <Badge variant="outline" className="text-xs tabular-nums" data-testid="share-badge">
          {sharePercent}
        </Badge>

        {entry.last_seen != null && (
          <Time value={entry.last_seen} mode="relative" />
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Concentration list panel
// ---------------------------------------------------------------------------

interface ConcentrationListProps {
  predicate: string;
  onSelectPredicate: (predicate: string) => void;
}

function ConcentrationList({ predicate, onSelectPredicate }: ConcentrationListProps) {
  const { data, isLoading, error, refetch } = useEntityConcentration(predicate || undefined);

  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="concentration-loading">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-3/4" />
      </div>
    );
  }

  if (error != null) {
    return (
      <div data-testid="concentration-error">
        <EmptyState
          title="Could not load concentration data"
          description="Owner access is required, or no relational predicates are registered."
          action={
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          }
        />
      </div>
    );
  }

  if (data == null) {
    return null;
  }

  // Render predicate tab strip using tabs from the response
  const effectivePredicate = data.predicate;

  return (
    <div className="space-y-4" data-testid="concentration-panel">
      {/* Predicate tabs from registry */}
      <PredicateTabStrip
        tabs={data.predicate_tabs}
        activePredicate={effectivePredicate}
        onSelect={onSelectPredicate}
      />

      {/* Rollup header */}
      <RollupHeader
        total={data.rollup.total}
        top3Share={data.rollup.top3_share}
        predicate={effectivePredicate}
      />

      {/* Entity list */}
      {data.items.length === 0 ? (
        <EmptyState
          title="No entities yet."
          description={`No active triples found for predicate "${effectivePredicate}". They will appear here once the butler builds the knowledge graph.`}
        />
      ) : (
        <ul data-testid="concentration-list">
          {data.items.map((entry, idx) => (
            <ConcentrationRow key={String(entry.entity_id)} entry={entry} rank={idx + 1} />
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConcentrationPage
// ---------------------------------------------------------------------------

export default function ConcentrationPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // ?predicate= URL state — the active predicate ID.
  // Absent param → empty string → backend defaults to 'knows'.
  const predicateParam = searchParams.get("predicate") ?? "";

  const handleSelectPredicate = useCallback(
    (predicate: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (predicate) {
            next.set("predicate", predicate);
          } else {
            next.delete("predicate");
          }
          return next;
        },
        { replace: false },
      );
    },
    [setSearchParams],
  );

  return (
    <Page
      archetype="overview"
      title="Concentration"
      description="Balance-sheet of relationship weight by predicate — see which entities dominate each relationship type."
      breadcrumbs={[{ label: "Entities", href: "/entities" }, { label: "Concentration" }]}
    >
      {/* SubpageTabs — Concentration is active */}
      <SubpageTabs />

      {/* Main content */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Edge-weight ranking</CardTitle>
        </CardHeader>
        <CardContent>
          <ConcentrationList
            predicate={predicateParam}
            onSelectPredicate={handleSelectPredicate}
          />
        </CardContent>
      </Card>
    </Page>
  );
}
