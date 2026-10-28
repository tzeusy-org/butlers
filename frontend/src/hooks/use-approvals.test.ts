import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...actual,
    useQuery: vi.fn(() => ({})),
  };
});

vi.mock("@/api/client.ts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client.ts")>();
  return {
    ...actual,
    getApprovalMetrics: vi.fn(),
  };
});

vi.mock("@/lib/event-bus", () => ({
  useEventBus: () => ({
    status: "open",
    health: "healthy",
    lastEventAt: null,
    subscribe: vi.fn(),
  }),
}));

import { useQuery } from "@tanstack/react-query";
import { getApprovalMetrics } from "@/api/client.ts";
import {
  isCompleteApprovalMetricsResponse,
  useApprovalMetrics,
} from "@/hooks/use-approvals";

const completeMetricsResponse = {
  data: {
total_approved_today: 0,
total_rejected_today: 0,
total_auto_approved_today: 0,
total_expired_today: 0,
avg_decision_latency_seconds: null,
auto_approval_rate: 0,
rejection_rate: 0,
failure_count_today: 0,
active_rules_count: 0,
callback_secret_configured: null,
delivery_due_count: 0,
delivery_retry_wait_count: 0,
delivery_expired_lease_count: 0,
delivery_ambiguous_count: 0,
delivery_stuck_count: 0,
delivery_oldest_due_age_seconds: null,
delivery_by_state: {},
delivery_by_reason: {},
delivery_sources_complete: false, total_pending: 0 },
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
