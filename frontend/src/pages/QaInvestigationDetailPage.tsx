import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useHealingAttempt } from "@/hooks/use-qa";
import type { HealingAttempt } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTs(iso: string | null | undefined): string {
  if (!iso) return "--";
  return new Date(iso).toLocaleString();
}

function formatDuration(
  start: string | null | undefined,
  end: string | null | undefined,
): string {
  if (!start || !end) return "--";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "--";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
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
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; className: string }> = {
    investigating: { label: "investigating", className: "border-amber-500 text-amber-600" },
    dispatch_pending: { label: "pending", className: "border-blue-500 text-blue-600" },
    pr_open: { label: "PR open", className: "border-blue-500 text-blue-600" },
    pr_merged: { label: "PR merged", className: "bg-emerald-600 text-white hover:bg-emerald-600/90" },
    failed: { label: "failed", className: "" },
    timeout: { label: "timeout", className: "" },
    unfixable: { label: "unfixable", className: "" },
    anonymization_failed: { label: "anon failed", className: "" },
  };

  const c = config[status];
  if (!c) return <Badge variant="outline">{status}</Badge>;

  if (status === "pr_merged") {
    return <Badge className={c.className}>{c.label}</Badge>;
  }
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
// PR card
// ---------------------------------------------------------------------------

