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
 */

import { useQuery } from "@tanstack/react-query";

import { getAttentionLedgerSummary } from "@/api/index.ts";
import type { AttentionSourceSummary } from "@/api/index.ts";
import { QueryBoundary } from "@/components/ui/query-boundary.tsx";
import { cn } from "@/lib/utils";

const Q = {
  summary: () => ["attention-ledger", "summary"] as const,
};

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
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
    <div
      data-testid="attention-source-row"
      data-flagged={summary.suppressed_never_delivered}
      className={cn(
        "grid grid-cols-[1fr_repeat(5,minmax(0,72px))] items-center gap-2 py-2",
        "border-t border-border/50 first:border-t-0 font-mono text-xs",
      )}
    >
      <span
        className={cn(
          "truncate font-medium",
          summary.suppressed_never_delivered ? "text-[var(--red-text)]" : "text-foreground",
        )}
      >
        {summary.origin_butler}
      </span>
      <span className="text-right text-foreground">{summary.delivered}</span>
      <span className="text-right text-muted-foreground">{summary.coalesced}</span>
      <span className="text-right text-muted-foreground">{summary.deferred}</span>
      <span
        className={cn(
          "text-right",
          summary.suppressed_never_delivered
            ? "text-[var(--red-text)] font-semibold"
            : "text-muted-foreground",
        )}
      >
        {summary.suppressed}
      </span>
      <span className="text-right text-muted-foreground">{summary.total}</span>
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

          <div className="grid grid-cols-[1fr_repeat(5,minmax(0,72px))] gap-2 pb-1 font-mono text-[10px] uppercase tracking-widest text-muted-foreground">
            <span>Source</span>
            <span className="text-right">Delivered</span>
            <span className="text-right">Coalesced</span>
            <span className="text-right">Deferred</span>
            <span className="text-right">Suppressed</span>
            <span className="text-right">Total</span>
          </div>
          {bySource.map((s) => (
            <SourceRow key={s.origin_butler} summary={s} />
          ))}
        </QueryBoundary>
      </div>
    </div>
  );
}
