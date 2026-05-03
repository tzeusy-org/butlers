import { EmptyState } from "@/components/ui/empty-state";
import { useState } from "react";
import { Time } from "@/components/ui/time";
import type { AuditEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
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
  entries: AuditEntry[];
  isLoading?: boolean;
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
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// AuditLogTable
// ---------------------------------------------------------------------------

export default function AuditLogTable({ entries, isLoading }: AuditLogTableProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  function toggleExpanded(id: string) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  if (!isLoading && entries.length === 0) {
    return (
      <EmptyState
        title="No audit entries found"
        description="Audit log entries will appear here as butlers perform operations."
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-[100px]">Time</TableHead>
          <TableHead className="w-[120px]">Butler</TableHead>
          <TableHead className="w-[180px]">Operation</TableHead>
          <TableHead className="w-[100px]">Result</TableHead>
          <TableHead>Request Summary</TableHead>
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
                  <Time value={entry.created_at} mode="relative" />
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{entry.butler}</Badge>
                </TableCell>
                <TableCell>
                  <code className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono">
                    {entry.operation}
                  </code>
                </TableCell>
                <TableCell>
                  <Badge variant={entry.result === "success" ? "default" : "destructive"}>
                    {entry.result}
                  </Badge>
                </TableCell>
                <TableCell className="max-w-xs truncate text-xs text-muted-foreground">
                  {JSON.stringify(entry.request_summary)}
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
                <TableCell colSpan={5} className="bg-muted/30 p-4">
                  <div className="space-y-3 text-sm">
                    <div>
                      <span className="font-medium">Request:</span>
                      <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">
                        {JSON.stringify(entry.request_summary, null, 2)}
                      </pre>
                    </div>
                    <div>
                      <span className="font-medium">User Context:</span>
                      <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">
                        {JSON.stringify(entry.user_context, null, 2)}
                      </pre>
                    </div>
                    {entry.result === "error" && entry.error && (
                      <div>
                        <span className="font-medium text-destructive">Error:</span>
                        <pre className="mt-1 overflow-x-auto rounded bg-destructive/10 p-2 text-xs text-destructive">
                          {entry.error}
                        </pre>
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
