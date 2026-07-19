import { describe, expect, it } from "vitest";

import type {
  ApprovalMetrics,
  ApprovalSummary,
  BoardRow,
  Issue,
  NotificationStats,
  QaSummary,
} from "@/api/types";
import { deriveOverviewTriageModel } from "./model";

const NOW = new Date("2026-05-14T12:00:00.000Z");

/**
 * A GET /api/butlers/board row -- the canonical, cadence-aware liveness
 * verdict (bu-qvnce.4). `activity` is the single source of truth the model
 * derives runtimeState/needsAttention from; defaults to "idle" (a fine,
 * no-attention-needed butler) so tests only need to override what they care
 * about.
 */
function boardRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    name: "general",
    type: "butler",
    description: null,
    status: "ok",
    activity: "idle",
    cell_tone: "green",
    eligibility: "active",
    quarantine_reason: null,
    quarantined_at: null,
    sessions_24h: 0,
    cost_today: null,
    load_pct: null,
    max_concurrent: null,
    active_session_count: 0,
    last_session_at: null,
    last_heartbeat_at: null,
    heartbeat_age_seconds: null,
    heartbeat_unavailable: false,
    schema_unreachable: false,
    hourly_stripe: [],
    hourly_total: 0,
    cadence_seconds: null,
    cadence_label: null,
    silence_seconds: null,
    cadence_status: "on_schedule",
    ...overrides,
  };
}

function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    severity: "medium",
    type: "runtime",
    butler: "general",
    description: "General issue",
    link: "/issues",
    first_seen_at: "2026-05-14T10:00:00.000Z",
    last_seen_at: "2026-05-14T11:00:00.000Z",
    occurrences: 1,
    issue_key: "runtime::general",
    ...overrides,
  };
}

function approvalMetrics(
  overrides: Partial<ApprovalMetrics> = {},
): ApprovalMetrics {
  return {
    total_pending: 0,
    total_approved_today: 0,
    total_rejected_today: 0,
    total_auto_approved_today: 0,
    total_expired_today: 0,
    avg_decision_latency_seconds: null,
    auto_approval_rate: 0,
    rejection_rate: 0,
    failure_count_today: 0,
    active_rules_count: 0,
    ...overrides,
  };
}

function approvalSummary(
  id: string,
  overrides: Partial<ApprovalSummary> = {},
): ApprovalSummary {
  return {
    id,
    butler: "general",
    tool_name: "send_email",
    status: "pending",
    created_at: "2026-05-14T10:00:00.000Z",
    expires_at: null,
    why: null,
    ...overrides,
  };
}

function notificationStats(
  overrides: Partial<NotificationStats> = {},
): NotificationStats {
  return {
    total: 0,
    sent: 0,
    failed: 0,
    by_channel: {},
    by_butler: {},
    ...overrides,
  };
}

function qaSummary(overrides: Partial<QaSummary> = {}): QaSummary {
  return {
    staffer_status: "healthy",
    last_patrol_at: null,
    next_patrol_at: null,
    last_patrol: null,
    stats_24h: {
      patrols_completed: 1,
      total_findings: 0,
      novel_findings: 0,
      dispatched_investigations: 0,
      prs_opened: 0,
    },
    stats_all_time: {
      total_patrols: 1,
      total_findings: 0,
      novel_findings: 0,
      dispatched_investigations: 0,
      prs_merged: 0,
      prs_failed: 0,
      success_rate: 0,
    },
    kpis: {
      prs_landed_24h: 0,
      mttr_24h_seconds: null,
      self_resolved_7d_pct: 0,
      active_cases_now: 0,
      failed_24h: 0,
      prs_landed_prior_24h: 0,
      mttr_prior_24h_seconds: null,
      self_resolved_prior_7d_pct: null,
      failed_prior_24h: 0,
    },
    active_breakdown: {
      awaiting_ci: 0,
      escalated_open_cases: 0,
    },
    active_sources: [],
    circuit_breaker: {
      tripped: false,
      consecutive_failures: 0,
      threshold: 5,
    },
    credentials_status: {
      gh_token_present: null,
      git_author_name_present: null,
      git_author_email_present: null,
      provisioning_hint: null,
    },
    port: null,
    model: null,
    patrol_interval_minutes: null,
    runtime_credential_alert: null,
    ...overrides,
  };
}

