// ---------------------------------------------------------------------------
// MealsPage — /health/meals [bu-w7b18.5]
//
// Reframed to the Dispatch language: a mono eyebrow + Display-500 headline +
// serif Voice lead, then a two-column layout (meals rule-list / daily totals).
// No Card shells. Filter state is lifted here so the right-column nutrition
// totals use the same date range as the meal list.
//
// Left column — MealTracker (day-grouped rule-list):
//   - meal-type filter badges (All / breakfast / lunch / dinner / snack)
//   - date-range inputs (From / To) + Clear
//   - meals grouped by day under mono day-header Eyebrows
//   - Log meal / edit / delete affordances
//
// Right column — Daily totals mini-KPI:
//   - Sourced from GET /api/health/nutrition/summary
//   - NOT re-computed client-side from individual meal rows
//   - Shows total calories + macros for the visible window
//   - Em-dash for absent/zero values (meal_count === 0)
//
// Spec: dashboard-domain-pages/spec.md → "Meals page with day-grouped display"
// bu-w7b18.5
// ---------------------------------------------------------------------------

import { useState } from "react";

import { useNutritionSummary } from "@/hooks/use-health";
import MealTracker from "@/components/health/MealTracker";
import { Display } from "@/components/ui/Display";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Voice } from "@/components/ui/Voice";

// ---------------------------------------------------------------------------
// Default window helpers
// ---------------------------------------------------------------------------

/** ISO date string for today (YYYY-MM-DD). */
function todayISO(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** ISO date string for N days ago (YYYY-MM-DD). */
function daysAgoISO(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------
// DailyTotals — right-column mini-KPI sourced from /nutrition/summary
// ---------------------------------------------------------------------------

function fmtNum(v: number | undefined | null, decimals = 0): string {
  if (v == null || isNaN(v)) return "—";
  return v.toFixed(decimals);
}

interface DailyTotalsProps {
  since: string;
  until: string;
}

function DailyTotals({ since, until }: DailyTotalsProps) {
  // Fall back to a default 30-day window when no date range is set,
  // so the nutrition/summary call is always enabled.
  const start = since || daysAgoISO(30);
  const end = until || todayISO();

  const { data, isLoading } = useNutritionSummary({ start, end });

  const noData = !isLoading && (!data || data.meal_count === 0);

  const totals: Array<{ label: string; value: string; unit: string }> = [
    {
      label: "Calories",
      value: noData ? "—" : fmtNum(data?.total_calories),
      unit: "kcal",
    },
    {
      label: "Protein",
      value: noData ? "—" : fmtNum(data?.total_protein_g, 1),
      unit: "g",
    },
    {
      label: "Carbs",
      value: noData ? "—" : fmtNum(data?.total_carbs_g, 1),
      unit: "g",
    },
    {
      label: "Fat",
      value: noData ? "—" : fmtNum(data?.total_fat_g, 1),
      unit: "g",
    },
  ];

  return (
    <aside
      className="md:w-48 md:shrink-0"
      aria-label="Daily nutrition totals"
      data-testid="nutrition-totals"
    >
      <Eyebrow as="div" className="mb-3">
        {since || until ? "Totals for range" : "Last 30 days"}
      </Eyebrow>

      {isLoading ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="flex justify-between border-b border-border/40 py-1.5">
              <span className="h-3 w-14 rounded bg-muted animate-pulse" />
              <span className="h-3 w-10 rounded bg-muted animate-pulse" />
            </div>
          ))}
        </div>
      ) : noData ? (
        <Voice variant="italic" className="text-sm text-muted-foreground">
          No nutrition data for this window.
        </Voice>
      ) : (
        <>
          <ul className="flex flex-col">
            {totals.map((item) => (
              <li
                key={item.label}
                className="grid grid-cols-[1fr_auto] items-baseline gap-3 border-b border-border/40 py-1.5 last:border-0"
              >
                <Mono muted>{item.label}</Mono>
                <span className="text-sm tabular-nums">
                  {item.value}
                  {item.value !== "—" && (
                    <Mono muted className="ml-0.5">{item.unit}</Mono>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {data && data.meal_count > 0 && (
            <div className="mt-3">
              <Mono muted>{data.meal_count} meal{data.meal_count !== 1 ? "s" : ""} · {data.days} day{data.days !== 1 ? "s" : ""}</Mono>
            </div>
          )}
        </>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// MealsPage
// ---------------------------------------------------------------------------

export default function MealsPage() {
  // Filter state lifted so both MealTracker and DailyTotals share it.
  const [typeFilter, setTypeFilter] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <Eyebrow as="div">health · meals</Eyebrow>
        <Display>Meals</Display>
        <Voice className="max-w-2xl text-muted-foreground">
          Your eating log, grouped by day. Add, edit, or remove meals here; entries logged via your
          Health butler appear automatically.
        </Voice>
      </header>

      <div className="flex flex-col gap-8 md:flex-row md:items-start md:gap-10">
        {/* Left column — day-grouped meal rule-list + filters + add */}
        <div className="min-w-0 flex-1">
          <MealTracker
            typeFilter={typeFilter}
            since={since}
            until={until}
            setTypeFilter={setTypeFilter}
            setSince={setSince}
            setUntil={setUntil}
          />
        </div>

        {/* Right column — daily totals from /nutrition/summary */}
        <DailyTotals since={since} until={until} />
      </div>
    </div>
  );
}
