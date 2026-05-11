// ---------------------------------------------------------------------------
// ButlerMemoryTab — bu-iuol4.20
//
// Memory bespoke tab for any butler detail page.
//
// Layout (4-col panel grid, 2 rows):
//   Row 1: 4 KPI cells — episodes / facts / entities / rules.
//          Each shows the global count with a "+N today" sub-line where
//          derivable from the stats response.
//   Row 2: Full-width "recent writes" panel — latest N memory episodes.
//          Each row: 50px mono timestamp (ms precision), 90px kind label,
//          flex content text.
//
// Hooks:
//   useMemoryStats()                     — global memory tier counts
//   useEntities({ limit: 1 })            — entity total via meta.total
//   useMemoryRecentWrites(butler, limit)  — latest episodes for this butler
//
// MemoryTierCards / MemoryBrowser are kept intact as separate-route components
// used by the /memory domain page. They are NOT rendered here and are not
// deprecated — they serve a different purpose (deep-browse) vs this tab's
// summary+recent-writes view.
//
// ?butler= filter status: supported by the /memory/episodes API client
// (EpisodeParams.butler is serialised by episodeSearchParams). If the backend
// does not honour the filter, the panel degrades gracefully — it still renders
// all episodes, but without butler isolation.
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";

import { AlertTriangle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell } from "./atoms";
import { useMemoryStats, useMemoryRecentWrites } from "@/hooks/use-memory";
import { useEntities } from "@/hooks/use-memory";
import type { Episode } from "@/api/types";

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
    <div className="space-y-2" data-testid="loading-rows">
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
// Row 1: KPI quartet
// ---------------------------------------------------------------------------

interface KpiQuartetProps {
  totalEpisodes: number;
  totalFacts: number;
  totalEntities: number;
  totalRules: number;
  episodesToday: number | null;
  factsToday: number | null;
  rulesActive: number | null;
  isLoading: boolean;
  isError: boolean;
}

function KpiQuartet({
  totalEpisodes,
  totalFacts,
  totalEntities,
  totalRules,
  episodesToday,
  factsToday,
  rulesActive,
  isLoading,
  isError,
}: KpiQuartetProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="kpi-quartet">
        {Array.from({ length: 4 }, (_, i) => (
          <Card key={i}>
            <CardContent className="pt-4">
              <div className="space-y-1" data-testid="loading-line">
                <Skeleton className="h-2.5 w-24 rounded" />
                <Skeleton className="h-7 w-12 rounded" />
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <Card data-testid="kpi-quartet">
        <CardContent className="pt-4">
          <ErrorLine>Could not load memory stats.</ErrorLine>
        </CardContent>
      </Card>
    );
  }

  const episodesTodaySub = episodesToday !== null ? `+${episodesToday} today` : undefined;
  const factsTodaySub = factsToday !== null ? `+${factsToday} active` : undefined;
  const rulesSub = rulesActive !== null ? `+${rulesActive} active` : undefined;

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="kpi-quartet">
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Episodes"
            value={String(totalEpisodes)}
            sub={episodesTodaySub}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Facts"
            value={String(totalFacts)}
            sub={factsTodaySub}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Entities"
            value={String(totalEntities)}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Rules"
            value={String(totalRules)}
            sub={rulesSub}
          />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 2: Recent writes panel
// ---------------------------------------------------------------------------

interface RecentWriteRowProps {
  episode: Episode;
}

function RecentWriteRow({ episode }: RecentWriteRowProps) {
  return (
    <div
      className="flex items-baseline gap-3 py-1.5 border-b border-border/40 last:border-b-0"
      data-testid="recent-write-row"
    >
      {/* Timestamp — 50px mono, ms precision */}
      <span className="shrink-0 w-[50px] font-mono text-xs text-muted-foreground tnum">
        <Time value={episode.created_at} mode="absolute" precision="ms" />
      </span>
      {/* Kind label — 90px mono */}
      <span
        className="shrink-0 w-[90px] font-mono text-xs text-muted-foreground truncate"
        title={episode.butler}
      >
        {episode.butler}
      </span>
      {/* Content — flex */}
      <span className="flex-1 text-xs text-foreground min-w-0 line-clamp-1">
        {episode.content}
      </span>
    </div>
  );
}

interface RecentWritesPanelProps {
  episodes: Episode[];
  isLoading: boolean;
  isError: boolean;
}

function RecentWritesPanel({ episodes, isLoading, isError }: RecentWritesPanelProps) {
  if (isLoading) {
    return <LoadingRows count={6} />;
  }

  if (isError) {
    return <ErrorLine>Could not load recent writes.</ErrorLine>;
  }

  if (episodes.length === 0) {
    return (
      <p
        className="text-sm text-muted-foreground italic font-[family-name:var(--font-serif,serif)]"
        data-testid="empty-state-line"
      >
        No memory writes recorded yet.
      </p>
    );
  }

  return (
    <div data-testid="recent-writes-list">
      {episodes.map((ep) => (
        <RecentWriteRow key={ep.id} episode={ep} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerMemoryTab — entry point
// ---------------------------------------------------------------------------

interface ButlerMemoryTabProps {
  butlerName: string;
}

export default function ButlerMemoryTab({ butlerName }: ButlerMemoryTabProps) {
  const {
    data: statsResponse,
    isLoading: statsLoading,
    isError: statsError,
  } = useMemoryStats();

  const {
    data: entitiesResponse,
    isLoading: entitiesLoading,
    isError: entitiesError,
  } = useEntities({ limit: 1 });

  const {
    data: recentWritesResponse,
    isLoading: recentWritesLoading,
    isError: recentWritesError,
  } = useMemoryRecentWrites(butlerName, 10);

  const stats = statsResponse?.data;
  const totalEpisodes = stats?.total_episodes ?? 0;
  const totalFacts = stats?.total_facts ?? 0;
  const totalRules = stats?.total_rules ?? 0;
  const activeFacts = stats?.active_facts ?? null;
  const activeRules = stats
    ? stats.established_rules + stats.proven_rules
    : null;
  const totalEntities = entitiesResponse?.meta.total ?? 0;

  const kpiLoading = statsLoading || entitiesLoading;
  const kpiError = statsError || entitiesError;

  const episodes = recentWritesResponse?.data ?? [];

  return (
    <div className="space-y-4 pt-4" data-testid="butler-memory-tab">
      {/* Row 1: KPI quartet */}
      <KpiQuartet
        totalEpisodes={totalEpisodes}
        totalFacts={totalFacts}
        totalEntities={totalEntities}
        totalRules={totalRules}
        episodesToday={null}
        factsToday={activeFacts}
        rulesActive={activeRules}
        isLoading={kpiLoading}
        isError={kpiError}
      />

      {/* Row 2: Recent writes — full width */}
      <Card data-testid="recent-writes-card">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent writes</CardTitle>
        </CardHeader>
        <CardContent>
          <RecentWritesPanel
            episodes={episodes}
            isLoading={recentWritesLoading}
            isError={recentWritesError}
          />
        </CardContent>
      </Card>
    </div>
  );
}
