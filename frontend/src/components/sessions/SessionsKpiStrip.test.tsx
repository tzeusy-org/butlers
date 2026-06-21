// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import { render, cleanup } from "@testing-library/react";

import type { SessionAggregate } from "@/api/types";

vi.mock("@/hooks/use-sessions", () => ({
  useSessionAggregate: vi.fn(),
}));

import { useSessionAggregate } from "@/hooks/use-sessions";
import { SessionsKpiStrip } from "@/components/sessions/SessionsKpiStrip";

const mockUseSessionAggregate = vi.mocked(useSessionAggregate);

function makeAggregate(overrides: Partial<SessionAggregate> = {}): SessionAggregate {
  return {
    total: 100,
    success_count: 90,
    failed_count: 5,
    running_count: 5,
    success_rate: 0.9474,
    input_tokens: 1_500_000,
    output_tokens: 500_000,
    by_butler: [{ butler: "health", count: 60 }],
    ...overrides,
  };
}

function setAggregate(agg: SessionAggregate) {
  mockUseSessionAggregate.mockReturnValue({
    data: { data: agg, meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useSessionAggregate>);
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(cleanup);

describe("SessionsKpiStrip", () => {
  it("renders window-true totals, success rate, and the top butler", () => {
    setAggregate(makeAggregate());
    const { getByTestId } = render(<SessionsKpiStrip filterParams={{ butler: "health" }} />);
    const text = getByTestId("sessions-kpi-strip").textContent ?? "";
    expect(text).toContain("100"); // total
    expect(text).toContain("94.7%"); // success rate
    expect(text).toContain("health"); // top butler
    expect(text).toContain("Matching filters"); // honesty label
  });

  it("renders an honest dash when success_rate is null (no completed sessions)", () => {
    setAggregate(
      makeAggregate({ success_rate: null, success_count: 0, failed_count: 0, running_count: 3 }),
    );
    const { getByTestId } = render(<SessionsKpiStrip filterParams={{}} />);
    const text = getByTestId("sessions-kpi-strip").textContent ?? "";
    expect(text).toContain("—");
    expect(text).toContain("no completed sessions");
    expect(text).not.toContain("NaN");
  });

  it("passes the filter params straight through to the aggregate hook", () => {
    setAggregate(makeAggregate());
    render(<SessionsKpiStrip filterParams={{ butler: "finance", status: "running" }} />);
    expect(mockUseSessionAggregate).toHaveBeenCalledWith({ butler: "finance", status: "running" });
  });
});
