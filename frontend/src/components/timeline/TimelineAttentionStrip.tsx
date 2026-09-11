/**
 * Read-only recent-failure inspection for the Timeline.
 *
 * The API deliberately reports records currently marked failed in one
 * server-captured 24-hour window. This component never acknowledges, retries,
 * or infers recovery from those records.
 */

import { useState } from "react";
import { Link } from "react-router";

import type {
  TimelineAttentionItem,
  TimelineAttentionResponse,
} from "@/api/types.ts";
import { Button } from "@/components/ui/button";
import { Time } from "@/components/ui/time";

const DETAILS_ID = "timeline-attention-details";

export interface TimelineAttentionStripProps {
  attention?: TimelineAttentionResponse;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
  selectedButlers: string[];
  trace?: string;
}

function notificationHref(
  item: TimelineAttentionItem,
  selectedButlers: string[],
  trace?: string,
): string {
  const params = new URLSearchParams({ event: item.id });
  const scopedButlers = selectedButlers.length > 0 ? selectedButlers : [item.butler];
  if (scopedButlers.length > 0) params.set("butler", scopedButlers.join(","));
  if (trace) params.set("trace", trace);
  return `/timeline?${params.toString()}`;
}

function sessionHref(item: TimelineAttentionItem): string {
  const base = `/sessions/${encodeURIComponent(item.id)}`;
  return item.butler ? `${base}?butler=${encodeURIComponent(item.butler)}` : base;
}

function itemHref(item: TimelineAttentionItem, selectedButlers: string[], trace?: string): string {
  return item.kind === "session" ? sessionHref(item) : notificationHref(item, selectedButlers, trace);
}

function itemLabel(item: TimelineAttentionItem): string {
  return item.kind === "session" ? "Run" : "Delivery record";
}

function availabilityCopy(attention: TimelineAttentionResponse): string {
  const { availability, healthy_sources, expected_sources } = attention.meta;
  const sourceCount = `${healthy_sources} of ${expected_sources} sources available`;
  if (availability === "unavailable") return `Failure data unavailable: ${sourceCount}.`;
  if (availability === "partial") {
    return `Partial failure data: ${sourceCount}. Some records may be missing.`;
  }
  return `${sourceCount}.`;
}

function degradedCopy(attention: TimelineAttentionResponse): string | null {
  const { degraded_sources: sources, degraded_butlers: butlers } = attention.meta;
  const names = [...sources, ...butlers].filter((name, index, all) => all.indexOf(name) === index);
  return names.length > 0 ? `Unavailable sources: ${names.join(", ")}.` : null;
}

