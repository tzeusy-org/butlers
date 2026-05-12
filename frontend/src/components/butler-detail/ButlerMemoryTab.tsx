// ---------------------------------------------------------------------------
// ButlerMemoryTab — bu-9l25l (epic bu-hdavr F.4)
//
// Memory bespoke tab for any butler detail page.
//
// Layout (panel-grid frame, 4 columns):
//   Row 1: 4 KPI panels — episodes / facts / entities / rules.
//          Each uses <Panel> atom with a 28px KpiCell value and "+N today"
//          sub-line derived from the *_24h fields.
//   Row 2: Full-width "recent writes" panel (span=4, scroll=true,
//          height="320px") — latest 10 memory episodes for this butler.
//          Each row: 80px relative <Time>, 90px mono butler label, flex content.
//
// Hooks:
//   useButlerMemoryStats(name)           — per-butler KPI counts + 24h deltas
//   useMemoryRecentWrites(butler, limit) — latest episodes for this butler
//
// Doctrine gates (must hold after restyle):
//   - No <Card> wrapping KPI cells (DA.5).
//   - No raw oklch/hex literals.
//   - No em-dashes in JSX text.
//   - No pid field.
//   - Token-only chrome.
// ---------------------------------------------------------------------------

import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { ErrorLine, KpiCell, Panel } from "./atoms";
import { useButlerMemoryStats } from "@/hooks/use-butler-analytics";
import { useMemoryRecentWrites } from "@/hooks/use-memory";
import type { Episode } from "@/api/types";

// ---------------------------------------------------------------------------
// Row 1: KPI quartet — 4 Panel atoms
// ---------------------------------------------------------------------------

interface KpiQuartetProps {
  totalEpisodes: number;
  totalFacts: number;
  totalEntities: number;
  totalRules: number;
  episodesToday: number;
  factsToday: number;
  entitiesToday: number;
  rulesToday: number;
  isLoading: boolean;
  isError: boolean;
}

/** Skeleton for a single KPI panel. */
function KpiPanelSkeleton({ testId }: { testId?: string }) {
  return (
    <Panel testId={testId}>
      <div className="space-y-1" data-testid="loading-line">
        <Skeleton className="h-2.5 w-24 rounded" />
        <Skeleton className="h-7 w-12 rounded" />
      </div>
    </Panel>
  );
}

function KpiQuartet({
  totalEpisodes,
  totalFacts,
  totalEntities,
  totalRules,
  episodesToday,
  factsToday,
  entitiesToday,
  rulesToday,
  isLoading,
  isError,
}: KpiQuartetProps) {
  if (isLoading) {
    return (
      <div
        className="col-span-1 lg:col-span-4 grid grid-cols-2 sm:grid-cols-4"
        data-testid="kpi-quartet"
      >
        <KpiPanelSkeleton testId="kpi-item" />
        <KpiPanelSkeleton testId="kpi-item" />
        <KpiPanelSkeleton testId="kpi-item" />
        <KpiPanelSkeleton testId="kpi-item" />
      </div>
    );
  }

  if (isError) {
    return (
      <Panel testId="kpi-quartet">
        <ErrorLine>Could not load memory stats.</ErrorLine>
      </Panel>
    );
  }

  return (
    <div
      className="col-span-1 lg:col-span-4 grid grid-cols-2 sm:grid-cols-4"
      data-testid="kpi-quartet"
    >
      <Panel title="episodes" testId="kpi-item">
        <KpiCell
          label="Episodes"
          value={String(totalEpisodes)}
          sub={`+${episodesToday} today`}
          big
        />
      </Panel>
      <Panel title="facts" testId="kpi-item">
        <KpiCell
          label="Facts"
          value={String(totalFacts)}
          sub={`+${factsToday} today`}
          big
        />
      </Panel>
      <Panel title="entities" testId="kpi-item">
        <KpiCell
          label="Entities"
          value={String(totalEntities)}
          sub={`+${entitiesToday} today`}
          big
        />
      </Panel>
      <Panel title="rules" testId="kpi-item">
        <KpiCell
          label="Rules"
          value={String(totalRules)}
          sub={`+${rulesToday} today`}
          big
        />
      </Panel>
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
      className="flex items-baseline gap-3 py-1.5 border-b border-border/40 last:border-b-0 min-w-0"
      data-testid="recent-write-row"
    >
      {/* Timestamp — 80px relative */}
      <span className="shrink-0 w-[80px] font-mono text-xs text-muted-foreground tnum">
        <Time value={episode.created_at} mode="relative" />
      </span>
      {/* Butler name — 90px mono */}
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

interface RecentWritesPanelBodyProps {
  episodes: Episode[];
  isLoading: boolean;
  isError: boolean;
}

function RecentWritesPanelBody({ episodes, isLoading, isError }: RecentWritesPanelBodyProps) {
  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="loading-rows">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex items-center gap-2" data-testid="loading-line">
            <Skeleton className="h-3 w-28 rounded" />
            <Skeleton className="h-3 flex-1 rounded" />
          </div>
        ))}
      </div>
    );
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
    data: memoryStats,
    isLoading: statsLoading,
    isError: statsError,
  } = useButlerMemoryStats(butlerName);

  const {
    data: recentWritesResponse,
    isLoading: recentWritesLoading,
    isError: recentWritesError,
  } = useMemoryRecentWrites(butlerName, 10);

  const totalEpisodes = memoryStats?.total_episodes ?? 0;
  const episodesToday = memoryStats?.episodes_24h ?? 0;
  const totalFacts = memoryStats?.total_facts ?? 0;
  const factsToday = memoryStats?.facts_24h ?? 0;
  const totalEntities = memoryStats?.total_entities ?? 0;
  const entitiesToday = memoryStats?.entities_24h ?? 0;
  const totalRules = memoryStats?.total_rules ?? 0;
  const rulesToday = memoryStats?.rules_24h ?? 0;

  const episodes = recentWritesResponse?.data ?? [];

  return (
    <div
      className="grid grid-cols-1 lg:grid-cols-4 border-t border-l border-border/60"
      data-testid="butler-memory-tab"
    >
      {/* Row 1: KPI quartet — span full width, inner 4-col grid */}
      <KpiQuartet
        totalEpisodes={totalEpisodes}
        totalFacts={totalFacts}
        totalEntities={totalEntities}
        totalRules={totalRules}
        episodesToday={episodesToday}
        factsToday={factsToday}
        entitiesToday={entitiesToday}
        rulesToday={rulesToday}
        isLoading={statsLoading}
        isError={statsError}
      />

      {/* Row 2: Recent writes — full width */}
      <Panel
        title="recent writes"
        span={4}
        scroll
        height="320px"
        testId="recent-writes-card"
      >
        <RecentWritesPanelBody
          episodes={episodes}
          isLoading={recentWritesLoading}
          isError={recentWritesError}
        />
      </Panel>
    </div>
  );
}
