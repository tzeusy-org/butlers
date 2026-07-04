import type {
  ApprovalMetrics,
  ApprovalSummary,
  BoardRow,
  Issue,
  NotificationStats,
  QaSummary,
  TimelineEvent,
} from "@/api/types";
import { NEEDS_YOU_ACTIVITIES } from "@/hooks/use-butler-status-board";

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
  includeOldIssueRows?: boolean;
  maxRecentIssueRows?: number;
  maxTimelineRows?: number;
  /** Cap on individually-actionable approval rows (bu-86c4c.14); extras collapse into a "N more" link row. */
  maxAttentionApprovalRows?: number;
}

export interface OverviewDerivationInput {
  /**
   * Rows from GET /api/butlers/board -- the SAME canonical, cadence-aware
   * liveness verdict the /butlers status board renders (bu-qvnce.4). The
   * Overview no longer derives its own status/threshold classification from
   * a raw butler list + heartbeat facts; `row.activity` is the single source
   * of truth for whether a butler needs attention (see NEEDS_YOU_ACTIVITIES).
   */
  boardRows?: BoardRow[];
  /** True when GET /api/butlers/board failed to load. */
  butlersError?: boolean;
  issues?: Issue[];
  issuesError?: boolean;
  approvalMetrics?: ApprovalMetrics | null;
  /**
   * Individual pending approvals (top few, any order) -- when present, the
   * Needs-attention list renders one actionable row per approval (verb-
   * labeled inline approve/deny/defer, bu-86c4c.14) instead of the aggregate
   * "N pending approvals" count row. Falls back to the aggregate row from
   * `approvalMetrics` when this is absent/empty (detail fetch not wired, or
   * erroring) so a real pending-approvals signal never silently disappears.
   */
  approvals?: ApprovalSummary[] | null;
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
  /**
   * True when this row exists because an upstream data source failed to
   * load (not because of a real operational signal). Rendered with
   * role="alert" so a degraded source announces itself distinctly.
   */
  isSourceError?: boolean;
  /**
   * Present only on an individually-actionable pending-approval row (kind
   * "approval") -- the underlying approval id. DashboardPage uses this to
   * wire live approve/deny/defer mutations onto the row before handing it to
   * AttentionList (bu-86c4c.14 -- Act loop / hot queue: approve/deny/defer
   * executable from the dashboard's attention list without leaving the
   * pane). Absent on the aggregate-count fallback row and every other kind.
   */
  approvalId?: string;
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
  /**
   * True when the butler-health source (`GET /api/butlers`) failed to load.
   * Distinguishes "the health source is down" from "there are genuinely no
   * butlers", so the UI can surface a degraded state instead of a serene
   * empty page. Mirrors the per-source error flags threaded into Now rows.
   */
  butlersError: boolean;
  /**
   * True when the issues source (`useIssues`) failed to load. Distinguishes
   * "the issues source is down" from "there are genuinely no issues" -- a
   * failed fetch must never render as the calm "Nothing waiting." empty
   * state (bu-86c4c.2, JARVIS audit move 1b: "truth amnesty").
   */
  issuesError: boolean;
}

const DEFAULT_RECENT_ISSUE_HOURS = 24;
const DEFAULT_MAX_RECENT_ISSUE_ROWS = 5;
const DEFAULT_MAX_TIMELINE_ROWS = 2;
const DEFAULT_MAX_ATTENTION_APPROVAL_ROWS = 3;

export function deriveOverviewTriageModel(
  input: OverviewDerivationInput,
  options: OverviewDerivationOptions = {},
): OverviewTriageModel {
  const now = options.now ?? new Date();
  const recentIssueHours = options.recentIssueHours ?? DEFAULT_RECENT_ISSUE_HOURS;
  const maxRecentIssueRows = options.maxRecentIssueRows ?? DEFAULT_MAX_RECENT_ISSUE_ROWS;
  const maxTimelineRows = options.maxTimelineRows ?? DEFAULT_MAX_TIMELINE_ROWS;
  const maxAttentionApprovalRows =
    options.maxAttentionApprovalRows ?? DEFAULT_MAX_ATTENTION_APPROVAL_ROWS;

  const butlerRows = (input.boardRows ?? []).filter((row) => row.type === "butler");
  const operationsRows = butlerRows.map(deriveButlerIndexRow);

  const issueBuckets = bucketIssues(input.issues ?? [], now, recentIssueHours);
  const runtimeRows = operationsRows
    .filter((row) => row.needsAttention)
    .map(runtimeAttentionRow);
  const approvalRows = approvalAttentionRows(
    input.approvals,
    input.approvalMetrics,
    maxAttentionApprovalRows,
  );
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

  const issuesSourceErrorRows: OverviewAttentionRow[] = input.issuesError
    ? [
        {
          id: "issues:source-error",
          kind: "issue",
          severity: "high",
          title: "Issues feed unavailable",
          detail: "Could not load recent issues -- retry from the issues page.",
          href: "/issues",
          isSourceError: true,
        },
      ]
    : [];

  const attentionRows = [
    ...issuesSourceErrorRows,
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
      totalButlers: butlerRows.length,
      // "Healthy" now counts exactly the rows the attention list does NOT
      // flag (the inverse of needsAttention below) -- the KPI and the
      // attention list are derived from the identical per-row classification,
      // so they can never disagree about which butlers are fine (bu-qvnce.4).
      healthyButlers: operationsRows.filter((row) => !row.needsAttention).length,
      sessions24h: butlerRows.reduce((sum, row) => sum + (row.sessions_24h ?? 0), 0),
      pendingApprovals: input.approvalMetrics?.total_pending ?? 0,
    },
    attentionRows,
    operationsRows,
    nowRows,
    hiddenOldIssueGroups,
    butlersError: input.butlersError ?? false,
    issuesError: input.issuesError ?? false,
  };
}

