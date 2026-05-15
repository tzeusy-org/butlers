import type {
  ApprovalMetrics,
  ButlerHeartbeat,
  ButlerSummary,
  CostSummary,
  HeartbeatFacts,
  Issue,
  NotificationStats,
  QaSummary,
  TimelineEvent,
} from "@/api/types";

export type OverviewSeverity = "critical" | "high" | "medium" | "low" | "info";

export type OverviewAttentionKind =
  | "issue"
  | "runtime"
  | "approval"
  | "notification"
  | "qa"
  | "old-issues-summary";

export interface OverviewDerivationOptions {
  now?: Date;
  recentIssueHours?: number;
  staleHeartbeatSeconds?: number;
  includeOldIssueRows?: boolean;
  maxRecentIssueRows?: number;
  maxTimelineRows?: number;
}

export interface OverviewDerivationInput {
  butlers?: ButlerSummary[];
  costs?: CostSummary | null;
  issues?: Issue[];
  heartbeats?: HeartbeatFacts | null;
  approvalMetrics?: ApprovalMetrics | null;
  notificationStats?: NotificationStats | null;
  notificationStatsError?: boolean;
  qaSummary?: QaSummary | null;
  qaSummaryError?: boolean;
  timeline?: TimelineEvent[];
  timelineError?: boolean;
}

export interface OverviewRuntimeKpis {
  totalButlers: number;
  healthyButlers: number;
  sessions24h: number;
  pendingApprovals: number;
}

export type OverviewRuntimeState =
  | "healthy"
  | "active"
  | "stale"
  | "degraded"
  | "offline"
  | "unknown";

export interface OverviewButlerIndexRow {
  name: string;
  status: string;
  sessions24h: number;
  costUsd: number;
  lastSessionAt: string | null;
  activeSessionCount: number;
  heartbeatAgeSeconds: number | null;
  runtimeState: OverviewRuntimeState;
  needsAttention: boolean;
}

export interface OverviewAttentionRow {
  id: string;
  kind: OverviewAttentionKind;
  severity: OverviewSeverity;
  title: string;
  detail: string;
  href: string | null;
  count?: number;
  lastSeenAt?: string | null;
  butlers?: string[];
}

export interface OverviewNowRow {
  id: string;
  kind: "approval" | "qa" | "notification" | "activity" | "error";
  label: string;
  detail: string;
  href: string | null;
  count?: number;
}

export interface OverviewTriageModel {
  kpis: OverviewRuntimeKpis;
  attentionRows: OverviewAttentionRow[];
  operationsRows: OverviewButlerIndexRow[];
  nowRows: OverviewNowRow[];
  hiddenOldIssueGroups: number;
}

const DEFAULT_RECENT_ISSUE_HOURS = 24;
const DEFAULT_STALE_HEARTBEAT_SECONDS = 5 * 60;
const DEFAULT_MAX_RECENT_ISSUE_ROWS = 5;
const DEFAULT_MAX_TIMELINE_ROWS = 2;

const HEALTHY_STATUSES = new Set(["ok", "online", "healthy"]);
const DEGRADED_STATUSES = new Set(["degraded", "waiting", "paused", "error", "failed"]);
const OFFLINE_STATUSES = new Set(["offline", "down", "unavailable"]);

