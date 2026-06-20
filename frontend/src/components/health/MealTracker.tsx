// ---------------------------------------------------------------------------
// MealTracker — Dispatch day-grouped rule-list for logged meals [bu-w7b18.5]
//
// Reframed from the Table/Card view (bu-5oeoq) to the Dispatch language:
// meals render as a hairline-separated rule-list grouped by day, with a mono
// day-header (Eyebrow) above each group. No Card shells, no Table chrome.
//
// Row anatomy: mono time | meal-type · description · nutrition | arrow → | actions
// Filter state (typeFilter, since, until) is owned by the parent MealsPage
// and passed in as controlled props so the right-column nutrition totals share
// the same date range.
//
// Spec: dashboard-domain-pages/spec.md → "Meals page with day-grouped display"
// bu-w7b18.5
// ---------------------------------------------------------------------------

import { useState } from "react";
import { toast } from "sonner";

import type { Meal, MealParams } from "@/api/types";
import { MealForm } from "@/components/health/MealForm";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { Skeleton } from "@/components/ui/skeleton";
import { Voice } from "@/components/ui/Voice";
import { cn } from "@/lib/utils";
import { useDeleteMeal, useMeals } from "@/hooks/use-health";

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Format a nutrition dict as a short inline string. Returns "—" when absent. */
function fmtNutrition(nutrition: Record<string, unknown> | null): string {
  if (!nutrition) return "";
  const cal = nutrition["calories"] ?? nutrition["estimated_calories"];
  if (cal != null) {
    const kcal = typeof cal === "number" ? Math.round(cal) : cal;
    return `${kcal} kcal`;
  }
  // Fallback: first non-null key-value pair
  const parts: string[] = [];
  for (const [k, v] of Object.entries(nutrition)) {
    if (v == null) continue;
    parts.push(`${k}: ${v}`);
  }
  return parts.slice(0, 2).join(", ");
}

