// ---------------------------------------------------------------------------
// MealTracker day-grouping timezone repro [bu-s0d8j]
//
// Two meals straddle owner-midnight (Asia/Singapore, UTC+8): one at 23:30 and
// one at 00:30 owner-local — the same host-UTC calendar day. Correct owner-tz
// bucketing splits them into two day groups with owner-tz headers; the old
// host-local getters would collapse them into one (on a UTC host) or split them
// differently on another host. We render under several host timezones and
// assert the day-group headers are identical and owner-tz-correct — proving the
// buckets follow the owner's clock, not the host's.
// ---------------------------------------------------------------------------

// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import MealTracker, { type MealTrackerProps } from "@/components/health/MealTracker";

vi.mock("@/hooks/use-health", () => ({
  useMeals: () => ({
    data: {
      data: [
        {
          id: "meal-late", // 2026-07-12 00:30 SGT — owner day 12
          type: "snack",
          description: "Midnight snack",
          nutrition: null,
          eaten_at: "2026-07-11T16:30:00Z",
          notes: null,
          created_at: "2026-07-11T16:30:00Z",
        },
        {
          id: "meal-early", // 2026-07-11 23:30 SGT — owner day 11
          type: "dinner",
          description: "Late dinner",
          nutrition: null,
          eaten_at: "2026-07-11T15:30:00Z",
          notes: null,
          created_at: "2026-07-11T15:30:00Z",
        },
      ],
      meta: { total: 2, has_more: false },
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useDeleteMeal: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const noopProps: MealTrackerProps = {
  typeFilter: "",
  since: "",
  until: "",
  setTypeFilter: vi.fn(),
  setSince: vi.fn(),
  setUntil: vi.fn(),
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MealTracker: owner-tz day grouping", () => {
  const originalTZ = process.env.TZ;

  afterEach(() => {
    process.env.TZ = originalTZ;
  });

  it("splits owner-midnight-straddling meals into owner-tz days across host timezones", () => {
    for (const tz of ["UTC", "America/Los_Angeles", "Pacific/Kiritimati"]) {
      process.env.TZ = tz;
      const { unmount } = render(<MealTracker {...noopProps} />);

      const groups = screen.getAllByRole("rowgroup");
      const labels = groups.map((g) => g.getAttribute("aria-label"));

      // Two distinct owner-tz days, newest first (server order preserved).
      // Assert on the owner-tz calendar day (locale renders "Jul 12, 2026").
      expect(labels).toHaveLength(2);
      expect(labels[0]).toContain("Jul 12, 2026");
      expect(labels[1]).toContain("Jul 11, 2026");

      unmount();
      cleanup();
    }
  });
});
