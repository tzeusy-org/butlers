// ---------------------------------------------------------------------------
// ButlerLifestyleTasteTab — bu-2jtfw.10
//
// Taste tab for the Lifestyle butler detail page, backed by the taste
// ledger (works/taste_signals/verdicts) instead of raw prose facts.
//
// Layout (4-col panel grid, 2 rows):
//   Row 1: KPI strip (span 4)
//     — total works, total verdicts, signals in the last 7 days
//   Row 2: Taste verdicts (span 2) + Recently added works (span 2)
//     — owner-asserted opinions (includes migrated legacy facts) |
//       latest works observed by the connector/resolver
//
// Previous version (bu-iuol4.33) queried subject="user" facts directly,
// which missed 59 of 61 taste rows (most live under spotify:* subjects) and
// rendered a decorative "weekly digest archive" stub with no backing data
// (bu-4q6hg). Both are retired here in favour of the ledger read surface.
// ---------------------------------------------------------------------------

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell, ErrorLine } from "./atoms";
import {
  useLifestyleTasteSummary,
  useLifestyleTasteVerdicts,
  useLifestyleTasteWorks,
} from "@/hooks/use-memory";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

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
  totalWorks: number;
  totalVerdicts: number;
  recentSignals7d: number;
  ledgerAvailable: boolean;
  isLoading: boolean;
  isError: boolean;
}

function LifestyleKpiStrip({
  totalWorks,
  totalVerdicts,
  recentSignals7d,
  ledgerAvailable,
  isLoading,
  isError,
}: LifestyleKpiStripProps) {
  const kpiSkeleton = (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 px-4 py-3">
      {Array.from({ length: 3 }, (_, i) => (
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
        {!ledgerAvailable && (
          <p className="text-xs text-muted-foreground mb-2" data-testid="ledger-degraded-note">
            Ledger totals are temporarily unavailable.
          </p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
          <div data-testid="kpi-item">
            <KpiCell label="Works tracked" value={String(totalWorks)} />
          </div>
          <div data-testid="kpi-item">
            <KpiCell label="Taste verdicts" value={String(totalVerdicts)} />
          </div>
          <div data-testid="kpi-item">
            <KpiCell label="Signals (7d)" value={String(recentSignals7d)} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Panel 2: Taste verdicts — owner-asserted opinions
// ---------------------------------------------------------------------------

interface TasteVerdictsPanelProps {
  verdicts: { id: string; verdict_text: string }[];
  isLoading: boolean;
  isError: boolean;
}

function TasteVerdictsPanel({ verdicts, isLoading, isError }: TasteVerdictsPanelProps) {
  if (isLoading) {
    return <LoadingRows count={3} />;
  }

  if (isError) {
    return <ErrorLine>Could not load taste verdicts.</ErrorLine>;
  }

  if (verdicts.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No taste verdicts recorded yet.
      </p>
    );
  }

  return (
    <div className="flex flex-wrap gap-2" data-testid="taste-chips">
      {verdicts.map((verdict) => (
        <Badge key={verdict.id} variant="secondary" className="text-xs" data-testid="taste-chip">
          {verdict.verdict_text}
        </Badge>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel 3: Recently added works
// ---------------------------------------------------------------------------

interface RecentWorksPanelProps {
  works: { id: string; kind: string; title: string | null; created_at: string }[];
  isLoading: boolean;
  isError: boolean;
}

function RecentWorksPanel({ works, isLoading, isError }: RecentWorksPanelProps) {
  if (isLoading) {
    return <LoadingRows count={5} />;
  }

  if (isError) {
    return <ErrorLine>Could not load recent works.</ErrorLine>;
  }

  if (works.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No works recorded yet.
      </p>
    );
  }

  return (
    <ul className="space-y-2" data-testid="recent-works-list">
      {works.map((work) => (
        <li
          key={work.id}
          className="flex items-start gap-3 text-sm"
          data-testid="recent-work-item"
        >
          <span className="shrink-0 text-xs text-muted-foreground tnum whitespace-nowrap">
            <Time value={work.created_at} mode="relative" />
          </span>
          <div className="min-w-0">
            <span className="font-mono text-xs text-muted-foreground capitalize">
              {work.kind}
            </span>
            <p className="text-sm text-foreground leading-snug truncate">
              {work.title ?? "Untitled"}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// ButlerLifestyleTasteTab — entry point
// ---------------------------------------------------------------------------

export default function ButlerLifestyleTasteTab() {
  const {
    data: summary,
    isLoading: summaryLoading,
    isError: summaryError,
  } = useLifestyleTasteSummary();

  const {
    data: verdictsResponse,
    isLoading: verdictsLoading,
    isError: verdictsError,
  } = useLifestyleTasteVerdicts({ limit: 20 });

  const {
    data: worksResponse,
    isLoading: worksLoading,
    isError: worksError,
  } = useLifestyleTasteWorks({ limit: 10 });

  const verdicts = verdictsResponse?.data ?? [];
  const works = worksResponse?.data ?? [];

  const hasError = summaryError || verdictsError || worksError;

  return (
    <div className="space-y-4 pt-4" data-testid="lifestyle-taste-tab">
      {/* Error banner */}
      {hasError && (
        <p className="text-sm text-destructive" data-testid="taste-load-error">
          Some lifestyle taste data failed to load. Unavailable panels will retry automatically.
        </p>
      )}

      {/* Row 1: KPI strip */}
      <LifestyleKpiStrip
        totalWorks={summary?.total_works ?? 0}
        totalVerdicts={verdictsResponse?.meta.total ?? summary?.total_verdicts ?? 0}
        recentSignals7d={summary?.recent_signals_7d ?? 0}
        ledgerAvailable={summary?.ledger_available ?? true}
        isLoading={summaryLoading}
        isError={summaryError}
      />

      {/* Row 2: Taste verdicts + recently added works */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card className="lg:col-span-2" data-testid="taste-summary-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Taste verdicts</CardTitle>
          </CardHeader>
          <CardContent>
            <TasteVerdictsPanel
              verdicts={verdicts}
              isLoading={verdictsLoading}
              isError={verdictsError}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="recent-works-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Recently added</CardTitle>
          </CardHeader>
          <CardContent>
            <RecentWorksPanel works={works} isLoading={worksLoading} isError={worksError} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
