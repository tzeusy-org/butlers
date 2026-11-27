// @vitest-environment jsdom

import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useCalendarProposals } from "@/hooks/use-calendar-workspace";
import { useCalendarPanels } from "@/pages/calendar/use-calendar-panels";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useAcceptCalendarProposal: () => ({}),
  useDismissCalendarProposal: () => ({}),
  useCalendarWorkspaceAudit: () => ({}),
  useCalendarProposals: vi.fn(),
}));

function proposals(data: Record<string, unknown>, extra: Record<string, unknown> = {}) {
  vi.mocked(useCalendarProposals).mockReturnValue({
    isError: false,
    isLoading: false,
    data: { data: { entries: [], ...data } },
    ...extra,
  } as unknown as ReturnType<typeof useCalendarProposals>);
  return renderHook(() =>
    useCalendarPanels({ queryStart: "a", queryEnd: "b", timezone: "UTC" }),
  ).result.current;
}

describe("useCalendarPanels proposal count", () => {
  it("counts the entries of a complete lane without the panel being open", () => {
    expect(proposals({ entries: [{}, {}] }).proposalCount).toBe(2);
  });

  it("is unknown, not zero, when rows were dropped", () => {
    const panels = proposals({ entries: [], proposals_dropped_rows: 1 });
    expect(panels.proposalCount).toBeNull();
    expect(panels.proposalsError).toBe(true);
  });

  it("is unknown when the lane is unavailable or the read failed", () => {
    expect(proposals({ proposals_available: false }).proposalsError).toBe(true);
    expect(proposals({}, { isError: true }).proposalCount).toBeNull();
  });
});
