/**
 * DashboardPage -- editorial archetype landing for the Overview page.
 *
 * Replaces the vertical-D overview layout with the two-column editorial
 * archetype: left column carries the narrative (date eyebrow, Display
 * headline, Voice paragraph, attention list, KPI strip) and right column
 * carries the index (ButlerIndex, NextList).
 *
 * Layout: two columns 1.4fr / 1fr, gap 56px.
 * Frame: <Page archetype="editorial"> (max-width 1280px, padding 48px 56px).
 *
 * Data:
 *   useBriefing()           -- DateEyebrow, BriefingStatus, Headline, Elaboration
 *   useIssues()             -- AttentionList
 *   useButlers()            -- ButlerIndex, RuntimeSummaryKpi
 *   useCostSummary("today") -- ButlerIndex per-butler cost
 *   useApprovalMetrics()    -- RuntimeSummaryKpi "approvals" cell, NextList pending approvals
 *
 * bu-1fpvp.2 -- Frontend: replace DashboardPage with editorial layout.
 * bu-bm58r.1 -- Runtime summary KPI card from existing hooks.
 */

import { Page } from "@/components/ui/page";
import { Link } from "react-router";
import type { ReactNode } from "react";
import { useBriefing } from "@/hooks/use-briefing";
import { useButlers } from "@/hooks/use-butlers";
import { useCostSummary } from "@/hooks/use-costs";
import { useIssues } from "@/hooks/use-issues";
import { useApprovalMetrics } from "@/hooks/use-approvals";
import { useQaSummary } from "@/hooks/use-qa";

import { AttentionList } from "@/components/overview/AttentionList";
import { BriefingStatus } from "@/components/overview/BriefingStatus";
import { ButlerIndex } from "@/components/overview/ButlerIndex";
import { DateEyebrow } from "@/components/overview/DateEyebrow";
import { Elaboration } from "@/components/overview/Elaboration";
import { Headline } from "@/components/overview/Headline";
import { NextList } from "@/components/overview/NextList";
import { RuntimeSummaryKpi } from "@/components/overview/RuntimeSummaryKpi";
import { Section } from "@/components/overview/Section";
import type { QaSummary } from "@/api/types";

type QaWidgetStatus = "running" | "tripped" | "stopped";

function normalizeQaStatus(summary: QaSummary): QaWidgetStatus {
  if (summary.circuit_breaker.tripped) return "tripped";

  const raw = summary.staffer_status.toLowerCase();
  if (raw.includes("trip")) return "tripped";
  if (
    raw.includes("run") ||
    raw.includes("active") ||
    raw.includes("online") ||
    raw.includes("healthy") ||
    raw === "ok"
  ) {
    return "running";
  }
  return "stopped";
}

function formatPatrolTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

function QaWidgetRow({
  label,
  children,
  first = false,
}: {
  label: string;
  children: ReactNode;
  first?: boolean;
}) {
  return (
    <div
      role="listitem"
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto",
        alignItems: "center",
        gap: "12px",
        paddingTop: "10px",
        paddingBottom: "10px",
        borderTop: first ? "1px solid var(--border)" : undefined,
        borderBottom: "1px solid var(--border)",
      }}
    >
      <span
        className="tnum"
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "11px",
          color: "var(--muted-foreground)",
          lineHeight: 1.4,
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function QaWidgetMonoValue({ children }: { children: ReactNode }) {
  return (
    <span
      className="tnum"
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "11px",
        color: "var(--foreground)",
        lineHeight: 1.4,
      }}
    >
      {children}
    </span>
  );
}

