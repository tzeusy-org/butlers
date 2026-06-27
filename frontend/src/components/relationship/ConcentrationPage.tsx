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

import { Fragment, useCallback } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import type { ConcentrationEntry, ConcentrationResponse, PredicateTab } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import {
  ProvenanceMarks,
  stalenessBandForTimestamp,
} from "@/components/ui/Provenance";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { SubpageTabs } from "@/components/relationship/SubpageTabs";
import { useEntityConcentration } from "@/hooks/use-entities";

/** Tail threshold: entities holding less than 1% of total weight (spec). */
const TAIL_SHARE_THRESHOLD = 0.01;

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
      {tabs.map(({ predicate, label, entity_count }) => {
        const isActive = predicate === activePredicate;
        return (
          <button
            key={predicate}
            type="button"
            onClick={() => onSelect(predicate)}
            aria-pressed={isActive}
            data-predicate={predicate}
            className={[
              "flex items-center gap-1.5 px-3 py-2 text-sm font-medium transition-colors",
              "hover:text-foreground",
              isActive
                ? "border-b-2 border-foreground text-foreground -mb-px"
                : "text-muted-foreground border-b-2 border-transparent -mb-px",
            ].join(" ")}
          >
            {label}
            <span
              className="inline-flex items-center rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-mono tabular-nums text-muted-foreground leading-none"
              data-testid="predicate-tab-count"
              aria-label={`${entity_count} entities`}
            >
              {entity_count}
            </span>
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
      className="flex flex-wrap items-end gap-x-6 gap-y-2"
      data-testid="rollup-header"
    >
      {/* Inline meta: predicate + total weight */}
      <div className="flex items-center gap-4 text-sm text-muted-foreground">
        <span>
          Predicate:{" "}
          <span className="font-mono text-foreground font-medium">{predicate}</span>
        </span>
        <span>
          Total weight:{" "}
          <span className="tabular-nums text-foreground font-medium">{total}</span>
        </span>
      </div>

      {/* Top-3 share — 22px tnum headline (spec). */}
      {top3Share != null && (
        <div className="flex flex-col gap-0.5" data-testid="top3-share-kpi">
          <span className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
            top-3 share
          </span>
          <span className="text-[22px] font-medium tabular-nums leading-none text-foreground">
            {(top3Share * 100).toFixed(1)}%
          </span>
        </div>
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
  /** Largest weight_sum across the active predicate's rows (bar denominator). */
  maxWeight: number;
  /** Navigate to this entity's detail page (the only row affordance). */
  onOpen: (entityId: string) => void;
}

function ConcentrationRow({ entry, rank, maxWeight, onOpen }: ConcentrationRowProps) {
  const sharePercent =
    entry.share != null ? `${(entry.share * 100).toFixed(1)}%` : "—";

  // Targets — where this entity's predicate points (e.g. the organizations for
  // a `works-at` row). Entity-kind targets are hyperlinks to the target entity;
  // literal targets render as plain text. Rendered as a subtitle line *outside*
  // the row's navigation button so the inner anchors are not nested in a button.
  const targets = entry.targets ?? [];

  // Weight bar: width = weight / max × 100%, 6px tall, NO animation (spec).
  // Guard a zero/absent max so the bar is empty rather than NaN%.
  const barPercent =
    maxWeight > 0 ? `${Math.round((entry.weight_sum / maxWeight) * 100)}%` : "0%";

  return (
    <li
      className="border-b last:border-0"
      data-testid="concentration-row"
      data-entity-id={entry.entity_id}
    >
      {/* Read-mode: the whole row is the click target (cursor: pointer), no
          hover treatment beyond the standard list-row tint. */}
      <button
        type="button"
        onClick={() => onOpen(entry.entity_id)}
        className="flex w-full items-center gap-3 py-2 px-2 rounded-sm text-left hover:bg-muted/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Open ${entry.canonical_name}`}
      >
        {/* Rank badge */}
        <span
          className="inline-flex items-center justify-center h-5 w-6 shrink-0 rounded text-xs font-mono tabular-nums bg-muted text-muted-foreground"
          aria-label={`Rank ${rank}`}
        >
          {rank}
        </span>

        {/* Name + proportional weight bar */}
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-medium truncate" title={entry.canonical_name}>
            {entry.canonical_name}
          </span>
          {/* Weight bar — proportional, quiet, no animation. */}
          <span
            className="mt-1 block w-full overflow-hidden rounded-sm"
            style={{ height: 6, backgroundColor: "var(--border)" }}
            data-testid="weight-bar"
            role="meter"
            aria-label={`Weight ${entry.weight_sum} of ${maxWeight}`}
            aria-valuenow={entry.weight_sum}
            aria-valuemin={0}
            aria-valuemax={maxWeight}
          >
            <span
              className="block h-full"
              style={{ width: barPercent, backgroundColor: "var(--mfg)" }}
            />
          </span>
        </span>

        {/* Metrics */}
        <span className="flex items-center gap-3 text-xs text-muted-foreground shrink-0">
          <span
            className="tabular-nums"
            title={`Weight sum: ${entry.weight_sum}`}
            data-testid="weight-sum"
          >
            {entry.weight_sum}
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

          {/* Provenance marks — src + verified (spec: "each row carries its
              `src` and `verified` marks"). Quiet attribution, not a score. */}
          <ProvenanceMarks
            src={entry.src}
            verified={entry.verified}
            data-testid="concentration-provenance"
          />

          {/* Staleness dim treatment on `last_seen`: a row whose most-recent
              observation is stale visibly recedes (spec). The band is derived
              from the timestamp with the same thresholds as the server. */}
          {entry.last_seen != null && (
            <span
              data-stale={
                stalenessBandForTimestamp(entry.last_seen) === "stale"
                  ? "true"
                  : undefined
              }
              className={
                stalenessBandForTimestamp(entry.last_seen) === "stale"
                  ? "opacity-40"
                  : undefined
              }
            >
              <Time value={entry.last_seen} mode="relative" />
            </span>
          )}
        </span>
      </button>

      {/* Targets line — "→ where the predicate points", with hyperlinks to
          entity-kind objects (e.g. the corporation for a works-at row). Sits
          below the row button so its anchors are not nested inside a button. */}
      {targets.length > 0 && (
        <div
          className="flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 pl-11 pr-2 pb-2 -mt-1 text-xs"
          data-testid="concentration-targets"
        >
          <span className="text-muted-foreground/60" aria-hidden>
            →
          </span>
          {targets.map((t, i) => (
            <Fragment key={`${t.object_kind}:${t.entity_id ?? t.name}:${i}`}>
              {i > 0 && (
                <span className="text-muted-foreground/40" aria-hidden>
                  ·
                </span>
              )}
              {t.entity_id != null ? (
                <Link
                  to={`/entities/${t.entity_id}`}
                  className="font-medium text-foreground underline [text-underline-offset:3px] decoration-muted-foreground/40 hover:decoration-foreground"
                  data-testid="concentration-target-link"
                  title={t.name}
                >
                  {t.name}
                </Link>
              ) : (
                <span
                  className="text-muted-foreground"
                  data-testid="concentration-target-literal"
                  title={t.name}
                >
                  {t.name}
                </span>
              )}
            </Fragment>
          ))}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Footer KPI strip — total touches | entity count | top entity | tail share
// ---------------------------------------------------------------------------

interface KpiStripProps {
  data: ConcentrationResponse;
}

/** One hairline-divided cell in the footer KPI strip. */
function KpiCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 px-4 first:pl-0">
      <span className="text-[10px] font-mono uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <span className="text-sm font-medium tabular-nums text-foreground truncate" title={value}>
        {value}
      </span>
    </div>
  );
}

function KpiStrip({ data }: KpiStripProps) {
  // total touches — the predicate's total edge weight.
  const totalTouches = data.rollup.total;
  // entity count — number of entities under the active predicate.
  const entityCount = data.total;
  // top entity — the highest-weight row (items are server-sorted descending).
  const topEntity = data.items[0]?.canonical_name ?? "—";
  // tail share — combined share held by entities below 1%.
  const tailShare = data.items.reduce(
    (sum, e) => (e.share != null && e.share < TAIL_SHARE_THRESHOLD ? sum + e.share : sum),
    0,
  );

  return (
    <div
      className="mt-4 flex items-stretch divide-x divide-border border-t border-border pt-3"
      data-testid="concentration-kpi-strip"
    >
      <KpiCell label="total touches" value={totalTouches.toLocaleString()} />
      <KpiCell label="entities" value={entityCount.toLocaleString()} />
      <KpiCell label="top entity" value={topEntity} />
      <KpiCell label="tail < 1%" value={`${(tailShare * 100).toFixed(1)}%`} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Concentration list panel
// ---------------------------------------------------------------------------

interface ConcentrationListProps {
  predicate: string;
  onSelectPredicate: (predicate: string) => void;
  /** Navigate to an entity's detail page (row click). */
  onOpenEntity: (entityId: string) => void;
}

function ConcentrationList({
  predicate,
  onSelectPredicate,
  onOpenEntity,
}: ConcentrationListProps) {
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

  // Bar denominator: the largest weight_sum across the active predicate's rows.
  const maxWeight = data.items.reduce((m, e) => Math.max(m, e.weight_sum), 0);

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
        <>
          <ul data-testid="concentration-list">
            {data.items.map((entry, idx) => (
              <ConcentrationRow
                key={String(entry.entity_id)}
                entry={entry}
                rank={idx + 1}
                maxWeight={maxWeight}
                onOpen={onOpenEntity}
              />
            ))}
          </ul>

          {/* Footer KPI strip */}
          <KpiStrip data={data} />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConcentrationPage
// ---------------------------------------------------------------------------

export default function ConcentrationPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  // ?predicate= URL state — the active predicate ID.
  // Absent param → empty string → backend defaults to 'knows'.
  const predicateParam = searchParams.get("predicate") ?? "";

  const handleOpenEntity = useCallback(
    (entityId: string) => {
      navigate(`/entities/${entityId}`);
    },
    [navigate],
  );

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
      description="Balance-sheet of relationship weight by predicate. See which entities dominate each relationship type."
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
            onOpenEntity={handleOpenEntity}
          />
        </CardContent>
      </Card>
    </Page>
  );
}
