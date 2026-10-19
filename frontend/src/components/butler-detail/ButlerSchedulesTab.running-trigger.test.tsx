// @vitest-environment jsdom
/**
 * ButlerSchedulesTab: a trigger the API answers 202 "running" (it outlasted the accept window)
 * reads as running, not as triggered or failed (bu-iw3s5w). Real failures stay errors.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { toast } from "sonner";

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

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
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

const mockTrigger = vi.fn();

function idleMutation() {
  return { mutate: vi.fn(), mutateAsync: vi.fn(), error: null, isPending: false };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useSchedules).mockReturnValue({
    data: { data: [schedule], meta: {} },
    isLoading: false,
    isError: false,
    error: null,
  } as never);
  vi.mocked(useCreateSchedule).mockReturnValue(idleMutation() as never);
  vi.mocked(useDeleteSchedule).mockReturnValue(idleMutation() as never);
  vi.mocked(useToggleSchedule).mockReturnValue(idleMutation() as never);
  vi.mocked(useUpdateSchedule).mockReturnValue(idleMutation() as never);
  vi.mocked(useTriggerSchedule).mockReturnValue({
    ...idleMutation(),
    mutate: mockTrigger,
  } as never);
});

afterEach(() => {
  cleanup();
});

describe("ButlerSchedulesTab running schedule trigger", () => {
  function clickRunAndGetOnSuccess() {
    render(<ButlerSchedulesTab butlerName="general" />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    expect(mockTrigger).toHaveBeenCalledTimes(1);
    return mockTrigger.mock.calls[0][1].onSuccess as (response: unknown) => void;
  }

  it("shows an info toast, not success, when the trigger is still running", () => {
    const onSuccess = clickRunAndGetOnSuccess();

    onSuccess({ data: { id: "schedule-1", status: "running" } });

    expect(toast.info).toHaveBeenCalledWith(
      'Schedule "daily_digest" is running; it may take a few minutes',
    );
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("still shows success when the trigger completed", () => {
    const onSuccess = clickRunAndGetOnSuccess();

    onSuccess({ data: { id: "schedule-1", status: "triggered", name: "daily_digest" } });

    expect(toast.success).toHaveBeenCalledWith('Schedule "daily_digest" triggered');
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("still shows a red error for a failed trigger", () => {
    render(<ButlerSchedulesTab butlerName="general" />);
    fireEvent.click(screen.getByRole("button", { name: "Run" }));
    const onError = mockTrigger.mock.calls[0][1].onError as (err: unknown) => void;

    onError(new Error("boom"));

    expect(toast.error).toHaveBeenCalledWith("Failed to trigger schedule: boom");
    expect(toast.info).not.toHaveBeenCalled();
  });
});
