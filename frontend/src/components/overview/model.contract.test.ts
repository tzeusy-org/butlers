/**
 * Cross-surface consistency test (bu-gcz9e.2): headline state_class implies
 * attention-row-count/severity bounds, pinned from SHARED fixtures.
 *
 * See tests/dashboard/test_briefing_attention_contract.py for the full
 * rationale and the backend half of this contract. Both files read the same
 * named scenarios from
 * ./__fixtures__/attention-contract-scenarios.json and assert the SAME
 * scenario's bounds against their own runtime's derivation function --
 * `classify()` there, `deriveOverviewTriageModel()` here. A future change
 * that lets the briefing headline and the Overview attention list drift
 * apart (e.g. the headline says "busy" while the list renders "Nothing
 * waiting.") fails one of the two files.
 */
import { describe, expect, it } from "vitest";

import type {
  BoardRow,
  Issue,
  NotificationStats,
  QaSummary,
} from "@/api/types";
import scenarios from "./__fixtures__/attention-contract-scenarios.json";
import { deriveOverviewTriageModel, type OverviewSeverity } from "./model";

const NOW = new Date("2026-07-12T12:00:00.000Z");

// Mirrors model.ts's internal OVERVIEW_SEVERITY_RANK (not exported -- this
// is a fixed 5-value vocabulary owned by this same file's contract, so a
// small duplicated copy here is lower-risk than exporting an internal
// implementation constant purely for test use).
const SEVERITY_RANK: Record<OverviewSeverity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

