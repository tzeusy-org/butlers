// ---------------------------------------------------------------------------
// ButlerMessengerConversationsTab — bu-iuol4.34
//
// Delivery health bespoke tab for the Messenger butler detail page.
//
// Messenger is delivery-infrastructure, not a user-facing chat butler.
// This tab surfaces delivery health (SLA, circuit state, dead-letters,
// queue depth) — NOT message threads. User-facing chat lives in the global
// /messages surface.
//
// Layout (4-col panel grid, 4 rows):
//   Row 1: 4 KPI cells — deliveries (24h), success rate %, dead-letter count,
//           avg latency placeholder
//   Row 2: Active channels (span 2) — per-channel circuit status + failure rate
//   Row 3: Recent failures (span 2) — top 5 dead-letter rows
//   Row 4: Delivery pipeline (span 4) — queue depth by channel + priority
//
// API endpoints (bu-iuol4.35):
//   GET /api/messenger/delivery-stats?window_hours=24
//   GET /api/messenger/circuit-status
//   GET /api/messenger/queue-depth
//   GET /api/messenger/dead-letters?limit=20
//
// Circuit-status `source` note:
//   When source === 'db_approximation' a note is shown to indicate this is
//   derived from DB outcomes, not the live in-memory CircuitBreaker state.
// ---------------------------------------------------------------------------

import type { ReactNode } from "react";

import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import { KpiCell } from "./atoms";
import {
  useMessengerCircuitStatus,
  useMessengerDeadLetters,
  useMessengerDeliveryStats,
  useMessengerQueueDepth,
} from "@/hooks/use-messenger";

import type {
  MessengerCircuitChannelEntry,
  MessengerDeadLetterEntry,
} from "@/api/index.ts";

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
// Computed helpers
// ---------------------------------------------------------------------------

/** Compute success rate percentage (0–100) from delivered + failed counts. */
function computeSuccessRate(delivered: number, failed: number): number | null {
  const total = delivered + failed;
  if (total === 0) return null;
  return Math.round((delivered / total) * 100);
}

// ---------------------------------------------------------------------------
// Row 1: KPI quartet
// ---------------------------------------------------------------------------

interface KpiQuartetProps {
  delivered: number;
  successRate: number | null;
  deadLetterCount: number;
  isLoading: boolean;
  isError: boolean;
}

function KpiQuartet({
  delivered,
  successRate,
  deadLetterCount,
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
          <ErrorLine>Could not load delivery stats.</ErrorLine>
        </CardContent>
      </Card>
    );
  }

  const successRateDisplay =
    successRate !== null ? `${successRate}%` : "—";
  const deadLetterTone = deadLetterCount > 0 ? "amber" : "fg";

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3" data-testid="kpi-quartet">
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Deliveries (24h)"
            value={String(delivered)}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Success rate"
            value={successRateDisplay}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Dead letters"
            value={String(deadLetterCount)}
            tone={deadLetterTone}
          />
        </CardContent>
      </Card>
      <Card data-testid="kpi-item">
        <CardContent className="pt-4">
          <KpiCell
            label="Avg latency"
            value="—"
            sub="not tracked"
          />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 2: Active channels panel (circuit status, span 2)
// ---------------------------------------------------------------------------