function RefreshWarning({ onRetry }: { onRetry: () => void }) {
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-t border-[var(--amber)]/30 px-3 py-2 text-xs text-[var(--amber-text)]"
      role="alert"
      data-testid="timeline-attention-refresh-error"
    >
      <span>Recent failure records could not be refreshed. Showing the last successful read.</span>
      <Button type="button" variant="link" size="sm" className="ml-auto h-auto p-0" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

export function TimelineAttentionStrip({
  attention,
  isLoading,
  isError,
  onRetry,
  selectedButlers,
  trace,
}: TimelineAttentionStripProps) {
  const [expanded, setExpanded] = useState(true);
  const meta = attention?.meta;
  const rows = attention ? attention.data : [];
  const isUnavailable = meta?.availability === "unavailable";
  const hasTruncation = Boolean(meta && (meta.has_more || meta.total > rows.length));

  return (
    <section
      className="overflow-hidden rounded border border-destructive/30 bg-destructive/5"
      aria-labelledby="timeline-attention-title"
      data-testid="timeline-attention-strip"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
        <div className="min-w-0">
          <h2 id="timeline-attention-title" className="text-sm font-medium">
            Recent records marked failed (created in last 24h)
          </h2>
          {meta && (
            <>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
                <span data-testid="timeline-attention-failed-sessions">Runs: {meta.failed_sessions}</span>
                <span data-testid="timeline-attention-failed-notifications">
                  Delivery records: {meta.failed_notifications}
                </span>
                <span data-testid="timeline-attention-total">Total: {meta.total}</span>
              </div>
              <div className="mt-1 font-mono text-[11px] text-muted-foreground">
                <p data-testid="timeline-attention-availability">{availabilityCopy(attention!)}</p>
                {degradedCopy(attention!) && (
                  <p className="mt-1 text-[var(--amber-text)]" data-testid="timeline-attention-degraded">
                    {degradedCopy(attention!)}
                  </p>
                )}
              </div>
            </>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {attention && (
            <Button
              type="button"
              variant="ghost"
              size="xs"
              aria-expanded={expanded}
              aria-controls={DETAILS_ID}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "Hide details" : "Show details"}
            </Button>
          )}
        </div>
      </div>

      {isLoading && !attention ? (
        <p className="border-t border-border/60 px-3 py-2 font-mono text-[11px] text-muted-foreground" role="status">
          Loading recent failed records...
        </p>
      ) : isError && !attention ? (
        <div
          className="flex flex-wrap items-center gap-2 border-t border-destructive/30 px-3 py-2 text-xs text-destructive"
          role="alert"
        >
          <span>Recent failure records are unavailable.</span>
          <Button type="button" variant="link" size="sm" className="ml-auto h-auto p-0" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : attention ? (
        <>
          {isError && <RefreshWarning onRetry={onRetry} />}
          <div id={DETAILS_ID} hidden={!expanded}>
            {isUnavailable ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-border/60 px-3 py-2 text-xs text-destructive">
                <span data-testid="timeline-attention-unavailable">
                  Recent failed records are unavailable. Retry to inspect this 24-hour window.
                </span>
                <Button type="button" variant="link" size="sm" className="ml-auto h-auto p-0" onClick={onRetry}>
                  Retry
                </Button>
              </div>
            ) : rows.length > 0 ? (
              <>
                <ul className="divide-y divide-border/60 border-t border-border/60" aria-label="Recent failed records">
                  {rows.map((item) => (
                    <li key={`${item.kind}:${item.id}`}>
                      <Link
                        to={itemHref(item, selectedButlers, trace)}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs hover:bg-muted/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
                        data-testid={`timeline-attention-item-${item.kind}`}
                        data-record-id={item.id}
                        aria-label={`Inspect failed ${item.kind} ${item.id}`}
                      >
                        <span className="w-28 shrink-0 font-mono text-[10px] uppercase tracking-wide text-destructive">
                          {itemLabel(item)}
                        </span>
                        <span className="min-w-0 flex-1 break-all font-mono text-[11px] text-foreground">
                          {item.id}
                        </span>
                        <span className="font-mono text-[11px] text-muted-foreground">{item.butler}</span>
                        <Time value={item.timestamp} mode="relative" className="text-muted-foreground" />
                      </Link>
                    </li>
                  ))}
                </ul>
                {hasTruncation && (
                  <p className="border-t border-border/60 px-3 py-2 font-mono text-[11px] text-muted-foreground" data-testid="timeline-attention-truncated">
                    Showing {rows.length} of {meta?.total ?? rows.length}
                  </p>
                )}
              </>
            ) : isError ? (
              <p className="border-t border-border/60 px-3 py-2 font-mono text-[11px] text-[var(--amber-text)]" data-testid="timeline-attention-stale-empty">
                The last successful read had no matching records; refresh to confirm the current status.
              </p>
            ) : meta?.availability === "complete" ? (
              <p className="border-t border-border/60 px-3 py-2 font-serif text-sm italic text-muted-foreground" data-testid="timeline-attention-empty">
                No matching records currently marked failed
              </p>
            ) : (
              <p className="border-t border-border/60 px-3 py-2 font-mono text-[11px] text-[var(--amber-text)]" data-testid="timeline-attention-incomplete">
                No complete failure count is available while a source is unavailable.
              </p>
            )}
          </div>
        </>
      ) : (
        <p className="border-t border-border/60 px-3 py-2 font-mono text-[11px] text-muted-foreground" role="status">
          Recent failure records are not available yet.
        </p>
      )}
    </section>
  );
}
