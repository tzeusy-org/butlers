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
import {
  isFailedMaintenanceEvent,
  isMaintenanceEvent,
  isSuccessfulMaintenanceEvent,
} from "@/lib/timeline-machine-class";

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
  /** Reveal reviewed internal maintenance as per-butler rollups in Now. */
  includeInternal?: boolean;
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
  /** ISO start timestamp used to scope notificationStats to its 24-hour window. */
  notificationSince?: string;
  /** ISO end timestamp used to keep the notification drill-down window closed. */
  notificationUntil?: string;
  notificationStatsError?: boolean;
  qaSummary?: QaSummary | null;
  qaSummaryError?: boolean;
  timeline?: TimelineEvent[];
  timelineError?: boolean;
  /**
   * Monthly spend-ceiling fleet-halt state, derived from GET
   * /api/dispatch/attempts via useFleetHaltStatus (bu-7o89u.3). Absent/null
   * means the caller hasn't wired the source (renders no row, same as every
   * other optional source above).
   */
  fleetHalt?: OverviewFleetHaltStatus | null;
}

/**
 * The subset of useFleetHaltStatus's return value the attention-row deriver
 * needs. Kept as its own interface (rather than importing the hook's type
 * directly) so model.ts stays a pure function of plain data, consistent with
 * every other *Error-flag input above.
 */
export interface OverviewFleetHaltStatus {
  active: boolean;
  deniedToday: number;
  deniedTotal: number;
  since: string | null;
  /** True when the underlying GET /api/dispatch/attempts fetch itself failed. */
  isSourceError: boolean;
}

export interface OverviewRuntimeKpis {
  totalButlers: number;
  healthyButlers: number;
  sessions24h: number;
  pendingApprovals: number;
}

export type OverviewRuntimeState =
  "healthy" | "active" | "stale" | "degraded" | "offline" | "unknown";

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
  /** True only for a confirmed operational failure, never an unknown outcome. */
  isFailure?: boolean;
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

const DEFAULT_RECENT_ISSUE_HOURS = 12;
const QA_PATROL_FAILURE_WINDOW_MS = 24 * 60 * 60 * 1000;
const DEFAULT_MAX_RECENT_ISSUE_ROWS = 5;
const DEFAULT_MAX_TIMELINE_ROWS = 2;
const DEFAULT_MAX_ATTENTION_APPROVAL_ROWS = 3;

/**
 * Global attention-row severity ranking (bu-gcz9e.3), used to stable-sort
 * `attentionRows` across ALL kinds (issue, runtime, approval, notification,
 * qa) rather than the previous fixed kind-concatenation order, which let a
 * lower-severity row from an earlier-concatenated kind outrank a
 * higher-severity row from a later one (e.g. a "stale" butler outranking a
 * tripped QA circuit breaker purely by kind position).
 */
const OVERVIEW_SEVERITY_RANK: Record<OverviewSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

