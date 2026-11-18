// @vitest-environment jsdom

import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";

import { useConfirmReachProposal, useDismissReachProposal } from "@/hooks/use-reach-clauses";

const api = vi.hoisted(() => ({
  confirmReachProposal: vi.fn(),
  dismissReachProposal: vi.fn(),
  listReachClauses: vi.fn(),
  listReachProposals: vi.fn(),
  revokeReachClause: vi.fn(),
}));
vi.mock("@/api/reach.ts", () => api);

function setup() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { invalidate, wrapper };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("reach proposal mutations", () => {
  it("confirm refetches proposals and clauses on success", async () => {
    api.confirmReachProposal.mockResolvedValue({ data: { id: "c-1" } });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useConfirmReachProposal(), { wrapper });
    await act(async () => result.current.mutate("p-1"));
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2));
    expect(api.confirmReachProposal.mock.calls[0][0]).toBe("p-1");
    const keys = invalidate.mock.calls.map((call) => call[0]?.queryKey);
    expect(keys).toContainEqual(["reach-proposals"]);
    expect(keys).toContainEqual(["reach-clauses"]);
  });

  it("confirm refetches even when the proposal has expired (no optimistic state)", async () => {
    api.confirmReachProposal.mockRejectedValue(new Error("400"));
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useConfirmReachProposal(), { wrapper });
    await act(async () => result.current.mutate("p-1"));
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(2));
  });

  it("dismiss refetches the proposals on settle", async () => {
    api.dismissReachProposal.mockResolvedValue({ data: { id: "p-1" } });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useDismissReachProposal(), { wrapper });
    await act(async () => result.current.mutate("p-1"));
    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(1));
    expect(invalidate.mock.calls[0][0]?.queryKey).toEqual(["reach-proposals"]);
  });
});
