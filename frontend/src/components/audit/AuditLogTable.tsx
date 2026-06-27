import { EmptyState } from "@/components/ui/empty-state";
import { useState } from "react";
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
                  {entry.actor}
                </TableCell>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                    {entry.action}
                  </code>
                </TableCell>
                <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                  {entry.target ?? <span className="italic">—</span>}
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
                        <p className="mt-0.5">{entry.actor}</p>
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
                          <p className="mt-0.5 font-mono text-xs">{entry.target}</p>
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
