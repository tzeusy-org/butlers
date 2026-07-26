/**
 * Regression coverage for rule-promotion cache reconciliation.
 *
 * The promotion trigger runs outside the dashboard request path and does not
 * publish a fleet event for this query family. These hooks therefore need a
 * primary polling cadence so an already-open approvals page removes a
 * superseded card and refreshes its lifecycle stats without requiring focus
 * or a manual mutation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockInvalidateQueries = vi.fn();
const mockQueryClient = { invalidateQueries: mockInvalidateQueries };

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((options: unknown) => options),
    useQuery: vi.fn((options: unknown) => options),
    useQueryClient: () => mockQueryClient,
  };
});

import { useMutation, useQuery } from "@tanstack/react-query";
import {
  rulePromotionKeys,
  useConfirmRulePromotion,
  useDismissRulePromotion,
  useRulePromotionStats,
  useRulePromotions,
} from "./use-rule-promotions";

const mockUseMutation = vi.mocked(useMutation);

function capturedMutationOptions(): {
  onSuccess?: (...args: unknown[]) => void;
  onSettled?: (...args: unknown[]) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

function settleWithStaleOwnerConflict(
  options: ReturnType<typeof capturedMutationOptions>,
  variables: unknown,
) {
  expect(options.onSettled).toBeTypeOf("function");
  options.onSettled?.(
    undefined,
    new Error("409 Conflict: the promotion suggestion is no longer pending"),
    variables,
    undefined,
  );
}

beforeEach(() => {
  vi.mocked(useQuery).mockClear();
  mockUseMutation.mockClear();
  mockInvalidateQueries.mockClear();
});

describe("rule-promotion polling", () => {
  it("polls the pending surface every 30 seconds for scheduler transitions", () => {
    useRulePromotions();

    // Hardcoded 30_000 (not RULE_PROMOTION_POLL_MS) is intentional: this test
    // verifies the ACTUAL configured cadence, not that the hook echoes back
    // whatever its own constant happens to hold -- importing the constant
    // here would make the assertion tautological (bu-ep4ks.15).
    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      // eslint-disable-next-line no-restricted-syntax -- see reason above
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });

  it("polls lifecycle stats every 30 seconds for scheduler transitions", () => {
    useRulePromotionStats();

    expect(vi.mocked(useQuery)).toHaveBeenCalledWith(
      // eslint-disable-next-line no-restricted-syntax -- see reason above
      expect.objectContaining({ refetchInterval: 30_000 }),
    );
  });
});

describe("rule-promotion mutation cache reconciliation", () => {
  it("refreshes the promotion surface and stats after a stale-owner confirm conflict", () => {
    useConfirmRulePromotion();
    const options = capturedMutationOptions();

    settleWithStaleOwnerConflict(options, "suggestion-1");

    // The all-key prefix covers both the pending card surface and the stats tile.
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: rulePromotionKeys.all });
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({ queryKey: ["ingestion-rules"] });
  });

  it("refreshes the promotion surface and stats after a stale-owner dismiss conflict", () => {
    useDismissRulePromotion();
    const options = capturedMutationOptions();

    settleWithStaleOwnerConflict(options, {
      suggestionId: "suggestion-1",
      request: { reason: "not now" },
    });

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: rulePromotionKeys.all });
    expect(mockInvalidateQueries).not.toHaveBeenCalledWith({ queryKey: ["ingestion-rules"] });
  });

  it("refreshes promotion data and ingestion rules after a successful confirmation", () => {
    useConfirmRulePromotion();
    const options = capturedMutationOptions();

    expect(options.onSuccess).toBeTypeOf("function");
    options.onSuccess?.(undefined, "suggestion-1", undefined);
    expect(options.onSettled).toBeTypeOf("function");
    options.onSettled?.(undefined, null, "suggestion-1", undefined);

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: rulePromotionKeys.all });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["ingestion-rules"] });
  });
});