export function deriveOverviewTriageModel(
  input: OverviewDerivationInput,
  options: OverviewDerivationOptions = {},
): OverviewTriageModel {
  const now = options.now ?? new Date();
  const recentIssueHours = options.recentIssueHours ?? DEFAULT_RECENT_ISSUE_HOURS;
  const staleHeartbeatSeconds =
    options.staleHeartbeatSeconds ?? DEFAULT_STALE_HEARTBEAT_SECONDS;
  const maxRecentIssueRows = options.maxRecentIssueRows ?? DEFAULT_MAX_RECENT_ISSUE_ROWS;
  const maxTimelineRows = options.maxTimelineRows ?? DEFAULT_MAX_TIMELINE_ROWS;

  const butlers = (input.butlers ?? []).filter((butler) => butler.type === "butler");
  const heartbeatByName = new Map<string, ButlerHeartbeat>(
    (input.heartbeats?.butlers ?? []).map((heartbeat) => [heartbeat.name, heartbeat]),
  );

  const operationsRows = butlers.map((butler) =>
    deriveButlerIndexRow(
      butler,
      heartbeatByName.get(butler.name) ?? null,
      input.costs?.by_butler?.[butler.name] ?? 0,
      staleHeartbeatSeconds,
      input.heartbeats != null,
    ),
  );

  const issueBuckets = bucketIssues(input.issues ?? [], now, recentIssueHours);
  const runtimeRows = operationsRows
    .filter((row) => row.needsAttention)
    .map(runtimeAttentionRow);
  const approvalRows = approvalAttentionRows(input.approvalMetrics);
  const notificationRows = notificationAttentionRows(input.notificationStats);
  const qaRows = qaAttentionRows(input.qaSummary);
  const currentHighIssues = issueBuckets.currentHigh.slice(0, maxRecentIssueRows);
  const remainingIssueSlots = Math.max(maxRecentIssueRows - currentHighIssues.length, 0);
  const recentIssues = issueBuckets.recent.slice(0, remainingIssueSlots);
  const hiddenCurrentIssueGroups =
    Math.max(issueBuckets.currentHigh.length - currentHighIssues.length, 0) +
    Math.max(issueBuckets.recent.length - recentIssues.length, 0);

  const currentHighIssueRows = currentHighIssues.map((issue) => issueAttentionRow(issue, now));
  const recentIssueRows = recentIssues.map((issue) => issueAttentionRow(issue, now));
  const hiddenOldIssueGroups = options.includeOldIssueRows ? 0 : issueBuckets.old.length;
  const hiddenIssueGroups = hiddenOldIssueGroups + hiddenCurrentIssueGroups;

  const attentionRows = [
    ...currentHighIssueRows,
    ...runtimeRows,
    ...approvalRows,
    ...notificationRows,
    ...qaRows,
    ...recentIssueRows,
  ];

  if (hiddenIssueGroups > 0) {
    const onlyOldGroups = hiddenCurrentIssueGroups === 0;
    attentionRows.push({
      id: "issues-old-summary",
      kind: "old-issues-summary",
      severity: "info",
      title: `${hiddenIssueGroups} ${onlyOldGroups ? "older" : "more"} issue group${
        hiddenIssueGroups === 1 ? "" : "s"
      }`,
      detail: onlyOldGroups
        ? "Older groups stay on the issues page unless they become current again."
        : "The full issue list stays on the issues page.",
      href: "/issues",
      count: hiddenIssueGroups,
    });
  }

  if (options.includeOldIssueRows) {
    attentionRows.push(
      ...issueBuckets.old.map((issue) => issueAttentionRow(issue, now)),
    );
  }

  const nowRows = deriveNowRows(input, maxTimelineRows);

  return {
    kpis: {
      totalButlers: butlers.length,
      healthyButlers: butlers.filter((butler) => isHealthyStatus(butler.status)).length,
      sessions24h: butlers.reduce((sum, butler) => sum + (butler.sessions_24h ?? 0), 0),
      pendingApprovals: input.approvalMetrics?.total_pending ?? 0,
    },
    attentionRows,
    operationsRows,
    nowRows,
    hiddenOldIssueGroups,
  };
}

