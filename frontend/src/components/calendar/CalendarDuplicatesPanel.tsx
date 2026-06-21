/**
 * Calendar duplicate-review panel (bu-fol6y) — the FE surface for the
 * cross-source dedup backend shipped in bu-tjo2m1 / PR #2674.
 *
 * The workspace read silently collapses cross-source duplicate events (the same
 * Google event synced into multiple butler schemas, cross-calendar copies). This
 * panel makes that invisible behaviour reviewable:
 *
 *  - **Cluster list** ← GET /calendar/workspace/duplicates: every group of >1
 *    members the dedup would collapse, showing the kept survivor + the copies it
 *    hides, the match pass (origin-ref vs title), and the member count.
 *  - **Keep-separate toggle** → POST /calendar/workspace/duplicates/keep-separate:
 *    pins a cluster so the read stops collapsing it (every copy shows again).
 *  - **Match-strategy / noisy-threshold control** → PATCH /calendar/workspace/dedup-rules:
 *    tunes which collapse passes run and the minimum cluster size surfaced.
 *
 * Honest empty-state: `available=false` (the read could not run) renders
 * "review unavailable" — distinct from `available=true` with no clusters, which
 * genuinely means nothing is being collapsed in this window.
 *
 * Prop-driven (query + mutations are owned by the page) so it is trivially
 * unit-testable, mirroring {@link CalendarProposalsPanel}.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import type {
  CalendarDedupMatchStrategy,
  CalendarDuplicateCluster,
  CalendarDuplicatesResponse,
  UnifiedCalendarEntry,
} from "@/api/types.ts";
import type {
  usePatchCalendarDedupRules,
  useSetCalendarKeepSeparate,
} from "@/hooks/use-calendar-workspace.ts";
import { formatEventTime, tzDayKey } from "@/lib/calendar-grid.ts";
import { cn } from "@/lib/utils.ts";

/** Pill geometry shared with the calendar toolbar (Design Language §4c). */
const PILL =
  "inline-flex items-center justify-center gap-1.5 h-7 rounded-[3px] border px-2.5 " +
  "font-mono text-[11px] leading-none transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30 " +
  "disabled:pointer-events-none disabled:opacity-40";

const MATCH_STRATEGIES: { value: CalendarDedupMatchStrategy; label: string; hint: string }[] = [
  { value: "exact", label: "Exact", hint: "Only identical origin-ref copies" },
  { value: "balanced", label: "Balanced", hint: "Origin-ref + same title/time" },
  { value: "aggressive", label: "Aggressive", hint: "Also ignores title punctuation" },
];

/** Human label for a single entry's when (matches the proposals panel format). */
function whenLabel(entry: UnifiedCalendarEntry, timezone: string): string {
  const start = new Date(entry.start_at);
  const end = new Date(entry.end_at);
  const startValid = !Number.isNaN(start.getTime());
  const endValid = !Number.isNaN(end.getTime());
  if (!startValid) return "";
  const sameDay = endValid && tzDayKey(start, timezone) === tzDayKey(end, timezone);
  const head = formatEventTime(start, timezone, "EEE, MMM d · HH:mm");
  if (!endValid) return head;
  return sameDay
    ? `${head}–${formatEventTime(end, timezone, "HH:mm")}`
    : `${head} – ${formatEventTime(end, timezone, "EEE, MMM d · HH:mm")}`;
}

/** Where a duplicate copy comes from (butler/source), for the collapsed list. */
function sourceLabel(entry: UnifiedCalendarEntry): string {
  return entry.butler_name || entry.source_key || "unknown source";
}

export interface CalendarDuplicatesPanelProps {
  /** Duplicate-review payload (clusters + active rules + availability). */
  data?: CalendarDuplicatesResponse;
  /** Whether the duplicates query is still loading (first fetch). */
  isLoading?: boolean;
  /** Whether the duplicates query errored. */
  isError?: boolean;
  /** The query error, when present. */
  error?: Error | null;
  /** Rules PATCH mutation (from {@link usePatchCalendarDedupRules}). */
  rulesMutation: ReturnType<typeof usePatchCalendarDedupRules>;
  /** Keep-separate toggle mutation (from {@link useSetCalendarKeepSeparate}). */
  keepSeparateMutation: ReturnType<typeof useSetCalendarKeepSeparate>;
  /** Workspace timezone used to render cluster times (not browser-local). */
  timezone: string;
}