describe("deriveOverviewTriageModel", () => {
  it("sorts needs-attention rows by severity, equal-severity kinds keeping stable concatenation order", () => {
    // What this actually proves: the critical issue sorts to the top and the
    // medium issue to the bottom (severity ordering). The middle rows
    // [runtime, approval, notification, qa] are ALL severity "medium", so their
    // relative order is the stable-sort tiebreak (the source concatenation
    // order deriveOverviewTriageModel builds them in), NOT an inherent
    // kind-priority ranking. The genuine critical-first / actionability case is
    // proven by the severity assertions here and elsewhere (post-PR #3158,
    // bu-fwnmg).
    const model = deriveOverviewTriageModel(
      {
        boardRows: [boardRow({ name: "general", activity: "overdue" })],
        issues: [
          issue({
            severity: "medium",
            description: "Recent medium issue",
            first_seen_at: "2026-05-14T09:00:00.000Z",
            last_seen_at: "2026-05-14T11:30:00.000Z",
          }),
          issue({
            severity: "critical",
            description: "Current critical issue",
            first_seen_at: "2026-05-14T08:00:00.000Z",
            last_seen_at: "2026-05-14T11:00:00.000Z",
          }),
        ],
        approvalMetrics: approvalMetrics({ total_pending: 2 }),
        notificationStats: notificationStats({ failed: 3 }),
        qaSummary: qaSummary({
          kpis: { ...qaSummary().kpis, active_cases_now: 1 },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.map((row) => row.kind)).toEqual([
      "issue",
      "runtime",
      "approval",
      "notification",
      "qa",
      "issue",
    ]);
    expect(model.attentionRows[0]?.title).toBe("Current critical issue");
    expect(model.attentionRows[5]?.title).toBe("Recent medium issue");
  });

  it("sorts within-severity by first_seen_at ascending (older first, spec D4)", () => {
    // Three medium issues with different first_seen_at values.
    // Spec D4: within a severity tier, older unresolved issues (smaller first_seen_at) sort before newer.
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "medium",
            description: "Newest medium",
            first_seen_at: "2026-05-14T11:00:00.000Z",
            last_seen_at: "2026-05-14T11:59:00.000Z",
          }),
          issue({
            severity: "medium",
            description: "Oldest medium",
            first_seen_at: "2026-05-14T08:00:00.000Z",
            last_seen_at: "2026-05-14T11:59:00.000Z",
          }),
          issue({
            severity: "medium",
            description: "Middle medium",
            first_seen_at: "2026-05-14T09:30:00.000Z",
            last_seen_at: "2026-05-14T11:59:00.000Z",
          }),
        ],
      },
      { now: NOW },
    );

    const titles = model.attentionRows
      .filter((row) => row.kind === "issue")
      .map((row) => row.title);
    expect(titles).toEqual(["Oldest medium", "Middle medium", "Newest medium"]);
  });

  it("falls back to last_seen_at ascending when first_seen_at is absent", () => {
    // When first_seen_at is missing the fallback is last_seen_at, still ascending (older first).
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "medium",
            description: "Later last seen",
            first_seen_at: null,
            last_seen_at: "2026-05-14T11:30:00.000Z",
          }),
          issue({
            severity: "medium",
            description: "Earlier last seen",
            first_seen_at: null,
            last_seen_at: "2026-05-14T09:00:00.000Z",
          }),
        ],
      },
      { now: NOW },
    );

    const titles = model.attentionRows
      .filter((row) => row.kind === "issue")
      .map((row) => row.title);
    expect(titles).toEqual(["Earlier last seen", "Later last seen"]);
  });

  it("counts old issue groups for summary instead of emitting full rows by default", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "high",
            description: "Old high issue",
            first_seen_at: "2026-05-12T10:00:00.000Z",
            last_seen_at: "2026-05-12T11:00:00.000Z",
          }),
          issue({
            severity: "medium",
            description: "Current medium issue",
            first_seen_at: "2026-05-14T08:00:00.000Z",
            last_seen_at: "2026-05-14T11:00:00.000Z",
          }),
        ],
      },
      { now: NOW, recentIssueHours: 24 },
    );

    expect(model.hiddenOldIssueGroups).toBe(1);
    expect(model.attentionRows.map((row) => row.title)).toEqual([
      "Current medium issue",
      "1 older issue group",
    ]);
    expect(model.attentionRows[1]).toMatchObject({
      href: "/issues",
      count: 1,
    });
    expect(
      model.attentionRows.find((row) => row.title === "Old high issue"),
    ).toBeUndefined();
  });

  it("treats an issue group last seen more than twelve hours ago as historical by default", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "high",
            description: "Historical model-not-found error",
            first_seen_at: "2026-05-13T20:00:00.000Z",
            last_seen_at: "2026-05-13T21:00:00.000Z",
          }),
        ],
      },
      { now: NOW },
    );

    expect(
      model.attentionRows.find(
        (row) => row.title === "Historical model-not-found error",
      ),
    ).toBeUndefined();
    expect(model.hiddenOldIssueGroups).toBe(1);
    expect(model.attentionRows).toContainEqual(
      expect.objectContaining({
        title: "1 older issue group",
        kind: "old-issues-summary",
      }),
    );
  });

  it("treats a future-dated issue group as historical rather than current attention", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "high",
            description: "Future clock-skewed issue",
            last_seen_at: "2026-05-14T12:10:00.000Z",
          }),
        ],
      },
      { now: NOW },
    );

    expect(
      model.attentionRows.find(
        (row) => row.title === "Future clock-skewed issue",
      ),
    ).toBeUndefined();
    expect(model.hiddenOldIssueGroups).toBe(1);
  });

  it.each([
    ["at the twelve-hour start", "2026-05-14T00:00:00.000Z", true],
    ["at now", "2026-05-14T12:00:00.000Z", true],
    ["one millisecond before the start", "2026-05-13T23:59:59.999Z", false],
    ["one millisecond after now", "2026-05-14T12:00:00.001Z", false],
  ])("uses a closed twelve-hour issue window %s", (_label, lastSeenAt, isCurrent) => {
    const description = "Issue-boundary sentinel";
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "high",
            description,
            last_seen_at: lastSeenAt,
          }),
        ],
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.title === description)).toBe(
      isCurrent,
    );
  });

  it("caps visible issue groups and summarizes hidden groups behind the issues link", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            description: "Issue 1",
            last_seen_at: "2026-05-14T11:50:00.000Z",
          }),
          issue({
            description: "Issue 2",
            last_seen_at: "2026-05-14T11:40:00.000Z",
          }),
          issue({
            description: "Issue 3",
            last_seen_at: "2026-05-14T11:30:00.000Z",
          }),
          issue({
            description: "Issue 4",
            last_seen_at: "2026-05-14T11:20:00.000Z",
          }),
          issue({
            description: "Issue 5",
            last_seen_at: "2026-05-14T11:10:00.000Z",
          }),
          issue({
            description: "Old issue",
            last_seen_at: "2026-05-12T11:00:00.000Z",
          }),
        ],
      },
      { now: NOW, maxRecentIssueRows: 3 },
    );

    expect(model.attentionRows.map((row) => row.title)).toEqual([
      "Issue 1",
      "Issue 2",
      "Issue 3",
      "3 more issue groups",
    ]);
    expect(
      model.attentionRows.find((row) => row.title === "Issue 4"),
    ).toBeUndefined();
    expect(
      model.attentionRows.find((row) => row.title === "Old issue"),
    ).toBeUndefined();
    expect(model.attentionRows.at(-1)).toMatchObject({
      kind: "old-issues-summary",
      href: "/issues",
      count: 3,
    });
  });

  it("can emit old issue rows when explicitly requested", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            description: "Old issue",
            first_seen_at: "2026-05-12T10:00:00.000Z",
            last_seen_at: "2026-05-12T11:00:00.000Z",
          }),
        ],
      },
      { now: NOW, includeOldIssueRows: true },
    );

    expect(model.hiddenOldIssueGroups).toBe(0);
    expect(model.attentionRows[0]).toMatchObject({
      kind: "issue",
      title: "Old issue",
    });
    expect(model.attentionRows[0]?.detail).toContain("last seen 2d ago");
  });

  it("still summarizes capped current groups when old issue rows are included", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            description: "Issue 1",
            last_seen_at: "2026-05-14T11:50:00.000Z",
          }),
          issue({
            description: "Issue 2",
            last_seen_at: "2026-05-14T11:40:00.000Z",
          }),
          issue({
            description: "Issue 3",
            last_seen_at: "2026-05-14T11:30:00.000Z",
          }),
          issue({
            description: "Issue 4",
            last_seen_at: "2026-05-14T11:20:00.000Z",
          }),
          issue({
            description: "Old issue",
            last_seen_at: "2026-05-12T11:00:00.000Z",
          }),
        ],
      },
      { now: NOW, includeOldIssueRows: true, maxRecentIssueRows: 2 },
    );

    expect(model.hiddenOldIssueGroups).toBe(0);
    expect(model.attentionRows.map((row) => row.title)).toEqual([
      "Issue 1",
      "Issue 2",
      "2 more issue groups",
      "Old issue",
    ]);
    expect(model.attentionRows.at(2)).toMatchObject({
      kind: "old-issues-summary",
      href: "/issues",
      count: 2,
    });
  });

  it("keeps an issue with no last-seen timestamp in historical detail only", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            first_seen_at: "2026-05-14T10:00:00.000Z",
            last_seen_at: null,
          }),
        ],
      },
      {
        now: NOW,
        includeOldIssueRows: true,
      },
    );

    expect(model.attentionRows[0]?.detail).toContain("first seen 2h ago");
    expect(model.attentionRows[0]?.lastSeenAt).toBeNull();
    expect(model.hiddenOldIssueGroups).toBe(0);
  });

  it("does not treat issue rows with missing timestamps as current", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            first_seen_at: null,
            last_seen_at: null,
            occurrences: 1,
          }),
        ],
      },
      { now: NOW },
    );

    expect(model.attentionRows).toEqual([
      expect.objectContaining({
        kind: "old-issues-summary",
        title: "1 older issue group",
        count: 1,
      }),
    ]);
    expect(model.hiddenOldIssueGroups).toBe(1);
  });

  it("renders multiple-butler issue group metadata", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            butler: "general",
            butlers: ["general", "health", "relationship"],
            occurrences: 4,
            last_seen_at: "2026-05-14T11:30:00.000Z",
          }),
        ],
      },
      { now: NOW },
    );

    expect(model.attentionRows[0]).toMatchObject({
      count: 4,
      lastSeenAt: "2026-05-14T11:30:00.000Z",
      butlers: ["general", "health", "relationship"],
    });
    expect(model.attentionRows[0]?.detail).toContain(
      "general, health, and relationship",
    );
    expect(model.attentionRows[0]?.detail).toContain("4 occurrences");
    expect(model.attentionRows[0]?.detail).toContain("last seen 30m ago");
  });

  it("handles zero and pending approvals in kpis, attention, and now rows", () => {
    const zeroModel = deriveOverviewTriageModel({
      approvalMetrics: approvalMetrics({ total_pending: 0 }),
    });
    expect(zeroModel.kpis.pendingApprovals).toBe(0);
    expect(zeroModel.attentionRows.some((row) => row.kind === "approval")).toBe(
      false,
    );
    expect(zeroModel.nowRows.some((row) => row.kind === "approval")).toBe(
      false,
    );

    const pendingModel = deriveOverviewTriageModel({
      approvalMetrics: approvalMetrics({ total_pending: 4 }),
    });
    expect(pendingModel.kpis.pendingApprovals).toBe(4);
    expect(
      pendingModel.attentionRows.find((row) => row.kind === "approval"),
    ).toMatchObject({
      title: "4 pending approvals",
      href: "/approvals",
    });
    expect(
      pendingModel.nowRows.find((row) => row.kind === "approval"),
    ).toMatchObject({
      label: "4 pending approvals",
    });
  });

  it("derives overdue-cadence attention and enriched butler index metadata from the board's own verdict", () => {
    // The board's `activity: "overdue"` is the canonical cadence-aware
    // verdict (bu-qvnce.4) -- the model no longer runs its own stale-
    // heartbeat threshold against a raw heartbeat_age_seconds number.
    const model = deriveOverviewTriageModel({
      boardRows: [
        boardRow({
          name: "health",
          sessions_24h: 7,
          cost_today: 0.123,
          last_session_at: "2026-05-14T11:30:00.000Z",
          heartbeat_age_seconds: 1_200,
          activity: "overdue",
        }),
      ],
    });

    expect(model.operationsRows).toEqual([
      expect.objectContaining({
        name: "health",
        sessions24h: 7,
        costUsd: 0.123,
        lastSessionAt: "2026-05-14T11:30:00.000Z",
        heartbeatAgeSeconds: 1_200,
        runtimeState: "stale",
        needsAttention: true,
      }),
    ]);
    expect(
      model.attentionRows.find((row) => row.kind === "runtime"),
    ).toMatchObject({
      title: "health heartbeat is stale",
      detail: "Last heartbeat 20m ago",
      // bu-86c4c.4 -- drill-down sweep: a heartbeat row names exactly one
      // butler, so it must deep-link to that butler's detail page, not the
      // generic /system fleet page.
      href: "/butlers/health",
    });
  });

  it("maps running/idle board activity to KPIs and active-session metadata to the index", () => {
    const model = deriveOverviewTriageModel({
      boardRows: [
        boardRow({
          name: "general",
          sessions_24h: 3,
          activity: "running",
          active_session_count: 2,
          last_session_at: "2026-05-14T11:55:00.000Z",
        }),
        boardRow({ name: "health", sessions_24h: 4, activity: "idle" }),
      ],
    });

    expect(model.kpis).toMatchObject({
      totalButlers: 2,
      healthyButlers: 2,
      sessions24h: 7,
    });
    expect(model.operationsRows[0]).toMatchObject({
      name: "general",
      runtimeState: "active",
      activeSessionCount: 2,
      lastSessionAt: "2026-05-14T11:55:00.000Z",
      needsAttention: false,
    });
  });

  it("keeps null last-session fields visible as null instead of inventing activity", () => {
    const model = deriveOverviewTriageModel({
      boardRows: [
        boardRow({
          name: "relationship",
          activity: "idle",
          last_session_at: null,
          heartbeat_age_seconds: 30,
        }),
      ],
    });

    expect(model.operationsRows[0]).toMatchObject({
      name: "relationship",
      lastSessionAt: null,
      heartbeatAgeSeconds: 30,
      runtimeState: "healthy",
    });
  });

  it("derives time-bounded notification failure pressure and preserves its window in the drill-down", () => {
    const notificationSince = "2026-05-13T12:00:00.000Z";
    const notificationUntil = "2026-05-14T12:00:00.000Z";
    const model = deriveOverviewTriageModel({
      notificationStats: notificationStats({ total: 9, sent: 7, failed: 2 }),
      notificationSince,
      notificationUntil,
    });

    expect(
      model.attentionRows.find((row) => row.kind === "notification"),
    ).toMatchObject({
      title: "2 failed notifications in the last 24 hours",
      detail: "Delivery pressure in the last 24 hours needs review.",
      href: `/notifications?status=terminal_failed&since=${encodeURIComponent(notificationSince)}&until=${encodeURIComponent(notificationUntil)}`,
      count: 2,
    });
    expect(
      model.nowRows.find((row) => row.kind === "notification"),
    ).toMatchObject({
      label: "2 failed notifications in the last 24 hours",
      detail: "Delivery failures occurred in the last 24 hours.",
      href: `/notifications?status=terminal_failed&since=${encodeURIComponent(notificationSince)}&until=${encodeURIComponent(notificationUntil)}`,
    });
  });

  it("keeps QA clean states quiet and surfaces QA error states", () => {
    const cleanModel = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary(),
      },
      { now: NOW },
    );
    expect(cleanModel.attentionRows.some((row) => row.kind === "qa")).toBe(
      false,
    );
    expect(cleanModel.nowRows.some((row) => row.kind === "qa")).toBe(false);

    const errorModel = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          last_patrol: {
            id: "patrol-1",
            started_at: "2026-05-14T11:00:00.000Z",
            completed_at: "2026-05-14T11:01:00.000Z",
            status: "failed",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 60,
            sources_polled: ["sessions"],
            error_detail: "log scanner failed",
          },
        }),
      },
      { now: NOW },
    );

    expect(
      errorModel.attentionRows.find((row) => row.kind === "qa"),
    ).toMatchObject({
      severity: "high",
      title: "QA patrol failed",
      detail: "log scanner failed",
    });
    expect(errorModel.nowRows.find((row) => row.kind === "qa")).toMatchObject({
      label: "QA patrol failed",
    });
  });

  it("surfaces active QA investigations as attention", () => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          kpis: { ...qaSummary().kpis, active_cases_now: 2 },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.find((row) => row.kind === "qa")).toMatchObject({
      severity: "medium",
      title: "2 active QA investigations",
      detail: "QA has active investigation work.",
      count: 2,
    });
    expect(model.nowRows.find((row) => row.kind === "qa")).toMatchObject({
      label: "2 active QA investigations",
    });
  });

  it("keeps completed QA dispatches out of attention and labels them as time-bounded activity", () => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          stats_24h: { ...qaSummary().stats_24h, dispatched_investigations: 1 },
          kpis: { ...qaSummary().kpis, active_cases_now: 0 },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(false);
    expect(model.nowRows.find((row) => row.kind === "qa")).toMatchObject({
      label: "1 QA investigation dispatched in the last 24 hours",
      detail: "Dispatched in the last 24 hours.",
      count: 1,
    });
  });

  it("keeps novel QA findings out of attention and labels them as time-bounded activity", () => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          stats_24h: {
            ...qaSummary().stats_24h,
            novel_findings: 1,
            dispatched_investigations: 0,
          },
          kpis: { ...qaSummary().kpis, active_cases_now: 0 },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(false);
    expect(model.nowRows.find((row) => row.kind === "qa")).toMatchObject({
      label: "1 novel QA finding in the last 24 hours",
      detail: "Recorded in the last 24 hours.",
      count: 1,
    });
  });

  it("does not surface a failed QA patrol that is older than one day", () => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          last_patrol: {
            id: "historical-patrol",
            started_at: "2026-05-13T10:00:00.000Z",
            completed_at: "2026-05-13T10:01:00.000Z",
            status: "failed",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 60,
            sources_polled: ["sessions"],
            error_detail: "historical scanner failure",
          },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(false);
    expect(model.nowRows.some((row) => row.kind === "qa")).toBe(false);
  });

  it("does not surface a future-dated failed QA patrol as current attention", () => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          last_patrol: {
            id: "future-patrol",
            started_at: "2026-05-14T12:10:00.000Z",
            completed_at: "2026-05-14T12:11:00.000Z",
            status: "failed",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 60,
            sources_polled: ["sessions"],
            error_detail: "clock-skewed scanner failure",
          },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(false);
    expect(model.nowRows.some((row) => row.kind === "qa")).toBe(false);
  });

  it.each([
    ["at the twenty-four-hour start", "2026-05-13T12:00:00.000Z", true],
    ["at now", "2026-05-14T12:00:00.000Z", true],
    ["one millisecond before the start", "2026-05-13T11:59:59.999Z", false],
    ["one millisecond after now", "2026-05-14T12:00:00.001Z", false],
  ])("uses a closed twenty-four-hour QA patrol window %s", (_label, startedAt, isCurrent) => {
    const model = deriveOverviewTriageModel(
      {
        qaSummary: qaSummary({
          last_patrol: {
            id: "patrol-boundary",
            started_at: startedAt,
            completed_at: startedAt,
            status: "failed",
            findings_count: 0,
            novel_count: 0,
            dispatched_count: 0,
            log_lookback_minutes: 60,
            sources_polled: ["sessions"],
            error_detail: "boundary sentinel",
          },
        }),
      },
      { now: NOW },
    );

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(
      isCurrent,
    );
  });

  it("uses current butlers only for promoted runtime KPIs", () => {
    const model = deriveOverviewTriageModel({
      boardRows: [
        boardRow({ name: "general", sessions_24h: 3, activity: "idle" }),
        boardRow({ name: "health", sessions_24h: 2, activity: "quarantined" }),
        boardRow({
          name: "switchboard",
          type: "staffer",
          sessions_24h: 10,
          activity: "running",
        }),
      ],
      approvalMetrics: approvalMetrics({ total_pending: 1 }),
    });

    expect(model.kpis).toMatchObject({
      totalButlers: 2,
      healthyButlers: 1,
      sessions24h: 5,
      pendingApprovals: 1,
    });
  });

  it("emits a named error row for notifications when notificationStatsError is true", () => {
    const model = deriveOverviewTriageModel({
      notificationStats: null,
      notificationStatsError: true,
    });

    const errorRow = model.nowRows.find(
      (row) => row.id === "now:notifications:error",
    );
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "error",
      label: "Notification status: unavailable",
      href: "/notifications",
    });
    // Should NOT emit a normal notification row
    expect(model.nowRows.some((row) => row.id === "now:notifications")).toBe(
      false,
    );
  });

  it("emits a named error row for QA when qaSummaryError is true", () => {
    const model = deriveOverviewTriageModel({
      qaSummary: null,
      qaSummaryError: true,
    });

    const errorRow = model.nowRows.find((row) => row.id === "now:qa:error");
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "error",
      label: "QA status: unavailable",
      href: "/qa",
    });
    // Should NOT emit a normal QA row
    expect(model.nowRows.some((row) => row.id === "now:qa")).toBe(false);
  });

  it("emits a named error row for timeline when timelineError is true", () => {
    const model = deriveOverviewTriageModel({
      timeline: [],
      timelineError: true,
    });

    const errorRow = model.nowRows.find(
      (row) => row.id === "now:activity:error",
    );
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "error",
      label: "Timeline: unavailable",
      href: "/timeline",
    });
    // Should NOT emit any activity rows
    expect(model.nowRows.some((row) => row.kind === "activity")).toBe(false);
  });

  it("emits a named error row and sets butlersError when butlersError is true", () => {
    const model = deriveOverviewTriageModel({
      boardRows: [],
      butlersError: true,
    });

    const errorRow = model.nowRows.find(
      (row) => row.id === "now:butlers:error",
    );
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "error",
      label: "Butler health: unavailable",
      href: "/system",
    });
    expect(model.butlersError).toBe(true);
  });

  it("leaves butlersError false and emits no butler error row by default", () => {
    const model = deriveOverviewTriageModel({ boardRows: [] });
    expect(model.butlersError).toBe(false);
    expect(model.nowRows.some((row) => row.id === "now:butlers:error")).toBe(
      false,
    );
  });

  it("emits a named source-error attention row when butlersError is true (bu-gcz9e.2)", () => {
    // Before this row existed, a failed board fetch only surfaced via
    // butlersError in the KPI strip/Now list -- the Needs-attention list
    // rendered "Nothing waiting." even though the SAME board fetch drives
    // the briefing headline's "degraded" ("may be incomplete") state_class.
    const model = deriveOverviewTriageModel({
      boardRows: [],
      butlersError: true,
    });

    const errorRow = model.attentionRows.find(
      (row) => row.id === "runtime:source-error",
    );
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "runtime",
      severity: "high",
      title: "Butler status unavailable",
      href: "/butlers",
      isSourceError: true,
    });
    // attentionRows must not be empty, so AttentionList cannot fall back to
    // "Nothing waiting."
    expect(model.attentionRows.length).toBeGreaterThan(0);
  });

  it("emits no butlers source-error attention row by default", () => {
    const model = deriveOverviewTriageModel({ boardRows: [] });
    expect(
      model.attentionRows.some((row) => row.id === "runtime:source-error"),
    ).toBe(false);
  });

  it("emits a named source-error attention row and sets issuesError when issuesError is true (bu-86c4c.2)", () => {
    // This is the exact "Nothing waiting." truth-amnesty defect from the JARVIS
    // audit: a failed issues fetch must never look like a genuinely empty
    // attention list.
    const model = deriveOverviewTriageModel({
      issues: [],
      issuesError: true,
    });

    const errorRow = model.attentionRows.find(
      (row) => row.id === "issues:source-error",
    );
    expect(errorRow).toBeDefined();
    expect(errorRow).toMatchObject({
      kind: "issue",
      severity: "high",
      title: "Issues feed unavailable",
      href: "/issues",
      isSourceError: true,
    });
    expect(model.issuesError).toBe(true);
    // attentionRows must not be empty, so AttentionList cannot fall back to
    // "Nothing waiting."
    expect(model.attentionRows.length).toBeGreaterThan(0);
  });

  it("leaves issuesError false and emits no issues error row by default", () => {
    const model = deriveOverviewTriageModel({ issues: [] });
    expect(model.issuesError).toBe(false);
    expect(
      model.attentionRows.some((row) => row.id === "issues:source-error"),
    ).toBe(false);
  });

  it("does not emit error rows when error flags are false", () => {
    const model = deriveOverviewTriageModel({
      notificationStats: notificationStats({ failed: 0 }),
      notificationStatsError: false,
      qaSummary: qaSummary(),
      qaSummaryError: false,
      timeline: [],
      timelineError: false,
    });

    expect(model.nowRows.some((row) => row.kind === "error")).toBe(false);
  });

  it("prefers the error sentinel over any data when error flag is set alongside non-null data", () => {
    // Even if data was somehow provided alongside an error flag, error sentinel wins
    const model = deriveOverviewTriageModel({
      notificationStats: notificationStats({ failed: 5 }),
      notificationStatsError: true,
    });

    expect(
      model.nowRows.find((row) => row.id === "now:notifications:error"),
    ).toBeDefined();
    expect(model.nowRows.some((row) => row.id === "now:notifications")).toBe(
      false,
    );
  });
});

