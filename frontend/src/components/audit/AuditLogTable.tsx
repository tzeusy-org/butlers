import { EmptyState } from "@/components/ui/empty-state";
import { useState } from "react";
import type { MouseEvent } from "react";
import { Link } from "react-router";
import { Time } from "@/components/ui/time";
import type { AuditLogEntry } from "@/api/types";
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
// Props
// ---------------------------------------------------------------------------

interface AuditLogTableProps {
  entries: AuditLogEntry[];
  isLoading?: boolean;
  isError?: boolean;
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
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// AuditLogTable
// ---------------------------------------------------------------------------

export default function AuditLogTable({ entries, isLoading, isError }: AuditLogTableProps) {
  const [expandedId, setExpandedId] = useState<number | null>(null);

  function toggleExpanded(id: number) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  // Surface fetch failures (e.g. a 503 from an un-migrated audit table) as an
  // explicit error state rather than an honest-looking "no entries" empty state.
  if (!isLoading && isError) {
    return (
      <EmptyState
        title="Audit log unavailable."
        description="Failed to load audit log entries. The audit log may be temporarily unavailable. Try again shortly."
      />
    );
  }

  if (!isLoading && entries.length === 0) {
    return (
      <EmptyState
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
          <TableHead>Target</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading && <LoadingSkeleton />}

        {!isLoading &&
          entries.map((entry) => {
            return (
              <TableRow
                key={entry.id}
                className="cursor-pointer hover:bg-muted/50"
                onClick={() => toggleExpanded(entry.id)}
              >
                <TableCell className="text-muted-foreground text-xs whitespace-nowrap">
                  <Time value={entry.ts} mode="relative" />
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
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                    {entry.action}
                  </code>
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
            );
          })}

        {/* Expanded detail row */}
        {!isLoading &&
          expandedId != null &&
          (() => {
            const entry = entries.find((e) => e.id === expandedId);
            if (!entry) return null;
            return (
              <TableRow key={`${entry.id}-detail`}>
                <TableCell colSpan={4} className="bg-muted/30 p-4">
                  <div className="space-y-3 text-sm">
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
                          <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                            {entry.action}
                          </code>
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
                  </div>
                </TableCell>
              </TableRow>
            );
          })()}
      </TableBody>
    </Table>
  );
}
