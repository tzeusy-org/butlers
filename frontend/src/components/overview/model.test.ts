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
    last_patrol: null,
    stats_24h: {
      patrols_completed: 1,
      total_findings: 0,
      novel_findings: 0,
      dispatched_investigations: 0,
    },
    stats_all_time: {
      total_patrols: 1,
      total_findings: 0,
      novel_findings: 0,
      dispatched_investigations: 0,
    },
    active_sources: [],
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
    expect(model.attentionRows.find((row) => row.title === "Old high issue")).toBeUndefined();
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
    expect(model.attentionRows[0]?.detail).toContain("open for 2d");
  });

  it("uses local calendar days for issue age labels", () => {
    const model = deriveOverviewTriageModel(
      {
        issues: [
          issue({
            first_seen_at: new Date(2026, 4, 14, 23, 30).toISOString(),
            last_seen_at: new Date(2026, 4, 14, 23, 30).toISOString(),
          }),
        ],
      },
      {
        now: new Date(2026, 4, 15, 0, 30),
        includeOldIssueRows: true,
        recentIssueHours: 48,
      },
    );

    expect(model.attentionRows[0]?.detail).toContain("open for 1d");
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
