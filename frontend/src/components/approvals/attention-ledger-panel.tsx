/**
 * AttentionLedgerPanel — the attention ledger's first reader, surfaced in
 * the Trust Console (bu-tdd4k.4).
 *
 * `public.attention_ledger` has recorded every notify()/insight-delivery-
 * cycle egress decision since bu-qvnce.8, but nothing read it back until
 * this panel: a source could be suppressed-but-never-delivered
 * indefinitely with no visible symptom. That was not hypothetical --
 * secrets_lifecycle's `deliver()` had a bare (unqualified-schema)
 * `butler_registry` lookup that silently suppressed every push for days
 * (120 suppressed / 0 delivered) until bu-tdd4k.2 fixed it.
 *
 * This panel renders the per-`origin_butler` delivery-vs-suppression rollup
 * (`GET /api/attention/ledger/summary`) as a table, with any
 * `suppressed_never_delivered` source surfaced in a loud RED banner --
 * deliberately distinct from the page's existing amber `SourceDegradedNote`
 * vocabulary, which means "the data source is unreachable," not "a source
 * is actively failing to deliver." This is a genuine finding, not a
 * degraded-infra signal, so it must not be dismissible as calm.
 *
 * Below it, the insight efficacy rows (`GET /api/attention/efficacy`,
 * bu-s11n0s.15 S2): per class and priority band, how often a premise
 * resolved when the insight was delivered versus naturally held back.
 * Observational and observe-only; it shapes nothing.
 */

import { useQuery } from "@tanstack/react-query";

import { getAttentionLedgerSummary, getInsightEfficacy } from "@/api/index.ts";
import type { AttentionSourceSummary, InsightClassEfficacy } from "@/api/index.ts";
import { QueryBoundary } from "@/components/ui/query-boundary.tsx";
import { formatDurationCompact } from "@/lib/format-duration";
import { cn } from "@/lib/utils";
import { Time } from "@/components/ui/time";

const Q = {
  summary: () => ["attention-ledger", "summary"] as const,
  efficacy: () => ["attention-ledger", "efficacy"] as const,
};

function fmtTs(iso: string | null) {
  if (!iso) return "—";
  return <Time value={iso} mode="absolute" precision="minute" compact />;
}

// ---------------------------------------------------------------------------
// Flagged-source banner -- the marquee signal this panel exists to surface.
// Red (not amber): this is a genuine "a source is failing" finding, not a
// "the data source is unreachable" degraded-infra note.
// ---------------------------------------------------------------------------

