/**
 * QaOverviewPage -- QA Staffer dossier shell.
 *
 * Layout (vertical order):
 *   1. Sticky top bar: severity filter + theme toggle
 *   2. Page header: Dispatch eyebrow + H1 + runtime caption + clock
 *   3. QaKpiStrip: 4-cell KPI row
 *   4. Two-pane body: CaseList rail (320px) + CaseDossier main column
 *
 * URL-driven case selection: `?case=<id>` selects a case in the rail.
 * Clicking a case row calls setParams with a functional update to preserve existing params.
 *
 * bu-21uf7 -- Rewrite QaOverviewPage.tsx as dossier shell
 */

import { useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";

import { CaseDossier, CaseList, QaKpiStrip } from "@/components/qa";
import { Time } from "@/components/ui/time";
import { useForceQaPatrol, useQaCases, useQaSummary } from "@/hooks/use-qa";
import { useDarkMode } from "@/hooks/useDarkMode";

// ---------------------------------------------------------------------------
// Severity + time-range filter types
// ---------------------------------------------------------------------------

type SeverityFilter = "all" | "high" | "medium" | "low";

const SEVERITY_OPTIONS: Array<{ value: SeverityFilter; label: string }> = [
  { value: "all", label: "All" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

type SinceFilter = "24h" | "7d" | "30d" | "all";

const SINCE_OPTIONS: Array<{ value: SinceFilter; label: string }> = [
  { value: "24h", label: "24h" },
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "all", label: "All" },
];

/** Human-readable label for the active time range, used in CaseList. */
function caseListSinceLabel(since: SinceFilter): string {
  if (since === "all") return "Cases · all cases";
  return `Cases · last ${since}`;
}

// ---------------------------------------------------------------------------
// Sticky top bar
// ---------------------------------------------------------------------------

function StickyTopBar({
  severity,
  onSeverityChange,
  since,
  onSinceChange,
  onForcePatrol,
  forcePatrolPending,
}: {
  severity: SeverityFilter;
  onSeverityChange: (sev: SeverityFilter) => void;
  since: SinceFilter;
  onSinceChange: (since: SinceFilter) => void;
  onForcePatrol: () => void;
  forcePatrolPending: boolean;
}) {
  const { theme, setTheme, resolvedTheme } = useDarkMode();

  function toggleTheme() {
    if (theme === "system") {
      setTheme(resolvedTheme === "dark" ? "light" : "dark");
    } else {
      setTheme(theme === "dark" ? "light" : "dark");
    }
  }

  return (
    <div className="sticky top-0 z-20 flex items-center justify-between border-b border-border/60 bg-background/95 px-6 py-2 backdrop-blur-sm">
      <div className="flex items-center gap-4">
        {/* Severity filter */}
        <div className="flex items-center gap-1" role="group" aria-label="Filter by severity">
          {SEVERITY_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onSeverityChange(opt.value)}
              className={[
                "rounded px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors duration-fast focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                severity === opt.value
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:text-foreground",
              ].join(" ")}
              aria-pressed={severity === opt.value}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Divider between filter groups */}
        <span aria-hidden="true" className="h-4 w-px bg-border/60" />

        {/* Time range filter */}
        <div className="flex items-center gap-1" role="group" aria-label="Time range">
          {SINCE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => onSinceChange(opt.value)}
              className={[
                "rounded px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors duration-fast focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                since === opt.value
                  ? "bg-foreground text-background"
                  : "text-muted-foreground hover:text-foreground",
              ].join(" ")}
              aria-pressed={since === opt.value}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3">
        {/* Force patrol — trigger an immediate patrol cycle */}
        <button
          type="button"
          onClick={onForcePatrol}
          disabled={forcePatrolPending}
          aria-label="Force patrol"
          className="rounded px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground transition-colors duration-fast hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {forcePatrolPending ? "Patrolling…" : "Force patrol"}
        </button>

        {/* Theme toggle */}
        <button
          type="button"
          onClick={toggleTheme}
          aria-label="Toggle theme"
        className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground transition-colors duration-fast hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      >
        {resolvedTheme === "dark" ? (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="5" />
            <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
          </svg>
        ) : (
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        )}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page header
// ---------------------------------------------------------------------------

function PageHeader({ summary }: { summary: ReturnType<typeof useQaSummary> }) {
  const data = summary.data?.data;

  const port = data?.port ?? null;
  const model = data?.model ?? null;
  const patrolInterval = data?.patrol_interval_minutes ?? null;

  const caption = [
    port !== null && `port :${port}`,
    model && `model ${model}`,
    patrolInterval !== null && `patrol every ${patrolInterval}m`,
  ].filter(Boolean).join(" · ");

  return (
    <header className="border-b border-border/60 px-6 py-5">
      <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        QA Staffer · dossier
      </p>
      <div className="flex items-baseline justify-between">
        <h1 className="font-sans text-2xl font-medium leading-tight tracking-[-0.02em] text-foreground">
          What the staff caught and fixed
        </h1>
        <Time
          value={new Date()}
          mode="clock-24h-mono"
          className="font-mono text-sm text-muted-foreground tabular-nums"
          showTitle={false}
        />
      </div>
      {caption && (
        <p className="mt-1.5 font-mono text-[10px] text-muted-foreground">{caption}</p>
      )}
    </header>
  );
}

// ---------------------------------------------------------------------------
// Loading + error states for CaseDossier region
// ---------------------------------------------------------------------------

function DossierPlaceholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-1 items-start px-6 pt-6">
      <p className="font-serif text-[15px] italic text-muted-foreground">{children}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaOverviewPage
// ---------------------------------------------------------------------------

export default function QaOverviewPage() {
  const [params, setParams] = useSearchParams();
  const [severity, setSeverity] = useState<SeverityFilter>("all");
  const [since, setSince] = useState<SinceFilter>("7d");

  const selectedCaseId = params.get("case") ?? undefined;

  const summary = useQaSummary();
  const forcePatrol = useForceQaPatrol();
  const cases = useQaCases({
    sev: severity === "all" ? undefined : severity,
    since,
  });

  function handleForcePatrol() {
    if (forcePatrol.isPending) return;
    if (!window.confirm("Trigger an immediate QA patrol cycle now?")) return;
    forcePatrol.mutate(undefined, {
      onSuccess: (res) => {
        toast.success(res.data?.message ?? "Patrol triggered");
      },
      onError: (err) => {
        toast.error(
          `Force patrol failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  const casesData = cases.data?.data ?? [];

  // Auto-select first case when no URL param is set and data is loaded
  const effectiveCaseId = selectedCaseId ?? casesData[0]?.id;

  function handleCaseSelect(id: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set("case", id);
      return next;
    });
  }

  const summaryData = summary.data?.data;

  return (
    <div className="flex min-h-full flex-col">
      <StickyTopBar
        severity={severity}
        onSeverityChange={setSeverity}
        since={since}
        onSinceChange={setSince}
        onForcePatrol={handleForcePatrol}
        forcePatrolPending={forcePatrol.isPending}
      />

      <PageHeader summary={summary} />

      {/* KPI strip */}
      <div className="border-b border-border/60 px-6 py-4">
        <QaKpiStrip kpis={summaryData?.kpis} active={summaryData?.active_breakdown} />
      </div>

      {/* Two-pane body: case rail + dossier */}
      <div className="flex flex-1 overflow-hidden">
        {/* Case rail */}
        <div className="shrink-0 overflow-y-auto border-r border-border/60 px-4 py-4">
          {cases.isLoading ? (
            <p className="font-serif text-sm italic text-muted-foreground">Loading cases…</p>
          ) : cases.isError ? (
            <p className="font-serif text-sm italic text-destructive">
              Couldn't reach the staffer.
            </p>
          ) : casesData.length === 0 ? (
            <p className="font-serif text-sm italic text-muted-foreground">
              Nothing in the dossier.
            </p>
          ) : (
            <CaseList
              cases={casesData}
              selectedId={effectiveCaseId ?? null}
              onSelect={handleCaseSelect}
              headerLabel={caseListSinceLabel(since)}
            />
          )}
        </div>

        {/* Dossier body */}
        <main className="min-w-0 flex-1 overflow-y-auto px-6 py-6">
          {cases.isError || summary.isError ? (
            <DossierPlaceholder>Couldn't reach the staffer.</DossierPlaceholder>
          ) : cases.isLoading ? (
            <DossierPlaceholder>Loading…</DossierPlaceholder>
          ) : casesData.length === 0 ? (
            <DossierPlaceholder>Nothing in the dossier.</DossierPlaceholder>
          ) : effectiveCaseId ? (
            <CaseDossier caseId={effectiveCaseId} />
          ) : (
            <DossierPlaceholder>Select a case to inspect the dossier.</DossierPlaceholder>
          )}
        </main>
      </div>
    </div>
  );
}