function deriveButlerIndexRow(
  butler: ButlerSummary,
  heartbeat: ButlerHeartbeat | null,
  costUsd: number,
  staleHeartbeatSeconds: number,
  heartbeatSourceLoaded: boolean,
): OverviewButlerIndexRow {
  const heartbeatAgeSeconds = heartbeat?.heartbeat_age_seconds ?? null;
  const status = butler.status.toLowerCase();
  const isMissingHeartbeat = heartbeatSourceLoaded && heartbeat == null;
  const isStaleHeartbeat =
    heartbeatAgeSeconds != null && heartbeatAgeSeconds > staleHeartbeatSeconds;

  let runtimeState: OverviewRuntimeState = "unknown";
  if (OFFLINE_STATUSES.has(status) || isMissingHeartbeat) {
    runtimeState = "offline";
  } else if (DEGRADED_STATUSES.has(status)) {
    runtimeState = "degraded";
  } else if (isStaleHeartbeat) {
    runtimeState = "stale";
  } else if ((heartbeat?.active_session_count ?? 0) > 0) {
    runtimeState = "active";
  } else if (isHealthyStatus(status)) {
    runtimeState = "healthy";
  }

  return {
    name: butler.name,
    status: butler.status,
    sessions24h: butler.sessions_24h ?? 0,
    costUsd,
    lastSessionAt: heartbeat?.last_session_at ?? butler.last_session_started_at ?? null,
    activeSessionCount: heartbeat?.active_session_count ?? 0,
    heartbeatAgeSeconds,
    runtimeState,
    needsAttention:
      runtimeState === "offline" ||
      runtimeState === "degraded" ||
      runtimeState === "stale",
  };
}

function bucketIssues(
  issues: Issue[],
  now: Date,
  recentIssueHours: number,
): { currentHigh: Issue[]; recent: Issue[]; old: Issue[] } {
  const currentHigh: Issue[] = [];
  const recent: Issue[] = [];
  const old: Issue[] = [];

  for (const issue of issues) {
    const isRecent = issueIsRecent(issue, now, recentIssueHours);
    if (!isRecent) {
      old.push(issue);
      continue;
    }

    if (isHighIssue(issue)) {
      currentHigh.push(issue);
    } else {
      recent.push(issue);
    }
  }

  currentHigh.sort(compareIssues);
  recent.sort(compareIssues);
  old.sort(compareIssues);

  return { currentHigh, recent, old };
}

function issueIsRecent(issue: Issue, now: Date, recentIssueHours: number): boolean {
  const timestamp = issue.last_seen_at ?? issue.first_seen_at;
  if (!timestamp) return true;
  const seenAt = Date.parse(timestamp);
  if (Number.isNaN(seenAt)) return true;
  return now.getTime() - seenAt <= recentIssueHours * 60 * 60 * 1000;
}

function isHighIssue(issue: Issue): boolean {
  const severity = issue.severity.toLowerCase();
  return severity === "critical" || severity === "high" || severity === "error";
}

function compareIssues(a: Issue, b: Issue): number {
  const severityDelta = issueSeverityRank(a.severity) - issueSeverityRank(b.severity);
  if (severityDelta !== 0) return severityDelta;
  const timeA = issueSortTimestamp(a);
  const timeB = issueSortTimestamp(b);
  if (!timeA && !timeB) return 0;
  if (!timeA) return 1;
  if (!timeB) return -1;
  return timeA.localeCompare(timeB);
}

function issueSeverityRank(severity: string): number {
  switch (severity.toLowerCase()) {
    case "critical":
    case "high":
    case "error":
      return 0;
    case "medium":
    case "warning":
    case "warn":
      return 1;
    default:
      return 2;
  }
}

function issueSortTimestamp(issue: Issue): string {
  // Spec D4: sort by first_seen_at ascending (older issues first within a severity tier).
  // Falls back to last_seen_at when first_seen_at is absent.
  return issue.first_seen_at ?? issue.last_seen_at ?? "";
}