/** Maps the board's canonical activity verb onto the Overview's own runtime-state vocabulary. */
function runtimeStateForActivity(activity: BoardRow["activity"]): OverviewRuntimeState {
  switch (activity) {
    case "running":
      return "active";
    case "idle":
      return "healthy";
    case "overdue":
      return "stale";
    case "offline":
      return "offline";
    case "quarantined":
      return "degraded";
    case "unknown":
      return "unknown";
  }
}

function deriveButlerIndexRow(row: BoardRow): OverviewButlerIndexRow {
  return {
    name: row.name,
    status: row.status,
    sessions24h: row.sessions_24h,
    costUsd: row.cost_today ?? 0,
    lastSessionAt: row.last_session_at,
    activeSessionCount: row.active_session_count,
    heartbeatAgeSeconds: row.heartbeat_age_seconds,
    runtimeState: runtimeStateForActivity(row.activity),
    // Same set the /butlers status board's needsYou strip uses (bu-qvnce.1) --
    // one canonical "does this need a look" verdict shared by both surfaces.
    needsAttention: NEEDS_YOU_ACTIVITIES.has(row.activity),
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
    // Deep-link to the affected butler, not the generic /system page
    // (bu-86c4c.4 -- drill-down sweep): the row already names exactly one
    // butler, so the owner should land on its detail page, not a fleet-wide
    // dashboard they then have to search.
    href: `/butlers/${encodeURIComponent(row.name)}`,
    butlers: [row.name],
  };
}

/** Soonest-expiry-first, no-expiry items last (stable on ties). */
function byExpirySoonFirst(a: ApprovalSummary, b: ApprovalSummary): number {
  const aTime = a.expires_at ? new Date(a.expires_at).getTime() : Infinity;
  const bTime = b.expires_at ? new Date(b.expires_at).getTime() : Infinity;
  return aTime - bTime;
}

function approvalAttentionRows(
  approvals: ApprovalSummary[] | null | undefined,
  metrics: ApprovalMetrics | null | undefined,
  maxRows: number,
): OverviewAttentionRow[] {
  // Individual, actionable rows -- lets the owner approve/deny/defer inline
  // from the attention list (bu-86c4c.14) instead of only linking out.
  // Intentionally a simpler ranking than ApprovalsPage's full expiry +
  // blast-radius score: this is a "what needs a look" preview, not the
  // triage queue itself.
  if (approvals && approvals.length > 0) {
    const sorted = [...approvals].sort(byExpirySoonFirst);
    const shown = sorted.slice(0, maxRows);
    const rows: OverviewAttentionRow[] = shown.map((a) => ({
      id: `approvals:${a.id}`,
      kind: "approval",
      severity: "medium",
      title: a.tool_name.replace(/_/g, " "),
      detail: a.why ?? `${a.butler} · awaiting decision`,
      href: `/approvals/${a.id}`,
      approvalId: a.id,
    }));
    const remaining = sorted.length - shown.length;
    if (remaining > 0) {
      rows.push({
        id: "approvals:more",
        kind: "approval",
        severity: "low",
        title: `${remaining} more pending approval${remaining === 1 ? "" : "s"}`,
        detail: "Review the full queue.",
        href: "/approvals",
        count: remaining,
      });
    }
    return rows;
  }

  // Fallback: only the aggregate count is available (the detail list isn't
  // wired by this caller, or came back empty/erroring while metrics still
  // report pending > 0). Keeps the existing summary-only row so a real
  // pending-approvals signal never silently disappears.
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

  if (input.butlersError) {
    rows.push({
      id: "now:butlers:error",
      kind: "error",
      label: "Butler health: unavailable",
      detail: "Butler health data could not be loaded.",
      href: "/system",
    });
  }

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
