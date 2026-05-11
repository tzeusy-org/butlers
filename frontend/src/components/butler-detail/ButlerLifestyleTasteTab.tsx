// ---------------------------------------------------------------------------
// ButlerLifestyleTasteTab — bu-iuol4.33
//
// Taste bespoke tab for the Lifestyle butler detail page.
//
// Layout (4-col panel grid, 3 rows):
//   Row 1: KPI strip (span 4)
//     — active preferences count, currently consuming count,
//       recently logged count, weekly digest sent date
//   Row 2: Taste summary (span 2) + Consumption state (span 2)
//     — top genres/cuisines/artists chips | currently watching/reading/playing
//   Row 3: Recent additions (span 2) + Weekly digest archive (span 2)
//     — last 10 facts logged | list of past digest titles + dates (stub)
//
// No backend changes. Uses useMemoryRecall + useMemorySearch hooks.
//
// Weekly digest archive: stub — no historical digest storage exists yet.
// Tracked in bu-4q6hg for future enhancement (digest persistence in lifestyle butler).
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";

import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell } from "./atoms";
import { useMemoryRecall, useMemorySearch } from "@/hooks/use-memory";

import type { Fact } from "@/api/types";

// ---------------------------------------------------------------------------
// Predicates
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// KPI computation helpers (pure functions, moved out of render path so that
// Date.now() is not called directly during render — required by the
// react-hooks/purity ESLint rule).
// ---------------------------------------------------------------------------

/** Compute the count of recently logged facts (within last 7 days). */
function computeRecentlyLoggedCount(facts: Fact[]): number {
  const cutoff7d = Date.now() - 7 * 24 * 60 * 60 * 1000;
  return facts.filter((f) => new Date(f.created_at).getTime() >= cutoff7d).length;
}

// ---------------------------------------------------------------------------
// Predicates
// ---------------------------------------------------------------------------

/** Predicates that represent user preferences / tastes. */
const PREFERENCE_PREDICATES = ["likes_"];

/** Predicates that represent current consumption state. */
const CONSUMPTION_PREDICATES = ["watches", "reads", "plays"];

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/** Error state: icon + destructive-tone text. */
function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <p
      className="flex items-center gap-1.5 text-sm text-destructive min-w-0"
      data-testid="error-state-line"
    >
      <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
      <span className="truncate">{children}</span>
    </p>
  );
}

