/**
 * MealsPage DailyTotals — degraded-source honesty [bu-hmdqz.13]
 *
 * A failing nutrition-summary read MUST render a named SourceDegradedNote, never
 * the calm "No nutrition data for this window." empty copy.
 */

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MealsPage from "@/pages/MealsPage";

const refetchSummary = vi.fn();

vi.mock("@/hooks/use-health", () => ({
  useNutritionSummary: () => ({
    data: undefined,
    isLoading: false,
    isError: true,
    refetch: refetchSummary,
  }),
  useMeals: () => ({ data: { data: [], meta: { total: 0, has_more: false } }, isLoading: false, isError: false }),
  useDeleteMeal: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("@/components/ui/timezone-context", () => ({
  useTimezone: () => "UTC",
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MealsPage DailyTotals — degraded source honesty", () => {
  it("names a failing nutrition source instead of 'No nutrition data'", () => {
    render(<MealsPage />);
    expect(screen.getByTestId("nutrition-totals-degraded")).toBeTruthy();
    expect(screen.queryByText("No nutrition data for this window.")).toBeNull();
  });
});
