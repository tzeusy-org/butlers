import { useState } from "react";
import { Link } from "react-router";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Page } from "@/components/ui/page";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDismissQaIssue,
  useForceQaPatrol,
  useQaCircuitBreaker,
  useQaInvestigations,
  useQaKnownIssues,
  useQaPatrols,
  useQaSummary,
  useQaTrends,
  useResetQaCircuitBreaker,
  useUndismissQaIssue,
} from "@/hooks/use-qa";
import type {
  QaInvestigation,
  QaKnownIssue,
  QaPatrolSummary,
  QaSummary,
  QaTrends,
} from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(start: string, end: string | null | undefined): string {
  if (!end) return "running...";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "--";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

// NOTE: formatRelative uses a custom compact format ("just now", "5m ago", "2h ago", "3d ago")
// that differs from date-fns formatDistanceToNow. Not migrated to <Time> -- kept intentionally.
function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "--";
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Severity badge
// ---------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: number }) {
  const labels: Record<number, string> = { 0: "critical", 1: "high", 2: "medium", 3: "low", 4: "info" };
  const label = labels[severity] ?? String(severity);
  const classNames: Record<number, string> = {
    0: "bg-red-600 text-white hover:bg-red-600/90",
    1: "bg-orange-500 text-white hover:bg-orange-500/90",
    2: "bg-yellow-500 text-white hover:bg-yellow-500/90",
    3: "bg-slate-400 text-white hover:bg-slate-400/90",
    4: "bg-sky-400 text-white hover:bg-sky-400/90",
  };
  return <Badge className={classNames[severity] ?? ""}>{label}</Badge>;
}

// ---------------------------------------------------------------------------
// Source type badge
// ---------------------------------------------------------------------------

