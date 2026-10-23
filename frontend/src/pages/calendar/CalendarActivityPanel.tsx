/**
 * Audit-log view for the calendar workspace Activity tab: paged rows, per-row
 * status and session links.
 */

import { format } from "date-fns";
import { Link } from "react-router";

import type { CalendarAuditEntry } from "@/api/types.ts";
import { FetchingDim } from "@/components/ui/fetching-dim";
import { Mono } from "@/components/ui/Mono";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Row } from "@/components/ui/Row";
import { StateDot } from "@/components/ui/StateDot";
import { Tip } from "@/components/ui/tip";
import { Voice } from "@/components/ui/Voice";
import { KindTag, PillButton } from "@/pages/calendar/atoms";

function auditStatusLabel(status: CalendarAuditEntry["action_status"]): string {
  switch (status) {
    case "applied":
      return "applied";
    case "pending":
      return "pending";
    case "failed":
      return "failed";
    case "noop":
      return "noop";
    default:
      return String(status);
  }
}

function auditStatusColor(status: CalendarAuditEntry["action_status"]): string {
  switch (status) {
    case "applied":
      return "text-[var(--green)]";
    case "pending":
      return "text-[var(--yellow)]";
    case "failed":
      return "text-[var(--red-text)]";
    case "noop":
      return "text-[var(--mfg)]";
    default:
      return "text-[var(--mfg)]";
  }
}

interface CalendarActivityPanelProps {
  auditQuery: {
    isLoading: boolean;
    isFetching?: boolean;
    isError: boolean;
    error: Error | null;
    data?: {
      data?: {
        entries?: CalendarAuditEntry[];
        total?: number;
        offset?: number;
        limit?: number;
        sources_available?: boolean;
      };
    };
  };
  offset: number;
  limit: number;
  onPageChange: (offset: number) => void;
}

export function CalendarActivityPanel({
  auditQuery,
  offset,
  limit,
  onPageChange,
}: CalendarActivityPanelProps) {
  const entries = auditQuery.data?.data?.entries ?? [];
  const total = auditQuery.data?.data?.total ?? 0;
  // Audit fan-out honesty (bu-yjfk2): false only when a calendar schema's
  // action-log fan-out failed. A partial failure silently drops that schema's
  // rows and undercounts `total`, so an empty/short log would otherwise read as
  // a complete, quiet history. Absent/true means every source answered.
  const sourcesAvailable = auditQuery.data?.data?.sources_available !== false;
  const hasPrev = offset > 0;
  const hasNext = offset + limit < total;
  const shouldDim =
    !!auditQuery.isFetching && !auditQuery.isLoading && !auditQuery.isError;

  if (auditQuery.isLoading) {
    return (
      <Voice variant="italic" className="text-[var(--mfg)]">
        Loading activity log…
      </Voice>
    );
  }
  if (auditQuery.isError) {
    return (
      <div role="alert" className="flex items-start gap-2 py-1">
        <StateDot state="error" className="mt-[7px]" />
        <p className="text-sm text-fg">
          Failed to load activity log.{" "}
          <span className="text-[var(--mfg)]">
            {auditQuery.error instanceof Error
              ? auditQuery.error.message
              : "Unknown error"}
          </span>
        </p>
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <FetchingDim
        isFetching={shouldDim}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3">
          {!sourcesAvailable ? (
            <SourceDegradedNote
              testId="audit-sources-degraded"
              label="Activity log"
              detail="some sources unavailable: recent mutations may be missing"
            />
          ) : null}
          <Voice variant="italic" className="text-[var(--mfg)]">
            No calendar mutations logged yet.
          </Voice>
        </div>
      </FetchingDim>
    );
  }

  return (
    <FetchingDim
      isFetching={shouldDim}
      className="flex min-h-0 flex-1 flex-col"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        {!sourcesAvailable ? (
          <SourceDegradedNote
            testId="audit-sources-degraded"
            label="Activity log"
            detail="some sources unavailable: recent mutations may be missing"
          />
        ) : null}
        <div className="flex items-center justify-between gap-4">
          <Mono muted className="tabular-nums">
            {total} {total === 1 ? "entry" : "entries"} total · showing{" "}
            {offset + 1}–{Math.min(offset + limit, total)}
          </Mono>
          <div className="flex items-center gap-1">
            <PillButton
              disabled={!hasPrev}
              onClick={() => onPageChange(Math.max(0, offset - limit))}
            >
              ‹ Prev
            </PillButton>
            <PillButton
              disabled={!hasNext}
              onClick={() => onPageChange(offset + limit)}
            >
              Next ›
            </PillButton>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pr-1" role="list">
          {entries.map((entry) => {
            const createdAt = entry.created_at
              ? format(new Date(entry.created_at), "yyyy-MM-dd HH:mm:ss")
              : "";
            const summaryTitle =
              typeof entry.payload_summary?.title === "string"
                ? entry.payload_summary.title
                : null;

            return (
              <Row
                key={entry.id}
                mark={
                  <span
                    className={`font-mono text-[10px] uppercase tracking-[0.12em] ${auditStatusColor(entry.action_status)}`}
                  >
                    {auditStatusLabel(entry.action_status)}
                  </span>
                }
                meta={
                  <div className="flex items-center gap-3">
                    {entry.source_session_id ? (
                      <Tip content={`Session ${entry.source_session_id}`}>
                        <Link
                          to={`/sessions/${entry.source_session_id}`}
                          className="font-mono text-[10px] text-[var(--mfg)] underline decoration-dotted hover:text-fg"
                        >
                          session ›
                        </Link>
                      </Tip>
                    ) : null}
                  </div>
                }
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium text-fg">
                      {entry.action_type}
                      {summaryTitle ? `: ${summaryTitle}` : ""}
                    </span>
                    {entry.source_butler ? (
                      <KindTag>{entry.source_butler}</KindTag>
                    ) : null}
                  </div>
                  <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
                    <Mono muted className="tabular-nums">
                      {createdAt}
                    </Mono>
                    {entry.error ? (
                      <span
                        className="max-w-[20rem] truncate text-[11px] text-[var(--red-text)]"
                        title={entry.error}
                      >
                        {entry.error}
                      </span>
                    ) : null}
                    {entry.origin_ref ? (
                      <Mono muted className="truncate">
                        {entry.origin_ref}
                      </Mono>
                    ) : null}
                  </div>
                </div>
              </Row>
            );
          })}
        </div>
      </div>
    </FetchingDim>
  );
}
