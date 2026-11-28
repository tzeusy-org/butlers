// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useRsvpDocket } from "@/hooks/use-rsvp-docket";
import { useCalendarProposals } from "@/hooks/use-calendar-workspace";
import { useCalendarPanels } from "@/pages/calendar/use-calendar-panels";

vi.mock("@/hooks/use-calendar-workspace", () => ({
  useAcceptCalendarProposal: () => ({}),
  useDismissCalendarProposal: () => ({}),
  useCalendarWorkspaceAudit: () => ({}),
  useCalendarProposals: vi.fn(),
}));

vi.mock("@/hooks/use-rsvp-docket", () => ({
  useRsvpDocket: vi.fn(() => ({})),
  useSubmitRsvpDocket: () => ({}),
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

describe("useCalendarPanels docket", () => {
  it("reads the docket only while its panel is open, and the panels are exclusive", () => {
    vi.mocked(useCalendarProposals).mockReturnValue({
      isError: false,
      isLoading: false,
      data: { data: { entries: [] } },
    } as unknown as ReturnType<typeof useCalendarProposals>);
    const { result } = renderHook(() =>
      useCalendarPanels({ queryStart: "a", queryEnd: "b", timezone: "UTC" }),
    );
    expect(vi.mocked(useRsvpDocket).mock.lastCall?.[0]).toEqual({ enabled: false });

    act(() => result.current.togglePanel("docket"));
    expect(result.current.docketOpen).toBe(true);
    expect(vi.mocked(useRsvpDocket).mock.lastCall?.[0]).toEqual({ enabled: true });

    act(() => result.current.togglePanel("proposals"));
    expect(result.current.docketOpen).toBe(false);
    expect(result.current.proposalsOpen).toBe(true);
  });
});
