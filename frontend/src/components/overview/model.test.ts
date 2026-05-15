import { describe, expect, it } from "vitest";

import type {
  ApprovalMetrics,
  ButlerSummary,
  Issue,
  NotificationStats,
  QaSummary,
} from "@/api/types";
import { deriveOverviewTriageModel } from "./model";

const NOW = new Date("2026-05-14T12:00:00.000Z");

function butler(overrides: Partial<ButlerSummary> = {}): ButlerSummary {
  return {
    name: "general",
    status: "ok",
    port: 40101,
    type: "butler",
    sessions_24h: 0,
    last_session_started_at: null,
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
    ...overrides,
  };
}

function approvalMetrics(overrides: Partial<ApprovalMetrics> = {}): ApprovalMetrics {
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

function notificationStats(overrides: Partial<NotificationStats> = {}): NotificationStats {
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
      prs_landed_prior_24h: 0,
      mttr_prior_24h_seconds: null,
      self_resolved_prior_7d_pct: null,
    },
    active_breakdown: {
      awaiting_ci: 0,
      escalated_open_cases: 0,
    },
    active_sources: [],
    circuit_breaker: {
      tripped: false,
      consecutive_failures: 0,
    },
    credentials_status: {
      gh_token_present: null,
      provisioning_hint: null,
    },
    ...overrides,
  };
}

