import { ApiError } from "@/api/client";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Fragment, useState } from "react";
import type { MouseEvent } from "react";
import { Link } from "react-router";
import { Time } from "@/components/ui/time";
import type { AuditLogEntry } from "@/api/types";
import { CollapsibleJson } from "@/components/sessions/ToolCallTimeline";
import { DisclosureRow } from "@/components/ui/DisclosureRow";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

// ---------------------------------------------------------------------------
// Identifier pivots (bu-86c4c.4 -- JARVIS audit move 2b: drill-down sweep)
//
// actor: every audit row shares the same `actor` column the page's own
// filter bar already understands via ?actor= (AuditLogPage.tsx), so an
// actor cell pivots to that pre-filtered view of itself.
//
// target: `target` is a scheme-prefixed string (e.g. "butler:qa",
// "u:google", "rule:42" -- see audit.append()'s docstring in
// src/butlers/api/routers/audit.py). Only schemes with a real owning page
// are made into links -- an honest "no link" beats a link to a page that
// doesn't understand the predicate:
//   - "butler:<name>"  -> /butlers/<name> (butler detail)
//   - "u:<provider>"   -> /secrets?focus=u:<provider> (credential passport
//                         deep-link focus routing, already used by the
//                         secrets page for the same scheme)
// "rule:<id>" (spend rules) has no per-rule deep link on /spend yet --
// left as plain text; see PR follow-ups.
// ---------------------------------------------------------------------------

function actorHref(actor: string): string {
  return `/audit-log?actor=${encodeURIComponent(actor)}`;
}

// action: every row's `action` verb is exact-match filterable via ?action=
// (AuditLogPage.tsx), the same predicate the page's own filter bar already
// understands -- so an action cell pivots to that pre-filtered view of
// itself (bu-qvnce.13, "action-cell pivot links").
function actionHref(action: string): string {
  return `/audit-log?action=${encodeURIComponent(action)}`;
}

function targetHref(target: string): string | null {
  if (target.startsWith("butler:")) {
    const name = target.slice("butler:".length);
    if (!name) return null;
    return `/butlers/${encodeURIComponent(name)}`;
  }
  if (target.startsWith("u:")) {
    return `/secrets?focus=${encodeURIComponent(target)}`;
  }
  return null;
}

/** Stop the click from bubbling to the row's own toggle-expand handler. */
function stopRowToggle(e: MouseEvent) {
  e.stopPropagation();
}

// ---------------------------------------------------------------------------
// Outcome (JARVIS audit move 6) -- the audit log persists result/error/
// metadata since core_122 but never projected them until now, so every row
// read as neither success nor failure. Three honest states: success, error,
// or unknown (rows written before the audit-writer unification, whose
// `result` column is NULL -- not a fourth outcome, an absence of one).
// ---------------------------------------------------------------------------

function OutcomeBadge({ result }: { result: string | null | undefined }) {
  if (result === "error") {
    return (
      <span className="text-xs font-medium text-[var(--red-text)]" data-testid="outcome-error">
        Error
      </span>
    );
  }
  if (result === "success") {
    return (
      <span className="text-xs font-medium text-[var(--green)]" data-testid="outcome-success">
        Success
      </span>
    );
  }
  return (
    <span className="text-xs italic text-muted-foreground" data-testid="outcome-unknown">
      Unknown
    </span>
  );
}

/**
 * First line of an error message, matching the backend's grouping
 * normalization (audit_grouping.py: `SPLIT_PART(error, E'\n', 1)`) closely
 * enough for a "search the Issues feed for this text" hop -- not a precise
 * group-key reconstruction (that logic, including tmp-path collapsing and
 * slugging, lives server-side only), just enough to pivot a failure row to
 * its likely issue group without duplicating that normalization here.
 */
