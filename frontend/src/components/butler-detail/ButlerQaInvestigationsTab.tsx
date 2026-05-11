// ---------------------------------------------------------------------------
// ButlerQaInvestigationsTab — bu-iuol4.28
//
// Investigations bespoke tab for the QA butler detail page.
//
// Four sections (4-col grid):
//   1. KPI quartet (row 1, span 4)  — open count / closed in 24h / patrols in 24h / MTTR 24h
//   2. Patrol cadence stripe (row 2, span 4) — recent patrols in 24h
//   3. Recent investigations table (row 3, span 4) — last 5 investigations
//   4. Selected investigation panel (row 4, span 4) — inline detail + link to full page
//
// All data comes from existing hooks. No new HTTP routes.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { Link } from "react-router";

import type { QaInvestigation, QaPatrolSummary } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Time } from "@/components/ui/time";
import {
  useQaCircuitBreaker,
  useQaInvestigations,
  useQaPatrols,
  useQaSummary,
} from "@/hooks/use-qa";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Compact relative age — e.g. "just now", "5m ago", "2h ago", "3d ago". */
function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** Format seconds to a human-readable MTTR string (e.g. "4h 12m"). */
function formatMttr(seconds: number | null | undefined): string {
  if (seconds == null || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

// ---------------------------------------------------------------------------
// Severity badge — numeric → label/colour
// ---------------------------------------------------------------------------

const SEV_LABELS: Record<number, string> = {
  0: "critical",
  1: "high",
  2: "medium",
  3: "low",
  4: "info",
};

const SEV_CLASS: Record<number, string> = {
  0: "bg-red-600 text-white hover:bg-red-600/90",
  1: "bg-orange-500 text-white hover:bg-orange-500/90",
  2: "bg-yellow-500 text-white hover:bg-yellow-500/90",
  3: "bg-slate-400 text-white hover:bg-slate-400/90",
  4: "bg-sky-400 text-white hover:bg-sky-400/90",
};

function SeverityBadge({ severity }: { severity: number }) {
  const label = SEV_LABELS[severity] ?? String(severity);
  const cls = SEV_CLASS[severity] ?? "";
  return (
    <Badge className={cls} data-testid="severity-badge">
      {label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Investigation status badge
// ---------------------------------------------------------------------------

const STATUS_CONFIG: Record<string, { label: string; className: string }> = {
  dispatch_pending: { label: "pending", className: "border-slate-400 text-slate-500" },
  investigating: { label: "investigating", className: "border-amber-500 text-amber-600" },
  pr_open: { label: "PR open", className: "border-blue-500 text-blue-600" },
  pr_merged: { label: "PR merged", className: "bg-emerald-600 text-white hover:bg-emerald-600/90" },
  failed: { label: "failed", className: "" },
  timeout: { label: "timeout", className: "" },
  unfixable: { label: "unfixable", className: "" },
  anonymization_failed: { label: "anon failed", className: "" },
};

const TERMINAL_STATUSES = new Set(["failed", "timeout", "unfixable", "anonymization_failed"]);

function StatusBadge({ status }: { status: string }) {
  const c = STATUS_CONFIG[status];
  if (!c) return <Badge variant="outline">{status}</Badge>;
  if (status === "pr_merged") return <Badge className={c.className}>{c.label}</Badge>;
  if (TERMINAL_STATUSES.has(status)) return <Badge variant="destructive">{c.label}</Badge>;
  return (
    <Badge variant="outline" className={c.className}>
      {c.label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Section 1: KPI quartet
// ---------------------------------------------------------------------------

interface KpiQuartetProps {
  openCount: number;
  closedIn24h: number;
  patrolsIn24h: number;
  mttrSeconds: number | null;
  isLoading: boolean;
}

function KpiQuartet({
  openCount,
  closedIn24h,
  patrolsIn24h,
  mttrSeconds,
  isLoading,
}: KpiQuartetProps) {
  const kpis = [
    {
      label: "Open investigations",
      value: isLoading ? "…" : String(openCount),
      testId: "kpi-open",
    },
    {
      label: "Closed (24h)",
      value: isLoading ? "…" : String(closedIn24h),
      testId: "kpi-closed-24h",
    },
    {
      label: "Patrols (24h)",
      value: isLoading ? "…" : String(patrolsIn24h),
      testId: "kpi-patrols-24h",
    },
    {
      label: "MTTR (24h)",
      value: isLoading ? "…" : formatMttr(mttrSeconds),
      testId: "kpi-mttr",
    },
  ];

  return (
    <div
      className="grid grid-cols-2 gap-3 lg:grid-cols-4"
      data-testid="qa-kpi-quartet"
    >
      {kpis.map((kpi) => (
        <Card key={kpi.label}>
          <CardContent className="pt-4">
            <p className="text-xs text-muted-foreground">{kpi.label}</p>
            <p
              className="mt-0.5 font-mono text-2xl font-bold tabular-nums truncate"
              data-testid={kpi.testId}
            >
              {kpi.value}
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 2: Patrol cadence stripe (24h)
// ---------------------------------------------------------------------------

interface PatrolStripeProps {
  patrols: QaPatrolSummary[];
  isLoading: boolean;
}

function PatrolStatusChip({ status }: { status: string }) {
  if (status === "clean") {
    return (
      <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90 text-xs">
        clean
      </Badge>
    );
  }
  if (status === "findings_dispatched") {
    return (
      <Badge className="bg-blue-600 text-white hover:bg-blue-600/90 text-xs">
        dispatched
      </Badge>
    );
  }
  if (status === "running") {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600 text-xs">
        running
      </Badge>
    );
  }
  if (status === "error") return <Badge variant="destructive" className="text-xs">error</Badge>;
  if (status === "skipped_overlap") return <Badge variant="secondary" className="text-xs">skipped</Badge>;
  return <Badge variant="outline" className="text-xs">{status}</Badge>;
}

function PatrolCadenceStripe({ patrols, isLoading }: PatrolStripeProps) {
  if (isLoading && patrols.length === 0) {
    return (
      <Card data-testid="patrol-cadence-stripe">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent patrols · 24h</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2" data-testid="patrol-stripe-loading">
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="flex items-center gap-3" data-testid="loading-line">
                <Skeleton className="h-4 w-24 rounded" />
                <Skeleton className="h-5 w-16 rounded-full" />
                <Skeleton className="h-4 w-12 rounded" />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card data-testid="patrol-cadence-stripe">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Recent patrols · 24h</CardTitle>
      </CardHeader>
      <CardContent>
        {patrols.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No patrols recorded in the last 24 hours.
          </p>
        ) : (
          <ul className="divide-y" data-testid="patrol-stripe-list" aria-label="Patrol cadence">
            {patrols.slice(0, 8).map((patrol) => (
              <li
                key={patrol.id}
                className="flex items-center gap-3 py-2 text-sm"
                data-testid="patrol-stripe-row"
              >
                <span className="text-xs text-muted-foreground tabular-nums min-w-[72px]">
                  {formatRelative(patrol.started_at)}
                </span>
                <PatrolStatusChip status={patrol.status} />
                <span className="text-xs text-muted-foreground tabular-nums">
                  {patrol.findings_count} finding{patrol.findings_count !== 1 ? "s" : ""}
                  {patrol.novel_count > 0 && (
                    <> · {patrol.novel_count} novel</>
                  )}
                </span>
                <Link
                  to={`/qa/patrols/${patrol.id}`}
                  className="ml-auto text-xs text-primary underline-offset-4 hover:underline font-mono"
                  aria-label="View patrol detail"
                >
                  {patrol.id.slice(0, 8)}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 3: Recent investigations table
// ---------------------------------------------------------------------------

interface RecentInvestigationsTableProps {
  investigations: QaInvestigation[];
  isLoading: boolean;
  selectedId: string | null;
  onSelect: (inv: QaInvestigation) => void;
}

function RecentInvestigationsTable({
  investigations,
  isLoading,
  selectedId,
  onSelect,
}: RecentInvestigationsTableProps) {
  return (
    <Card data-testid="recent-investigations-card">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Recent investigations</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && investigations.length === 0 ? (
          <div className="space-y-2" data-testid="investigations-loading">
            {Array.from({ length: 5 }, (_, i) => (
              <div key={i} className="flex items-center gap-3" data-testid="loading-line">
                <Skeleton className="h-4 w-16 rounded" />
                <Skeleton className="h-5 w-14 rounded-full" />
                <Skeleton className="h-4 w-20 rounded" />
                <Skeleton className="h-4 w-24 rounded" />
              </div>
            ))}
          </div>
        ) : investigations.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="empty-state-line">
            No investigations found.
          </p>
        ) : (
          <div className="overflow-x-auto" data-testid="investigations-table">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-xs text-muted-foreground">
                  <th className="py-1.5 pr-3 text-left font-medium">ID</th>
                  <th className="py-1.5 pr-3 text-left font-medium">Sev</th>
                  <th className="py-1.5 pr-3 text-left font-medium">Title</th>
                  <th className="py-1.5 pr-3 text-left font-medium">Butler</th>
                  <th className="py-1.5 pr-3 text-left font-medium">Age</th>
                  <th className="py-1.5 text-left font-medium">State</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {investigations.slice(0, 5).map((inv) => (
                  <tr
                    key={inv.id}
                    className={`cursor-pointer transition-colors hover:bg-muted/50 ${
                      selectedId === inv.id ? "bg-muted" : ""
                    }`}
                    onClick={() => onSelect(inv)}
                    data-testid="investigation-row"
                    aria-selected={selectedId === inv.id}
                  >
                    <td className="py-2 pr-3">
                      <span className="font-mono text-xs text-muted-foreground tabular-nums">
                        {inv.id.slice(0, 8)}
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      <SeverityBadge severity={inv.severity} />
                    </td>
                    <td className="py-2 pr-3 max-w-[200px] truncate text-xs font-medium">
                      {inv.exception_type}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge variant="outline" className="font-mono text-xs">
                        {inv.butler_name}
                      </Badge>
                    </td>
                    <td className="py-2 pr-3 text-xs text-muted-foreground tabular-nums whitespace-nowrap">
                      <Time value={inv.created_at} mode="relative" />
                    </td>
                    <td className="py-2">
                      <StatusBadge status={inv.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 4: Selected investigation inline panel
// ---------------------------------------------------------------------------

interface InvestigationDetailPanelProps {
  investigation: QaInvestigation;
  onClose: () => void;
}

function InvestigationDetailPanel({
  investigation: inv,
  onClose,
}: InvestigationDetailPanelProps) {
  return (
    <Card data-testid="investigation-detail-panel">
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="space-y-1 min-w-0">
            <CardTitle className="text-sm font-medium">Investigation detail</CardTitle>
            <p className="font-mono text-xs text-muted-foreground tabular-nums">
              {inv.id}
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-xs text-muted-foreground hover:text-foreground shrink-0"
            aria-label="Close investigation detail"
          >
            Close
          </button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <SeverityBadge severity={inv.severity} />
            <StatusBadge status={inv.status} />
            <Badge variant="outline" className="font-mono text-xs">
              {inv.butler_name}
            </Badge>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-baseline gap-3">
              <span className="w-24 shrink-0 text-xs text-muted-foreground font-medium">
                Exception
              </span>
              <code className="text-xs break-all">{inv.exception_type}</code>
            </div>
            {inv.call_site && (
              <div className="flex items-baseline gap-3">
                <span className="w-24 shrink-0 text-xs text-muted-foreground font-medium">
                  Call site
                </span>
                <code className="text-xs break-all text-muted-foreground">{inv.call_site}</code>
              </div>
            )}
            {inv.sanitized_msg && (
              <div className="flex items-baseline gap-3">
                <span className="w-24 shrink-0 text-xs text-muted-foreground font-medium">
                  Message
                </span>
                <span className="text-xs break-all">{inv.sanitized_msg}</span>
              </div>
            )}
            <div className="flex items-baseline gap-3">
              <span className="w-24 shrink-0 text-xs text-muted-foreground font-medium">
                Created
              </span>
              <span className="text-xs tabular-nums">
                <Time value={inv.created_at} mode="absolute" />
              </span>
            </div>
            {inv.pr_number && (
              <div className="flex items-baseline gap-3">
                <span className="w-24 shrink-0 text-xs text-muted-foreground font-medium">
                  Pull request
                </span>
                {inv.pr_url ? (
                  <a
                    href={inv.pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-primary underline-offset-4 hover:underline"
                  >
                    #{inv.pr_number}
                  </a>
                ) : (
                  <span className="text-xs">#{inv.pr_number}</span>
                )}
              </div>
            )}
          </div>

          <div className="pt-1">
            <Link
              to={`/qa/investigations/${inv.id}`}
              className="text-xs text-primary underline-offset-4 hover:underline"
              data-testid="investigation-detail-link"
            >
              Open full investigation page
            </Link>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Circuit breaker status chip (inline summary)
// ---------------------------------------------------------------------------

function CircuitBreakerChip() {
  const { data, isLoading } = useQaCircuitBreaker();
  const status = data?.data;
  const tripped = status?.tripped ?? false;

  if (isLoading) return <Skeleton className="h-5 w-28 rounded-full" />;

  return tripped ? (
    <Badge variant="destructive" className="text-xs" data-testid="circuit-breaker-tripped">
      Circuit breaker: open
    </Badge>
  ) : (
    <Badge
      variant="outline"
      className="border-emerald-500 text-emerald-600 text-xs"
      data-testid="circuit-breaker-closed"
    >
      Circuit breaker: closed
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Compute open / closed-in-24h from investigation list
// ---------------------------------------------------------------------------

const OPEN_STATUSES = new Set([
  "dispatch_pending",
  "investigating",
  "pr_open",
]);

const CLOSED_STATUSES = new Set(["pr_merged", "failed", "timeout", "unfixable", "anonymization_failed"]);

function computeInvestigationKpis(investigations: QaInvestigation[]) {
  const now = Date.now();
  const cutoff24h = now - 24 * 60 * 60 * 1000;

  const openCount = investigations.filter((inv) => OPEN_STATUSES.has(inv.status)).length;

  const closedIn24h = investigations.filter(
    (inv) =>
      CLOSED_STATUSES.has(inv.status) &&
      inv.closed_at &&
      new Date(inv.closed_at).getTime() >= cutoff24h,
  ).length;

  // Rough MTTR: mean resolution time (closed_at - created_at) for items closed in 24h
  const resolvedItems = investigations.filter(
    (inv) =>
      inv.closed_at &&
      CLOSED_STATUSES.has(inv.status) &&
      new Date(inv.closed_at).getTime() >= cutoff24h,
  );

  let mttrSeconds: number | null = null;
  if (resolvedItems.length > 0) {
    const totalMs = resolvedItems.reduce((sum, inv) => {
      const ms =
        new Date(inv.closed_at!).getTime() - new Date(inv.created_at).getTime();
      return sum + Math.max(0, ms);
    }, 0);
    mttrSeconds = Math.round(totalMs / resolvedItems.length / 1000);
  }

  return { openCount, closedIn24h, mttrSeconds };
}

// ---------------------------------------------------------------------------
// ButlerQaInvestigationsTab — entry point
// ---------------------------------------------------------------------------

export default function ButlerQaInvestigationsTab() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: summaryResp, isLoading: summaryLoading } = useQaSummary();
  const { data: investigationsResp, isLoading: invLoading } = useQaInvestigations({ limit: 50 });
  const { data: patrolsResp, isLoading: patrolsLoading } = useQaPatrols({ limit: 24 });

  const summary = summaryResp?.data;
  const investigations = investigationsResp?.data ?? [];
  const patrols = patrolsResp?.data ?? [];

  const { openCount, closedIn24h, mttrSeconds } = computeInvestigationKpis(investigations);
  const patrolsIn24h = summary?.stats_24h.patrols_completed ?? 0;

  const kpiLoading = summaryLoading || invLoading;

  const selectedInvestigation = selectedId
    ? (investigations.find((inv) => inv.id === selectedId) ?? null)
    : null;

  function handleSelect(inv: QaInvestigation) {
    setSelectedId((current) => (current === inv.id ? null : inv.id));
  }

  function handleClose() {
    setSelectedId(null);
  }

  return (
    <div className="space-y-4 pt-4" data-testid="qa-investigations-tab">
      {/* Row 1: KPI quartet */}
      <KpiQuartet
        openCount={openCount}
        closedIn24h={closedIn24h}
        patrolsIn24h={patrolsIn24h}
        mttrSeconds={mttrSeconds}
        isLoading={kpiLoading}
      />

      {/* Row 1b: Circuit breaker status (inline chip) */}
      <div className="flex items-center gap-2">
        <CircuitBreakerChip />
      </div>

      {/* Row 2: Patrol cadence stripe (full-width, 24h) */}
      <PatrolCadenceStripe patrols={patrols} isLoading={patrolsLoading} />

      {/* Row 3: Recent investigations table (5 rows) */}
      <RecentInvestigationsTable
        investigations={investigations}
        isLoading={invLoading}
        selectedId={selectedId}
        onSelect={handleSelect}
      />

      {/* Row 4: Selected investigation inline detail panel */}
      {selectedInvestigation && (
        <InvestigationDetailPanel
          investigation={selectedInvestigation}
          onClose={handleClose}
        />
      )}
    </div>
  );
}