function issueAttentionRow(issue: Issue, now: Date): OverviewAttentionRow {
  const affectedButlers = humanButlerNames(issue.butlers?.length ? issue.butlers : [issue.butler]);
  const details = [affectedButlers];
  if (issue.error_message) details.push(issue.error_message);
  if ((issue.occurrences ?? 0) > 1) {
    details.push(`${issue.occurrences} occurrences`);
  }
  const recency = issueRecencyDetail(issue, now);
  if (recency) details.push(recency);

  return {
    id: `issue:${issue.type}:${issue.butler}:${issue.description}`,
    kind: "issue",
    severity: normalizeIssueSeverity(issue.severity),
    title: issue.description,
    detail: details.join(" · "),
    href: issue.link,
    count: (issue.occurrences ?? 0) > 1 ? issue.occurrences : undefined,
    lastSeenAt: issue.last_seen_at ?? null,
    butlers: issue.butlers,
  };
}

function runtimeAttentionRow(row: OverviewButlerIndexRow): OverviewAttentionRow {
  const title =
    row.runtimeState === "stale"
      ? `${row.name} heartbeat is stale`
      : `${row.name} is ${row.runtimeState}`;
  const detail =
    row.runtimeState === "stale" && row.heartbeatAgeSeconds != null
      ? `Last heartbeat ${formatDuration(row.heartbeatAgeSeconds)} ago`
      : `Status ${row.status}`;

  return {
    id: `runtime:${row.name}:${row.runtimeState}`,
    kind: "runtime",
    severity: row.runtimeState === "offline" || row.runtimeState === "degraded" ? "high" : "medium",
    title,
    detail,
    href: "/system",
    butlers: [row.name],
  };
}

function approvalAttentionRows(metrics: ApprovalMetrics | null | undefined): OverviewAttentionRow[] {
  const pending = metrics?.total_pending ?? 0;
  if (pending <= 0) return [];
  return [
    {
      id: "approvals:pending",
      kind: "approval",
      severity: "medium",
      title: `${pending} pending approval${pending === 1 ? "" : "s"}`,
      detail: "Owner decision needed.",
      href: "/approvals",
      count: pending,
    },
  ];
}

function notificationAttentionRows(
  stats: NotificationStats | null | undefined,
): OverviewAttentionRow[] {
  const failed = stats?.failed ?? 0;
  if (failed <= 0) return [];
  return [
    {
      id: "notifications:failed",
      kind: "notification",
      severity: "medium",
      title: `${failed} failed notification${failed === 1 ? "" : "s"}`,
      detail: "Delivery pressure needs review.",
      href: "/notifications",
      count: failed,
    },
  ];
}

function qaAttentionRows(summary: QaSummary | null | undefined): OverviewAttentionRow[] {
  if (!summary) return [];
  const qaState = summarizeQaState(summary);
  if (!qaState) return [];
  return [
    {
      id: "qa:attention",
      kind: "qa",
      severity: qaState.severity,
      title: qaState.title,
      detail: qaState.detail,
      href: "/qa",
      count: qaState.count,
    },
  ];
}