/** Map circuit state to a badge variant + class. */
function CircuitStateBadge({ state }: { state: string }) {
  if (state === "closed") {
    return (
      <Badge
        variant="outline"
        className="border-emerald-500 text-emerald-600 text-xs"
        data-testid="circuit-state-badge"
      >
        closed
      </Badge>
    );
  }
  if (state === "open") {
    return (
      <Badge variant="destructive" className="text-xs" data-testid="circuit-state-badge">
        open
      </Badge>
    );
  }
  if (state === "half_open") {
    return (
      <Badge
        variant="outline"
        className="border-amber-500 text-amber-600 text-xs"
        data-testid="circuit-state-badge"
      >
        half open
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-xs" data-testid="circuit-state-badge">
      {state}
    </Badge>
  );
}

interface ActiveChannelsPanelProps {
  channels: MessengerCircuitChannelEntry[];
  source: string;
  isLoading: boolean;
  isError: boolean;
}

function ActiveChannelsPanel({
  channels,
  source,
  isLoading,
  isError,
}: ActiveChannelsPanelProps) {
  if (isLoading && channels.length === 0) {
    return <LoadingRows count={3} />;
  }

  if (isError) {
    return <ErrorLine>Could not load circuit status.</ErrorLine>;
  }

  return (
    <div data-testid="channels-panel">
      {/* DB-approximation note */}
      {source === "db_approximation" && (
        <p
          className="mb-3 text-xs text-muted-foreground"
          data-testid="db-approximation-note"
        >
          Circuit state is DB-derived (not live in-memory state).
        </p>
      )}
      {channels.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
          No channel activity in the last 15 min.
        </p>
      ) : (
        <ul className="divide-y" data-testid="channel-list" aria-label="Active channels">
          {channels.map((ch) => (
            <li
              key={ch.name}
              className="flex items-center gap-3 py-2 text-sm"
              data-testid="channel-row"
            >
              <span className="font-mono text-xs font-medium min-w-[80px]">
                {ch.name}
              </span>
              <CircuitStateBadge state={ch.state} />
              {ch.failure_rate_15m != null && (
                <span className="text-xs text-muted-foreground tnum ml-auto">
                  {(ch.failure_rate_15m * 100).toFixed(1)}% fail/15m
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row 3: Recent failures panel (dead letters, span 2)
// ---------------------------------------------------------------------------

interface RecentFailuresPanelProps {
  letters: MessengerDeadLetterEntry[];
  isLoading: boolean;
  isError: boolean;
}

function RecentFailuresPanel({ letters, isLoading, isError }: RecentFailuresPanelProps) {
  if (isLoading && letters.length === 0) {
    return <LoadingRows count={4} />;
  }

  if (isError) {
    return <ErrorLine>Could not load dead letters.</ErrorLine>;
  }

  if (letters.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
        No dead letters.
      </p>
    );
  }

  return (
    <ul className="divide-y" data-testid="dead-letter-list" aria-label="Recent failures">
      {letters.slice(0, 5).map((letter) => (
        <li
          key={letter.id}
          className="py-2 space-y-0.5 text-sm"
          data-testid="dead-letter-row"
        >
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono text-xs shrink-0">
              {letter.channel}
            </Badge>
            <span className="font-mono text-xs text-muted-foreground tnum truncate">
              {letter.id.slice(0, 8)}
            </span>
            <span className="ml-auto text-xs text-muted-foreground tnum shrink-0">
              {letter.retry_count} attempt{letter.retry_count !== 1 ? "s" : ""}
            </span>
          </div>
          {letter.error_message && (
            <p className="text-xs text-destructive truncate">{letter.error_message}</p>
          )}
          {letter.attempted_at && (
            <p className="text-xs text-muted-foreground tnum">
              <Time value={letter.attempted_at} mode="relative" />
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Row 4: Delivery pipeline panel (queue depth, span 4)
// ---------------------------------------------------------------------------

interface DeliveryPipelinePanelProps {
  total: number;
  byChannel: Record<string, number>;
  byPriority: Record<string, number>;
  isLoading: boolean;
  isError: boolean;
}

function DeliveryPipelinePanel({
  total,
  byChannel,
  byPriority,
  isLoading,
  isError,
}: DeliveryPipelinePanelProps) {
  if (isLoading) {
    return (
      <div className="flex gap-6" data-testid="pipeline-panel">
        <div className="space-y-1" data-testid="loading-line">
          <Skeleton className="h-2.5 w-24 rounded" />
          <Skeleton className="h-8 w-12 rounded" />
        </div>
      </div>
    );
  }

  if (isError) {
    return <ErrorLine>Could not load queue depth.</ErrorLine>;
  }

  const channelEntries = Object.entries(byChannel);
  const priorityEntries = Object.entries(byPriority);

  return (
    <div
      className="grid grid-cols-1 sm:grid-cols-3 gap-6"
      data-testid="pipeline-panel"
    >
      {/* Total queue depth */}
      <div data-testid="pipeline-total">
        <KpiCell label="Queue depth" value={String(total)} />
      </div>

      {/* By channel */}
      <div>
        <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground mb-2">
          By channel
        </p>
        {channelEntries.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="empty-state-line">
            Queue empty.
          </p>
        ) : (
          <ul className="space-y-1" data-testid="queue-by-channel">
            {channelEntries.map(([ch, count]) => (
              <li
                key={ch}
                className="flex items-center justify-between text-xs"
                data-testid="queue-channel-row"
              >
                <span className="font-mono text-muted-foreground">{ch}</span>
                <span className="tnum font-medium">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* By priority */}
      <div>
        <p className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground mb-2">
          By priority
        </p>
        {priorityEntries.length === 0 ? (
          <p className="text-xs text-muted-foreground" data-testid="empty-state-line">
            No priority data.
          </p>
        ) : (
          <ul className="space-y-1" data-testid="queue-by-priority">
            {priorityEntries.map(([priority, count]) => (
              <li
                key={priority}
                className="flex items-center justify-between text-xs"
                data-testid="queue-priority-row"
              >
                <span className="font-mono text-muted-foreground">{priority}</span>
                <span className="tnum font-medium">{count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ButlerMessengerConversationsTab — entry point
// ---------------------------------------------------------------------------

export default function ButlerMessengerConversationsTab() {
  const {
    data: deliveryStats,
    isLoading: statsLoading,
    isError: statsError,
  } = useMessengerDeliveryStats({ window_hours: 24 });

  const {
    data: circuitStatus,
    isLoading: circuitLoading,
    isError: circuitError,
  } = useMessengerCircuitStatus();

  const {
    data: queueDepth,
    isLoading: queueLoading,
    isError: queueError,
  } = useMessengerQueueDepth();

  const {
    data: deadLetters,
    isLoading: deadLettersLoading,
    isError: deadLettersError,
  } = useMessengerDeadLetters({ limit: 20 });

  const delivered = deliveryStats?.delivered ?? 0;
  const failed = deliveryStats?.failed ?? 0;
  const deadLetterCount = deliveryStats?.dead_letter ?? 0;
  const successRate = computeSuccessRate(delivered, failed);

  const channels = circuitStatus?.channels ?? [];
  const circuitSource = circuitStatus?.source ?? "db_approximation";

  const letters = deadLetters?.letters ?? [];

  const queueTotal = queueDepth?.total ?? 0;
  const byChannel = queueDepth?.by_channel ?? {};
  const byPriority = queueDepth?.by_priority ?? {};

  const hasError = statsError || circuitError || queueError || deadLettersError;

  return (
    <div className="space-y-4 pt-4" data-testid="messenger-conversations-tab">
      {/* Error banner */}
      {hasError && (
        <p className="text-sm text-destructive" data-testid="messenger-load-error">
          Some data failed to load. Displayed values may be incomplete.
        </p>
      )}

      {/* Row 1: KPI quartet */}
      <KpiQuartet
        delivered={delivered}
        successRate={successRate}
        deadLetterCount={deadLetterCount}
        isLoading={statsLoading}
        isError={statsError}
      />

      {/* Rows 2+3: Channels + dead letters side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <Card className="lg:col-span-2" data-testid="active-channels-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Active channels</CardTitle>
          </CardHeader>
          <CardContent>
            <ActiveChannelsPanel
              channels={channels}
              source={circuitSource}
              isLoading={circuitLoading}
              isError={circuitError}
            />
          </CardContent>
        </Card>

        <Card className="lg:col-span-2" data-testid="recent-failures-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Recent failures</CardTitle>
          </CardHeader>
          <CardContent>
            <RecentFailuresPanel
              letters={letters}
              isLoading={deadLettersLoading}
              isError={deadLettersError}
            />
          </CardContent>
        </Card>
      </div>

      {/* Row 4: Delivery pipeline */}
      <Card data-testid="delivery-pipeline-card">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Delivery pipeline</CardTitle>
        </CardHeader>
        <CardContent>
          <DeliveryPipelinePanel
            total={queueTotal}
            byChannel={byChannel}
            byPriority={byPriority}
            isLoading={queueLoading}
            isError={queueError}
          />
        </CardContent>
      </Card>
    </div>
  );
}
