import { Link, useParams } from "react-router";

import { Breadcrumbs } from "@/components/ui/breadcrumbs";
import { Time } from "@/components/ui/time";
import { useQaPatrol } from "@/hooks/use-qa";
import type { QaFindingRecord } from "@/api/index.ts";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDuration(start: string, end: string | null | undefined): string {
  if (!end) return "running";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "--";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function formatSourceType(value: string): string {
  const labels: Record<string, string> = {
    log_scanner: "log",
    session_records: "session",
    butler_reports: "butler",
  };
  return labels[value] ?? value.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Severity glyph
// ---------------------------------------------------------------------------

const SEVERITY_CLASS: Record<number, string> = {
  0: "bg-destructive",
  1: "bg-destructive",
  2: "bg-amber-500",
  3: "bg-muted-foreground",
  4: "bg-muted-foreground",
};

function SeverityGlyph({ severity }: { severity: number }) {
  const cls = SEVERITY_CLASS[severity] ?? "bg-muted-foreground";
  const labels: Record<number, string> = {
    0: "critical",
    1: "high",
    2: "medium",
    3: "low",
    4: "info",
  };
  return (
    <span
      className={`mt-0.5 h-2 w-2 shrink-0 ${cls}`}
      aria-label={labels[severity] ?? "unknown"}
    />
  );
}

// ---------------------------------------------------------------------------
// Dedup mark
// ---------------------------------------------------------------------------

function DedupMark({ reason }: { reason: string | null }) {
  if (!reason) {
    return (
      <span className="font-mono text-[10px] uppercase tracking-[0.10em] text-emerald-600 tnum">
        novel
      </span>
    );
  }
  const labels: Record<string, string> = {
    active_attempt: "active",
    open_pr: "open pr",
    dismissed: "dismissed",
    cooldown: "cooldown",
  };
  return (
    <span className="font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
      {labels[reason] ?? reason}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Findings list (rule-separated rows, no table)
// ---------------------------------------------------------------------------

interface FindingRowProps {
  finding: QaFindingRecord;
}

function FindingRow({ finding }: FindingRowProps) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,180px)_1fr_auto] items-start gap-x-3 py-3">
      {/* mark column */}
      <SeverityGlyph severity={finding.severity} />

      {/* id + butler column */}
      <p className="min-w-0 truncate font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground tnum">
        {finding.fingerprint.slice(0, 8)} &middot; {finding.source_butler}
      </p>

      {/* summary column (1fr) */}
      <p className="min-w-0 text-[12px] leading-relaxed text-foreground">
        {finding.event_summary}
        {finding.healing_attempt_id && (
          <span className="ml-2">
            <Link
              to={`/qa/investigations/${finding.healing_attempt_id}`}
              className="font-mono text-[10px] uppercase tracking-[0.08em] text-primary underline-offset-4 hover:underline"
            >
              {"→"} investigate
            </Link>
          </span>
        )}
      </p>

      {/* meta column */}
      <DedupMark reason={finding.dedup_reason} />
    </div>
  );
}