function deriveNowRows(input: OverviewDerivationInput, maxTimelineRows: number): OverviewNowRow[] {
  const rows: OverviewNowRow[] = [];
  const pendingApprovals = input.approvalMetrics?.total_pending ?? 0;
  if (pendingApprovals > 0) {
    rows.push({
      id: "now:approvals",
      kind: "approval",
      label: `${pendingApprovals} pending approval${pendingApprovals === 1 ? "" : "s"}`,
      detail: "Awaiting owner decision.",
      href: "/approvals",
      count: pendingApprovals,
    });
  }

  if (input.qaSummaryError) {
    rows.push({
      id: "now:qa:error",
      kind: "error",
      label: "QA status: unavailable",
      detail: "QA data could not be loaded.",
      href: "/qa",
    });
  } else {
    const qaState = summarizeQaState(input.qaSummary);
    if (qaState) {
      rows.push({
        id: "now:qa",
        kind: "qa",
        label: qaState.title,
        detail: qaState.detail,
        href: "/qa",
        count: qaState.count,
      });
    }
  }

  if (input.notificationStatsError) {
    rows.push({
      id: "now:notifications:error",
      kind: "error",
      label: "Notification status: unavailable",
      detail: "Notification data could not be loaded.",
      href: "/notifications",
    });
  } else {
    const failedNotifications = input.notificationStats?.failed ?? 0;
    if (failedNotifications > 0) {
      rows.push({
        id: "now:notifications",
        kind: "notification",
        label: `${failedNotifications} failed notification${
          failedNotifications === 1 ? "" : "s"
        }`,
        detail: "Delivery failures are present.",
        href: "/notifications",
        count: failedNotifications,
      });
    }
  }

  if (input.timelineError) {
    rows.push({
      id: "now:activity:error",
      kind: "error",
      label: "Timeline: unavailable",
      detail: "Timeline data could not be loaded.",
      href: "/timeline",
    });
  } else {
    rows.push(
      ...(input.timeline ?? [])
        .slice(0, maxTimelineRows)
        .map((event): OverviewNowRow => ({
          id: `now:activity:${event.id}`,
          kind: "activity",
          label: event.summary,
          detail: `${event.butler} · ${event.type}`,
          href: "/timeline",
        })),
    );
  }

  return rows;
}

function summarizeQaState(
  summary: QaSummary | null | undefined,
): { title: string; detail: string; severity: OverviewSeverity; count?: number } | null {
  if (!summary) return null;
  if (summary.last_patrol?.status === "failed" || summary.last_patrol?.error_detail) {
    return {
      title: "QA patrol failed",
      detail: summary.last_patrol.error_detail ?? "Last patrol ended in a failed state.",
      severity: "high",
    };
  }

  if (summary.stats_24h.dispatched_investigations > 0) {
    return {
      title: `${summary.stats_24h.dispatched_investigations} QA investigation${
        summary.stats_24h.dispatched_investigations === 1 ? "" : "s"
      } dispatched`,
      detail: "QA has active follow-up work.",
      severity: "medium",
      count: summary.stats_24h.dispatched_investigations,
    };
  }

  if (summary.stats_24h.novel_findings > 0) {
    return {
      title: `${summary.stats_24h.novel_findings} novel QA finding${
        summary.stats_24h.novel_findings === 1 ? "" : "s"
      }`,
      detail: "New QA findings need review.",
      severity: "medium",
      count: summary.stats_24h.novel_findings,
    };
  }

  return null;
}

function normalizeIssueSeverity(severity: string): OverviewSeverity {
  switch (severity.toLowerCase()) {
    case "critical":
      return "critical";
    case "high":
    case "error":
      return "high";
    case "medium":
    case "warning":
    case "warn":
      return "medium";
    case "low":
      return "low";
    default:
      return "info";
  }
}

function isHealthyStatus(status: string): boolean {
  return HEALTHY_STATUSES.has(status.toLowerCase());
}

function issueRecencyDetail(issue: Issue, now: Date): string | null {
  const timestamp = issue.last_seen_at ?? issue.first_seen_at;
  if (!timestamp) return null;
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return null;
  const diffSeconds = Math.max(0, Math.floor((now.getTime() - parsed.getTime()) / 1000));
  const prefix = issue.last_seen_at ? "last seen" : "first seen";
  if (diffSeconds < 60) return `${prefix} just now`;
  return `${prefix} ${formatDuration(diffSeconds)} ago`;
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(seconds / 3600);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

function humanButlerNames(names: string[]): string {
  const uniqueNames = [...new Set(names.filter(Boolean))];
  if (uniqueNames.length === 0) return "Unknown butler";
  if (uniqueNames.length === 1) return uniqueNames[0];
  if (uniqueNames.length === 2) return `${uniqueNames[0]} and ${uniqueNames[1]}`;
  return `${uniqueNames.slice(0, -1).join(", ")}, and ${uniqueNames[uniqueNames.length - 1]}`;
}
