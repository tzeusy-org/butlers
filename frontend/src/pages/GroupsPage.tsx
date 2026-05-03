import { useState } from "react";
import { Time } from "@/components/ui/time";

import type { GroupParams } from "@/api/types";
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
import { useGroups } from "@/hooks/use-contacts";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-16" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No groups found"
      description="Groups will appear here as you organize contacts into categories."
    />
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function labelStyle(label: { color: string | null; name: string }): string {
  if (label.color) return label.color;
  const colors = [
    "var(--category-1)", "var(--category-2)", "var(--category-3)", "var(--category-4)",
    "var(--category-5)", "var(--category-6)", "var(--category-7)", "var(--category-8)",
  ];
  let hash = 0;
  for (let i = 0; i < label.name.length; i++) {
    hash = (hash * 31 + label.name.charCodeAt(i)) | 0;
  }
  return colors[Math.abs(hash) % colors.length];
}

// ---------------------------------------------------------------------------
// GroupsPage
// ---------------------------------------------------------------------------

export default function GroupsPage() {
  const [page, setPage] = useState(0);

  const params: GroupParams = {
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useGroups(params);

  const groups = data?.groups ?? [];
  const total = data?.total ?? 0;
  const hasMore = groups.length === PAGE_SIZE && (page + 1) * PAGE_SIZE < total;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <div className="space-y-6">
      {/* Page heading */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Groups</h1>
        <p className="text-muted-foreground mt-1">
          Organize contacts into groups.
        </p>
      </div>

      {/* Groups table */}
      <Card>
        <CardHeader>
          <CardTitle>All Groups</CardTitle>
          <CardDescription>
            {total > 0 ? `${total.toLocaleString()} group${total !== 1 ? "s" : ""}` : ""}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {!isLoading && groups.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Members</TableHead>
                  <TableHead>Labels</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : (
                  groups.map((group) => (
                    <TableRow key={group.id}>
                      <TableCell className="font-medium">{group.name}</TableCell>
                      <TableCell className="text-muted-foreground text-sm max-w-xs truncate">
                        {group.description ?? "\u2014"}
                      </TableCell>
                      <TableCell className="tabular-nums text-sm">
                        {group.member_count}
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          {group.labels.map((label) => (
                            <Badge
                              key={label.id}
                              variant="outline"
                              style={{
                                borderColor: labelStyle(label),
                                color: labelStyle(label),
                              }}
                              className="text-xs"
                            >
                              {label.name}
                            </Badge>
                          ))}
                          {group.labels.length === 0 && (
                            <span className="text-muted-foreground text-xs">{"\u2014"}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <Time value={group.created_at} mode="absolute" precision="day" />
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
