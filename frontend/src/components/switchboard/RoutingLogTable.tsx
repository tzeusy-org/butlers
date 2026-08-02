/**
 * RoutingLogTable — table showing switchboard routing log entries.
 *
 * Features:
 * - Table: Timestamp, Source Butler, Target Butler, Tool Name, Success, Duration, Error
 * - Filters: source_butler, target_butler dropdowns
 * - Pagination
 * - Loading skeleton, empty state
 */

import { useState } from "react";

import type { RoutingEntry, RoutingLogParams } from "@/api/types.ts";
import { Time } from "@/components/ui/time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { QueryBoundary, SourceDegradedNote } from "@/components/ui/query-boundary";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRoutingLog } from "@/hooks/use-general";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyRoutingLogState() {
  return (
    <EmptyState
      variant="page"
      title="No routing log entries found."
      description="Entries appear as inter-butler requests pass through the switchboard."
    />
  );
}

function RoutingLogEntriesTable({
  entries,
  isLoading,
}: {
  entries: RoutingEntry[];
  isLoading: boolean;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Timestamp</TableHead>
          <TableHead>Source</TableHead>
          <TableHead>Target</TableHead>
          <TableHead>Tool</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Duration</TableHead>
          <TableHead>Error</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {isLoading ? (
          <SkeletonRows />
        ) : (
          entries.map((entry) => (
            <TableRow key={entry.id}>
              <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                <Time value={entry.created_at} mode="absolute" precision="second" compact />
              </TableCell>
              <TableCell className="text-sm font-medium">
                {entry.source_butler}
              </TableCell>
              <TableCell className="text-sm font-medium">
                {entry.target_butler}
              </TableCell>
              <TableCell>
                <code className="text-xs">{entry.tool_name}</code>
              </TableCell>
              <TableCell>
                <Badge variant={entry.success ? "default" : "destructive"}>
                  {entry.success ? "OK" : "Failed"}
                </Badge>
              </TableCell>
              <TableCell className="text-sm text-muted-foreground">
                {entry.duration_ms != null ? `${entry.duration_ms}ms` : "\u2014"}
              </TableCell>
              <TableCell className="max-w-xs truncate text-xs text-destructive">
                {entry.error ?? "\u2014"}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}

// ---------------------------------------------------------------------------
// RoutingLogTable
// ---------------------------------------------------------------------------

export default function RoutingLogTable() {
  const [page, setPage] = useState(0);
  const [sourceFilter, setSourceFilter] = useState("");
  const [targetFilter, setTargetFilter] = useState("");

  const params: RoutingLogParams = {
    source_butler: sourceFilter || undefined,
    target_butler: targetFilter || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading, isError, error, refetch } = useRoutingLog(params);

  const totalPages = data ? Math.max(1, Math.ceil(data.meta.total / PAGE_SIZE)) : 1;
  const currentPage = page + 1;

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Source butler..."
          value={sourceFilter}
          onChange={(e) => {
            setSourceFilter(e.target.value);
            setPage(0);
          }}
          className="w-48"
        />
        <Input
          placeholder="Target butler..."
          value={targetFilter}
          onChange={(e) => {
            setTargetFilter(e.target.value);
            setPage(0);
          }}
          className="w-48"
        />
        {(sourceFilter || targetFilter) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setSourceFilter("");
              setTargetFilter("");
              setPage(0);
            }}
          >
            Clear filters
          </Button>
        )}
      </div>

      <QueryBoundary
        isLoading={isLoading}
        isError={isError && !data}
        error={error}
        isEmpty={data != null && !isError && data.data.length === 0}
        onRetry={() => void refetch()}
        sourceLabel="routing log"
        loadingFallback={<RoutingLogEntriesTable entries={[]} isLoading />}
        emptyFallback={<EmptyRoutingLogState />}
      >
        {data && (
          <>
            {isError && (
              <SourceDegradedNote
                label="Routing log"
                detail="could not be reached"
                onRetry={() => void refetch()}
                testId="routing-log-degraded"
              />
            )}
            <RoutingLogEntriesTable entries={data.data} isLoading={false} />
          </>
        )}
      </QueryBoundary>

      {/* Pagination stays available when a later page is temporarily empty. */}
      {data && data.meta.total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            Page {currentPage} of {totalPages}
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!data.meta.has_more}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