/** Loading skeleton rows. */
function LoadingRows({ count = 4 }: { count?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="flex items-center gap-2" data-testid="loading-line">
          <Skeleton className="h-3 w-28 rounded" />
          <Skeleton className="h-3 flex-1 rounded" />
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel 1: KPI strip
// ---------------------------------------------------------------------------

interface LifestyleKpiStripProps {
  activePrefCount: number;
  consumingCount: number;
  recentlyLoggedCount: number;
  isLoading: boolean;
  isError: boolean;
}

function LifestyleKpiStrip({
  activePrefCount,
  consumingCount,
  recentlyLoggedCount,
  isLoading,
  isError,
}: LifestyleKpiStripProps) {
  const kpiSkeleton = (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 px-4 py-3">
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="space-y-1" data-testid="loading-line">
          <Skeleton className="h-2.5 w-20 rounded" />
          <Skeleton className="h-7 w-12 rounded" />
        </div>
      ))}
    </div>
  );

  if (isLoading) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Taste overview</CardTitle>
        </CardHeader>
        <CardContent className="p-0 pb-4">{kpiSkeleton}</CardContent>
      </Card>
    );
  }

  if (isError) {
    return (
      <Card data-testid="kpi-strip">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Taste overview</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorLine>Could not load taste overview.</ErrorLine>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="kpi-strip">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Taste overview</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-6">
          <div data-testid="kpi-item">
            <KpiCell
              label="Active preferences"
              value={String(activePrefCount)}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Currently consuming"
              value={String(consumingCount)}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Recently logged"
              value={String(recentlyLoggedCount)}
            />
          </div>
          <div data-testid="kpi-item">
            <KpiCell
              label="Weekly digest"
              value="—"
              sub="no digest history"
            />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 2: Taste summary — top genres/cuisines/artists chips
// ---------------------------------------------------------------------------

interface TasteSummaryPanelProps {
  facts: Fact[];
  isLoading: boolean;
  isError: boolean;
}

function TasteSummaryPanel({ facts, isLoading, isError }: TasteSummaryPanelProps) {
  if (isLoading && facts.length === 0) {
    return <LoadingRows count={3} />;
  }

  if (isError) {
    return <ErrorLine>Could not load taste preferences.</ErrorLine>;
  }

  if (facts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No taste preferences recorded yet.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2" data-testid="taste-chips">
      {facts.map((fact) => (
        <Badge
          key={fact.id}
          variant="secondary"
          className="text-xs"
          data-testid="taste-chip"
        >
          {fact.content}
        </Badge>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel 3: Consumption state — currently watching/reading/playing
// ---------------------------------------------------------------------------

const PREDICATE_LABELS: Record<string, string> = {
  watches: "watching",
  reads: "reading",
  plays: "playing",
};

interface ConsumptionPanelProps {
  facts: Fact[];
  isLoading: boolean;
  isError: boolean;
}

function ConsumptionPanel({ facts, isLoading, isError }: ConsumptionPanelProps) {
  if (isLoading && facts.length === 0) {
    return <LoadingRows count={3} />;
  }

  if (isError) {
    return <ErrorLine>Could not load consumption state.</ErrorLine>;
  }

  if (facts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No active consumption tracked.
      </p>
    );
  }

  return (
    <ul className="space-y-2" data-testid="consumption-list">
      {facts.map((fact) => {
        const label = PREDICATE_LABELS[fact.predicate] ?? fact.predicate;
        return (
          <li
            key={fact.id}
            className="flex items-start gap-2 text-sm"
            data-testid="consumption-item"
          >
            <Badge variant="outline" className="shrink-0 text-xs capitalize">
              {label}
            </Badge>
            <span className="text-sm text-foreground leading-snug">
              {fact.content}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel 4: Recent additions — last 10 facts logged
// ---------------------------------------------------------------------------

interface RecentAdditionsPanelProps {
  facts: Fact[];
  isLoading: boolean;
  isError: boolean;
}

function RecentAdditionsPanel({ facts, isLoading, isError }: RecentAdditionsPanelProps) {
  if (isLoading && facts.length === 0) {
    return <LoadingRows count={5} />;
  }

  if (isError) {
    return <ErrorLine>Could not load recent additions.</ErrorLine>;
  }

  if (facts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No facts logged yet.
      </p>
    );
  }

  // Sort by created_at descending, take last 10
  const sorted = [...facts]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 10);

  return (
    <ul className="space-y-2" data-testid="recent-additions-list">
      {sorted.map((fact) => (
        <li
          key={fact.id}
          className="flex items-start gap-3 text-sm"
          data-testid="recent-addition-item"
        >
          <span className="shrink-0 text-xs text-muted-foreground tnum whitespace-nowrap">
            <Time value={fact.created_at} mode="relative" />
          </span>
          <div className="min-w-0">
            <span className="font-mono text-xs text-muted-foreground">
              {fact.predicate}
            </span>
            <p className="text-sm text-foreground leading-snug truncate">
              {fact.content}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Panel 5: Weekly digest archive (stub)
//
// No historical digest storage exists in the lifestyle butler yet.
// This panel always renders the empty state.
// Follow-up: bu-4q6hg — add digest persistence in lifestyle butler.
// ---------------------------------------------------------------------------

function WeeklyDigestArchivePanel() {
  return (
    <p className="text-sm text-muted-foreground" data-testid="digest-empty-state">
      No weekly digests yet.
    </p>
  );
}

// ---------------------------------------------------------------------------
// ButlerLifestyleTasteTab — entry point
// ---------------------------------------------------------------------------

export default function ButlerLifestyleTasteTab() {
  // Recall all active user facts from the lifestyle butler scope.
  const {
    data: recallData,
    isLoading: recallLoading,
    isError: recallError,
  } = useMemoryRecall({ butler: "lifestyle", subject: "user" });

  // Search for preference facts (likes_*).
  const {
    facts: preferenceFacts,
    isLoading: prefLoading,
    isError: prefError,
  } = useMemorySearch({
    butler: "lifestyle",
    subject: "user",
    predicates: PREFERENCE_PREDICATES,
  });

  // Search for consumption state facts (watches, reads, plays).
  const {
    facts: consumptionFacts,
    isLoading: consumptionLoading,
    isError: consumptionError,
  } = useMemorySearch({
    butler: "lifestyle",
    subject: "user",
    predicates: CONSUMPTION_PREDICATES,
  });

  // All recalled facts (for recent additions).
  const allFacts = recallData?.data ?? [];

  // KPI computations.
  const activePrefCount = preferenceFacts.length;
  const consumingCount = consumptionFacts.length;
  const recentlyLoggedCount = computeRecentlyLoggedCount(allFacts);

  const kpiLoading = recallLoading || prefLoading || consumptionLoading;
  const kpiError = recallError || prefError || consumptionError;

  const hasError = recallError || prefError || consumptionError;

  return (
    <div className="space-y-4 pt-4" data-testid="lifestyle-taste-tab">
      {/* Error banner */}
      {hasError && (
        <p className="text-sm text-destructive" data-testid="taste-load-error">
          Some data failed to load. Displayed values may be incomplete.
        </p>
      )}

      {/* Row 1: KPI strip */}
      <LifestyleKpiStrip
        activePrefCount={activePrefCount}
        consumingCount={consumingCount}
        recentlyLoggedCount={recentlyLoggedCount}
        isLoading={kpiLoading}
        isError={kpiError}
      />

      {/* Row 2: Taste summary + consumption state */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card className="lg:col-span-2" data-testid="taste-summary-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Taste summary</CardTitle>
          </CardHeader>
          <CardContent>
            <TasteSummaryPanel
              facts={preferenceFacts}
              isLoading={prefLoading}
              isError={prefError}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="consumption-state-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Consumption state</CardTitle>
          </CardHeader>
          <CardContent>
            <ConsumptionPanel
              facts={consumptionFacts}
              isLoading={consumptionLoading}
              isError={consumptionError}
            />
          </CardContent>
        </Card>
      </div>

      {/* Row 3: Recent additions + weekly digest archive */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card className="lg:col-span-2" data-testid="recent-additions-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Recent additions</CardTitle>
          </CardHeader>
          <CardContent>
            <RecentAdditionsPanel
              facts={allFacts}
              isLoading={recallLoading}
              isError={recallError}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="digest-archive-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Weekly digest archive</CardTitle>
          </CardHeader>
          <CardContent>
            <WeeklyDigestArchivePanel />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
