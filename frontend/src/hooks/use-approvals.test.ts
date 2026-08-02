import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: vi.fn(() => ({})),
  };
});

vi.mock("@/api/index.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...actual,
    getApprovalMetrics: vi.fn(),
  };
});

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({ status: "open", lastEventAt: null, subscribe: vi.fn() }),
}));

import { useQuery } from "@tanstack/react-query";
import { getApprovalMetrics } from "@/api/index.ts";
import {
  isCompleteApprovalMetricsResponse,
  useApprovalMetrics,
} from "@/hooks/use-approvals";

const completeMetricsResponse = {
  data: { total_pending: 0 },
  meta: {},
};

const malformedResponses: Array<[string, unknown]> = [
  ["missing meta", { data: { total_pending: 0 } }],
  ["missing data", { meta: {} }],
  ["missing total_pending", { data: {}, meta: {} }],
  ["a non-numeric total_pending", { data: { total_pending: "0" }, meta: {} }],
  ["a negative total_pending", { data: { total_pending: -1 }, meta: {} }],
];

function capturedApprovalMetricsQueryFn(): () => Promise<unknown> {
  const options = vi.mocked(useQuery).mock.calls.at(-1)?.[0] as
    | { queryFn?: () => Promise<unknown> }
    | undefined;
  if (!options?.queryFn) throw new Error("useApprovalMetrics did not register a query function");
  return options.queryFn;
}

describe("approval metrics response validity", () => {
  it("accepts a complete zero response", () => {
    expect(isCompleteApprovalMetricsResponse(completeMetricsResponse)).toBe(true);
  });

  it.each(malformedResponses)("rejects %s", (_caseName, response) => {
    expect(isCompleteApprovalMetricsResponse(response)).toBe(false);
  });
});

describe("useApprovalMetrics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns a complete metrics response", async () => {
    vi.mocked(getApprovalMetrics).mockResolvedValueOnce(completeMetricsResponse as never);

    useApprovalMetrics();

    await expect(capturedApprovalMetricsQueryFn()()).resolves.toBe(completeMetricsResponse);
  });

  it.each(malformedResponses)(
    "turns %s HTTP-200 metrics into a query error",
    async (_caseName, response) => {
      vi.mocked(getApprovalMetrics).mockResolvedValueOnce(response as never);

      useApprovalMetrics();

      await expect(capturedApprovalMetricsQueryFn()()).rejects.toThrow(
        "Approval metrics response is incomplete",
      );
    },
  );
});