function FindingsList({ findings }: { findings: QaFindingRecord[] }) {
  if (findings.length === 0) {
    return (
      <p className="py-6 font-[family-name:var(--font-serif,serif)] text-sm italic text-muted-foreground">
        No findings in this patrol.
      </p>
    );
  }

  return (
    <div className="divide-y divide-border">
      {findings.map((f) => (
        <FindingRow key={f.id} finding={f} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dispatch summary (rule-separated)
// ---------------------------------------------------------------------------

function DispatchedRow({ finding }: { finding: QaFindingRecord }) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,180px)_1fr_auto] items-center gap-x-3 py-3">
      <SeverityGlyph severity={finding.severity} />

      <p className="min-w-0 truncate font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground tnum">
        {finding.fingerprint.slice(0, 8)} &middot; {finding.source_butler}
      </p>

      <p className="min-w-0 truncate text-[12px] text-foreground">{finding.event_summary}</p>

      <Link
        to={"/qa/investigations/" + finding.healing_attempt_id}
        className="font-mono text-[10px] uppercase tracking-[0.08em] text-primary underline-offset-4 hover:underline"
      >
        View
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Patrol status label
// ---------------------------------------------------------------------------

function patrolStatusLabel(status: string): string {
  const map: Record<string, string> = {
    clean: "clean",
    findings_dispatched: "dispatched",
    running: "running",
    error: "error",
    skipped_overlap: "skipped",
  };
  return map[status] ?? status;
}

// ---------------------------------------------------------------------------
// Eyebrow primitive
// ---------------------------------------------------------------------------

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
      {children}
    </p>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PageSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="h-4 w-48 rounded bg-muted" />
      <div className="h-6 w-64 rounded bg-muted" />
      <div className="h-4 w-full max-w-sm rounded bg-muted" />
      <div className="space-y-3 pt-4">
        <div className="h-3 w-full rounded bg-muted" />
        <div className="h-3 w-5/6 rounded bg-muted" />
        <div className="h-3 w-4/6 rounded bg-muted" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaPatrolDetailPage
// ---------------------------------------------------------------------------

export default function QaPatrolDetailPage() {
  const { patrolId = "" } = useParams<{ patrolId: string }>();
  const { data: response, isLoading, isError } = useQaPatrol(patrolId || undefined);
  const patrol = response?.data;

  if (isLoading) return <PageSkeleton />;

  if (!patrolId || isError || !patrol) {
    return (
      <div className="space-y-4">
        <Breadcrumbs items={[{ label: "QA", href: "/qa" }]} />
        <p className="font-[family-name:var(--font-serif,serif)] text-sm italic text-muted-foreground">
          Patrol not found.
        </p>
      </div>
    );
  }

  const dispatched = patrol.findings.filter((f) => f.healing_attempt_id);
  const duration = formatDuration(patrol.started_at, patrol.completed_at);
  const sourcesLabel = patrol.sources_polled.map(formatSourceType).join(", ");

  return (
    <div className="space-y-8">
      {/* Breadcrumbs + header */}
      <Breadcrumbs
        items={[
          { label: "QA", href: "/qa" },
          { label: `Patrol ${patrol.id.slice(0, 8)}` },
        ]}
      />

      {/* Page header */}
      <header className="space-y-1">
        <Eyebrow>QA Patrol</Eyebrow>
        <h1 className="font-sans text-[22px] font-medium leading-[1.25] tracking-normal text-foreground">
          Patrol · <Time value={patrol.started_at} mode="absolute" />
        </h1>
        <p className="font-mono text-[10px] uppercase tracking-[0.10em] text-muted-foreground tnum">
          {[duration, patrolStatusLabel(patrol.status), sourcesLabel, `${patrol.log_lookback_minutes}m lookback`]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </header>

      {/* Findings section */}
      <section className="space-y-2" aria-label="Findings">
        <Eyebrow>
          Findings ({patrol.findings_count}) &middot; {patrol.novel_count} novel &middot;{" "}
          {patrol.findings_count - patrol.novel_count} deduplicated
        </Eyebrow>
        <hr className="border-border" />
        <FindingsList findings={patrol.findings} />
      </section>

      {/* Dispatch summary section */}
      {dispatched.length > 0 && (
        <section className="space-y-2" aria-label="Dispatched investigations">
          <Eyebrow>
            Dispatched ({dispatched.length})
          </Eyebrow>
          <hr className="border-border" />
          <div className="divide-y divide-border">
            {dispatched.map((f) => (
              <DispatchedRow key={f.id} finding={f} />
            ))}
          </div>
        </section>
      )}

      {/* Error detail */}
      {patrol.error_detail && (
        <section className="space-y-2" aria-label="Patrol error">
          <Eyebrow>Error</Eyebrow>
          <p className="font-mono text-[11px] text-destructive">{patrol.error_detail}</p>
        </section>
      )}
    </div>
  );
}