function PrCard({ attempt }: { attempt: HealingAttempt }) {
  if (!attempt.pr_url && !attempt.pr_number) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Pull Request
          {attempt.pr_number && (
            <Badge variant="outline" className="font-mono">
              #{attempt.pr_number}
            </Badge>
          )}
        </CardTitle>
        <CardDescription>
          GitHub PR created by the investigation agent.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-4">
          <StatusBadge status={attempt.status} />
          {attempt.pr_url && (
            <a
              href={attempt.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary text-sm underline-offset-4 hover:underline"
            >
              Open on GitHub
            </a>
          )}
          <span className="text-muted-foreground text-xs font-mono">
            {attempt.branch_name ?? "—"}
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

interface TimelineStep {
  label: string;
  time: string | null;
  active: boolean;
  done: boolean;
}

function buildTimeline(attempt: HealingAttempt): TimelineStep[] {
  const steps: TimelineStep[] = [
    {
      label: "Dispatched",
      time: attempt.created_at,
      active: false,
      done: true,
    },
    {
      label: "Investigating",
      time: attempt.status === "investigating" ? attempt.updated_at : null,
      active: attempt.status === "investigating",
      done: !["dispatch_pending", "investigating"].includes(attempt.status),
    },
    {
      label: "PR opened",
      time:
        ["pr_open", "pr_merged"].includes(attempt.status) ? attempt.updated_at : null,
      active: attempt.status === "pr_open",
      done: attempt.status === "pr_merged",
    },
    {
      label: "PR merged",
      time: attempt.status === "pr_merged" ? attempt.closed_at : null,
      active: false,
      done: attempt.status === "pr_merged",
    },
  ];

  // If terminal failure, replace the last active step
  if (["failed", "timeout", "unfixable", "anonymization_failed"].includes(attempt.status)) {
    return [
      ...steps.slice(0, 2),
      {
        label: attempt.status,
        time: attempt.closed_at,
        active: false,
        done: true,
      },
    ];
  }

  return steps;
}

function Timeline({ attempt }: { attempt: HealingAttempt }) {
  const steps = buildTimeline(attempt);

  return (
    <ol className="relative ml-3 border-l border-muted">
      {steps.map((step, idx) => (
        <li key={idx} className="mb-6 ml-6">
          <span
            className={`absolute -left-3 flex size-6 items-center justify-center rounded-full ring-4 ring-background ${
              step.done
                ? "bg-emerald-500"
                : step.active
                  ? "bg-amber-400"
                  : "bg-muted"
            }`}
          />
          <div className="flex items-center gap-2">
            <span
              className={`text-sm font-medium ${
                step.done
                  ? "text-foreground"
                  : step.active
                    ? "text-amber-600"
                    : "text-muted-foreground"
              }`}
            >
              {step.label}
            </span>
            {step.time && (
              <span className="text-muted-foreground text-xs">{formatTs(step.time)}</span>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-8 w-64" />
      <Card>
        <CardHeader>
          <Skeleton className="h-5 w-32" />
        </CardHeader>
        <CardContent className="space-y-3">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </CardContent>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaInvestigationDetailPage
// ---------------------------------------------------------------------------

export default function QaInvestigationDetailPage() {
  const { attemptId = "" } = useParams<{ attemptId: string }>();
  const { data: attempt, isLoading, isError } = useHealingAttempt(attemptId || undefined);

  if (!attemptId) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold tracking-tight">Investigation Detail</h1>
        <Card>
          <CardContent>
            <p className="text-muted-foreground py-12 text-center text-sm">
              No investigation ID provided.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isLoading) return <PageSkeleton />;

  if (isError || !attempt) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Button variant="outline" size="sm" asChild>
            <Link to="/qa">Back to QA</Link>
          </Button>
          <h1 className="text-2xl font-bold tracking-tight">Investigation Detail</h1>
        </div>
        <Card>
          <CardContent>
            <p className="text-destructive py-12 text-center text-sm">
              Investigation not found or failed to load.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Breadcrumbs */}
      <Breadcrumbs
        items={[
          { label: "QA", href: "/qa" },
          { label: `Investigation ${attempt.id.slice(0, 8)}` },
        ]}
      />

      <div className="flex items-center gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Investigation Detail</h1>
        <StatusBadge status={attempt.status} />
        <SeverityBadge severity={attempt.severity} />
      </div>

      {/* Metadata */}
      <Card>
        <CardHeader>
          <CardTitle>Metadata</CardTitle>
          <CardDescription>
            <code className="text-xs">{attempt.id}</code>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            <dt className="text-muted-foreground font-medium">Butler</dt>
            <dd>
              <Badge variant="outline" className="font-mono">
                {attempt.butler_name}
              </Badge>
            </dd>

            <dt className="text-muted-foreground font-medium">Fingerprint</dt>
            <dd>
              <code className="text-xs">{attempt.fingerprint.slice(0, 16)}…</code>
            </dd>

            <dt className="text-muted-foreground font-medium">Created</dt>
            <dd>{formatTs(attempt.created_at)}</dd>

            <dt className="text-muted-foreground font-medium">Updated</dt>
            <dd>{formatTs(attempt.updated_at)}</dd>

            {attempt.closed_at && (
              <>
                <dt className="text-muted-foreground font-medium">Closed</dt>
                <dd>{formatTs(attempt.closed_at)}</dd>
              </>
            )}

            <dt className="text-muted-foreground font-medium">Duration</dt>
            <dd>{formatDuration(attempt.created_at, attempt.closed_at)}</dd>
          </dl>
        </CardContent>
      </Card>

      {/* Timeline */}
      <Card>
        <CardHeader>
          <CardTitle>Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <Timeline attempt={attempt} />
        </CardContent>
      </Card>

      {/* Error context */}
      <Card>
        <CardHeader>
          <CardTitle>Error Context</CardTitle>
          <CardDescription>Sanitized error details — no raw log content.</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            <dt className="text-muted-foreground font-medium">Exception</dt>
            <dd>
              <code className="text-sm">{attempt.exception_type}</code>
            </dd>

            <dt className="text-muted-foreground font-medium">Call site</dt>
            <dd>
              <code className="text-xs">{attempt.call_site}</code>
            </dd>

            {attempt.sanitized_msg && (
              <>
                <dt className="text-muted-foreground font-medium">Summary</dt>
                <dd className="text-sm">{attempt.sanitized_msg}</dd>
              </>
            )}

            {attempt.error_detail && (
              <>
                <dt className="text-muted-foreground font-medium">Error detail</dt>
                <dd className="text-destructive text-xs">{attempt.error_detail}</dd>
              </>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* PR card */}
      <PrCard attempt={attempt} />

      {/* Agent session link */}
      {attempt.healing_session_id && (
        <Card>
          <CardHeader>
            <CardTitle>Agent Session</CardTitle>
          </CardHeader>
          <CardContent>
            <Link
              to={`/sessions/${attempt.healing_session_id}`}
              className="text-primary text-sm underline-offset-4 hover:underline"
            >
              View agent session {attempt.healing_session_id.slice(0, 8)}
            </Link>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
