// @vitest-environment jsdom
/**
 * Approve and Deny redeem the dossier's single-use owner-delivery token when it
 * has one; with the rollout off there is none and the legacy routes decide.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { toast } from "sonner";

import {
  ApiError,
  approveApproval,
  denyApproval,
  getApprovalDetail,
  redeemOwnerDeliveryAction,
} from "@/api/index.ts";

vi.mock("@/api/index.ts", () => {
  class MockApiError extends Error {
    readonly detail?: unknown;
    constructor(_code: string, message: string, _status: number, detail?: unknown) {
      super(message);
      this.detail = detail;
    }
  }
  const done = () => Promise.resolve({ data: { id: "a1", status: "rejected" } });
  return {
    ApiError: MockApiError,
    approveApproval: vi.fn(done),
    denyApproval: vi.fn(done),
    deferApproval: vi.fn(done),
    getApprovalDetail: vi.fn(),
    redeemOwnerDeliveryAction: vi.fn(done),
  };
});

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}));

import { useApprovalDecisionMutations } from "./use-approval-decisions";

const SHOWN = { tool_name: "send_email", args_digest: "a".repeat(64) };
const TOKENS = {
  subject_id: "s1",
  generation: 1,
  approve_token: "od1:approve",
  reject_token: "od1:reject",
};

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => useApprovalDecisionMutations(), { wrapper }), invalidate };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("owner-delivery decisions", () => {
  it("approves through the token, echoing the call the owner was shown", async () => {
    const { result } = setup();

    await act(async () => {
      await result.current.approveMut.mutateAsync({
        id: "a1",
        displayed_call: SHOWN,
        owner_delivery: TOKENS,
      });
    });

    expect(redeemOwnerDeliveryAction).toHaveBeenCalledWith({
      token: "od1:approve",
      displayed_call: SHOWN,
    });
    expect(approveApproval).not.toHaveBeenCalled();
  });

  it("denies through the token and keeps the owner's reason", async () => {
    const { result } = setup();

    await act(async () => {
      await result.current.denyMut.mutateAsync({
        id: "a1",
        reason: "Not this one",
        owner_delivery: TOKENS,
      });
    });

    expect(redeemOwnerDeliveryAction).toHaveBeenCalledWith({
      token: "od1:reject",
      reason: "Not this one",
    });
    expect(denyApproval).not.toHaveBeenCalled();
  });

  it("uses the legacy routes when the dossier carries no token (rollout off)", async () => {
    const { result } = setup();

    await act(async () => {
      await result.current.approveMut.mutateAsync({ id: "a1", displayed_call: SHOWN });
      await result.current.denyMut.mutateAsync({ id: "a1" });
    });

    expect(approveApproval).toHaveBeenCalledWith("a1", { displayed_call: SHOWN });
    expect(denyApproval).toHaveBeenCalledWith("a1", undefined);
    expect(redeemOwnerDeliveryAction).not.toHaveBeenCalled();
  });

  it("never falls back to the legacy route when a token is refused, and re-reads the dossier", async () => {
    vi.mocked(redeemOwnerDeliveryAction).mockRejectedValueOnce(
      new ApiError("owner_delivery_refused", "owner_delivery_refused", 409, {
        error: "owner_delivery_refused",
        reason: "subject_resolved",
      }),
    );
    const { result, invalidate } = setup();

    await act(async () => {
      await result.current.approveMut
        .mutateAsync({ id: "a1", displayed_call: SHOWN, owner_delivery: TOKENS })
        .catch(() => undefined);
    });

    expect(approveApproval).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      "This approval changed or was already decided. Nothing ran from this tap.",
    );
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["approvals", "detail", "a1"] }),
    );
  });

  it("carries the token of the reviewed detail from a summary surface", async () => {
    vi.mocked(getApprovalDetail).mockResolvedValueOnce({
      data: {
        id: "a1",
        status: "pending",
        call_preview: {
          title: "Send email",
          facts: [{ label: "Body", value: "Reviewed" }],
          displayed_call: SHOWN,
        },
        owner_delivery: TOKENS,
      },
    } as never);
    const { result } = setup();

    await act(async () => {
      await result.current.requestReview("a1");
    });

    expect(result.current.review?.owner_delivery).toEqual(TOKENS);
  });
});
