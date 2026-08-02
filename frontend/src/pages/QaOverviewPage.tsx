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

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDownIcon } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import type { CircuitBreakerAttempt, QaCaseSummary } from "@/api/types";
import { CaseDossier, CaseList, QaKpiStrip, QaVerdictOpener } from "@/components/qa";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ListTriageFooterHint } from "@/components/ui/list-triage-footer";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Time } from "@/components/ui/time";
import { Tip } from "@/components/ui/tip";
import { useButlers } from "@/hooks/use-butlers";
import { useListTriage } from "@/hooks/use-list-triage";
import {
  useForceQaPatrol,
  useQaCases,
  useQaCircuitBreaker,
  useQaPatrols,
  useQaSummary,
  useResetQaCircuitBreaker,
} from "@/hooks/use-qa";
import { useRegisterCommands, type PaletteCommand } from "@/lib/command-registry";
import { getQaPatrolStatusPresentation } from "@/lib/qa-patrol-status";

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
  { value: "failed", label: "Failed" },
  { value: "escalated", label: "Escalated" },
];

/** Human-readable label for the active time range, used in CaseList. */
function caseListSinceLabel(since: SinceFilter): string {
  if (since === "all") return "Cases · all cases";
  return `Cases · last ${since}`;
}

// ---------------------------------------------------------------------------
// Circuit-breaker tri-state (bu-533qx.2)
//
// The breaker has three honest states, not two: `closed` (proven healthy),
// `tripped` (proven halted), and `unknown` (the feeding query is loading or
// errored, so the breaker's real state cannot be proven). A dead summary
// query must NEVER paint the calm `closed` over a state we cannot see —
// unknown is named, not defaulted away. Reset is only offered under `tripped`,
// where trippedness is proven; under `unknown` reset is withheld because we
// cannot prove the breaker is actually open.
// ---------------------------------------------------------------------------

type BreakerState = "closed" | "tripped" | "unknown";

/** Derive the breaker's tri-state from its feeding query. Loading and error
 *  both map to `unknown` — an errored query forces `unknown` even if a stale
 *  success is still cached, so a dead feed is never rendered as calm. */