function boardRow(
  overrides: Partial<BoardRow> & Pick<BoardRow, "name" | "activity">,
): BoardRow {
  return {
    type: "butler",
    description: null,
    status: "ok",
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

interface ContractScenario {
  name: string;
  board_rows: Array<{ name: string; activity: BoardRow["activity"] }>;
  board_source_error: boolean;
  approvals_pending: number;
  failed_notifications: number;
  notifications_source_available: boolean;
  issues?: Array<
    Pick<
      Issue,
      | "severity"
      | "type"
      | "butler"
      | "description"
      | "first_seen_at"
      | "last_seen_at"
    >
  >;
  qa: {
    last_patrol_failed: boolean;
    novel_findings: number;
    dispatched_investigations: number;
    active_cases_now?: number;
    circuit_breaker_tripped?: boolean;
    circuit_breaker_consecutive_failures?: number;
  } | null;
  expect: {
    backend_state_class: string;
    min_attention_rows?: number;
    max_attention_rows?: number;
    min_top_severity_rank?: OverviewSeverity;
    max_severity_rank?: OverviewSeverity;
    requires_degraded_signal?: boolean;
    requires_notification_source_row?: boolean;
    requires_butlers_source_row?: boolean;
    hidden_old_issue_groups?: number;
    requires_qa_activity?: boolean;
  };
}

const SCENARIOS = scenarios.scenarios as ContractScenario[];

describe("attention contract (bu-gcz9e.2, shared fixtures)", () => {
  it("loads a nonempty, uniquely-named scenario set", () => {
    // A silently-empty/broken fixture file would make every it.each below
    // vacuously pass -- guard against that, mirroring the backend's
    // test_fixture_file_is_nonempty / test_every_scenario_has_a_unique_name.
    expect(SCENARIOS.length).toBeGreaterThanOrEqual(5);
    expect(new Set(SCENARIOS.map((s) => s.name)).size).toBe(SCENARIOS.length);
  });

  it.each(SCENARIOS)("$name", (scenario) => {
    const model = deriveOverviewTriageModel(
      {
        boardRows: scenario.board_rows.map((r) => boardRow(r)),
        butlersError: scenario.board_source_error,
        approvalMetrics: {
          total_pending: scenario.approvals_pending,
          total_approved_today: 0,
          total_rejected_today: 0,
          total_auto_approved_today: 0,
          total_expired_today: 0,
          avg_decision_latency_seconds: null,
          auto_approval_rate: 0,
          rejection_rate: 0,
          failure_count_today: 0,
          active_rules_count: 0,
        },
        notificationStats: notificationStats({
          failed: scenario.failed_notifications,
          source_available: scenario.notifications_source_available,
        }),
        qaSummary: scenario.qa
          ? qaSummary({
              circuit_breaker: {
                tripped: scenario.qa.circuit_breaker_tripped ?? false,
                consecutive_failures:
                  scenario.qa.circuit_breaker_consecutive_failures ?? 0,
              },
              last_patrol: {
                id: "patrol-contract-1",
                started_at: "2026-07-12T10:00:00.000Z",
                completed_at: "2026-07-12T10:05:00.000Z",
                status: scenario.qa.last_patrol_failed ? "failed" : "completed",
                findings_count: scenario.qa.novel_findings,
                novel_count: scenario.qa.novel_findings,
                dispatched_count: scenario.qa.dispatched_investigations,
                log_lookback_minutes: 60,
                sources_polled: ["sessions"],
                error_detail: null,
              },
              stats_24h: {
                patrols_completed: 1,
                total_findings: scenario.qa.novel_findings,
                novel_findings: scenario.qa.novel_findings,
                dispatched_investigations:
                  scenario.qa.dispatched_investigations,
                prs_opened: 0,
              },
              kpis: {
                ...qaSummary().kpis,
                active_cases_now: scenario.qa.active_cases_now ?? 0,
              },
            })
          : null,
        issues: (scenario.issues ?? []).map((issue, index) => ({
          ...issue,
          link: "/issues",
          issue_key: `contract:${scenario.name}:${index}`,
        })),
      },
      { now: NOW },
    );

    const rows = model.attentionRows;
    const { expect: bounds } = scenario;

    if (bounds.min_attention_rows !== undefined) {
      expect(
        rows.length,
        `scenario ${scenario.name}: expected >= ${bounds.min_attention_rows} attention rows, got ${rows.length}`,
      ).toBeGreaterThanOrEqual(bounds.min_attention_rows);
    }
    if (bounds.max_attention_rows !== undefined) {
      expect(
        rows.length,
        `scenario ${scenario.name}: expected <= ${bounds.max_attention_rows} attention rows, got ${rows.length}`,
      ).toBeLessThanOrEqual(bounds.max_attention_rows);
    }
    if (bounds.min_top_severity_rank !== undefined) {
      expect(
        rows.length,
        `scenario ${scenario.name}: expected at least one row`,
      ).toBeGreaterThan(0);
      const topRank = Math.min(...rows.map((r) => SEVERITY_RANK[r.severity]));
      expect(
        topRank,
        `scenario ${scenario.name}: expected top severity at least as severe as ` +
          `${bounds.min_top_severity_rank}, rows were ${JSON.stringify(rows.map((r) => r.severity))}`,
      ).toBeLessThanOrEqual(SEVERITY_RANK[bounds.min_top_severity_rank]);
    }
    if (bounds.max_severity_rank !== undefined) {
      const worstAllowedRank = SEVERITY_RANK[bounds.max_severity_rank];
      for (const row of rows) {
        expect(
          SEVERITY_RANK[row.severity],
          `scenario ${scenario.name}: row ${row.id} severity ${row.severity} exceeds cap ${bounds.max_severity_rank}`,
        ).toBeGreaterThanOrEqual(worstAllowedRank);
      }
    }
    if (bounds.requires_degraded_signal) {
      // Mirrors the fleet degraded-source convention (butlers/CLAUDE.md): a
      // source that failed to answer must never render as a truthful
      // all-clear. On the Overview page that means EITHER a dedicated
      // source-error attention row, OR one of the top-level *Error flags
      // that another surface (KPI strip, Operations index) reads.
      const hasDegradedSignal =
        rows.some((r) => r.isSourceError) ||
        model.butlersError ||
        model.issuesError;
      expect(
        hasDegradedSignal,
        `scenario ${scenario.name}: backend classifies "degraded" but the Overview model ` +
          `shows no source-error row and no *Error flag -- the attention list would render ` +
          `"Nothing waiting." beneath a headline saying the picture may be incomplete`,
      ).toBe(true);
    }
    if (bounds.requires_notification_source_row) {
      expect(
        rows.some((r) => r.kind === "notification" && r.isSourceError),
        `scenario ${scenario.name}: expected a notification source-error row`,
      ).toBe(true);
    }
    if (bounds.requires_butlers_source_row) {
      // Stricter than requires_degraded_signal: this is the exact defect the
      // bead pins -- a failed board fetch (the same fetch the briefing
      // headline classifies "degraded" from) must produce a row in
      // attentionRows itself, not just the separate butlersError flag that
      // only the KPI strip and Operations index read. Without it, the
      // Needs-attention list renders "Nothing waiting." beneath a headline
      // saying the picture may be incomplete.
      expect(
        rows.some((r) => r.kind === "runtime" && r.isSourceError),
        `scenario ${scenario.name}: expected a butlers/board source-error row in attentionRows`,
      ).toBe(true);
    }
    if (bounds.hidden_old_issue_groups !== undefined) {
      expect(model.hiddenOldIssueGroups).toBe(bounds.hidden_old_issue_groups);
    }
    if (bounds.requires_qa_activity) {
      expect(
        model.nowRows.some(
          (row) =>
            row.kind === "qa" && row.label.includes("in the last 24 hours"),
        ),
        `scenario ${scenario.name}: expected a time-bounded QA activity row`,
      ).toBe(true);
    }
  });
});