function FlaggedSourceBanner({ sources }: { sources: string[] }) {
  if (sources.length === 0) return null;
  return (
    <div
      role="alert"
      data-testid="attention-ledger-flagged-banner"
      className={cn(
        "mb-3 flex items-start gap-2 rounded-sm border border-[var(--red)]/40",
        "bg-[var(--red)]/10 px-3 py-2 text-xs text-[var(--red-text)]",
      )}
    >
      <span
        className="mt-0.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--red)]"
        aria-hidden="true"
      />
      <span className="font-medium">
        {sources.length === 1
          ? `${sources[0]} is suppressed but has never delivered in this window.`
          : `${sources.length} sources are suppressed but have never delivered in this window: ${sources.join(", ")}.`}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-source row
// ---------------------------------------------------------------------------

function SourceRow({ summary }: { summary: AttentionSourceSummary }) {
  return (
    <tr
      data-testid="attention-source-row"
      data-flagged={summary.suppressed_never_delivered}
      className="border-t border-border/50 first:border-t-0"
    >
      <th
        scope="row"
        className={cn(
          "max-w-0 truncate py-2 pr-2 text-left align-middle font-medium",
          summary.suppressed_never_delivered ? "text-[var(--red-text)]" : "text-foreground",
        )}
      >
        {summary.origin_butler}
      </th>
      <td className="px-1 py-2 text-right align-middle text-foreground">{summary.delivered}</td>
      <td className="px-1 py-2 text-right align-middle text-muted-foreground">{summary.coalesced}</td>
      <td className="px-1 py-2 text-right align-middle text-muted-foreground">{summary.deferred}</td>
      <td
        className={cn(
          "px-1 py-2 text-right align-middle",
          summary.suppressed_never_delivered
            ? "text-[var(--red-text)] font-semibold"
            : "text-muted-foreground",
        )}
      >
        {summary.suppressed}
      </td>
      {/* Genuine terminal failures (bu-hmdqz.3) -- always red-toned when
          non-zero, never rendered as calm/neutral like coalesced/deferred:
          a "failed" row is never automatically retried unless the caller
          explicitly queued a retry envelope. */}
      <td
        className={cn(
          "px-1 py-2 text-right align-middle",
          summary.failed > 0 ? "text-[var(--red-text)] font-semibold" : "text-muted-foreground",
        )}
      >
        {summary.failed}
      </td>
      <td
        className={cn(
          "px-1 py-2 text-right align-middle",
          summary.expired_unseen > 0
            ? "text-[var(--amber-text)] font-semibold"
            : "text-muted-foreground",
        )}
      >
        {summary.expired_unseen}
      </td>
      <td className="px-1 py-2 text-right align-middle text-muted-foreground">{summary.total}</td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Insight efficacy rows (bu-s11n0s.15 S2) -- observational, observe-only
// ---------------------------------------------------------------------------

const VERDICT_LABEL: Record<InsightClassEfficacy["verdict"], string> = {
  effective: "effective",
  self_resolving: "self-resolving",
  ignored: "ignored",
  unmeasured: "unmeasured",
};

function fmtMedian(seconds: number | null) {
  return seconds == null ? "—" : formatDurationCompact(seconds * 1000);
}

function arm(resolved: number, n: number, median: number | null) {
  return `${resolved}/${n} · ${fmtMedian(median)}`;
}

function EfficacySection() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: Q.efficacy(),
    queryFn: () => getInsightEfficacy(),
  });
  // isError renders through QueryBoundary below, never as this empty list.
  const rows: InsightClassEfficacy[] = data ? data.data : [];
  const sourceUnavailable = data?.source_available === false;

  return (
    <div className="mt-6" data-testid="insight-efficacy-section">
      <div className="text-sm text-muted-foreground">
        Insight efficacy · observational: delivered vs. naturally held, resolved/n · median time to
        resolve
      </div>
      <div className="mt-2">
        <QueryBoundary
          isLoading={isLoading}
          isError={isError}
          error={error}
          isEmpty={rows.length === 0 && !sourceUnavailable}
          onRetry={() => void refetch()}
          sourceLabel="insight efficacy"
          loadingFallback={<div className="font-mono text-sm text-muted-foreground">loading…</div>}
          emptyFallback={
            <div className="font-mono text-sm text-muted-foreground">
              No insight outcomes have been measured yet.
            </div>
          }
        >
          {sourceUnavailable && (
            <div
              role="alert"
              data-testid="insight-efficacy-source-unavailable"
              className="mb-3 rounded-sm border border-[var(--amber)]/40 bg-[var(--amber)]/10 px-3 py-2 text-xs font-medium text-[var(--amber-text)]"
            >
              Insight efficacy: unavailable
            </div>
          )}
          <table className="w-full table-fixed border-collapse font-mono text-xs">
            <caption className="sr-only">Insight efficacy per class and priority band</caption>
            <thead className="text-[10px] uppercase tracking-widest text-muted-foreground">
              <tr>
                <th scope="col" className="pb-1 pr-2 text-left font-normal">Class</th>
                <th scope="col" className="px-1 pb-1 text-left font-normal">Band</th>
                <th scope="col" className="px-1 pb-1 text-right font-normal">Delivered</th>
                <th scope="col" className="px-1 pb-1 text-right font-normal">Held</th>
                <th scope="col" className="px-1 pb-1 text-right font-normal">Verdict</th>
                <th scope="col" className="px-1 pb-1 text-right font-normal">Computed</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={`${r.origin_butler}/${r.category}/${r.priority_band}`}
                  data-testid="insight-efficacy-row"
                  className="border-t border-border/50 first:border-t-0"
                >
                  <th
                    scope="row"
                    className="max-w-0 truncate py-2 pr-2 text-left font-medium text-foreground"
                  >
                    {r.origin_butler} · {r.category}
                  </th>
                  <td className="px-1 py-2 text-muted-foreground">{r.priority_band}</td>
                  <td className="px-1 py-2 text-right text-foreground">
                    {arm(r.treated_resolved, r.n_treated, r.treated_median_resolve_s)}
                  </td>
                  <td className="px-1 py-2 text-right text-foreground">
                    {arm(r.holdout_resolved, r.n_holdout, r.holdout_median_resolve_s)}
                  </td>
                  <td
                    className={cn(
                      "px-1 py-2 text-right",
                      r.verdict === "unmeasured" ? "text-muted-foreground" : "text-foreground",
                    )}
                  >
                    {VERDICT_LABEL[r.verdict] ?? r.verdict}
                  </td>
                  <td className="px-1 py-2 text-right text-muted-foreground">
                    {fmtTs(r.computed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </QueryBoundary>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export function AttentionLedgerPanel() {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: Q.summary(),
    queryFn: () => getAttentionLedgerSummary(),
  });

  const summary = data;
  const bySource = summary?.by_source ?? [];
  // Degraded (unreachable pool) renders its own note and never a truthful
  // "every source is healthy" -- distinct from isError (network/HTTP
  // failure), which QueryBoundary already handles.
  const sourceUnavailable = summary?.source_available === false;

  return (
    <div className="border-t border-border mt-8 pt-6" data-testid="attention-ledger-panel">
      <div className="flex items-center justify-between mb-1">
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            Attention Ledger
          </div>
          <div className="text-sm text-muted-foreground mt-0.5">
            Delivery vs. suppression per source
            {summary?.since && (
              <> · since {fmtTs(summary.since)}</>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4">
        <QueryBoundary
          isLoading={isLoading}
          isError={isError}
          error={error}
          // A source_available=false page carries an empty by_source array
          // too -- isEmpty must not swallow that into the calm empty-state
          // copy (the exact "empty vs. degraded" conflation the degraded-
          // envelope convention exists to forbid). The unavailable note
          // renders from `children`, so only a TRULY empty, reachable
          // source takes the emptyFallback branch.
          isEmpty={bySource.length === 0 && !sourceUnavailable}
          onRetry={() => void refetch()}
          sourceLabel="the attention ledger"
          loadingFallback={
            <div className="font-mono text-sm text-muted-foreground">loading…</div>
          }
          emptyFallback={
            <div className="font-mono text-sm text-muted-foreground">
              No egress activity recorded in this window.
            </div>
          }
        >
          {sourceUnavailable && (
            <div
              role="alert"
              data-testid="attention-ledger-source-unavailable"
              className="mb-3 flex items-center gap-2 rounded-sm border border-[var(--amber)]/40 bg-[var(--amber)]/10 px-3 py-2 text-xs text-[var(--amber-text)]"
            >
              <span
                className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--amber)]"
                aria-hidden="true"
              />
              <span className="font-medium">Attention ledger: unavailable</span>
            </div>
          )}

          <FlaggedSourceBanner sources={summary?.flagged_sources ?? []} />

          <table className="w-full table-fixed border-collapse font-mono text-xs">
            <caption className="sr-only">Attention ledger outcome comparison</caption>
            <colgroup>
              <col className="w-[40%]" />
              <col span={7} className="w-[8.5%]" />
            </colgroup>
            <thead className="text-[10px] uppercase tracking-widest text-muted-foreground">
              <tr>
                <th scope="col" className="pb-1 pr-2 text-left font-normal break-words">
                  Source
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Delivered
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Coalesced
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Deferred
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Suppressed
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Failed
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Expired unseen
                </th>
                <th scope="col" className="px-1 pb-1 text-right font-normal break-words">
                  Total
                </th>
              </tr>
            </thead>
            <tbody>
              {bySource.map((s) => (
                <SourceRow key={s.origin_butler} summary={s} />
              ))}
            </tbody>
          </table>
        </QueryBoundary>
      </div>

      <EfficacySection />
    </div>
  );
}