function QaStafferWidget({ summary }: { summary: QaSummary | null | undefined }) {
  const status = summary ? normalizeQaStatus(summary) : "stopped";
  const lastPatrol = summary?.last_patrol ?? null;

  return (
    <Section eyebrow="QA staffer">
      {!summary || status === "stopped" || !lastPatrol ? (
        <p
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: "14px",
            fontStyle: "italic",
            color: "var(--muted-foreground)",
            paddingTop: "10px",
            paddingBottom: "10px",
          }}
        >
          QA staffer not active
        </p>
      ) : (
        <div role="list" aria-label="QA staffer summary">
          <QaWidgetRow label="status" first>
            <QaWidgetMonoValue>{status}</QaWidgetMonoValue>
          </QaWidgetRow>

          <QaWidgetRow label="last patrol">
            <QaWidgetMonoValue>
              {formatPatrolTime(lastPatrol.started_at)} · {lastPatrol.status}
            </QaWidgetMonoValue>
          </QaWidgetRow>

          <QaWidgetRow label="active cases · now">
            <span
              className="tnum"
              style={{
                fontFamily: "var(--font-sans)",
                fontSize: "20px",
                fontWeight: 500,
                color: "var(--foreground)",
                lineHeight: 1,
              }}
            >
              {summary.kpis.active_cases_now}
            </span>
          </QaWidgetRow>

          <div role="listitem">
            <Link
              to="/qa"
              style={{
                display: "grid",
                gridTemplateColumns: "1fr auto",
                alignItems: "center",
                gap: "12px",
                paddingTop: "10px",
                paddingBottom: "10px",
                color: "var(--muted-foreground)",
                textDecoration: "none",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <span
                style={{
                  fontFamily: "var(--font-sans)",
                  fontSize: "13px",
                  fontWeight: 500,
                  color: "var(--foreground)",
                  lineHeight: 1.4,
                }}
              >
                Open QA
              </span>
              <span aria-hidden="true" style={{ fontSize: "16px", lineHeight: 1 }}>
                →
              </span>
            </Link>
          </div>
        </div>
      )}
    </Section>
  );
}

export default function DashboardPage() {
  // Briefing
  const {
    data: briefing,
    isFetching: briefingFetching,
    refetch: refetchBriefing,
  } = useBriefing();

  // Supporting data
  const { data: butlersResponse } = useButlers();
  const { data: costSummaryResponse } = useCostSummary("today");
  const { data: issuesResponse } = useIssues();
  const { data: approvalMetricsResponse } = useApprovalMetrics();
  const { data: qaSummaryResponse } = useQaSummary();

  // Derived values
  const butlers = butlersResponse?.data ?? [];
  const issues = issuesResponse?.data ?? [];
  const byButler = costSummaryResponse?.data.by_butler ?? {};
  const pendingApprovals = approvalMetricsResponse?.data.total_pending ?? 0;
  const qaSummary = qaSummaryResponse?.data;

  // Butler index rows: join butlers with cost data and 24h session counts
  const butlerIndexEntries = butlers
    .filter((b) => b.type === "butler")
    .map((b) => ({
      name: b.name,
      sessions: b.sessions_24h ?? 0,
      costUsd: byButler[b.name] ?? 0,
    }));

  // NextList: show pending approvals as upcoming items when available
  const nextItems =
    pendingApprovals > 0
      ? [
          {
            time: "now",
            label: `${pendingApprovals} pending approval${pendingApprovals === 1 ? "" : "s"}`,
            kind: "approval",
          },
        ]
      : [];

  // Briefing headline and greet with safe fallbacks
  const greet = briefing?.greet ?? "Good morning.";
  const headline = briefing?.headline ?? "Checking in.";
  const elaboration =
    briefing?.elaboration ??
    "Butlers are running. Check back in a moment for a fresh briefing.";

  return (
    <Page archetype="editorial" title="Overview">
      {/* Two-column editorial grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1.4fr 1fr",
          gap: "56px",
          alignItems: "start",
        }}
      >
        {/* Left column: narrative */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "28px" }}
          aria-label="Briefing"
        >
          {/* Date eyebrow with briefing status pill */}
          <DateEyebrow
            statusSlot={
              <BriefingStatus
                source={briefing?.source}
                generatedAt={briefing?.generated_at}
                isFetching={briefingFetching}
                onRefetch={() => { void refetchBriefing(); }}
              />
            }
          />

          {/* Display headline */}
          <Headline greet={greet} body={headline} />

          {/* Voice elaboration paragraph */}
          <Elaboration text={elaboration} isFetching={briefingFetching} />

          {/* Attention list */}
          <AttentionList items={issues} />

          {/* Runtime summary KPI: total / healthy / sessions_24h / pending approvals */}
          <RuntimeSummaryKpi />
        </div>

        {/* Right column: index */}
        <div
          style={{ display: "flex", flexDirection: "column", gap: "32px" }}
          aria-label="Butler index"
        >
          <ButlerIndex butlers={butlerIndexEntries} />
          <NextList items={nextItems} />
          <QaStafferWidget summary={qaSummary} />
        </div>
      </div>
    </Page>
  );
}