describe("deriveOverviewTriageModel", () => {
  it("sorts needs-attention rows by actionability", () => {
    const model = deriveOverviewTriageModel(
      {
        butlers: [butler({ name: "general" })],
        heartbeats: {
          butlers: [
            {
              name: "general",
              last_heartbeat_at: "2026-05-14T11:50:00.000Z",
              last_session_at: null,
              active_session_count: 0,
              heartbeat_age_seconds: 900,
            },
          ],
        },
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
        qaSummary: qaSummary({ stats_24h: { ...qaSummary().stats_24h, novel_findings: 1 } }),
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
    expect(model.attentionRows.find((row) => row.title === "Old high issue")).toBeUndefined();
  });

  it("caps visible issue groups and summarizes hidden groups behind the issues link", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({ description: "Issue 1", last_seen_at: "2026-05-14T11:50:00.000Z" }),
          issue({ description: "Issue 2", last_seen_at: "2026-05-14T11:40:00.000Z" }),
          issue({ description: "Issue 3", last_seen_at: "2026-05-14T11:30:00.000Z" }),
          issue({ description: "Issue 4", last_seen_at: "2026-05-14T11:20:00.000Z" }),
          issue({ description: "Issue 5", last_seen_at: "2026-05-14T11:10:00.000Z" }),
          issue({ description: "Old issue", last_seen_at: "2026-05-12T11:00:00.000Z" }),
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
    expect(model.attentionRows.find((row) => row.title === "Issue 4")).toBeUndefined();
    expect(model.attentionRows.find((row) => row.title === "Old issue")).toBeUndefined();
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
          issue({ description: "Issue 1", last_seen_at: "2026-05-14T11:50:00.000Z" }),
          issue({ description: "Issue 2", last_seen_at: "2026-05-14T11:40:00.000Z" }),
          issue({ description: "Issue 3", last_seen_at: "2026-05-14T11:30:00.000Z" }),
          issue({ description: "Issue 4", last_seen_at: "2026-05-14T11:20:00.000Z" }),
          issue({ description: "Old issue", last_seen_at: "2026-05-12T11:00:00.000Z" }),
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

  it("uses first-seen recency when last-seen is missing", () => {
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
      },
    );

    expect(model.attentionRows[0]?.detail).toContain("first seen 2h ago");
    expect(model.attentionRows[0]?.lastSeenAt).toBeNull();
  });

  it("keeps issue rows current when timestamps are missing", () => {
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

    expect(model.attentionRows).toHaveLength(1);
    expect(model.attentionRows[0]).toMatchObject({
      kind: "issue",
      title: "General issue",
      count: undefined,
    });
    expect(model.attentionRows[0]?.detail).not.toContain("seen");
    expect(model.hiddenOldIssueGroups).toBe(0);
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
    expect(zeroModel.attentionRows.some((row) => row.kind === "approval")).toBe(false);
    expect(zeroModel.nowRows.some((row) => row.kind === "approval")).toBe(false);

    const pendingModel = deriveOverviewTriageModel({
      approvalMetrics: approvalMetrics({ total_pending: 4 }),
    });
    expect(pendingModel.kpis.pendingApprovals).toBe(4);
    expect(pendingModel.attentionRows.find((row) => row.kind === "approval")).toMatchObject({
      title: "4 pending approvals",
      href: "/approvals",
    });
    expect(pendingModel.nowRows.find((row) => row.kind === "approval")).toMatchObject({
      label: "4 pending approvals",
    });
  });

  it("derives stale heartbeat attention and enriched butler index metadata", () => {
    const model = deriveOverviewTriageModel(
      {
        butlers: [
          butler({
            name: "health",
            sessions_24h: 7,
            last_session_started_at: "2026-05-14T08:30:00.000Z",
          }),
        ],
        costs: {
          total_cost_usd: 1.2,
          total_sessions: 7,
          total_input_tokens: 10,
          total_output_tokens: 20,
          by_butler: { health: 0.123 },
          by_model: {},
        },
        heartbeats: {
          butlers: [
            {
              name: "health",
              last_heartbeat_at: "2026-05-14T11:40:00.000Z",
              last_session_at: "2026-05-14T11:30:00.000Z",
              active_session_count: 0,
              heartbeat_age_seconds: 1_200,
            },
          ],
        },
      },
      { staleHeartbeatSeconds: 300 },
    );

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
    expect(model.attentionRows.find((row) => row.kind === "runtime")).toMatchObject({
      title: "health heartbeat is stale",
      detail: "Last heartbeat 20m ago",
    });
  });

  it("maps healthy statuses to KPIs and active heartbeat metadata to the index", () => {
    const model = deriveOverviewTriageModel({
      butlers: [
        butler({ name: "general", status: "ok", sessions_24h: 3 }),
        butler({ name: "health", status: "ok", sessions_24h: 4 }),
      ],
      heartbeats: {
        butlers: [
          {
            name: "general",
            last_heartbeat_at: "2026-05-14T11:59:00.000Z",
            last_session_at: "2026-05-14T11:55:00.000Z",
            active_session_count: 2,
            heartbeat_age_seconds: 30,
          },
          {
            name: "health",
            last_heartbeat_at: "2026-05-14T11:58:00.000Z",
            last_session_at: null,
            active_session_count: 0,
            heartbeat_age_seconds: 60,
          },
        ],
      },
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
      butlers: [butler({ name: "relationship", last_session_started_at: null })],
      heartbeats: {
        butlers: [
          {
            name: "relationship",
            last_heartbeat_at: "2026-05-14T11:59:00.000Z",
            last_session_at: null,
            active_session_count: 0,
            heartbeat_age_seconds: 30,
          },
        ],
      },
    });

    expect(model.operationsRows[0]).toMatchObject({
      name: "relationship",
      lastSessionAt: null,
      heartbeatAgeSeconds: 30,
      runtimeState: "healthy",
    });
  });

  it("derives notification failure pressure", () => {
    const model = deriveOverviewTriageModel({
      notificationStats: notificationStats({ total: 9, sent: 7, failed: 2 }),
    });

    expect(model.attentionRows.find((row) => row.kind === "notification")).toMatchObject({
      title: "2 failed notifications",
      href: "/notifications",
      count: 2,
    });
    expect(model.nowRows.find((row) => row.kind === "notification")).toMatchObject({
      label: "2 failed notifications",
    });
  });

  it("keeps QA clean states quiet and surfaces QA error states", () => {
    const cleanModel = deriveOverviewTriageModel({
      qaSummary: qaSummary(),
    });
    expect(cleanModel.attentionRows.some((row) => row.kind === "qa")).toBe(false);
    expect(cleanModel.nowRows.some((row) => row.kind === "qa")).toBe(false);

    const errorModel = deriveOverviewTriageModel({
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
    });

    expect(errorModel.attentionRows.find((row) => row.kind === "qa")).toMatchObject({
      severity: "high",
      title: "QA patrol failed",
      detail: "log scanner failed",
    });
    expect(errorModel.nowRows.find((row) => row.kind === "qa")).toMatchObject({
      label: "QA patrol failed",
    });
  });

  it("uses current butlers only for promoted runtime KPIs", () => {
    const model = deriveOverviewTriageModel({
      butlers: [
        butler({ name: "general", status: "ok", sessions_24h: 3 }),
        butler({ name: "health", status: "degraded", sessions_24h: 2 }),
        butler({
          name: "switchboard",
          status: "online",
          type: "staffer",
          sessions_24h: 10,
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
});
