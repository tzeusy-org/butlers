// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("ButlerSchedulesTab schedule toggle", () => {
  afterEach(cleanup);

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
    vi.mocked(useTriggerSchedule).mockReturnValue(idleMutation() as never);
    vi.mocked(useUpdateSchedule).mockReturnValue(idleMutation() as never);
  });

  it("sends the requested state and renders the server-observed audit receipt", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ data: receipt });
    vi.mocked(useToggleSchedule).mockReturnValue({
      mutateAsync,
      error: null,
      isPending: false,
    } as never);

    render(<ButlerSchedulesTab butlerName="general" />);
    fireEvent.click(screen.getByRole("button", { name: "On" }));

    expect(mutateAsync).toHaveBeenCalledWith({ scheduleId: "schedule-1", enabled: false });

    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("Server confirmed schedule \"daily_digest\" is disabled.");
    expect(status.textContent).toContain("schedule.toggle (success)");
  });

  it.each(["schedule-1", "schedule-2"])(
    "keeps each row pending when %s completes before the other row refuses",
    async (successfulId) => {
      const secondSchedule = {
        ...schedule,
        id: "schedule-2",
        name: "evening_digest",
        enabled: false,
      };
      vi.mocked(useSchedules).mockReturnValue({
        data: { data: [schedule, secondSchedule], meta: {} },
        isLoading: false,
        isError: false,
        error: null,
      } as never);

      const first = deferred<{ data: typeof receipt }>();
      const second = deferred<{ data: typeof receipt }>();
      const mutateAsync = vi.fn(({ scheduleId }: { scheduleId: string }) =>
        scheduleId === schedule.id ? first.promise : second.promise,
      );
      vi.mocked(useToggleSchedule).mockReturnValue({
        mutateAsync,
        error: null,
        isPending: false,
      } as never);

      const { container } = render(<ButlerSchedulesTab butlerName="general" />);
      const firstToggle = within(within(container).getByText(schedule.name).closest("tr")!).getByRole(
        "button",
        { name: "On" },
      ) as HTMLButtonElement;
      const secondToggle = within(within(container).getByText(secondSchedule.name).closest("tr")!).getByRole(
        "button",
        { name: "Off" },
      ) as HTMLButtonElement;
      fireEvent.click(firstToggle);
      fireEvent.click(secondToggle);

      expect(mutateAsync).toHaveBeenNthCalledWith(1, {
        scheduleId: schedule.id,
        enabled: false,
      });
      expect(mutateAsync).toHaveBeenNthCalledWith(2, {
        scheduleId: secondSchedule.id,
        enabled: true,
      });
      expect(firstToggle.disabled).toBe(true);
      expect(secondToggle.disabled).toBe(true);

      const successful = successfulId === schedule.id ? first : second;
      const refused = successfulId === schedule.id ? second : first;
      const successfulToggle = successfulId === schedule.id ? firstToggle : secondToggle;
      const refusedToggle = successfulId === schedule.id ? secondToggle : firstToggle;
      const successfulReceipt = successfulId === schedule.id
        ? receipt
        : {
            ...receipt,
            id: secondSchedule.id,
            name: secondSchedule.name,
            requested_enabled: true,
            observed_enabled: true,
          };

      await act(async () => successful.resolve({ data: successfulReceipt }));
      expect(successfulToggle.disabled).toBe(false);
      expect(refusedToggle.disabled).toBe(true);
      expect(within(container).getByRole("status").textContent).toContain(successfulReceipt.name);

      await act(async () => refused.reject(new Error("SCHEDULE_TOML_MANAGED")));
      expect(refusedToggle.disabled).toBe(false);
      expect(toast.success).toHaveBeenCalledTimes(1);
      expect(toast.error).toHaveBeenCalledWith(
        "Failed to toggle schedule: SCHEDULE_TOML_MANAGED",
      );
    },
  );
});