/** Format eaten_at as HH:MM in local time. */
function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Format eaten_at date as a human-readable day header (e.g. "Wed 1 Jan 2026"). */
function fmtDayHeader(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "Unknown date";
  return d.toLocaleDateString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Extract YYYY-MM-DD from an ISO datetime string for grouping. */
function extractDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso.slice(0, 10);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** Group meals by local date. Preserves server order (most recent first). */
function groupByDay(meals: Meal[]): Array<{ dateKey: string; meals: Meal[] }> {
  const map = new Map<string, Meal[]>();
  for (const meal of meals) {
    const key = extractDate(meal.eaten_at);
    const group = map.get(key);
    if (group) {
      group.push(meal);
    } else {
      map.set(key, [meal]);
    }
  }
  // Map preserves insertion order — meals are already ordered by the server.
  return Array.from(map.entries()).map(([dateKey, groupMeals]) => ({
    dateKey,
    meals: groupMeals,
  }));
}

// ---------------------------------------------------------------------------
// RowAction — quiet mono text button (matches MedicationTracker pattern)
// ---------------------------------------------------------------------------

function RowAction({
  children,
  onClick,
  "aria-label": ariaLabel,
  tone = "muted",
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  "aria-label": string;
  tone?: "muted" | "danger";
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      disabled={disabled}
      className={cn(
        "shrink-0 font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50",
        tone === "danger"
          ? "text-muted-foreground hover:text-[var(--red)]"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// MealRow — single Dispatch rule-list row
// ---------------------------------------------------------------------------

function MealRow({ meal, onEdit }: { meal: Meal; onEdit: (meal: Meal) => void }) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const deleteMutation = useDeleteMeal();

  const nutritionStr = fmtNutrition(meal.nutrition);

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(meal.id);
      toast.success("Meal deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete meal.");
    }
  }

  return (
    <>
      <Row
        mark={
          <Mono muted>{fmtTime(meal.eaten_at)}</Mono>
        }
        meta={
          <div className="flex items-center gap-3">
            <RowAction
              aria-label={`Edit ${meal.description}`}
              onClick={() => onEdit(meal)}
            >
              Edit
            </RowAction>
            <RowAction
              aria-label={`Delete ${meal.description}`}
              tone="danger"
              onClick={() => setConfirmingDelete(true)}
              disabled={deleteMutation.isPending}
            >
              Delete
            </RowAction>
            <span
              className="shrink-0 font-mono text-[11px] text-muted-foreground select-none"
              aria-hidden
            >
              →
            </span>
          </div>
        }
      >
        <div className="min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-medium truncate">{meal.description}</span>
            <Mono muted className="shrink-0 capitalize">
              {meal.type}
            </Mono>
            {nutritionStr && (
              <Mono muted className="shrink-0">
                {nutritionStr}
              </Mono>
            )}
          </div>
          {meal.notes && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{meal.notes}</p>
          )}
        </div>
      </Row>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {meal.description}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes this meal entry from your meal log. The record is
              retained for history but will no longer appear here.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
              disabled={deleteMutation.isPending}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// DayGroup — a mono day-header + the meals for that day
// ---------------------------------------------------------------------------

function DayGroup({
  dateKey,
  meals,
  onEdit,
}: {
  dateKey: string;
  meals: Meal[];
  onEdit: (meal: Meal) => void;
}) {
  const label = fmtDayHeader(meals[0]?.eaten_at ?? dateKey);

  return (
    <div>
      {/* Day header rule */}
      <div
        className="flex items-center gap-3 py-1.5 border-b border-border"
        role="rowgroup"
        aria-label={label}
      >
        <Eyebrow as="div">{label}</Eyebrow>
      </div>
      {/* Meals for this day */}
      <div className="flex flex-col">
        {meals.map((meal) => (
          <MealRow key={meal.id} meal={meal} onEdit={onEdit} />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MealTracker props — filter state lifted to MealsPage
// ---------------------------------------------------------------------------

export interface MealTrackerProps {
  typeFilter: string;
  since: string;
  until: string;
  setTypeFilter: (v: string) => void;
  setSince: (v: string) => void;
  setUntil: (v: string) => void;
}

// ---------------------------------------------------------------------------
// MealTracker
// ---------------------------------------------------------------------------

const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"] as const;

export default function MealTracker({
  typeFilter,
  since,
  until,
  setTypeFilter,
  setSince,
  setUntil,
}: MealTrackerProps) {
  const [page, setPage] = useState(0);
  // `null` = closed; `undefined` = add mode; a Meal = edit mode.
  const [formTarget, setFormTarget] = useState<Meal | null | undefined>(null);

  const params: MealParams = {
    type: typeFilter || undefined,
    since: since || undefined,
    until: until || undefined,
    offset: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };

  const { data, isLoading } = useMeals(params);

  const meals = data?.data ?? [];
  const total = data?.meta?.total ?? 0;
  const hasMore = data?.meta?.has_more ?? false;

  const rangeStart = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeEnd = Math.min((page + 1) * PAGE_SIZE, total);

  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  const groups = groupByDay(meals);

  return (
    <div className="space-y-4">
      {/* Toolbar: meal-type badges + date filters + Log meal */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          {/* Meal-type filter badges */}
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by meal type">
            {(["", ...MEAL_TYPES] as const).map((t) => {
              const label = t === "" ? "All" : t.charAt(0).toUpperCase() + t.slice(1);
              const active = typeFilter === t;
              return (
                <button
                  key={t || "all"}
                  type="button"
                  aria-pressed={active}
                  onClick={() => {
                    setTypeFilter(typeFilter === t ? "" : t);
                    setPage(0);
                  }}
                  className={cn(
                    "font-mono text-[11px] uppercase tracking-wider px-2 py-0.5 rounded-sm border transition-colors cursor-pointer",
                    active
                      ? "border-foreground text-foreground"
                      : "border-border text-muted-foreground hover:text-foreground hover:border-foreground/40",
                  )}
                >
                  {label}
                </button>
              );
            })}
          </div>

          {/* Date range inputs */}
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-xs font-mono uppercase tracking-wider">From</label>
            <input
              type="date"
              value={since}
              max={until || undefined}
              onChange={(e) => {
                setSince(e.target.value);
                setPage(0);
              }}
              className="w-36 rounded-sm border border-border bg-background px-2 py-0.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
            />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-muted-foreground text-xs font-mono uppercase tracking-wider">To</label>
            <input
              type="date"
              value={until}
              min={since || undefined}
              onChange={(e) => {
                setUntil(e.target.value);
                setPage(0);
              }}
              className="w-36 rounded-sm border border-border bg-background px-2 py-0.5 text-xs font-mono text-foreground focus:outline-none focus:ring-1 focus:ring-foreground/20"
            />
          </div>
          {(typeFilter || since || until) && (
            <button
              type="button"
              onClick={() => {
                setTypeFilter("");
                setSince("");
                setUntil("");
                setPage(0);
              }}
              className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>

        {/* Add meal affordance */}
        <button
          type="button"
          onClick={() => setFormTarget(undefined)}
          className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
          aria-label="Log meal"
        >
          Log meal
        </button>
      </div>

      {/* Day-grouped rule-list */}
      {isLoading ? (
        <div className="flex flex-col">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="flex items-center gap-3 border-b border-border py-2.5">
              <Skeleton className="h-3 w-10" />
              <Skeleton className="h-4 w-48" />
              <Skeleton className="ml-auto h-3 w-20" />
            </div>
          ))}
        </div>
      ) : groups.length === 0 ? (
        <Voice variant="italic" className="text-muted-foreground">
          No meals found.
        </Voice>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map(({ dateKey, meals: groupMeals }) => (
            <DayGroup
              key={dateKey}
              dateKey={dateKey}
              meals={groupMeals}
              onEdit={setFormTarget}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between">
          <Mono muted>
            {rangeStart}–{rangeEnd} of {total.toLocaleString()}
          </Mono>
          <div className="flex gap-3">
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
              className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground hover:text-foreground underline underline-offset-2 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-default"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {/* Add / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && setFormTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit meal" : "Log meal"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this meal entry's details."
                : "Log a meal. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <MealForm
            meal={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