function deriveBreakerState({
  isError,
  tripped,
}: {
  isError: boolean;
  tripped: boolean | undefined;
}): BreakerState {
  if (isError || tripped === undefined) return "unknown";
  return tripped ? "tripped" : "closed";
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
  breakerState,
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
  breakerState: BreakerState;
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
  const breakerTripped = breakerState === "tripped";
  const breakerUnknown = breakerState === "unknown";
  const circuitBreakerButtonClass = [
    "rounded border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors duration-fast focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
    breakerTripped
      ? "border-destructive/50 text-destructive hover:border-destructive hover:text-destructive disabled:cursor-not-allowed disabled:opacity-50"
      : breakerUnknown
        ? "border-[var(--amber)]/50 text-[var(--amber-text)] disabled:cursor-default disabled:opacity-100"
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
          aria-label={
            breakerTripped
              ? "Reset QA circuit breaker"
              : breakerUnknown
                ? "QA circuit breaker state unknown"
                : "QA circuit breaker closed"
          }
          className={circuitBreakerButtonClass}
        >
          {breakerTripped
            ? resetCircuitBreakerPending
              ? "Resetting…"
              : "Reset breaker"
            : breakerUnknown
              ? "Circuit breaker unknown"
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
// Evidence-bearing reset confirm (bu-533qx.2)
//
// Resetting the breaker re-admits dispatches after five consecutive failures.
// The operator must not reset blind: the five failing attempts that tripped
// the breaker are already on the wire (GET /api/qa/circuit-breaker →
// recent_attempts), so the confirm shows them as the evidence the reset acts
// on. Replaces the bare window.confirm the toolbar used to fire.
// ---------------------------------------------------------------------------

function ResetBreakerDialog({
  open,
  onOpenChange,
  attempts,
  attemptsAvailable,
  onConfirm,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  attempts: CircuitBreakerAttempt[];
  attemptsAvailable: boolean;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent data-testid="qa-breaker-reset-dialog">
        <AlertDialogHeader>
          <AlertDialogTitle>Reset the QA circuit breaker?</AlertDialogTitle>
          <AlertDialogDescription>
            The breaker tripped after {attempts.length > 0 ? "these" : "five"} consecutive
            investigation failures. Resetting re-admits new dispatches. The failure history stays
            recorded and un-fabricated.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {attempts.length > 0 ? (
          <ul
            data-testid="qa-breaker-reset-evidence"
            className="max-h-56 space-y-1 overflow-y-auto border-y border-border/60 py-2 font-mono text-[11px]"
          >
            {attempts.map((attempt) => (
              <li
                key={attempt.id}
                data-testid="qa-breaker-reset-attempt"
                className="flex items-baseline justify-between gap-3"
              >
                <span className="truncate text-muted-foreground">{attempt.id}</span>
                <span className="text-destructive uppercase tracking-[0.08em]">
                  {attempt.status}
                </span>
                <Time
                  value={attempt.closed_at}
                  mode="relative"
                  className="shrink-0 text-muted-foreground tabular-nums"
                />
              </li>
            ))}
          </ul>
        ) : (
          <p
            data-testid="qa-breaker-reset-evidence-unavailable"
            className="border-y border-border/60 py-2 font-mono text-[11px] text-[var(--amber-text)]"
          >
            {attemptsAvailable
              ? "No failing attempts on record: the breaker's evidence is empty."
              : "Failing-attempt evidence unavailable: the circuit-breaker source is unreachable."}
          </p>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={pending}
            data-testid="qa-breaker-reset-confirm"
            onClick={(event) => {
              // Keep the dialog mounted through the mutation so its pending
              // state is visible; the caller closes it on settle.
              event.preventDefault();
              onConfirm();
            }}
          >
            {pending ? "Resetting…" : "Reset breaker"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
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
          detail="patrol source unreachable: recent patrols unavailable"
          onRetry={() => void patrols.refetch()}
          testId="qa-patrol-strip-source-unavailable"
        />
      </div>
    );
  }

  if (rows.length === 0) return null;

  return (
    <FetchingDim isFetching={patrols.isFetching && !patrols.isLoading}>
      <div className="flex items-center gap-2 overflow-x-auto border-b border-border/60 px-6 py-2">
        <span className="shrink-0 font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          Recent patrols
        </span>
        {rows.map((patrol) => {
          const status = getQaPatrolStatusPresentation(patrol.status);
          return (
            <Tip
              key={patrol.id}
              content={`${status.label} · ${patrol.findings_count} findings`}
            >
              <Link
                to={`/qa/patrols/${patrol.id}`}
                className="flex shrink-0 items-center gap-1 rounded px-1 py-0.5 hover:bg-accent/60"
              >
                <span className="sr-only">
                  {status.label} patrol, {patrol.findings_count} findings
                </span>
                <span
                  aria-hidden="true"
                  className={`inline-block h-1.5 w-1.5 rounded-full ${status.dotClassName}`}
                />
                <Time
                  value={patrol.started_at}
                  mode="relative"
                  className="font-mono text-[10px] text-muted-foreground"
                  showTitle={false}
                />
              </Link>
            </Tip>
          );
        })}
      </div>
    </FetchingDim>
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
  // The five failing attempts that tripped the breaker — shown as evidence in
  // the reset confirm dialog (bu-533qx.2). GET /api/qa/circuit-breaker already
  // carries them; the toolbar just never consumed them.
  const circuitBreaker = useQaCircuitBreaker();
  const [resetDialogOpen, setResetDialogOpen] = useState(false);
  // Force Patrol confirm (bu-ep4ks.11 — the safety envelope for consequential
  // actions): this used to gate on a bare window.confirm, inconsistent with
  // the fleet's AlertDialog everywhere else on this same page.
  const [forcePatrolDialogOpen, setForcePatrolDialogOpen] = useState(false);
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
    setForcePatrolDialogOpen(true);
  }

  function confirmForcePatrol() {
    if (forcePatrol.isPending) return;
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
      onSettled: () => setForcePatrolDialogOpen(false),
    });
  }

  // Open the evidence-bearing confirm instead of a bare window.confirm — the
  // operator sees the five failing attempts before re-admitting dispatches
  // (bu-533qx.2). Only reachable while the breaker is proven tripped.
  function handleResetCircuitBreaker() {
    if (resetCircuitBreaker.isPending) return;
    setResetDialogOpen(true);
  }

  function confirmResetCircuitBreaker() {
    if (resetCircuitBreaker.isPending) return;
    resetCircuitBreaker.mutate(undefined, {
      onSuccess: (res) => {
        toast.success(res.data?.message ?? "Circuit breaker reset");
      },
      onError: (err) => {
        toast.error(
          `Circuit breaker reset failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        );
      },
      onSettled: () => setResetDialogOpen(false),
    });
  }

  // Breaker tri-state derived from the summary query (its feed for the
  // toolbar). Loading/error → unknown; never the calm default `closed`
  // (bu-533qx.2).
  const summaryData = summary.data?.data;
  const breakerState = deriveBreakerState({
    isError: summary.isError,
    tripped: summaryData?.circuit_breaker.tripped,
  });

  // Palette verbs (bu-t64p2 -- reachability sweep, bu-qvnce.11 slice 5).
  // "Force patrol" reuses the sticky top bar's handler. "Reset circuit breaker"
  // is registered ONLY while the breaker is proven tripped (bu-533qx.2) — under
  // closed there is nothing to reset, and under unknown trippedness is not
  // proven, so offering a reset would be a calm assertion the surface cannot back.
  const qaCommands = useMemo<PaletteCommand[]>(() => {
    const commands: PaletteCommand[] = [
      {
        id: "qa-force-patrol",
        label: "Force patrol",
        keywords: ["run", "patrol", "trigger"],
        perform: handleForcePatrol,
      },
    ];
    if (breakerState === "tripped") {
      commands.push({
        id: "qa-reset-circuit-breaker",
        label: "Reset circuit breaker",
        keywords: ["breaker", "reset", "dispatch", "unblock"],
        perform: handleResetCircuitBreaker,
      });
    }
    return commands;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the handlers are recreated every render and close over their mutations directly; re-register only when pending or breaker state changes.
  }, [forcePatrol.isPending, resetCircuitBreaker.isPending, breakerState]);
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

  // j/k case-rail keyboard path (bu-mmdef, keyboard chassis remainder -- the
  // rail was mouse-only, cut from #3586's scope for its distinct interaction
  // model). Selecting a case already drives the dossier column via the same
  // ?case= URL param a click would -- there is no separate act-verb, j/k
  // navigation IS the selection, same shape as an issues-panel roving cursor
  // with no verbs declared.
  const caseIds = useMemo(
    () => casesData.map((c) => c.id),
    [cases.data?.data], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const { hints: caseTriageHints } = useListTriage({
    ids: caseIds,
    selectedId: effectiveCaseId ?? null,
    onSelect: handleCaseSelect,
  });

  // Keep DOM focus in sync with the current selection (bu-ep4ks.12 focus-
  // reality doctrine, mirroring IssuesPage's identical effect). Matches by
  // attribute value rather than interpolating the case id into a CSS
  // selector -- ids are owner/server data, not selector syntax.
  useEffect(() => {
    if (!effectiveCaseId) return;
    const nodes = document.querySelectorAll<HTMLElement>("[data-case-id]");
    for (const node of nodes) {
      if (node.getAttribute("data-case-id") === effectiveCaseId) {
        node.focus({ preventScroll: true });
        break;
      }
    }
  }, [effectiveCaseId]);

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
        breakerState={breakerState}
        onResetCircuitBreaker={handleResetCircuitBreaker}
        resetCircuitBreakerPending={resetCircuitBreaker.isPending}
        onForcePatrol={handleForcePatrol}
        forcePatrolPending={forcePatrol.isPending}
      />

      <ResetBreakerDialog
        open={resetDialogOpen}
        onOpenChange={(open) => {
          // Don't let a backdrop/Escape dismiss abandon an in-flight reset.
          if (resetCircuitBreaker.isPending) return;
          setResetDialogOpen(open);
        }}
        attempts={circuitBreaker.isError ? [] : (circuitBreaker.data?.data.recent_attempts ?? [])}
        attemptsAvailable={!circuitBreaker.isError}
        onConfirm={confirmResetCircuitBreaker}
        pending={resetCircuitBreaker.isPending}
      />

      <ConfirmDialog
        open={forcePatrolDialogOpen}
        onOpenChange={(open) => {
          // Don't let a backdrop/Escape dismiss abandon an in-flight patrol dispatch.
          if (forcePatrol.isPending) return;
          setForcePatrolDialogOpen(open);
        }}
        title="Trigger an immediate QA patrol cycle now?"
        description="Runs a new patrol cycle outside the normal schedule."
        confirmLabel="Force patrol"
        pendingLabel="Patrolling…"
        pending={forcePatrol.isPending}
        onConfirm={confirmForcePatrol}
        testId="qa-force-patrol-dialog"
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
        <FetchingDim isFetching={summary.isFetching && !summary.isLoading && !summary.isError}>
          <QaKpiStrip kpis={summaryData?.kpis} active={summaryData?.active_breakdown} />
        </FetchingDim>
      </div>

      <PatrolPulseStrip />

      {/* Two-pane body: case rail + dossier */}
      <div className="flex flex-1 overflow-hidden">
        {/* Case rail */}
        <FetchingDim
          isFetching={cases.isFetching && !cases.isLoading && !cases.isError}
          className="shrink-0 overflow-y-auto border-r border-border/60 px-4 py-4"
        >
          {cases.isLoading ? (
            <p className="font-serif text-sm italic text-muted-foreground">Loading cases…</p>
          ) : cases.isError ? (
            <SourceDegradedNote
              label="Case rail"
              detail="unavailable"
              onRetry={() => void cases.refetch()}
              testId="qa-case-rail-degraded"
            />
          ) : casesData.length === 0 ? (
            <p className="font-serif text-sm italic text-muted-foreground">
              Nothing in the dossier.
            </p>
          ) : (
            <>
              <CaseList
                cases={casesData}
                selectedId={effectiveCaseId ?? null}
                onSelect={handleCaseSelect}
                headerLabel={caseListSinceLabel(since)}
                hasMore={cases.data?.meta?.has_more ?? false}
                totalCount={cases.data?.meta?.total}
              />
              {/* Shared footer hint strip (bu-qvnce.11 slice 4) -- advertises
                  the EXACT j/k bindings useListTriage just registered. */}
              <ListTriageFooterHint bindings={caseTriageHints} />
            </>
          )}
        </FetchingDim>

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