// ---------------------------------------------------------------------------
// KPI / attention-list coherence (bu-qvnce.4): the Healthy KPI and the
// runtime attention rows must never disagree, because both are derived from
// the exact same per-row board verdict (row.activity via NEEDS_YOU_ACTIVITIES)
// rather than two independently-maintained classifications.
// ---------------------------------------------------------------------------

describe("deriveOverviewTriageModel — KPI/attention-list coherence (bu-qvnce.4)", () => {
  it.each([
    ["running", false],
    ["idle", false],
    ["overdue", true],
    ["offline", true],
    ["quarantined", true],
    ["unknown", true],
  ] as const)(
    "activity=%s needsAttention=%s agrees between the Healthy KPI and the attention list",
    (activity, expectNeedsAttention) => {
      const model = deriveOverviewTriageModel({
        boardRows: [boardRow({ name: "general", activity })],
      });

      expect(model.operationsRows[0].needsAttention).toBe(expectNeedsAttention);
      const hasRuntimeRow = model.attentionRows.some(
        (row) => row.kind === "runtime" && row.butlers?.includes("general"),
      );
      expect(hasRuntimeRow).toBe(expectNeedsAttention);
      // Healthy KPI must be the exact inverse of whether the row appears in
      // the attention list -- never independently computed.
      expect(model.kpis.healthyButlers).toBe(expectNeedsAttention ? 0 : 1);
    },
  );

  it("mixed fleet: Healthy count and the set of attention-flagged names always agree", () => {
    const rows = [
      boardRow({ name: "general", activity: "running" }),
      boardRow({ name: "health", activity: "overdue" }),
      boardRow({ name: "finance", activity: "quarantined" }),
      boardRow({ name: "relationship", activity: "idle" }),
    ];
    const model = deriveOverviewTriageModel({ boardRows: rows });

    const flaggedNames = new Set(
      model.attentionRows
        .filter((row) => row.kind === "runtime")
        .flatMap((row) => row.butlers ?? []),
    );
    expect(flaggedNames).toEqual(new Set(["health", "finance"]));
    expect(model.kpis.healthyButlers).toBe(rows.length - flaggedNames.size);
    expect(model.kpis.totalButlers).toBe(rows.length);
  });
});

