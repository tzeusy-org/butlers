import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
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
import { useHealingAttempt, useQaFindingByAttempt } from "@/hooks/use-qa";
import type { HealingAttempt, QaFindingRecord } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
// Dispatch reason card (linked QA finding)
// ---------------------------------------------------------------------------

function formatSourceType(value: string): string {
  return value.replace(/_/g, " ");
}

function DispatchReasonCard({
  finding,
  isLoading,
  isError,
}: {
  finding: QaFindingRecord | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Dispatch Reason</CardTitle>
        <CardDescription>
          Why the QA patrol flagged this and queued an investigation.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-3/4" />
          </div>
        ) : isError || !finding ? (
          <p className="text-muted-foreground text-sm">
            No QA finding is linked to this attempt. This usually means the
            investigation was created outside the normal patrol pipeline (e.g.
            a manual retry or a synthetic dispatch).
          </p>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            <dt className="text-muted-foreground font-medium">Source</dt>
            <dd className="flex items-center gap-2">
              <Badge variant="outline" className="font-mono">
                {formatSourceType(finding.source_type)}
              </Badge>
              <span className="text-muted-foreground text-xs">via</span>
              <Badge variant="outline" className="font-mono">
                {finding.source_butler}
              </Badge>
            </dd>

            <dt className="text-muted-foreground font-medium">Summary</dt>
            <dd className="text-sm">{finding.event_summary}</dd>

            <dt className="text-muted-foreground font-medium">Occurrences</dt>
            <dd>
              <span className="font-mono text-sm">{finding.occurrence_count}</span>
              <span className="text-muted-foreground text-xs ml-2">
                (<Time value={finding.first_seen} mode="absolute" /> → <Time value={finding.last_seen} mode="absolute" />)
              </span>
            </dd>

            <dt className="text-muted-foreground font-medium">Patrol</dt>
            <dd>
              <Link
                to={`/qa/patrols/${finding.patrol_id}`}
                className="text-primary text-xs font-mono underline-offset-4 hover:underline"
              >
                {finding.patrol_id.slice(0, 8)}…
              </Link>
            </dd>

            {finding.dedup_reason && (
              <>
                <dt className="text-muted-foreground font-medium">Dedup</dt>
                <dd className="text-xs">{finding.dedup_reason}</dd>
              </>
            )}

            {finding.source_session_trigger_source && (
              <>
                <dt className="text-muted-foreground font-medium">Trigger</dt>
                <dd>
                  <code className="text-xs">
                    {finding.source_session_trigger_source}
                  </code>
                </dd>
              </>
            )}

            {finding.structured_evidence &&
              Object.keys(finding.structured_evidence).length > 0 && (
                <>
                  <dt className="text-muted-foreground font-medium self-start">
                    Evidence
                  </dt>
                  <dd>
                    <StructuredEvidence evidence={finding.structured_evidence} />
                  </dd>
                </>
              )}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function StructuredEvidence({ evidence }: { evidence: Record<string, unknown> }) {
  const entries = Object.entries(evidence);
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
      {entries.map(([key, value]) => {
        const display =
          typeof value === "string" || typeof value === "number" || typeof value === "boolean"
            ? String(value)
            : JSON.stringify(value);
        const isSessionId = key === "session_id" && typeof value === "string";
        return (
          <div key={key} className="contents">
            <dt className="text-muted-foreground font-mono">{key}</dt>
            <dd className="font-mono break-all">
              {isSessionId ? (
                <Link
                  to={`/sessions/${value}?butler=qa`}
                  className="text-primary underline-offset-4 hover:underline"
                >
                  {display}
                </Link>
              ) : (
                display
              )}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

// ---------------------------------------------------------------------------
// Triggering sessions card
// ---------------------------------------------------------------------------

function TriggeringSessionsCard({ attempt }: { attempt: HealingAttempt }) {
  if (!attempt.session_ids || attempt.session_ids.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Triggering Sessions</CardTitle>
        <CardDescription>
          Butler sessions whose failures produced this fingerprint. Open one
          to see the original traceback in context.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-1 text-sm">
          {attempt.session_ids.map((sid) => (
            <li key={sid}>
              <Link
                to={`/sessions/${sid}?butler=${encodeURIComponent(attempt.butler_name)}`}
                className="text-primary font-mono text-xs underline-offset-4 hover:underline"
              >
                {sid}
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
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
              <span className="text-muted-foreground text-xs"><Time value={step.time} mode="absolute" /></span>
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
  const {
    data: findingResp,
    isLoading: findingLoading,
    isError: findingError,
  } = useQaFindingByAttempt(attemptId || undefined);
  const finding = findingResp?.data;

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
          { label: "Investigations", href: "/qa/investigations" },
          { label: attempt.id.slice(0, 8) },
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
            <dd><Time value={attempt.created_at} mode="absolute" /></dd>

            <dt className="text-muted-foreground font-medium">Updated</dt>
            <dd><Time value={attempt.updated_at} mode="absolute" /></dd>

            {attempt.closed_at && (
              <>
                <dt className="text-muted-foreground font-medium">Closed</dt>
                <dd><Time value={attempt.closed_at} mode="absolute" /></dd>
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
          <CardDescription>
            Sanitized error details captured at fingerprint time. The raw
            stack trace lives in the triggering session's transcript.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-sm">
            <dt className="text-muted-foreground font-medium">Exception</dt>
            <dd>
              <code className="text-sm">{attempt.exception_type}</code>
            </dd>

            <dt className="text-muted-foreground font-medium">Call site</dt>
            <dd>
              <code className="text-xs break-all">{attempt.call_site}</code>
            </dd>

            {attempt.sanitized_msg && (
              <>
                <dt className="text-muted-foreground font-medium">Summary</dt>
                <dd className="text-sm">{attempt.sanitized_msg}</dd>
              </>
            )}
          </dl>

          {attempt.error_detail && (
            <div>
              <div className="text-muted-foreground text-sm font-medium mb-2">
                Error detail
              </div>
              <pre className="bg-muted text-destructive rounded-md border p-3 text-xs overflow-x-auto whitespace-pre-wrap break-words max-h-80">
                {attempt.error_detail}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Dispatch reason (linked QA finding) */}
      <DispatchReasonCard
        finding={finding}
        isLoading={findingLoading}
        isError={findingError}
      />

      {/* Triggering sessions */}
      <TriggeringSessionsCard attempt={attempt} />

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
              to={`/sessions/${attempt.healing_session_id}?butler=qa`}
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
