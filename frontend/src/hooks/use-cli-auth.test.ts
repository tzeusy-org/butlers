/**
 * Unit tests for CLI auth mutation cache refreshes.
 *
 * A CLI Test records a new result in persisted credential state. The passport
 * inventory and provider-status query must both refetch after an HTTP success,
 * including a failed provider test result.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const mockInvalidateQueries = vi.fn();
const mockQueryClient = { invalidateQueries: mockInvalidateQueries };

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((options: unknown) => options),
    useQueryClient: () => mockQueryClient,
  };
});

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    testCLIAuthApiKey: vi.fn(),
  };
});

import { useMutation } from "@tanstack/react-query";
import { cliAuthKeys, useTestCLIAuthApiKey } from "@/hooks/use-cli-auth.ts";
import { secretsInventoryKeys } from "@/hooks/use-secrets-inventory.ts";

const mockUseMutation = vi.mocked(useMutation);

function capturedMutationOptions(): {
  mutationFn: (provider: string) => unknown;
  onSuccess: (data: { success: boolean }) => void;
} {
  const calls = mockUseMutation.mock.calls;
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1][0] as ReturnType<typeof capturedMutationOptions>;
}

describe("useTestCLIAuthApiKey", () => {
  beforeEach(() => {
    mockUseMutation.mockClear();
    mockInvalidateQueries.mockClear();
  });

  it("invalidates persisted CLI state after a completed failed test", () => {
    useTestCLIAuthApiKey();
    const { onSuccess } = capturedMutationOptions();

    onSuccess({ success: false });

    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: secretsInventoryKeys.all });
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: cliAuthKeys.providers() });
  });
});
