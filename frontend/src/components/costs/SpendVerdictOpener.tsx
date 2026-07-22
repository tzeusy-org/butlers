// ---------------------------------------------------------------------------
// SpendVerdictOpener -- /spend page opener (bu-qvnce.9, JARVIS pursuit move
// 9, slice 2)
//
// Composes the forecast + movers data SpendPage already fetches into one
// synthesized verdict line via the shared DispatchVerdict primitive: pace
// (MTD burn rate), projection_confidence (fetched at ForecastData but
// discarded before render -- SpendPage.tsx:80 in the pre-move-9 audit), and
// the top mover, joined into one calm line when nothing is wrong.
//
// Deliberately does NOT restate the over-ceiling condition: SpendPage already
// renders a dedicated role="alert" banner for that (the "attention-row" just
// above this opener) -- duplicating the same warning in the verdict line
// would just be noise sitting right next to the real alert. An unavailable
// spend-comparison source (a genuine degraded input to THIS component's own
// pace/mover math) still names itself as a clause and suppresses the calm
// line, per the isError-suppression contract.
// ---------------------------------------------------------------------------

import { DispatchVerdict, type VerdictClause } from "@/components/ui/dispatch-verdict";
import { formatCostUsd } from "@/lib/format-cost";
import { computeMovers } from "@/lib/spend-movers";
import type { ForecastData } from "@/lib/spend-forecast";
import type { UnpricedModelUsage } from "@/api/types";

export interface SpendVerdictOpenerProps {
  forecast: ForecastData | undefined;
  forecastLoading: boolean;
  forecastError: boolean;
  currentByButler: Record<string, number>;
  priorByButler: Record<string, number>;
  unavailableButlers: ReadonlySet<string>;
  /** Usage omitted from either comparison-window priced subtotal. */
  comparisonUnpricedModels?: readonly UnpricedModelUsage[];
  moversLoading: boolean;
  moversError: boolean;
}

function unpricedCoverageClause(
  key: string,
  coverage: string,
  models: readonly UnpricedModelUsage[] | undefined,
): VerdictClause | null {
  if (!models || models.length === 0) return null;
  const names = Array.from(new Set(models.map(({ model }) => model))).sort();
  return {
    key,
    text: `${coverage} incomplete: ${names.length} unpriced model${names.length === 1 ? "" : "s"} (${names.join(", ")})`,
  };
}

function buildClauses({
  forecast,
  unavailableButlers,
  comparisonUnpricedModels,
}: Pick<
  SpendVerdictOpenerProps,
  "forecast" | "unavailableButlers" | "comparisonUnpricedModels"
>): VerdictClause[] {
  const clauses: VerdictClause[] = [];

  if (unavailableButlers.size > 0) {
    const names = Array.from(unavailableButlers).sort();
    clauses.push({
      key: "unavailable-butlers",
      text: `${names.length} butler${names.length === 1 ? "" : "s"} excluded from spend comparison, cost source unavailable: ${names.join(", ")}`,
    });
  }

  const forecastCoverage = unpricedCoverageClause(
    "forecast-unpriced-models",
    "forecast coverage",
    forecast?.unpriced_models,
  );
  if (forecastCoverage) clauses.push(forecastCoverage);

  const comparisonCoverage = unpricedCoverageClause(
    "comparison-unpriced-models",
    "spend comparison",
    comparisonUnpricedModels,
  );
  if (comparisonCoverage) clauses.push(comparisonCoverage);

  return clauses;
}

function buildAllClear(
  forecast: ForecastData,
  currentByButler: Record<string, number>,
  priorByButler: Record<string, number>,
  unavailableButlers: ReadonlySet<string>,
): string {
  const pace = forecast.mtd_usd / Math.max(forecast.days_elapsed, 1);
  const movers = computeMovers(currentByButler, priorByButler, unavailableButlers, 1);
  const topMover = movers[0];

  // Defensive: some fixtures/older responses may omit projection_confidence
  // even though the type declares it required -- never render a raw
  // "undefined-confidence" string if that ever happens in practice.
  const confidence =
    forecast.projection_confidence === "low" || forecast.projection_confidence === "normal"
      ? `${forecast.projection_confidence}-confidence projection`
      : null;

  const parts = [
    `${formatCostUsd(pace)}/day pace`,
    confidence,
    topMover
      ? `top mover ${topMover.name} ${topMover.delta > 0 ? "+" : "−"}${formatCostUsd(Math.abs(topMover.delta))}`
      : null,
  ].filter((p): p is string => Boolean(p));

  return `On pace: ${parts.join(", ")}`;
}

export function SpendVerdictOpener({
  forecast,
  forecastLoading,
  forecastError,
  currentByButler,
  priorByButler,
  unavailableButlers,
  comparisonUnpricedModels = [],
  moversLoading,
  moversError,
}: SpendVerdictOpenerProps) {
  const clauses = buildClauses({ forecast, unavailableButlers, comparisonUnpricedModels });
  // A settled forecast with ceiling_source_error=true (bu-7o89u.1: ledger MTD
  // pricing failed or no DB pool wired) carries fabricated mtd_usd=0 --
  // treat it as an errored source too so DispatchVerdict never computes a
  // "$0.00/day pace" calm line from it.
  const forecastDegraded = forecastError || forecast?.ceiling_source_error === true;

  return (
    <DispatchVerdict
      testId="spend"
      landmarkLabel="Spend verdict"
      sources={[
        { label: "spend forecast", isLoading: forecastLoading, isError: forecastDegraded },
        { label: "spend comparison", isLoading: moversLoading, isError: moversError },
      ]}
      clauses={clauses}
      allClear={
        forecast ? buildAllClear(forecast, currentByButler, priorByButler, unavailableButlers) : "On pace"
      }
    />
  );
}
