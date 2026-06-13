import { useMemo, useState } from "react";
import { format } from "date-fns";

import type { MealParams } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Time } from "@/components/ui/time";
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
import { useMeals } from "@/hooks/use-health";
import { ButlerManagedNote } from "@/components/health/ButlerManagedNote";
import { EmptyState as EmptyStateUI } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const PAGE_SIZE = 50;

const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"] as const;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <TableRow key={i}>
          <TableCell><Skeleton className="h-4 w-20" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
          <TableCell><Skeleton className="h-4 w-32" /></TableCell>
          <TableCell><Skeleton className="h-4 w-24" /></TableCell>
          <TableCell><Skeleton className="h-4 w-48" /></TableCell>
        </TableRow>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <EmptyStateUI
      title="No meals found."
      description="Meals appear as they are logged with nutrition data and eating patterns."
    />
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Group meals by date (YYYY-MM-DD). */
function groupByDate(meals: { eaten_at: string }[]): Map<string, typeof meals> {
  const groups = new Map<string, typeof meals>();
  for (const meal of meals) {
    const day = format(new Date(meal.eaten_at), "yyyy-MM-dd");
    const existing = groups.get(day) ?? [];
    existing.push(meal);
    groups.set(day, existing);
  }
  return groups;
}

/** Format nutrition JSONB as a short string. */
function formatNutrition(nutrition: Record<string, unknown> | null): string {
  if (!nutrition) return "\u2014";
  const parts: string[] = [];
  for (const [k, v] of Object.entries(nutrition)) {
    parts.push(`${k}: ${v}`);
  }
  return parts.join(", ") || "\u2014";
}

// ---------------------------------------------------------------------------
// MealsPage
// ---------------------------------------------------------------------------

export default function MealsPage() {
  const [typeFilter, setTypeFilter] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [page, setPage] = useState(0);

  const params: MealParams = {
    type: typeFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useMeals(params);

  const meals = useMemo(() => data?.data ?? [], [data]);
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  // Group meals by day for display
  const grouped = useMemo(() => groupByDate(meals), [meals]);

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Meals</h1>
        <p className="text-muted-foreground mt-1">
          Logged meals, nutrition, and eating patterns.
        </p>
        <ButlerManagedNote noun="Meals" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>All Meals</CardTitle>
          <CardDescription>
            {total > 0
              ? `${total.toLocaleString()} meal${total !== 1 ? "s" : ""}`
              : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filters */}
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge
                variant={typeFilter === "" ? "default" : "outline"}
                className="cursor-pointer"
                onClick={() => {
                  setTypeFilter("");
                  setPage(0);
                }}
              >
                All
              </Badge>
              {MEAL_TYPES.map((t) => (
                <Badge
                  key={t}
                  variant={typeFilter === t ? "default" : "outline"}
                  className="cursor-pointer capitalize"
                  onClick={() => {
                    setTypeFilter(typeFilter === t ? "" : t);
                    setPage(0);
                  }}
                >
                  {t}
                </Badge>
              ))}
            </div>
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
            {(typeFilter || since || until) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setTypeFilter("");
                  setSince("");
                  setUntil("");
                  setPage(0);
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {/* Grouped table */}
          {!isLoading && meals.length === 0 ? (
            <EmptyState />
          ) : isLoading ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Nutrition</TableHead>
                  <TableHead>Time</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <SkeletonRows />
              </TableBody>
            </Table>
          ) : (
            <div className="space-y-6">
              {Array.from(grouped.entries()).map(([day, dayMeals]) => (
                <div key={day}>
                  <h3 className="mb-2 text-sm font-semibold">
                    <Time value={day} mode="absolute" precision="weekday" showTitle={false} />
                  </h3>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Type</TableHead>
                        <TableHead>Description</TableHead>
                        <TableHead>Nutrition</TableHead>
                        <TableHead>Time</TableHead>
                        <TableHead>Notes</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {dayMeals.map((meal) => {
                        // We know meal is a Meal from the API, but TS sees it as the group type
                        const m = meal as (typeof meals)[number];
                        return (
                          <TableRow key={m.id}>
                            <TableCell>
                              <Badge variant="outline" className="text-xs capitalize">
                                {m.type}
                              </Badge>
                            </TableCell>
                            <TableCell className="max-w-xs truncate text-sm">
                              {m.description}
                            </TableCell>
                            <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                              {formatNutrition(m.nutrition)}
                            </TableCell>
                            <TableCell className="text-muted-foreground text-sm">
                              <Time value={m.eaten_at} mode="absolute" precision="time" showTitle={false} />
                            </TableCell>
                            <TableCell className="text-muted-foreground max-w-xs truncate text-sm">
                              {m.notes ?? "\u2014"}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              ))}
            </div>
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