export function CalendarDuplicatesPanel({
  data,
  isLoading = false,
  isError = false,
  error = null,
  rulesMutation,
  keepSeparateMutation,
  timezone,
}: CalendarDuplicatesPanelProps) {
  const rules = data?.rules;

  // Local draft of the noisy-threshold input so typing doesn't fire a PATCH per
  // keystroke; committed on blur / Enter. Kept in sync with the server value.
  const [thresholdDraft, setThresholdDraft] = useState<string>(
    rules ? String(rules.noisy_threshold) : "2",
  );
  // Cluster key currently mid-flight on a keep-separate toggle (disables its row).
  const [busyClusterKey, setBusyClusterKey] = useState<string | null>(null);

  useEffect(() => {
    if (rules) setThresholdDraft(String(rules.noisy_threshold));
  }, [rules]);

  async function applyStrategy(strategy: CalendarDedupMatchStrategy) {
    if (!rules || strategy === rules.match_strategy) return;
    try {
      await rulesMutation.mutateAsync({ match_strategy: strategy });
      toast.success(`Match strategy set to ${strategy}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update match strategy.");
    }
  }

  async function commitThreshold() {
    if (!rules) return;
    const parsed = Number.parseInt(thresholdDraft, 10);
    if (!Number.isFinite(parsed) || parsed < 2) {
      toast.error("Noisy threshold must be a whole number ≥ 2.");
      setThresholdDraft(String(rules.noisy_threshold));
      return;
    }
    if (parsed === rules.noisy_threshold) return;
    try {
      await rulesMutation.mutateAsync({ noisy_threshold: parsed });
      toast.success(`Noisy threshold set to ${parsed}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update noisy threshold.");
      setThresholdDraft(String(rules.noisy_threshold));
    }
  }

  async function toggleKeepSeparate(cluster: CalendarDuplicateCluster) {
    const next = !cluster.keep_separate;
    setBusyClusterKey(cluster.cluster_key);
    try {
      await keepSeparateMutation.mutateAsync({
        cluster_key: cluster.cluster_key,
        keep_separate: next,
        match_pass: cluster.match_pass,
        label: cluster.kept_entry.title,
      });
      toast.success(
        next ? "Cluster kept separate — copies will show." : "Cluster will collapse again.",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to update keep-separate.");
    } finally {
      setBusyClusterKey(null);
    }
  }

  if (isLoading) {
    return (
      <div data-testid="duplicates-panel" className="flex min-h-0 flex-1 flex-col gap-3">
        <p className="font-mono text-[11px] text-[var(--dim)]">Reviewing duplicates…</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div data-testid="duplicates-panel" role="alert" className="flex items-start gap-2 py-1">
        <span className="mt-[2px] font-mono text-[11px] text-[var(--red)]">●</span>
        <p className="text-sm text-[var(--fg)]">
          Failed to load duplicate review.{" "}
          <span className="text-[var(--mfg)]">
            {error instanceof Error ? error.message : "Unknown error"}
          </span>
        </p>
      </div>
    );
  }

  const clusters = data?.clusters ?? [];
  const available = data?.available ?? false;
  const rulesBusy = rulesMutation.isPending;

  return (
    <div data-testid="duplicates-panel" className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] pb-2">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.14em] text-[var(--mfg)]">
          Duplicate review
        </h2>
        <span className="font-mono text-[11px] tabular-nums text-[var(--dim)]">
          {clusters.length} cluster{clusters.length === 1 ? "" : "s"}
        </span>
      </div>

      {/* Dedup-rules control — match strategy + noisy threshold (PATCH dedup-rules). */}
      {rules ? (
        <div
          data-testid="dedup-rules-control"
          className="flex flex-wrap items-end gap-x-5 gap-y-3 rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3"
        >
          <div className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
              Match strategy
            </span>
            <div className="flex items-center gap-1.5">
              {MATCH_STRATEGIES.map((option) => {
                const active = rules.match_strategy === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    data-testid={`dedup-strategy-${option.value}`}
                    aria-pressed={active}
                    title={option.hint}
                    disabled={rulesBusy}
                    onClick={() => void applyStrategy(option.value)}
                    className={cn(
                      PILL,
                      active
                        ? "border-[var(--fg)] bg-[var(--fg)] text-[var(--bg)]"
                        : "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
                    )}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--mfg)]">
              Noisy threshold
            </span>
            <input
              type="number"
              min={2}
              max={1000}
              step={1}
              data-testid="dedup-threshold-input"
              aria-label="Noisy threshold (minimum cluster size)"
              value={thresholdDraft}
              disabled={rulesBusy}
              onChange={(e) => setThresholdDraft(e.target.value)}
              onBlur={() => void commitThreshold()}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void commitThreshold();
                }
              }}
              className="h-7 w-20 rounded-[3px] border border-[var(--border-strong)] bg-transparent px-2.5 font-mono text-[12px] tabular-nums text-[var(--fg)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--fg)]/30"
            />
          </label>
        </div>
      ) : null}

      {!available ? (
        <p data-testid="duplicates-unavailable" className="font-mono text-[11px] text-[var(--mfg)]">
          Duplicate review is unavailable right now — the underlying read could not run. Try again
          shortly.
        </p>
      ) : clusters.length === 0 ? (
        <p data-testid="duplicates-empty" className="font-mono text-[11px] text-[var(--mfg)]">
          No cross-source duplicates in this range — nothing is being collapsed.
        </p>
      ) : (
        <ul className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
          {clusters.map((cluster) => {
            const isBusy = busyClusterKey === cluster.cluster_key;
            const kept = cluster.kept_entry;
            const when = whenLabel(kept, timezone);
            return (
              <li
                key={cluster.cluster_key}
                data-testid="duplicate-cluster"
                data-cluster-key={cluster.cluster_key}
                className="rounded-[4px] border border-[var(--border)] bg-foreground/[0.015] p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-col gap-1">
                    <span className="truncate text-sm font-medium text-[var(--fg)]">
                      {kept.title}
                    </span>
                    {when ? (
                      <span className="font-mono text-[11px] tabular-nums text-[var(--mfg)]">
                        {when}
                      </span>
                    ) : null}
                  </div>
                  <span
                    title={`Matched on ${cluster.match_pass}`}
                    className="shrink-0 rounded-[3px] border border-[var(--border-strong)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em] text-[var(--mfg)]"
                  >
                    {cluster.match_pass === "origin_ref" ? "origin-ref" : "title"} ·{" "}
                    {cluster.member_count}
                  </span>
                </div>

                {/* The copies the dedup hides (or shows, when kept separate). */}
                <div className="mt-2 flex flex-col gap-1 border-l-2 border-[var(--border-strong)] pl-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--dim)]">
                    Keeps {sourceLabel(kept)} · {cluster.duplicate_entries.length} duplicate
                    {cluster.duplicate_entries.length === 1 ? "" : "s"}
                  </span>
                  {cluster.duplicate_entries.map((dup) => (
                    <span
                      key={dup.entry_id}
                      data-testid="duplicate-copy"
                      className="truncate font-mono text-[11px] text-[var(--mfg)]"
                    >
                      {sourceLabel(dup)}
                      {dup.calendar_id ? ` · ${dup.calendar_id}` : ""}
                    </span>
                  ))}
                </div>

                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    data-testid="duplicate-keep-separate"
                    aria-pressed={cluster.keep_separate}
                    disabled={isBusy}
                    onClick={() => void toggleKeepSeparate(cluster)}
                    className={cn(
                      PILL,
                      cluster.keep_separate
                        ? "border-[var(--fg)] bg-[var(--fg)] text-[var(--bg)] hover:opacity-90"
                        : "border-[var(--border-strong)] text-[var(--mfg)] hover:text-[var(--fg)]",
                    )}
                  >
                    {cluster.keep_separate ? "Kept separate" : "Keep separate"}
                  </button>
                  <span className="font-mono text-[10px] text-[var(--dim)]">
                    {cluster.keep_separate
                      ? "Not collapsed — all copies show in the calendar."
                      : "Collapsed to one entry in the calendar."}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default CalendarDuplicatesPanel;
