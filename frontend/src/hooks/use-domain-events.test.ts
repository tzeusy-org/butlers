import { beforeEach, describe, expect, it, vi } from "vitest";

const mockInvalidateQueries = vi.fn();

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((options: unknown) => options),
    useQueryClient: () => ({ invalidateQueries: mockInvalidateQueries }),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";

import { useReplayDomainEventDelivery } from "@/hooks/use-domain-events";

const mockUseMutation = vi.mocked(useMutation);

function capturedReplayOptions(): {
  onError: (error: Error) => void;
  onSuccess: () => void;
} {
  const options = mockUseMutation.mock.calls.at(-1)?.[0];
  expect(options).toBeDefined();
  return options as ReturnType<typeof capturedReplayOptions>;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useReplayDomainEventDelivery", () => {
  it("reports a failed replay without invalidating away the terminal row", () => {
    useReplayDomainEventDelivery();
    const options = capturedReplayOptions();

    options.onError(new Error("Replay transport unavailable"));

    expect(toast.error).toHaveBeenCalledWith("Replay transport unavailable");
    expect(mockInvalidateQueries).not.toHaveBeenCalled();
  });

  it("refreshes delivery state only after a successful replay", () => {
    useReplayDomainEventDelivery();
    const options = capturedReplayOptions();

    options.onSuccess();

    expect(mockInvalidateQueries).toHaveBeenCalledWith({
      queryKey: ["domain-event-deliveries"],
    });
    expect(toast.success).toHaveBeenCalledWith("Delivery queued for replay");
  });
});