// ---------------------------------------------------------------------------
// Per-item approval attention rows (bu-86c4c.14 -- Act loop / hot queue):
// approve/deny/defer executable from the dashboard without leaving the pane.
// ---------------------------------------------------------------------------

describe("deriveOverviewTriageModel — per-item approval attention rows (bu-86c4c.14)", () => {
  it("renders one actionable row per pending approval, carrying its id", () => {
    const model = deriveOverviewTriageModel({
      approvals: [
        approvalSummary("a1", { tool_name: "send_email" }),
        approvalSummary("a2"),
      ],
      approvalMetrics: approvalMetrics({ total_pending: 2 }),
    });

    const rows = model.attentionRows.filter((row) => row.kind === "approval");
    expect(rows).toHaveLength(2);
    expect(rows.map((r) => r.approvalId).sort()).toEqual(["a1", "a2"]);
    expect(rows[0]).toMatchObject({
      title: "send email",
      href: "/approvals/a1",
    });
  });

  it("ranks the soonest-to-expire approval first", () => {
    const soon = approvalSummary("expiring", {
      expires_at: new Date(NOW.getTime() + 5 * 60_000).toISOString(),
    });
    const noExpiry = approvalSummary("no-expiry");
    const model = deriveOverviewTriageModel({
      approvals: [noExpiry, soon],
      approvalMetrics: approvalMetrics({ total_pending: 2 }),
    });

    const rows = model.attentionRows.filter((row) => row.kind === "approval");
    expect(rows[0].approvalId).toBe("expiring");
  });

  it("caps actionable rows and collapses the remainder into a 'more' row with no approvalId", () => {
    const approvals = Array.from({ length: 5 }, (_, i) =>
      approvalSummary(`a${i}`),
    );
    const model = deriveOverviewTriageModel(
      { approvals, approvalMetrics: approvalMetrics({ total_pending: 5 }) },
      { maxAttentionApprovalRows: 2 },
    );

    const rows = model.attentionRows.filter((row) => row.kind === "approval");
    expect(rows).toHaveLength(3); // 2 actionable + 1 "more"
    expect(rows.filter((r) => r.approvalId).length).toBe(2);
    const more = rows.find((r) => !r.approvalId);
    expect(more).toMatchObject({
      title: "3 more pending approvals",
      href: "/approvals",
    });
  });

  it("falls back to the aggregate count row when no detail list is provided", () => {
    const model = deriveOverviewTriageModel({
      approvalMetrics: approvalMetrics({ total_pending: 4 }),
    });

    const rows = model.attentionRows.filter((row) => row.kind === "approval");
    expect(rows).toHaveLength(1);
    expect(rows[0].approvalId).toBeUndefined();
    expect(rows[0]).toMatchObject({
      title: "4 pending approvals",
      href: "/approvals",
    });
  });

  it("falls back to the aggregate count row when the detail list is empty but metrics still report pending", () => {
    const model = deriveOverviewTriageModel({
      approvals: [],
      approvalMetrics: approvalMetrics({ total_pending: 2 }),
    });

    const rows = model.attentionRows.filter((row) => row.kind === "approval");
    expect(rows).toHaveLength(1);
    expect(rows[0].approvalId).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Severity-first stable ordering across kinds + QA circuit-breaker +
// notifications source_available rows (bu-gcz9e.3).
// ---------------------------------------------------------------------------

describe("deriveOverviewTriageModel — severity-first stable ordering (bu-gcz9e.3)", () => {
  it("ranks a tripped QA circuit breaker (critical) above a lower-severity issue row, even though 'issue' is concatenated earlier than 'qa'", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            severity: "high",
            description: "High issue",
            first_seen_at: "2026-05-14T10:00:00.000Z",
            last_seen_at: "2026-05-14T11:00:00.000Z",
          }),
        ],
        qaSummary: qaSummary({
          circuit_breaker: { tripped: true, consecutive_failures: 4 },
        }),
      },
      { now: NOW },
    );

    // Without a real cross-kind severity sort, "issue" rows are concatenated
    // before "qa" rows and the high-severity issue would rank first despite
    // the QA breaker being more severe (critical > high).
    expect(model.attentionRows[0]).toMatchObject({
      kind: "qa",
      severity: "critical",
    });
    expect(model.attentionRows[1]).toMatchObject({
      kind: "issue",
      severity: "high",
    });
  });

  it("keeps a stable, deterministic order for same-severity rows across kinds (no shuffling between calls)", () => {
    const input = {
      boardRows: [boardRow({ name: "general", activity: "overdue" })], // runtime, severity medium
      approvalMetrics: approvalMetrics({ total_pending: 1 }), // approval, severity medium
      notificationStats: notificationStats({ failed: 1 }), // notification, severity medium
    };

    const first = deriveOverviewTriageModel(input, { now: NOW });
    const second = deriveOverviewTriageModel(input, { now: NOW });

    const kindsFirst = first.attentionRows.map((row) => row.kind);
    const kindsSecond = second.attentionRows.map((row) => row.kind);
    expect(kindsFirst).toEqual(["runtime", "approval", "notification"]);
    expect(kindsSecond).toEqual(kindsFirst);
  });

  it("surfaces a critical QA circuit-breaker-tripped attention row, taking precedence over a simultaneous failed patrol (mirrors QaVerdictOpener's clause precedence)", () => {
    const model = deriveOverviewTriageModel({
      qaSummary: qaSummary({
        circuit_breaker: { tripped: true, consecutive_failures: 6 },
        last_patrol: {
          id: "patrol-1",
          started_at: "2026-05-14T11:00:00.000Z",
          completed_at: "2026-05-14T11:01:00.000Z",
          status: "failed",
          findings_count: 0,
          novel_count: 0,
          dispatched_count: 0,
          log_lookback_minutes: 60,
          sources_polled: ["sessions"],
          error_detail: "log scanner failed",
        },
      }),
    });

    const qaRow = model.attentionRows.find((row) => row.kind === "qa");
    expect(qaRow).toMatchObject({
      severity: "critical",
      title: "QA circuit breaker tripped",
    });
    expect(qaRow?.detail).toContain("6 consecutive failures");
    // Same precedence applies to the Now list's QA item, since both derive
    // from the same summarizeQaState.
    const nowQaRow = model.nowRows.find((row) => row.kind === "qa");
    expect(nowQaRow).toMatchObject({ label: "QA circuit breaker tripped" });
  });

  it("does not surface a QA breaker row when the breaker is not tripped", () => {
    const model = deriveOverviewTriageModel({
      qaSummary: qaSummary({
        circuit_breaker: { tripped: false, consecutive_failures: 0 },
      }),
    });

    expect(model.attentionRows.some((row) => row.kind === "qa")).toBe(false);
  });

  it("surfaces a degraded attention row when notifications source_available is false", () => {
    const model = deriveOverviewTriageModel({
      notificationStats: notificationStats({
        failed: 0,
        source_available: false,
      }),
    });

    const row = model.attentionRows.find(
      (r) => r.id === "notifications:source-error",
    );
    expect(row).toBeDefined();
    expect(row).toMatchObject({
      kind: "notification",
      severity: "high",
      title: "Notifications feed unavailable",
      href: "/notifications",
      isSourceError: true,
    });
    // A real "0 failed" count must not additionally render as a calm
    // "0 failed notifications" row alongside the degraded row.
    expect(
      model.attentionRows.some((r) => r.id === "notifications:failed"),
    ).toBe(false);
  });

  it("does not surface a notifications degraded row when source_available is true or absent", () => {
    const availableModel = deriveOverviewTriageModel({
      notificationStats: notificationStats({
        failed: 0,
        source_available: true,
      }),
    });
    expect(
      availableModel.attentionRows.some(
        (r) => r.id === "notifications:source-error",
      ),
    ).toBe(false);

    const absentModel = deriveOverviewTriageModel({
      notificationStats: notificationStats({ failed: 0 }),
    });
    expect(
      absentModel.attentionRows.some(
        (r) => r.id === "notifications:source-error",
      ),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Fleet-halt attention row (bu-7o89u.3) -- monthly spend ceiling denying
// dispatches fleet-wide, sourced from useFleetHaltStatus.
// ---------------------------------------------------------------------------

describe("deriveOverviewTriageModel — fleet-halt attention row (bu-7o89u.3)", () => {
  it("surfaces a critical row naming the today/total counts and since-timestamp when active", () => {
    const model = deriveOverviewTriageModel({
      fleetHalt: {
        active: true,
        deniedToday: 4,
        deniedTotal: 12,
        since: "2026-05-10T08:00:00.000Z",
        isSourceError: false,
      },
    });

    const row = model.attentionRows.find((r) => r.id === "fleet-halt:ceiling");
    expect(row).toBeDefined();
    expect(row).toMatchObject({
      kind: "runtime",
      severity: "critical",
      title: "Monthly ceiling reached -- dispatches denied",
      href: "/spend",
      count: 4,
      lastSeenAt: "2026-05-10T08:00:00.000Z",
    });
    expect(row?.detail).toContain("4 denied today");
    expect(row?.detail).toContain("12 since");
  });

  it("renders no row when the fleet halt is not active", () => {
    const model = deriveOverviewTriageModel({
      fleetHalt: {
        active: false,
        deniedToday: 0,
        deniedTotal: 0,
        since: null,
        isSourceError: false,
      },
    });
    expect(model.attentionRows.some((r) => r.id === "fleet-halt:ceiling")).toBe(
      false,
    );
  });

  it("renders no row when fleetHalt is absent", () => {
    const model = deriveOverviewTriageModel({});
    expect(
      model.attentionRows.some((r) => r.id.startsWith("fleet-halt:")),
    ).toBe(false);
  });

  it("surfaces a degraded source-error row instead of silently reading as 'no halt' when the fetch fails", () => {
    const model = deriveOverviewTriageModel({
      fleetHalt: {
        active: false,
        deniedToday: 0,
        deniedTotal: 0,
        since: null,
        isSourceError: true,
      },
    });

    const row = model.attentionRows.find(
      (r) => r.id === "fleet-halt:source-error",
    );
    expect(row).toBeDefined();
    expect(row).toMatchObject({
      kind: "runtime",
      severity: "high",
      title: "Dispatch denial feed unavailable",
      href: "/spend",
      isSourceError: true,
    });
    expect(model.attentionRows.some((r) => r.id === "fleet-halt:ceiling")).toBe(
      false,
    );
  });

  it("ranks the fleet-halt critical row above a same-batch high-severity issue row (severity-first ordering)", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [issue({ severity: "high", description: "High issue" })],
        fleetHalt: {
          active: true,
          deniedToday: 1,
          deniedTotal: 1,
          since: "2026-05-14T11:00:00.000Z",
          isSourceError: false,
        },
      },
      { now: NOW },
    );

    const ids = model.attentionRows.map((r) => r.id);
    expect(ids.indexOf("fleet-halt:ceiling")).toBeLessThan(
      ids.findIndex((id) => id.startsWith("issue:")),
    );
  });
});
