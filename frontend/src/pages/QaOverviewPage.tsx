/**
 * QaOverviewPage -- QA Staffer dossier shell.
 *
 * Layout (vertical order):
 *   1. Sticky top bar: severity + since + state + butler filters, force patrol
 *   2. Page header: Dispatch eyebrow + H1 + runtime caption + clock (the
 *      shell's global PageHeader carries the one theme toggle — this page
 *      used to duplicate it locally; removed as cross-chrome cruft)
 *   3. QaKpiStrip: 4-cell KPI row
 *   4. Patrol pulse strip: last few patrols, linking to patrol detail
 *   5. Two-pane body: CaseList rail (320px) + CaseDossier main column
 *
 * URL-driven state: `?case=<id>` selects a case in the rail; `?sev=`,
 * `?since=`, `?state=`, and `?butler=` (comma-separated) drive the case
 * query and are shareable/bookmarkable — bu-86c4c.19 folded the standalone
 * /qa/investigations flat index into this page so there is one canonical
 * case index (JARVIS audit move 14). Filter/case changes call setParams
 * with a functional update to preserve existing params.
 *
 * bu-21uf7 -- Rewrite QaOverviewPage.tsx as dossier shell
 */

import { useMemo, useRef, useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import type { QaCaseSummary } from "@/api/types";
import { CaseDossier, CaseList, QaKpiStrip, QaVerdictOpener } from "@/components/qa";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tip";
import { useButlers } from "@/hooks/use-butlers";
import {
  useForceQaPatrol,
  useQaCases,
  useQaPatrols,
  useQaSummary,
  useResetQaCircuitBreaker,
} from "@/hooks/use-qa";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";

// ---------------------------------------------------------------------------
// Filter types (all URL-persisted — see useSearchParams below)
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

type StateFilter = "all" | QaCaseSummary["state"];

const STATE_OPTIONS: Array<{ value: StateFilter; label: string }> = [
  { value: "all", label: "All states" },
  { value: "detect", label: "Detect" },
  { value: "diagnose", label: "Diagnose" },
  { value: "pr", label: "PR open" },
  { value: "landed", label: "Landed" },
  { value: "escalated", label: "Escalated" },
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
  state,
  onStateChange,
  selectedButlers,
  onToggleButler,
  butlerOptions,
  breakerTripped,
  onResetCircuitBreaker,
  resetCircuitBreakerPending,
  onForcePatrol,
  forcePatrolPending,
}: {
  severity: SeverityFilter;
  onSeverityChange: (sev: SeverityFilter) => void;
  since: SinceFilter;
  onSinceChange: (since: SinceFilter) => void;
  state: StateFilter;
  onStateChange: (state: StateFilter) => void;
  selectedButlers: Set<string>;
  onToggleButler: (name: string) => void;
  butlerOptions: string[];
  breakerTripped: boolean;
  onResetCircuitBreaker: () => void;
  resetCircuitBreakerPending: boolean;
  onForcePatrol: () => void;
  forcePatrolPending: boolean;
}) {
  const [butlerMenuOpen, setButlerMenuOpen] = useState(false);
  const butlerMenuRef = useRef<HTMLDivElement | null>(null);

  const butlerLabel =
    selectedButlers.size === 0
      ? "All butlers"
      : `${selectedButlers.size} butler${selectedButlers.size === 1 ? "" : "s"}`;
  const circuitBreakerButtonClass = [
    "rounded border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors duration-fast focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
    breakerTripped
      ? "border-destructive/50 text-destructive hover:border-destructive hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
      : "border-border/60 text-muted-foreground disabled:cursor-default disabled:opacity-100",
  ].join(" ");

  return (
    <div className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-y-2 border-b border-border/60 bg-background/95 px-6 py-2 backdrop-blur-sm">
      <div className="flex flex-wrap items-center gap-4">
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

        {/* Divider between filter groups */}
        <span aria-hidden="true" className="h-4 w-px bg-border/60" />

        {/* State filter — folded in from the retired /qa/investigations index */}
        <label className="flex items-center gap-1.5">
          <span className="sr-only">State</span>
          <select
            aria-label="Filter by state"
            value={state}
            onChange={(event) => onStateChange(event.target.value as StateFilter)}
            className="h-6 rounded border border-border/60 bg-transparent px-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {STATE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>

        {/* Butler multi-select — folded in from the retired /qa/investigations index */}
        <div className="relative" ref={butlerMenuRef}>
          <button
            type="button"
            aria-label={`Butlers: ${butlerLabel}`}
            aria-haspopup="menu"
            aria-expanded={butlerMenuOpen}
            onClick={() => setButlerMenuOpen((open) => !open)}
            className="flex h-6 items-center gap-1 rounded border border-border/60 px-1.5 font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground transition-colors duration-fast hover:text-foreground"
          >
            {butlerLabel}
            <ChevronDownIcon className="size-3" aria-hidden="true" />
          </button>
          {butlerMenuOpen && (
            <div
              role="menu"
              tabIndex={-1}
              className="absolute left-0 top-7 z-30 w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
              onMouseLeave={() => setButlerMenuOpen(false)}
            >
              {butlerOptions.map((name) => (
                <button
                  key={name}
                  type="button"
                  role="menuitemcheckbox"
                  aria-checked={selectedButlers.has(name)}
                  onClick={() => onToggleButler(name)}
                  className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-[11px] text-foreground outline-none hover:bg-accent hover:text-accent-foreground focus-visible:bg-accent focus-visible:text-accent-foreground"
                >
                  <span className="w-3 text-center" aria-hidden="true">
                    {selectedButlers.has(name) ? "x" : ""}
                  </span>
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={breakerTripped ? onResetCircuitBreaker : undefined}
          disabled={!breakerTripped || resetCircuitBreakerPending}
          aria-label={breakerTripped ? "Reset QA circuit breaker" : "QA circuit breaker closed"}
          className={circuitBreakerButtonClass}
        >
          {breakerTripped
            ? resetCircuitBreakerPending
              ? "Resetting…"
              : "Reset breaker"
            : "Circuit breaker closed"}
        </button>

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
// Patrol pulse strip — links the overview to patrol detail (JARVIS audit
// move 14: "link patrols from the overview"), so the orphaned /qa/patrols/:id
// route is reachable from somewhere other than the butler-detail QA tab.
// ---------------------------------------------------------------------------

const PATROL_STRIP_LIMIT = 8;

function statusDotClass(status: string): string {
  if (status === "error" || status === "failed") return "bg-destructive";
  // Backend patrol status is "findings_dispatched" (qa.py _VALID_PATROL_STATUSES),
  // not "dispatched" -- the stale check never matched, so dispatched patrols
  // rendered clean-green instead of amber (bu-qvnce.2).
  if (status === "findings_dispatched") return "bg-[var(--amber)]";
  return "bg-[var(--green)]";
}

function PatrolPulseStrip() {
  const patrols = useQaPatrols({ limit: PATROL_STRIP_LIMIT });
  const rows = patrols.data?.data ?? [];

  // Loading: no fabricated strip until the first response lands. But a failed
  // patrols query is NOT "no patrols ran" — vanishing here makes a patrols-API
  // outage indistinguishable from a genuinely clear stream. Name the source
  // with a one-line degraded note instead (bu-jad4j.6 — the fleet
  // degraded-envelope convention; see CLAUDE.md API Conventions and
  // SourceDegradedNote). A reachable-but-empty source (no error, zero patrols)
  // still legitimately hides the strip.
  if (patrols.isLoading) return null;

  if (patrols.isError) {
    return (
      <div className="border-b border-border/60 px-6 py-2">
        <SourceDegradedNote
          label="Recent patrols"
          detail="patrol source unreachable — recent patrols unavailable"
          onRetry={() => void patrols.refetch()}
          testId="qa-patrol-strip-source-unavailable"
        />
      </div>
    );
  }

  if (rows.length === 0) return null;

  return (
    <div className="flex items-center gap-2 overflow-x-auto border-b border-border/60 px-6 py-2">
      <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        Recent patrols
      </span>
      {rows.map((patrol) => (
        <Tip
          key={patrol.id}
          content={`${patrol.status} · ${patrol.findings_count} findings`}
        >
          <Link
            to={`/qa/patrols/${patrol.id}`}
            className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 hover:bg-accent/60"
          >
            <span
              aria-hidden="true"
              className={`inline-block h-1.5 w-1.5 rounded-full ${statusDotClass(patrol.status)}`}
            />
            <Time
              value={patrol.started_at}
              mode="relative"
              className="font-mono text-[10px] text-muted-foreground"
              showTitle={false}
            />
          </Link>
        </Tip>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QaOverviewPage
// ---------------------------------------------------------------------------

export default function QaOverviewPage() {
  const [params, setParams] = useSearchParams();

  // All filters are URL-persisted (bu-86c4c.19 — folds the retired
  // /qa/investigations index's richer filter set in here, and makes the
  // lighter severity/since filters that already existed shareable too).
  const severity = (params.get("sev") as SeverityFilter | null) ?? "all";
  const since = (params.get("since") as SinceFilter | null) ?? "7d";
  const state = (params.get("state") as StateFilter | null) ?? "all";
  const selectedButlers = useMemo(
    () => new Set((params.get("butler") ?? "").split(",").filter(Boolean)),
    [params],
  );

  const selectedCaseId = params.get("case") ?? undefined;

  const summary = useQaSummary();
  const forcePatrol = useForceQaPatrol();
  const resetCircuitBreaker = useResetQaCircuitBreaker();
  const butlersQuery = useButlers();
  const cases = useQaCases({
    sev: severity === "all" ? undefined : severity,
    since,
    ...(state !== "all" ? { state } : {}),
    ...(selectedButlers.size > 0 ? { butler: Array.from(selectedButlers).sort() } : {}),
  });

  function setFilterParam(key: string, value: string | null) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value === null || value === "") {
        next.delete(key);
      } else {
        next.set(key, value);
      }
      return next;
    });
  }

  function handleToggleButler(name: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      const currentButlers = new Set((next.get("butler") ?? "").split(",").filter(Boolean));
      if (currentButlers.has(name)) {
        currentButlers.delete(name);
      } else {
        currentButlers.add(name);
      }
      if (currentButlers.size > 0) {
        next.set("butler", Array.from(currentButlers).sort().join(","));
      } else {
        next.delete("butler");
      }
      return next;
    });
  }

  function handleForcePatrol() {
    if (forcePatrol.isPending) return;
    if (!window.confirm("Trigger an immediate QA patrol cycle now?")) return;
    forcePatrol.mutate(undefined, {
      onSuccess: (res) => {
        // The endpoint returns HTTP 202 even when no patrol actually ran -- the
        // QA daemon may be unreachable or a cycle may already be in progress.
        // `triggered` is the honest signal; a 2xx alone is not. Only toast
        // success when a patrol was genuinely triggered; otherwise surface the
        // response's reason as a warning so a suppressed dispatch is not
        // mistaken for a dispatched one (bu-533qx.4).
        if (res.data?.triggered) {
          toast.success(res.data.message ?? "Patrol triggered");
        } else {
          toast.warning(res.data?.message ?? "Patrol not triggered");
        }
      },
      onError: (err) => {
        toast.error(
          `Force patrol failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  function handleResetCircuitBreaker() {
    if (resetCircuitBreaker.isPending) return;
    if (!window.confirm("Reset the QA circuit breaker and allow new investigations?")) return;
    resetCircuitBreaker.mutate(undefined, {
      onSuccess: (res) => {
        toast.success(res.data?.message ?? "Circuit breaker reset");
      },
      onError: (err) => {
        toast.error(
          `Circuit breaker reset failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
    });
  }

  // Palette verb (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5). Reuses
  // the sticky top bar's existing "Force patrol" handler, confirm dialog and
  // all -- no new behavior.
  const qaCommands = useMemo<PaletteCommand[]>(
    () => [
      {
        id: "qa-force-patrol",
        label: "Force patrol",
        keywords: ["run", "patrol", "trigger"],
        perform: handleForcePatrol,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- handleForcePatrol is recreated every render and closes over forcePatrol directly.
    [forcePatrol.isPending],
  );
  useRegisterCommands(qaCommands);

  const casesData = cases.data?.data ?? [];

  const butlerOptions = useMemo(() => {
    const liveNames = butlersQuery.data?.data.map((butler) => butler.name) ?? [];
    const caseButlers = cases.data?.data.map((c) => c.butler) ?? [];
    return Array.from(new Set([...liveNames, ...caseButlers])).sort();
  }, [butlersQuery.data?.data, cases.data?.data]);

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
  const breakerTripped = summaryData?.circuit_breaker.tripped ?? false;

  return (
    <div className="flex min-h-full flex-col">
      <StickyTopBar
        severity={severity}
        onSeverityChange={(v) => setFilterParam("sev", v === "all" ? null : v)}
        since={since}
        onSinceChange={(v) => setFilterParam("since", v === "7d" ? null : v)}
        state={state}
        onStateChange={(v) => setFilterParam("state", v === "all" ? null : v)}
        selectedButlers={selectedButlers}
        onToggleButler={handleToggleButler}
        butlerOptions={butlerOptions}
        breakerTripped={breakerTripped}
        onResetCircuitBreaker={handleResetCircuitBreaker}
        resetCircuitBreakerPending={resetCircuitBreaker.isPending}
        onForcePatrol={handleForcePatrol}
        forcePatrolPending={forcePatrol.isPending}
      />

      <PageHeader summary={summary} />

      {/* Verdict opener -- staffer status/patrol/breaker/credentials fields
          GET /api/qa/summary already returns but the KPI strip below never
          rendered (JARVIS pursuit move 9). */}
      <div className="border-b border-border/60 px-6 py-3">
        <QaVerdictOpener summary={summary} />
      </div>

      {/* KPI strip */}
      <div className="border-b border-border/60 px-6 py-4">
        <QaKpiStrip kpis={summaryData?.kpis} active={summaryData?.active_breakdown} />
      </div>

      <PatrolPulseStrip />

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
              hasMore={cases.data?.meta?.has_more ?? false}
              totalCount={cases.data?.meta?.total}
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