export function deriveOverviewTriageModel(
  input: OverviewDerivationInput,
  options: OverviewDerivationOptions = {},
): OverviewTriageModel {
  const now = options.now ?? new Date();
  const recentIssueHours =
    options.recentIssueHours ?? DEFAULT_RECENT_ISSUE_HOURS;
  const maxRecentIssueRows =
    options.maxRecentIssueRows ?? DEFAULT_MAX_RECENT_ISSUE_ROWS;
  const maxTimelineRows = options.maxTimelineRows ?? DEFAULT_MAX_TIMELINE_ROWS;
  const includeInternal = options.includeInternal ?? false;
  const maxAttentionApprovalRows =
    options.maxAttentionApprovalRows ?? DEFAULT_MAX_ATTENTION_APPROVAL_ROWS;

  const butlerRows = (input.boardRows ?? []).filter(
    (row) => row.type === "butler",
  );
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
  const notificationRows = notificationAttentionRows(
    input.notificationStats,
    input.notificationSince,
    input.notificationUntil,
  );
  const notificationSourceRows = notificationSourceErrorRows(
    input.notificationStats,
  );
  const qaRows = qaAttentionRows(input.qaSummary, now);
  const fleetHaltRows = fleetHaltAttentionRows(input.fleetHalt);
  const currentHighIssues = issueBuckets.currentHigh.slice(
    0,
    maxRecentIssueRows,
  );
  const remainingIssueSlots = Math.max(
    maxRecentIssueRows - currentHighIssues.length,
    0,
  );
  const recentIssues = issueBuckets.recent.slice(0, remainingIssueSlots);
  const hiddenCurrentIssueGroups =
    Math.max(issueBuckets.currentHigh.length - currentHighIssues.length, 0) +
    Math.max(issueBuckets.recent.length - recentIssues.length, 0);

  const currentHighIssueRows = currentHighIssues.map((issue) =>
    issueAttentionRow(issue, now),
  );
  const recentIssueRows = recentIssues.map((issue) =>
    issueAttentionRow(issue, now),
  );
  const hiddenOldIssueGroups = options.includeOldIssueRows
    ? 0
    : issueBuckets.old.length;
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

  const butlersSourceErrorRows = butlersSourceErrorAttentionRows(
    input.butlersError ?? false,
  );

  // Severity-first, stable across kinds (bu-gcz9e.3): an offline butler or a
  // tripped QA circuit breaker must never rank below a lower-severity issue
  // row just because "issue" happens to be concatenated earlier in this
  // list. Array.prototype.sort is spec-guaranteed stable (ES2019+), so
  // building the pre-sort list in this order gives same-severity rows a
  // deterministic tiebreak (kind, then each kind's own internal ordering --
  // e.g. currentHighIssueRows is already severity+recency sorted, approvalRows
  // is already expiry-sorted) instead of shuffling between renders.
  const attentionRows = [
    ...issuesSourceErrorRows,
    ...butlersSourceErrorRows,
    ...currentHighIssueRows,
    ...runtimeRows,
    ...fleetHaltRows,
    ...approvalRows,
    ...notificationRows,
    ...notificationSourceRows,
    ...qaRows,
    ...recentIssueRows,
  ].sort(
    (a, b) =>
      OVERVIEW_SEVERITY_RANK[a.severity] - OVERVIEW_SEVERITY_RANK[b.severity],
  );

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

  const nowRows = deriveNowRows(input, maxTimelineRows, now, includeInternal);

  return {
    kpis: {
      totalButlers: butlerRows.length,
      // "Healthy" now counts exactly the rows the attention list does NOT
      // flag (the inverse of needsAttention below) -- the KPI and the
      // attention list are derived from the identical per-row classification,
      // so they can never disagree about which butlers are fine (bu-qvnce.4).
      healthyButlers: operationsRows.filter((row) => !row.needsAttention)
        .length,
      sessions24h: butlerRows.reduce(
        (sum, row) => sum + (row.sessions_24h ?? 0),
        0,
      ),
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
function runtimeStateForActivity(
  activity: BoardRow["activity"],
): OverviewRuntimeState {
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

function issueIsRecent(
  issue: Issue,
  now: Date,
  recentIssueHours: number,
): boolean {
  // A group is current only when the source can prove it was seen recently.
  // Falling back to first_seen_at (or treating an absent/malformed timestamp
  // as current) lets permanently historical issue groups keep the Overview
  // urgent forever.
  const timestamp = issue.last_seen_at;
  if (!timestamp) return false;
  const seenAt = Date.parse(timestamp);
  if (Number.isNaN(seenAt)) return false;
  const ageMs = now.getTime() - seenAt;
  return ageMs >= 0 && ageMs <= recentIssueHours * 60 * 60 * 1000;
}

function isHighIssue(issue: Issue): boolean {
  const severity = issue.severity.toLowerCase();
  return severity === "critical" || severity === "high" || severity === "error";
}

function compareIssues(a: Issue, b: Issue): number {
  const severityDelta =
    issueSeverityRank(a.severity) - issueSeverityRank(b.severity);
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
  const affectedButlers = humanButlerNames(
    issue.butlers?.length ? issue.butlers : [issue.butler],
  );
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

function runtimeAttentionRow(
  row: OverviewButlerIndexRow,
): OverviewAttentionRow {
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
    severity:
      row.runtimeState === "offline" || row.runtimeState === "degraded"
        ? "high"
        : "medium",
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
  notificationSince: string | undefined,
  notificationUntil: string | undefined,
): OverviewAttentionRow[] {
  const failed = stats?.failed ?? 0;
  if (failed <= 0) return [];
  return [
    {
      id: "notifications:failed",
      kind: "notification",
      severity: "medium",
      title: `${failed} failed notification${failed === 1 ? "" : "s"} in the last 24 hours`,
      detail: "Delivery pressure in the last 24 hours needs review.",
      // Predicate-carrying door (bu-qvnce.13): stats counts terminal failures,
      // so the link uses the same lifecycle predicate and bounded window, not
      // the raw failed-attempt or all-time stream.
      href: failedNotificationsHref(notificationSince, notificationUntil),
      count: failed,
    },
  ];
}

function failedNotificationsHref(
  notificationSince: string | undefined,
  notificationUntil: string | undefined,
): string {
  const params = new URLSearchParams({ status: "terminal_failed" });
  if (notificationSince) params.set("since", notificationSince);
  if (notificationUntil) params.set("until", notificationUntil);
  return `/notifications?${params.toString()}`;
}

/**
 * Notifications degraded-source row (bu-gcz9e.3, fleet degraded-source
 * convention -- see butlers/CLAUDE.md API Conventions). `source_available
 * === false` means the Switchboard notifications pool was unreachable and
 * every count on `NotificationStats` is a fabricated zero, not a genuine
 * "no failures" result -- silence here would compose a false all-clear, so
 * this must render an explicit degraded row instead (mirrors the
 * `isSourceError` idiom `issuesSourceErrorRows` above already established
 * for the issues source).
 */
function notificationSourceErrorRows(
  stats: NotificationStats | null | undefined,
): OverviewAttentionRow[] {
  if (stats?.source_available !== false) return [];
  return [
    {
      id: "notifications:source-error",
      kind: "notification",
      severity: "high",
      title: "Notifications feed unavailable",
      detail:
        "The notifications source was unreachable -- failed-delivery counts may be stale.",
      href: "/notifications",
      isSourceError: true,
    },
  ];
}

/**
 * Fleet-halt attention row (bu-7o89u.3): the monthly spend ceiling denying
 * dispatches fleet-wide is the single most consequential thing the spend
 * system can do -- it must be loud (critical severity), not silent. Mirrors
 * the notifications degraded-source idiom (`isSourceError`) for the case
 * where the underlying GET /api/dispatch/attempts fetch itself failed: a
 * failed fetch must never read as "the fleet is fine" (fleet degraded-source
 * convention).
 */
function fleetHaltAttentionRows(
  status: OverviewFleetHaltStatus | null | undefined,
): OverviewAttentionRow[] {
  if (!status) return [];

  if (status.isSourceError) {
    return [
      {
        id: "fleet-halt:source-error",
        kind: "runtime",
        severity: "high",
        title: "Dispatch denial feed unavailable",
        detail:
          "Could not confirm whether the monthly ceiling is halting dispatches -- retry from Spend.",
        href: "/spend",
        isSourceError: true,
      },
    ];
  }

  if (!status.active) return [];

  return [
    {
      id: "fleet-halt:ceiling",
      kind: "runtime",
      severity: "critical",
      title: "Monthly ceiling reached -- dispatches denied",
      detail: `${status.deniedToday} denied today, ${status.deniedTotal} since ${
        status.since ? formatSinceTimestamp(status.since) : "unknown"
      }.`,
      href: "/spend",
      count: status.deniedToday,
      lastSeenAt: status.since,
    },
  ];
}

/** Short human date+time for the fleet-halt row's "since <ts>" clause. */
function formatSinceTimestamp(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "unknown";
  return parsed.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Butlers/board source-error row (bu-gcz9e.2, fleet degraded-source
 * convention -- mirrors `issuesSourceErrorRows` and
 * `notificationSourceErrorRows` above). `input.butlersError` means
 * `GET /api/butlers/board` failed to load -- the SAME fetch the briefing
 * headline classifies from (`dashboard_briefing.py::_fetch_board_state`),
 * which surfaces this exact failure as the `"degraded"` state_class
 * ("One source could not be reached, so this may be incomplete."). Before
 * this row existed, `butlersError` only reached the KPI strip and the `Now`
 * list (`now:butlers:error`) -- never `attentionRows` -- so a board outage
 * made the Needs-attention list render the calm "Nothing waiting." empty
 * state directly beneath a headline saying the picture might be incomplete.
 * The bu-gcz9e.2 cross-surface consistency test caught this gap.
 */
function butlersSourceErrorAttentionRows(
  butlersError: boolean,
): OverviewAttentionRow[] {
  if (!butlersError) return [];
  return [
    {
      id: "runtime:source-error",
      kind: "runtime",
      severity: "high",
      title: "Butler status unavailable",
      detail: "Could not load butler liveness -- retry from the butlers board.",
      href: "/butlers",
      isSourceError: true,
    },
  ];
}

function qaAttentionRows(
  summary: QaSummary | null | undefined,
  now: Date,
): OverviewAttentionRow[] {
  if (!summary) return [];
  const qaState = summarizeQaState(summary, now);
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

function deriveNowRows(
  input: OverviewDerivationInput,
  maxTimelineRows: number,
  now: Date,
  includeInternal: boolean,
): OverviewNowRow[] {
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
    const qaState = summarizeQaState(input.qaSummary, now);
    if (qaState) {
      rows.push({
        id: "now:qa",
        kind: "qa",
        label: qaState.title,
        detail: qaState.detail,
        href: "/qa",
        count: qaState.count,
      });
    } else {
      const qaActivity = summarizeQaActivity(input.qaSummary);
      if (qaActivity) {
        rows.push({
          id: "now:qa",
          kind: "qa",
          label: qaActivity.title,
          detail: qaActivity.detail,
          href: "/qa",
          count: qaActivity.count,
        });
      }
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
        } in the last 24 hours`,
        detail: "Delivery failures occurred in the last 24 hours.",
        // Predicate-carrying door (bu-qvnce.13): see notificationAttentionRows.
        href: failedNotificationsHref(
          input.notificationSince,
          input.notificationUntil,
        ),
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
    rows.push(...deriveTimelineNowRows(input.timeline ?? [], maxTimelineRows, includeInternal));
  }

  return rows;
}

function deriveTimelineNowRows(
  events: TimelineEvent[],
  maxTimelineRows: number,
  includeInternal: boolean,
): OverviewNowRow[] {
  const ownerLensEvents: TimelineEvent[] = [];
  const maintenanceByButler = new Map<string, TimelineEvent[]>();

  for (const event of events) {
    if (!isMaintenanceEvent(event)) {
      ownerLensEvents.push(event);
      continue;
    }

    if (includeInternal) {
      const butler = event.butler || "Unassigned";
      const grouped = maintenanceByButler.get(butler) ?? [];
      grouped.push(event);
      maintenanceByButler.set(butler, grouped);
    } else if (!isSuccessfulMaintenanceEvent(event)) {
      // Failed, running, and unknown internal runs are operationally meaningful
      // on the owner lens, so they retain ordinary Timeline activity rows.
      ownerLensEvents.push(event);
    }
  }

  const rows = ownerLensEvents
    .slice(0, maxTimelineRows)
    .map((event): OverviewNowRow => {
      const isFailure = isFailedMaintenanceEvent(event);
      return {
        id: `now:activity:${event.id}`,
        kind: "activity",
        label: event.summary,
        detail: `${event.butler} · ${event.type}`,
        href: "/timeline",
        ...(isFailure ? { isFailure: true } : {}),
      };
    });

  if (!includeInternal) return rows;

  // The query already bounds this page's source events. Always show one
  // compact row for each loaded butler group once the owner opted in, rather
  // than letting the owner-activity preview cap make the lens look empty.
  return [
    ...rows,
    ...Array.from(maintenanceByButler, ([butler, maintenanceEvents]): OverviewNowRow => {
      const runs = maintenanceEvents.length;
      const failed = maintenanceEvents.filter(isFailedMaintenanceEvent).length;
      return {
        id: `now:maintenance:${butler}`,
        kind: "activity",
        label: `${butler}: ${runs} maintenance ${runs === 1 ? "run" : "runs"}${
          failed > 0 ? ` · ${failed} failed` : ""
        }`,
        detail: failed > 0 ? `Internal activity · ${failed} failed` : "Internal activity",
        href: "/timeline?internal=1",
        count: runs,
        ...(failed > 0 ? { isFailure: true } : {}),
      };
    }),
  ];
}

function summarizeQaState(
  summary: QaSummary | null | undefined,
  now: Date,
): {
  title: string;
  detail: string;
  severity: OverviewSeverity;
  count?: number;
} | null {
  if (!summary) return null;

  // Circuit-breaker tripped takes precedence over every other QA state --
  // mirrors QaVerdictOpener.tsx's buildClauses ordering (breaker checked
  // first), which already proved this data exists on the response
  // (bu-gcz9e.3: this function previously never read `circuit_breaker` at
  // all). A tripped breaker means the QA staffer has stopped dispatching
  // entirely after repeated consecutive failures -- more severe than a
  // single failed patrol run, so it outranks the "last patrol failed" branch
  // below.
  if (summary.circuit_breaker.tripped) {
    return {
      title: "QA circuit breaker tripped",
      detail: `${summary.circuit_breaker.consecutive_failures} consecutive failures -- QA staffer stopped dispatching.`,
      severity: "critical",
    };
  }

  if (hasRecentQaPatrolFailure(summary, now)) {
    return {
      title: "QA patrol failed",
      detail:
        summary.last_patrol?.error_detail ??
        "Last patrol ended in a failed state.",
      severity: "high",
    };
  }

  const activeCases = summary.kpis.active_cases_now;
  if (activeCases > 0) {
    return {
      title: `${activeCases} active QA investigation${activeCases === 1 ? "" : "s"}`,
      detail: "QA has active investigation work.",
      severity: "medium",
      count: activeCases,
    };
  }

  return null;
}

function hasRecentQaPatrolFailure(summary: QaSummary, now: Date): boolean {
  const patrol = summary.last_patrol;
  // ``error`` is the persisted terminal failure state; error_detail is
  // optional context and must not turn another status into a failure.
  if (!patrol || patrol.status !== "error") return false;
  const startedAt = Date.parse(patrol.started_at);
  if (Number.isNaN(startedAt)) return false;
  const ageMs = now.getTime() - startedAt;
  return ageMs >= 0 && ageMs <= QA_PATROL_FAILURE_WINDOW_MS;
}

function summarizeQaActivity(
  summary: QaSummary | null | undefined,
): { title: string; detail: string; count: number } | null {
  if (!summary) return null;

  if (summary.stats_24h.dispatched_investigations > 0) {
    const dispatched = summary.stats_24h.dispatched_investigations;
    return {
      title: `${dispatched} QA investigation${dispatched === 1 ? "" : "s"} dispatched in the last 24 hours`,
      detail: "Dispatched in the last 24 hours.",
      count: dispatched,
    };
  }

  if (summary.stats_24h.novel_findings > 0) {
    const findings = summary.stats_24h.novel_findings;
    return {
      title: `${findings} novel QA finding${findings === 1 ? "" : "s"} in the last 24 hours`,
      detail: "Recorded in the last 24 hours.",
      count: findings,
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
  const diffSeconds = Math.max(
    0,
    Math.floor((now.getTime() - parsed.getTime()) / 1000),
  );
  const prefix = issue.last_seen_at ? "last seen" : "first seen";
  if (diffSeconds < 60) return `${prefix} just now`;
  return `${prefix} ${formatDuration(diffSeconds)} ago`;
}

// eslint-disable-next-line no-restricted-syntax -- day-scale contract with no sub-minute tier (bu-sd0l7.3), distinct from every lib/format-duration.ts shape.
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
  if (uniqueNames.length === 2)
    return `${uniqueNames[0]} and ${uniqueNames[1]}`;
  return `${uniqueNames.slice(0, -1).join(", ")}, and ${uniqueNames[uniqueNames.length - 1]}`;
}