function SourceTypeBadge({ sourceType }: { sourceType: string }) {
  const labels: Record<string, string> = {
    log_scanner: "log",
    session_records: "session",
    butler_reports: "butler",
  };
  return (
    <Badge variant="secondary" className="font-mono text-xs">
      {labels[sourceType] ?? sourceType}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Patrol status badge
// ---------------------------------------------------------------------------

function PatrolStatusBadge({ status }: { status: string }) {
  if (status === "clean") {
    return <Badge className="bg-emerald-600 text-white hover:bg-emerald-600/90">clean</Badge>;
  }
  if (status === "findings_dispatched") {
    return <Badge className="bg-blue-600 text-white hover:bg-blue-600/90">dispatched</Badge>;
  }
  if (status === "running") {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600">
        running
      </Badge>
    );
  }
  if (status === "error") {
    return <Badge variant="destructive">error</Badge>;
  }
  if (status === "skipped_overlap") {
    return <Badge variant="secondary">skipped</Badge>;
  }
  return <Badge variant="outline">{status}</Badge>;
}

// ---------------------------------------------------------------------------
// Stats card
// ---------------------------------------------------------------------------

function StatsCard({
  title,
  value,
  description,
}: {
  title: string;
  value: string | number;
  description?: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {description && <p className="text-muted-foreground mt-1 text-xs">{description}</p>}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Recent patrols table
// ---------------------------------------------------------------------------

function RecentPatrolsTable({ patrols }: { patrols: QaPatrolSummary[] }) {
  if (patrols.length === 0) {
    return (
      <EmptyState
        title="No patrol cycles recorded yet."
        description="The QA staffer records a cycle each time it runs."
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Started</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Sources</TableHead>
          <TableHead className="text-right">Findings</TableHead>
          <TableHead className="text-right">Novel</TableHead>
          <TableHead className="text-right">Dispatched</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {patrols.map((p) => (
          <TableRow key={p.id}>
            <TableCell>
              <Link
                to={`/qa/patrols/${p.id}`}
                className="text-primary font-mono text-xs underline-offset-4 hover:underline"
              >
                <Time value={p.started_at} mode="absolute" />
              </Link>
            </TableCell>
            <TableCell className="text-muted-foreground text-xs">
              {formatDuration(p.started_at, p.completed_at)}
            </TableCell>
            <TableCell>
              <PatrolStatusBadge status={p.status} />
            </TableCell>
            <TableCell>
              <div className="flex flex-wrap gap-1">
                {p.sources_polled.map((s) => (
                  <SourceTypeBadge key={s} sourceType={s} />
                ))}
              </div>
            </TableCell>
            <TableCell className="text-right">{p.findings_count}</TableCell>
            <TableCell className="text-right">{p.novel_count}</TableCell>
            <TableCell className="text-right">{p.dispatched_count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// Known issues panel
// ---------------------------------------------------------------------------

function KnownIssuesPanel() {
  const [showDismissed, setShowDismissed] = useState(false);
  const { data: response, isLoading, isError } = useQaKnownIssues({
    dismissed: showDismissed ? undefined : false,
    limit: 50,
  });
  const dismissMutation = useDismissQaIssue();
  const undismissMutation = useUndismissQaIssue();
  const [dismissingFp, setDismissingFp] = useState<string | null>(null);
  const issues = response?.data ?? [];

  if (isLoading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="text-destructive py-8 text-center text-sm">
        Failed to load known issues.
      </div>
    );
  }

  function handleDismiss(issue: QaKnownIssue) {
    setDismissingFp(issue.fingerprint);
    dismissMutation.mutate(
      { fingerprint: issue.fingerprint, body: { dismissed_by: "dashboard_user" } },
      { onSettled: () => setDismissingFp(null) },
    );
  }

  function handleUndismiss(issue: QaKnownIssue) {
    setDismissingFp(issue.fingerprint);
    undismissMutation.mutate(issue.fingerprint, { onSettled: () => setDismissingFp(null) });
  }

  const isDismissed = (issue: QaKnownIssue): boolean =>
    issue.dismissal !== null &&
    new Date(issue.dismissal.dismissed_until) > new Date();

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowDismissed((v) => !v)}
        >
          {showDismissed ? "Hide dismissed" : "Show dismissed"}
        </Button>
      </div>

      {issues.length === 0 && (
        <div className="text-muted-foreground py-8 text-center text-sm">
          {showDismissed ? "No known issues." : "No active issues. System is clean."}
        </div>
      )}

      {issues.map((issue) => {
        const dismissed = isDismissed(issue);
        return (
          <div
            key={issue.fingerprint}
            className={`rounded-md border p-3 text-sm ${dismissed ? "opacity-50" : ""}`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <SeverityBadge severity={issue.severity} />
                  <SourceTypeBadge sourceType={issue.source_type} />
                  <Badge variant="outline" className="font-mono text-xs">
                    {issue.source_butler}
                  </Badge>
                  <code className="text-muted-foreground text-xs">
                    {issue.fingerprint.slice(0, 12)}
                  </code>
                </div>
                <p className="text-muted-foreground truncate text-xs">{issue.exception_type}</p>
                <p className="truncate text-xs">{issue.event_summary}</p>
                <div className="text-muted-foreground text-xs">
                  {issue.occurrence_count} occurrence{issue.occurrence_count !== 1 ? "s" : ""} across{" "}
                  {issue.patrol_count} patrol{issue.patrol_count !== 1 ? "s" : ""} · last seen{" "}
                  {formatRelative(issue.last_seen)}
                </div>
                {issue.healing_attempt_id && (
                  <div className="text-xs">
                    <Link
                      to={`/qa/investigations/${issue.healing_attempt_id}`}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      View investigation
                    </Link>
                  </div>
                )}
              </div>
              <div className="flex shrink-0 gap-1">
                {dismissed ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={dismissingFp === issue.fingerprint}
                    onClick={() => handleUndismiss(issue)}
                  >
                    Restore
                  </Button>
                ) : (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={dismissingFp === issue.fingerprint}
                    onClick={() => handleDismiss(issue)}
                  >
                    Dismiss
                  </Button>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Investigation status badge (Kanban columns)
// ---------------------------------------------------------------------------

function InvestigationStatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; className: string }> = {
    dispatch_pending: { label: "pending", className: "border-slate-400 text-slate-500" },
    investigating: { label: "investigating", className: "border-amber-500 text-amber-600" },
    pr_open: { label: "PR open", className: "border-blue-500 text-blue-600" },
    pr_merged: {
      label: "PR merged",
      className: "bg-emerald-600 text-white hover:bg-emerald-600/90",
    },
    failed: { label: "failed", className: "" },
    timeout: { label: "timeout", className: "" },
    unfixable: { label: "unfixable", className: "" },
    anonymization_failed: { label: "anon failed", className: "" },
  };

  const c = config[status];
  if (!c) return <Badge variant="outline">{status}</Badge>;
  if (status === "pr_merged") return <Badge className={c.className}>{c.label}</Badge>;
  if (["failed", "timeout", "unfixable", "anonymization_failed"].includes(status)) {
    return <Badge variant="destructive">{c.label}</Badge>;
  }
  return (
    <Badge variant="outline" className={c.className}>
      {c.label}
    </Badge>
  );
}

// ---------------------------------------------------------------------------
// Investigation card (Kanban cell)
// ---------------------------------------------------------------------------

function InvestigationCard({ inv }: { inv: QaInvestigation }) {
  return (
    <Link to={`/qa/investigations/${inv.id}`}>
      <div className="hover:bg-muted/50 cursor-pointer rounded-md border p-3 text-sm transition-colors">
        <div className="mb-1 flex flex-wrap items-center gap-1.5">
          <InvestigationStatusBadge status={inv.status} />
          <SeverityBadge severity={inv.severity} />
        </div>
        <p className="truncate font-mono text-xs text-muted-foreground">{inv.exception_type}</p>
        <p className="mt-0.5 truncate text-xs">{inv.butler_name}</p>
        {inv.pr_number && (
          <p className="mt-0.5 text-xs text-blue-600">PR #{inv.pr_number}</p>
        )}
        <p className="mt-1 text-xs text-muted-foreground">{formatRelative(inv.created_at)}</p>
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Investigation pipeline (Kanban)
// ---------------------------------------------------------------------------

const KANBAN_COLUMNS: Array<{ status: string | null; label: string }> = [
  { status: "dispatch_pending", label: "Queued" },
  { status: "investigating", label: "Investigating" },
  { status: "pr_open", label: "PR Open" },
  { status: "pr_merged", label: "Merged" },
  { status: null, label: "Terminal" },
];

const TERMINAL_STATUSES = new Set(["failed", "timeout", "unfixable", "anonymization_failed"]);

function InvestigationPipeline() {
  const { data: response, isLoading, isError } = useQaInvestigations({ limit: 100 });
  const investigations = response?.data ?? [];

  if (isLoading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {KANBAN_COLUMNS.map((col) => (
          <div key={col.label} className="space-y-2">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
              {col.label}
            </p>
            {[1, 2].map((i) => (
              <Skeleton key={i} className="h-20 w-full" />
            ))}
          </div>
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <p className="text-sm text-destructive text-center py-4">
        Failed to load investigations.
      </p>
    );
  }

  const grouped = new Map<string, QaInvestigation[]>();
  for (const col of KANBAN_COLUMNS) {
    grouped.set(col.label, []);
  }

  for (const inv of investigations) {
    if (TERMINAL_STATUSES.has(inv.status)) {
      grouped.get("Terminal")!.push(inv);
    } else {
      const col = KANBAN_COLUMNS.find((c) => c.status === inv.status);
      if (col) grouped.get(col.label)!.push(inv);
    }
  }

  if (investigations.length === 0) {
    return (
      <EmptyState
        title="No investigations found."
        description="Patrol cycles dispatch investigations when novel issues are detected."
      />
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {KANBAN_COLUMNS.map((col) => {
        const items = grouped.get(col.label) ?? [];
        return (
          <div key={col.label} className="space-y-2">
            <div className="flex items-center gap-1.5">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                {col.label}
              </p>
              {items.length > 0 && (
                <Badge variant="secondary" className="h-4 px-1 text-xs">
                  {items.length}
                </Badge>
              )}
            </div>
            {items.length === 0 ? (
              <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground text-center">
                Empty
              </div>
            ) : (
              <div className="space-y-2 max-h-64 overflow-y-auto">
                {items.map((inv) => (
                  <InvestigationCard key={inv.id} inv={inv} />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Success rate trend chart
// ---------------------------------------------------------------------------

function SuccessRateTrendChart({ trends }: { trends: QaTrends }) {
  const data = trends.days.map((d) => ({
    date: d.date.slice(5), // mm-dd
    success_rate: Math.round(d.success_rate * 100),
    patrols: d.patrols_completed,
  }));

  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No patrol data in the last 7 days.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={192}>
      <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
        <defs>
          <linearGradient id="successGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
            <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          domain={[0, 100]}
          tickFormatter={(v: number) => `${v}%`}
          tick={{ fontSize: 11 }}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        <Tooltip formatter={(value: number | string | undefined) => [`${value ?? 0}%`, "Success rate"]} />
        <Area
          type="monotone"
          dataKey="success_rate"
          stroke="hsl(var(--primary))"
          fill="url(#successGradient)"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Source breakdown chart
// ---------------------------------------------------------------------------

const SOURCE_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

const SOURCE_LABELS: Record<string, string> = {
  log_scanner: "Log Scanner",
  session_records: "Sessions",
  butler_reports: "Butler Reports",
};

function SourceBreakdownChart({ trends }: { trends: QaTrends }) {
  const data = trends.source_breakdown.map((s) => ({
    name: SOURCE_LABELS[s.source_type] ?? s.source_type,
    value: s.count,
  }));

  if (data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        No finding data in the last 7 days.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={192}>
      <BarChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
        <XAxis dataKey="name" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={36} />
        <Tooltip />
        <Legend />
        <Bar dataKey="value" name="Findings" radius={[3, 3, 0, 0]}>
          {data.map((_, i) => (
            <Cell key={i} fill={SOURCE_COLORS[i % SOURCE_COLORS.length]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ---------------------------------------------------------------------------
// Status banner (circuit-breaker + next patrol)
// ---------------------------------------------------------------------------

function StatusBanner({ summary }: { summary: QaSummary }) {
  const lastPatrolStatus = summary.last_patrol?.status;
  const isError = lastPatrolStatus === "error";

  if (!isError) return null;

  return (
    <div className="flex items-start gap-3 rounded-md border border-destructive/50 bg-destructive/10 p-4 text-sm">
      <div className="flex-1 space-y-1">
        <p className="font-semibold text-destructive">Last patrol errored</p>
        <p className="text-muted-foreground text-xs">
          The most recent patrol cycle failed with an error. The QA staffer will retry on its next
          scheduled tick. Check the patrol detail for the error message.
        </p>
        {summary.last_patrol && (
          <Button variant="outline" size="sm" className="mt-2" asChild>
            <Link to={`/qa/patrols/${summary.last_patrol.id}`}>View patrol error</Link>
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Force patrol button
// ---------------------------------------------------------------------------

function ForcePatrolButton() {
  const mutation = useForceQaPatrol();
  const [lastMsg, setLastMsg] = useState<string | null>(null);

  function handleClick() {
    setLastMsg(null);
    mutation.mutate(undefined, {
      onSuccess: (resp) => {
        setLastMsg(resp.data?.message ?? "Patrol triggered.");
      },
      onError: () => {
        setLastMsg("Failed to trigger patrol. The daemon may be unavailable.");
      },
    });
  }

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
      <Button
        variant="outline"
        size="sm"
        disabled={mutation.isPending}
        onClick={handleClick}
      >
        {mutation.isPending ? "Triggering..." : "Run patrol"}
      </Button>
      {lastMsg && (
        <p
          className={`text-xs ${mutation.isError ? "text-destructive" : "text-muted-foreground"}`}
        >
          {lastMsg}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Circuit breaker status
// ---------------------------------------------------------------------------

function CircuitBreakerButton() {
  const { data, isLoading } = useQaCircuitBreaker();
  const resetMutation = useResetQaCircuitBreaker();
  const [lastMsg, setLastMsg] = useState<string | null>(null);

  const status = data?.data;
  const tripped = status?.tripped ?? false;

  function handleReset() {
    setLastMsg(null);
    resetMutation.mutate(undefined, {
      onSuccess: (resp) => {
        setLastMsg(resp.data?.message ?? "Circuit breaker reset.");
      },
      onError: () => {
        setLastMsg("Failed to reset circuit breaker.");
      },
    });
  }

  if (isLoading) {
    return <Skeleton className="h-8 w-36" />;
  }

  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
      {tripped ? (
        <Button
          variant="destructive"
          size="sm"
          disabled={resetMutation.isPending}
          onClick={handleReset}
        >
          {resetMutation.isPending ? "Resetting..." : "Circuit Breaker: OPEN"}
        </Button>
      ) : (
        <Badge variant="outline" className="border-green-500 text-green-600 px-3 py-1 text-xs">
          Circuit Breaker: closed
        </Badge>
      )}
      {lastMsg && (
        <p
          className={`text-xs ${resetMutation.isError ? "text-destructive" : "text-muted-foreground"}`}
        >
          {lastMsg}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaOverviewPage
// ---------------------------------------------------------------------------

const PAGE_SIZE = 20;

export default function QaOverviewPage() {
  const [page, setPage] = useState(0);
  const { data: summaryResponse, isLoading: summaryLoading, isError: summaryError } = useQaSummary();
  const { data: patrolsResponse, isLoading: patrolsLoading, isError: patrolsError } = useQaPatrols({
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });
  const { data: trendsResponse, isLoading: trendsLoading } = useQaTrends(7);

  const summary = summaryResponse?.data;
  const patrols = patrolsResponse?.data ?? [];
  const patrolsMeta = patrolsResponse?.meta;
  const totalPatrols = patrolsMeta?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalPatrols / PAGE_SIZE));
  const trends = trendsResponse?.data;

  const pageActions = (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
      <CircuitBreakerButton />
      <ForcePatrolButton />
    </div>
  );

  return (
    <Page
      archetype="overview"
      title="QA Staffer"
      description="System-wide quality patrol, investigation pipeline, and known issue tracking."
      actions={pageActions}
    >
      {/* Status banner -- shown when last patrol errored */}
      {summary && <StatusBanner summary={summary} />}

      {/* Summary stats */}
      {summaryLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : summaryError ? (
        <Card>
          <CardContent className="py-8 text-center">
            <p className="text-destructive text-sm">Failed to load QA summary.</p>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* Last patrol banner */}
          {summary?.last_patrol && (
            <Card>
              <CardContent className="pt-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="space-y-1">
                    <p className="text-muted-foreground text-sm font-medium">Last patrol</p>
                    <div className="flex items-center gap-2">
                      <PatrolStatusBadge status={summary.last_patrol.status} />
                      <span className="text-sm">
                        <Time value={summary.last_patrol.started_at} mode="absolute" />
                      </span>
                      <span className="text-muted-foreground text-xs">
                        ({formatRelative(summary.last_patrol.started_at)})
                      </span>
                    </div>
                    {summary.last_patrol.sources_polled.length > 0 && (
                      <div className="flex items-center gap-1">
                        <span className="text-muted-foreground text-xs">Sources:</span>
                        {summary.last_patrol.sources_polled.map((s) => (
                          <SourceTypeBadge key={s} sourceType={s} />
                        ))}
                      </div>
                    )}
                  </div>
                  <Button variant="outline" size="sm" asChild>
                    <Link to={`/qa/patrols/${summary.last_patrol.id}`}>View patrol</Link>
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}

          {!summary?.last_patrol && !summaryLoading && (
            <EmptyState
              title="No patrol cycles recorded."
              description="The QA staffer may not be running yet."
            />
          )}

          {/* 24h stats */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatsCard
              title="Patrols (24h)"
              value={summary?.stats_24h.patrols_completed ?? 0}
            />
            <StatsCard
              title="Findings (24h)"
              value={summary?.stats_24h.total_findings ?? 0}
              description={`${summary?.stats_24h.novel_findings ?? 0} novel`}
            />
            <StatsCard
              title="Dispatched (24h)"
              value={summary?.stats_24h.dispatched_investigations ?? 0}
            />
            <StatsCard
              title="All-time patrols"
              value={summary?.stats_all_time.total_patrols ?? 0}
              description={`${summary?.stats_all_time.dispatched_investigations ?? 0} investigations`}
            />
          </div>
        </>
      )}

      {/* Investigation pipeline (Kanban) */}
      <Card>
        <CardHeader>
          <CardTitle>Investigation Pipeline</CardTitle>
          <CardDescription>
            Active and recent investigations grouped by pipeline status.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InvestigationPipeline />
        </CardContent>
      </Card>

      {/* Analytics charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Success Rate (7d)</CardTitle>
            <CardDescription>Percentage of patrol cycles that completed cleanly.</CardDescription>
          </CardHeader>
          <CardContent>
            {trendsLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : trends ? (
              <SuccessRateTrendChart trends={trends} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No trend data available.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Source Breakdown (7d)</CardTitle>
            <CardDescription>Total findings per discovery source over the last 7 days.</CardDescription>
          </CardHeader>
          <CardContent>
            {trendsLoading ? (
              <Skeleton className="h-48 w-full" />
            ) : trends ? (
              <SourceBreakdownChart trends={trends} />
            ) : (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No trend data available.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Known issues panel */}
      <Card>
        <CardHeader>
          <CardTitle>Known Issues</CardTitle>
          <CardDescription>
            Active issues tracked across patrol cycles. Dismiss to suppress future investigations.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <KnownIssuesPanel />
        </CardContent>
      </Card>

      {/* Recent patrols table */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Patrols</CardTitle>
          <CardDescription>Patrol cycle history with findings and dispatch counts.</CardDescription>
          <CardAction>
            {summary?.active_sources && summary.active_sources.length > 0 && (
              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                Active sources:
                {summary.active_sources.map((s) => (
                  <SourceTypeBadge key={s} sourceType={s} />
                ))}
              </div>
            )}
          </CardAction>
        </CardHeader>
        <CardContent>
          {patrolsLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : patrolsError ? (
            <p className="text-destructive py-4 text-center text-sm">Failed to load patrols.</p>
          ) : (
            <RecentPatrolsTable patrols={patrols} />
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {totalPatrols > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Page {page + 1} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!patrolsMeta?.has_more}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </Page>
  );
}
