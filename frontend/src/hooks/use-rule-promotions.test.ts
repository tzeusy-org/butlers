/**
 * Regression coverage for scheduler-originated rule-promotion transitions.
 *
 * The promotion trigger runs outside the dashboard request path and does not
 * publish a fleet event for this query family. These hooks therefore need a
 * primary polling cadence so an already-open approvals page removes a
 * superseded card and refreshes its lifecycle stats without requiring focus
 * or a manual mutation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useQuery: vi.fn((options: unknown) => options),
  };
});

import { useQuery } from "@tanstack/react-query";
import { useRulePromotionStats, useRulePromotions } from "./use-rule-promotions";

describe("rule-promotion polling", () => {
  beforeEach(() => {
    vi.mocked(useQuery).mockClear();
  });

  it("polls the pending surface every 30 seconds for scheduler transitions", () => {
    useRulePromotions();

    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });

  it("polls lifecycle stats every 30 seconds for scheduler transitions", () => {
    useRulePromotionStats();

    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });
});
