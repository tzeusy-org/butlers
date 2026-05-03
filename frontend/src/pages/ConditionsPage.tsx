import { useState } from "react";
import { Time } from "@/components/ui/time";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useConditions } from "@/hooks/use-health";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const STATUS_COLORS: Record<string, string> = {
  active: "bg-green-500/15 text-green-700 dark:text-green-400",
  resolved: "bg-gray-500/15 text-gray-600 dark:text-gray-400",
  managed: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
};

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No conditions found"
      description="Health conditions will appear here as they are tracked by the Health butler."
    />
  );
}

// ---------------------------------------------------------------------------
// ConditionsPage
// ---------------------------------------------------------------------------

export default function ConditionsPage() {
  const [page, setPage] = useState(0);

  const { data, isLoading } = useConditions({
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  });

  const conditions = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Conditions</h1>
        <p className="text-muted-foreground mt-1">
          Health conditions and their current status.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Conditions</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} condition${total !== 1 ? "s" : ""}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!isLoading && conditions.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Diagnosed</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : (
                  conditions.map((cond) => (
                    <TableRow key={cond.id}>
                      <TableCell className="font-medium">{cond.name}</TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={STATUS_COLORS[cond.status.toLowerCase()] ?? ""}
                        >
                          {cond.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {cond.diagnosed_at
                          ? <Time value={cond.diagnosed_at} mode="absolute" precision="day" />
                          : "\u2014"}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                        {cond.notes ?? "\u2014"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <Time value={cond.updated_at} mode="absolute" precision="day" />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">
            Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
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
              disabled={!hasMore}
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
