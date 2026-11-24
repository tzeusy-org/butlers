// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ownerFetch } from "@/api/owner-session";
import { useOwnerDeliveryFocus } from "./use-owner-delivery-focus";

vi.mock("@/api/owner-session", () => ({ ownerFetch: vi.fn() }));
afterEach(() => { vi.restoreAllMocks(); vi.clearAllMocks(); vi.useRealTimers(); });

it("requires actual focus and stops its heartbeat when the dark endpoint answers 404", async () => {
  vi.useFakeTimers();
  const focus = vi.spyOn(document, "hasFocus").mockReturnValue(false);
  vi.mocked(ownerFetch).mockResolvedValue(new Response(null, { status: 404 }));
  const { unmount } = renderHook(() => useOwnerDeliveryFocus());
  expect(ownerFetch).not.toHaveBeenCalled();
  focus.mockReturnValue(true);
  await vi.advanceTimersByTimeAsync(30_000);
  expect(ownerFetch).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(120_000);
  expect(ownerFetch).toHaveBeenCalledTimes(1);
  unmount();
});