function firstErrorLine(error: string): string {
  return error.split("\n")[0]?.trim() || error;
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface AuditLogTableProps {
  entries: AuditLogEntry[];
  isLoading?: boolean;
  isError?: boolean;
  /**
   * The query's raw error (e.g. TanStack Query's `error`), used to
   * distinguish a deterministic 4xx (a bad/unsupported filter combination --
   * honest, actionable, and will happen again on retry) from a genuine 5xx/
   * network/timeout failure (transient, "try again shortly" is the right
   * copy) -- bu-hmdqz.4. Before the metadata tolerance fix, a poisoned row
   * made this endpoint 500 with `VALIDATION_ERROR` and this table rendered
   * the deterministic failure as "may be temporarily unavailable", which is
   * false: retrying changes nothing until the underlying data or filter
   * changes.
   */
  error?: unknown;
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <>
      {Array.from({ length: 8 }).map((_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-14" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// AuditLogTable
// ---------------------------------------------------------------------------

export default function AuditLogTable({ entries, isLoading, isError, error }: AuditLogTableProps) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  function toggleExpanded(id: number) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  // Surface fetch failures (e.g. a 503 from an un-migrated audit table) as an
  // explicit error state rather than an honest-looking "no entries" empty state.
  if (!isLoading && isError) {
    // Deterministic 4xx (e.g. an invalid filter combination the backend
    // rejected) is not "temporarily unavailable" -- retrying without
    // changing the request will fail again every time. Only a genuine 5xx/
    // network/timeout failure is transient (bu-hmdqz.4).
    const isDeterministic =
      error instanceof ApiError && error.status >= 400 && error.status < 500;
    return (
      <ErrorState
        title={isDeterministic ? "Could not load this audit log query." : "Audit log unavailable."}
        description={
          isDeterministic
            ? (error instanceof ApiError && error.message) ||
              "The request was rejected. Check the filters above."
            : "Failed to load audit log entries. The audit log may be temporarily unavailable. Try again shortly."
        }
      />
    );
  }

  if (!isLoading && entries.length === 0) {
    return (
      <EmptyState
        variant="page"
        title="No audit entries found."
        description="Audit log entries appear as butlers perform operations."
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[100px]">Time</TableHead>
          <TableHead className="w-[140px]">Actor</TableHead>
          <TableHead className="w-[200px]">Action</TableHead>
          <TableHead className="w-[90px]">Outcome</TableHead>
          <TableHead>Target</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading && <LoadingSkeleton />}

        {!isLoading &&
          entries.map((entry) => {
            const expanded = expandedId === entry.id;
            return (
              // Fragment (not just the summary TableRow) so the detail row
              // renders ADJACENT to its own row -- previously the single
              // expanded detail row was appended once after the entire
              // `entries.map()`, landing at the bottom of the table
              // regardless of which row was expanded.
              <Fragment key={entry.id}>
                <TableRow
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={() => toggleExpanded(entry.id)}
                  aria-expanded={expanded}
                  data-testid="audit-log-row"
                >
                  <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                    {/* The row's REAL keyboard-accessible disclosure trigger
                        (bu-f310e, mirroring TimelineTab's DisclosureRow
                        recomposition from bu-86c4c.16). DisclosureRow supplies
                        role="button", Enter+Space activation, and
                        aria-expanded/aria-controls. It wraps only the static
                        Time cell (no nested focusable descendants), so — unlike
                        putting a widget role on the whole <tr>, which sits
                        around the actor/action/target links — it passes axe's
                        nested-interactive check. stopPropagation keeps the
                        outer row's own mouse-convenience onClick from
                        double-firing the toggle. */}
                    <DisclosureRow
                      expanded={expanded}
                      onToggle={() => toggleExpanded(entry.id)}
                      onClick={(e) => e.stopPropagation()}
                      controlsId={expanded ? `audit-detail-${entry.id}` : undefined}
                      aria-label={`${expanded ? "Collapse" : "Expand"} audit entry details`}
                      className="inline-flex rounded-sm"
                      data-testid="audit-log-row-trigger"
                    >
                      <Time value={entry.ts} mode="relative" />
                    </DisclosureRow>
                  </TableCell>
                  <TableCell className="text-sm font-medium">
                    <Link
                      to={actorHref(entry.actor)}
                      onClick={stopRowToggle}
                      className="hover:underline"
                    >
                      {entry.actor}
                    </Link>
                  </TableCell>
                  <TableCell>
                    <Link
                      to={actionHref(entry.action)}
                      onClick={stopRowToggle}
                      className="hover:underline"
                    >
                      <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                        {entry.action}
                      </code>
                    </Link>
                  </TableCell>
                  <TableCell>
                    <OutcomeBadge result={entry.result} />
                  </TableCell>
                  <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                    {entry.target ? (
                      targetHref(entry.target) ? (
                        <Link
                          to={targetHref(entry.target)!}
                          onClick={stopRowToggle}
                          className="hover:underline"
                        >
                          {entry.target}
                        </Link>
                      ) : (
                        entry.target
                      )
                    ) : (
                      <span className="italic">—</span>
                    )}
                  </TableCell>
                </TableRow>

                {expanded && (
                  <TableRow data-testid="audit-log-detail-row">
                    <TableCell colSpan={5} className="bg-muted/30 p-4">
                      <div id={`audit-detail-${entry.id}`} className="space-y-3 text-sm">
                        <div className="grid grid-cols-2 gap-x-6 gap-y-2">
                          <div>
                            <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                              Actor
                            </span>
                            <p className="mt-0.5">
                              <Link to={actorHref(entry.actor)} className="hover:underline">
                                {entry.actor}
                              </Link>
                            </p>
                          </div>
                          <div>
                            <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                              Action
                            </span>
                            <p className="mt-0.5">
                              <Link to={actionHref(entry.action)} className="hover:underline">
                                <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                                  {entry.action}
                                </code>
                              </Link>
                            </p>
                          </div>
                          <div>
                            <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                              Outcome
                            </span>
                            <p className="mt-0.5">
                              <OutcomeBadge result={entry.result} />
                            </p>
                          </div>
                          {entry.target && (
                            <div>
                              <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                                Target
                              </span>
                              <p className="mt-0.5 font-mono text-xs">
                                {targetHref(entry.target) ? (
                                  <Link to={targetHref(entry.target)!} className="hover:underline">
                                    {entry.target}
                                  </Link>
                                ) : (
                                  entry.target
                                )}
                              </p>
                            </div>
                          )}
                          {entry.ip && (
                            <div>
                              <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                                IP
                              </span>
                              <p className="mt-0.5 font-mono text-xs">{entry.ip}</p>
                            </div>
                          )}
                          {entry.request_id && (
                            <div className="col-span-2">
                              <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                                Request ID
                              </span>
                              <p className="mt-0.5 font-mono text-xs">{entry.request_id}</p>
                            </div>
                          )}
                        </div>
                        {entry.note && (
                          <div>
                            <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                              Note
                            </span>
                            <p className="mt-0.5 text-xs">{entry.note}</p>
                          </div>
                        )}
                        {entry.error && (
                          <div>
                            <span className="font-medium text-muted-foreground text-xs uppercase tracking-wide">
                              Error
                            </span>
                            <p className="mt-0.5 text-xs text-[var(--red-text)]">{entry.error}</p>
                          </div>
                        )}
                        {entry.metadata && (
                          <CollapsibleJson label="Metadata" data={entry.metadata} />
                        )}
                        {entry.result === "error" && entry.error && (
                          <Link
                            to={`/issues?q=${encodeURIComponent(firstErrorLine(entry.error))}`}
                            className="inline-flex text-xs font-medium text-[var(--red-text)] hover:underline"
                            data-testid="audit-log-issues-link"
                          >
                            View in Issues →
                          </Link>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
      </TableBody>
    </Table>
  );
}
