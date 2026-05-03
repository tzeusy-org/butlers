import { useState } from "react";
import { format } from "date-fns";

import type { SymptomParams } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useSymptoms } from "@/hooks/use-health";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Return a color for severity 1-10 scale. */
function severityColor(severity: number): string {
  if (severity <= 3) return "var(--severity-low)";
  if (severity <= 6) return "var(--severity-medium)";
  return "var(--severity-high)";
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-28" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
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
      title="No symptoms found"
      description="Symptoms will appear here as they are logged with severity ratings and occurrence dates."
    />
  );
}

// ---------------------------------------------------------------------------
// SymptomsPage
// ---------------------------------------------------------------------------

export default function SymptomsPage() {
  const [nameFilter, setNameFilter] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(0);

  const params: SymptomParams = {
    name: nameFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useSymptoms(params);

  const symptoms = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  function handleNameChange(value: string) {
    setNameFilter(value);
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Symptoms</h1>
        <p className="text-muted-foreground mt-1">
          Track symptoms with severity ratings and occurrence dates.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Symptoms</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} symptom${total !== 1 ? "s" : ""}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3">
            <Input
              placeholder="Filter by name..."
              value={nameFilter}
              onChange={(e) => handleNameChange(e.target.value)}
              className="w-48"
            />
            <div className="flex items-center gap-2">
              <label className="text-muted-foreground text-sm">From</label>
              <Input
                type="date"
                value={since}
                onChange={(e) => {
                  setSince(e.target.value);
                  setPage(0);
                }}
                className="w-40"
              />
            </div>
            <div className="flex items-center gap-2">
              <label className="text-muted-foreground text-sm">To</label>
              <Input
                type="date"
                value={until}
                onChange={(e) => {
                  setUntil(e.target.value);
                  setPage(0);
                }}
                className="w-40"
              />
            </div>
            {(nameFilter || since || until) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setNameFilter("");
                  setSince("");
                  setUntil("");
                  setPage(0);
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {/* Table */}
          {!isLoading && symptoms.length === 0 ? (
            <EmptyState />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Occurred</TableHead>
                  <TableHead>Notes</TableHead>
                  <TableHead>Condition</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows />
                ) : (
                  symptoms.map((symptom) => (
                    <TableRow key={symptom.id}>
                      <TableCell className="font-medium">{symptom.name}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-16 overflow-hidden rounded-full bg-muted">
                            <div
                              className="h-full rounded-full"
                              style={{
                                width: `${(symptom.severity / 10) * 100}%`,
                                backgroundColor: severityColor(symptom.severity),
                              }}
                            />
                          </div>
                          <span className="text-sm tabular-nums">{symptom.severity}/10</span>
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {format(new Date(symptom.occurred_at), "MMM d, yyyy HH:mm")}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                        {symptom.notes ?? "\u2014"}
                      </TableCell>
                      <TableCell className="text-sm">
                        {symptom.condition_id ? (
                          <Badge variant="outline" className="text-xs">
                            {symptom.condition_id}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-xs">{"\u2014"}</span>
                        )}
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
