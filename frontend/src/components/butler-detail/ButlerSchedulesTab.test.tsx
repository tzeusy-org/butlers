// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import ButlerSchedulesTab from "@/components/butler-detail/ButlerSchedulesTab";
import {
  useCreateSchedule,
  useDeleteSchedule,
  useSchedules,
  useToggleSchedule,
  useTriggerSchedule,
  useUpdateSchedule,
} from "@/hooks/use-schedules";

vi.mock("@/hooks/use-schedules", () => ({
  useCreateSchedule: vi.fn(),
  useDeleteSchedule: vi.fn(),
  useSchedules: vi.fn(),
  useToggleSchedule: vi.fn(),
  useTriggerSchedule: vi.fn(),
  useUpdateSchedule: vi.fn(),
}));

const schedule = {
  id: "schedule-1",
  name: "daily_digest",
  cron: "0 9 * * *",
  prompt: "Run the daily digest",
  source: "db",
  enabled: true,
  next_run_at: null,
  last_run_at: null,
  created_at: "2026-09-16T00:00:00Z",
  updated_at: "2026-09-16T00:00:00Z",
};

const receipt = {
  id: "schedule-1",
  name: "daily_digest",
  source: "db",
  status: "updated" as const,
  outcome: "applied" as const,
  requested_enabled: false,
  observed_enabled: false,
  changed: true,
  next_run_at: null,
  audit: {
    action: "schedule.toggle" as const,
    result: "success" as const,
    target: "schedule:schedule-1",
  },
};

function idleMutation() {
  return { mutate: vi.fn(), error: null, isPending: false };
}

describe("ButlerSchedulesTab schedule toggle", () => {
  beforeEach(() => {
    vi.mocked(useSchedules).mockReturnValue({
      data: { data: [schedule], meta: {} },
      isLoading: false,
      isError: false,
      error: null,
    } as never);
    vi.mocked(useCreateSchedule).mockReturnValue(idleMutation() as never);
    vi.mocked(useDeleteSchedule).mockReturnValue(idleMutation() as never);
    vi.mocked(useTriggerSchedule).mockReturnValue(idleMutation() as never);
    vi.mocked(useUpdateSchedule).mockReturnValue(idleMutation() as never);
  });

  it("sends the requested state and renders the server-observed audit receipt", () => {
    const mutate = vi.fn();
    vi.mocked(useToggleSchedule).mockReturnValue({
      mutate,
      error: null,
      isPending: false,
    } as never);

    render(<ButlerSchedulesTab butlerName="general" />);
    fireEvent.click(screen.getByRole("button", { name: "On" }));

    expect(mutate).toHaveBeenCalledWith(
      { scheduleId: "schedule-1", enabled: false },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );

    const options = mutate.mock.calls[0][1] as { onSuccess: (value: { data: typeof receipt }) => void };
    act(() => options.onSuccess({ data: receipt }));

    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Server confirmed schedule \"daily_digest\" is disabled.");
    expect(status.textContent).toContain("schedule.toggle (success)");
  });
});
