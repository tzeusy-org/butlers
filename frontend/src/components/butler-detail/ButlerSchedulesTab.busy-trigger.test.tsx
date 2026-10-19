// @vitest-environment jsdom
/**
 * ButlerSchedulesTab: a busy schedule trigger (409 `scheduler_busy`) reads as a warning with the
 * server's message (bu-3fle50). The managed-schedule 409s and every other failure stay errors,
 * so the tab branches on the error code, never on the status or the message text.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { toast } from "sonner";

import ButlerSchedulesTab from "@/components/butler-detail/ButlerSchedulesTab";
import { ApiError } from "@/api/client";
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

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }));

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

const BUSY_MESSAGE = "Scheduled work is already running on this butler; try again when it finishes.";

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

function clickRunAndGetOnError() {
  render(<ButlerSchedulesTab butlerName="general" />);
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(mockTrigger).toHaveBeenCalledTimes(1);
  return mockTrigger.mock.calls[0][1].onError as (err: unknown) => void;
}

describe("ButlerSchedulesTab busy schedule trigger", () => {
  it("shows a busy 409 as a warning with the server message", () => {
    const onError = clickRunAndGetOnError();

    onError(new ApiError("scheduler_busy", BUSY_MESSAGE, 409));

    expect(toast.warning).toHaveBeenCalledWith(BUSY_MESSAGE);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it.each(["SCHEDULE_TOML_MANAGED", "SCHEDULE_MANAGED"])(
    "keeps the %s 409 refusal a red error",
    (code) => {
      const onError = clickRunAndGetOnError();

      onError(new ApiError(code, "This schedule is managed elsewhere.", 409));

      expect(toast.error).toHaveBeenCalledWith(
        "Failed to trigger schedule: This schedule is managed elsewhere.",
      );
      expect(toast.warning).not.toHaveBeenCalled();
    },
  );

  it("does not treat an unrelated 409 with busy-sounding text as busy", () => {
    const onError = clickRunAndGetOnError();

    onError(new ApiError("UNKNOWN_ERROR", BUSY_MESSAGE, 409));

    expect(toast.error).toHaveBeenCalledWith(`Failed to trigger schedule: ${BUSY_MESSAGE}`);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it.each([
    ["a 502 dispatch failure", new ApiError("UNKNOWN_ERROR", "Schedule trigger failed: boom", 502)],
    ["a plain error", new Error("network down")],
  ])("keeps %s a red error", (_label, err) => {
    const onError = clickRunAndGetOnError();

    onError(err);

    expect(toast.error).toHaveBeenCalledWith(`Failed to trigger schedule: ${err.message}`);
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("falls back to Unknown error for a non-Error rejection", () => {
    const onError = clickRunAndGetOnError();

    onError("nope");

    expect(toast.error).toHaveBeenCalledWith("Failed to trigger schedule: Unknown error");
  });
});
