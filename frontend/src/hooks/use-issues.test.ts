/**
 * Unit tests for the issues hooks — focused on the undismiss (restore) flow.
 *
 * Strategy mirrors use-secrets-mutations.test.ts: mock @tanstack/react-query's
 * useMutation + useQuery + useQueryClient, capture the options object passed by
 * each hook via the mock call list, then drive mutationFn / onMutate /
 * onSettled directly to verify the API call and cache behaviour.
 *
 * Covers:
 *   - useIssues passes include_dismissed through to getIssues, distinct keys
 *   - useUndismissIssue calls undismissIssue with the key
 *   - useUndismissIssue optimistically drops the issue from the dismissed view
 *   - useUndismissIssue invalidates the shared ["issues"] prefix on settle
 */

import { useMutation, useQuery } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockCancelQueries = vi.fn(() => Promise.resolve());
const mockInvalidateQueries = vi.fn();
const mockGetQueryData = vi.fn();
const mockSetQueryData = vi.fn();
const mockQueryClient = {
  cancelQueries: mockCancelQueries,
  invalidateQueries: mockInvalidateQueries,
  getQueryData: mockGetQueryData,
  setQueryData: mockSetQueryData,
};

vi.mock("@tanstack/react-query", async (importOriginal) => {
  const original = await importOriginal<typeof import("@tanstack/react-query")>();
  return {
    ...original,
    useMutation: vi.fn((opts: unknown) => opts),
    useQuery: vi.fn((opts: unknown) => opts),
    useQueryClient: () => mockQueryClient,
  };
});

vi.mock("@/api/index.ts", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/api/index.ts")>();
  return {
    ...original,
    getIssues: vi.fn(() => Promise.resolve({ data: [] })),
    dismissIssue: vi.fn(() => Promise.resolve({ data: {} })),
    undismissIssue: vi.fn(() => Promise.resolve({ data: {} })),
  };
});

import { getIssues, undismissIssue } from "@/api/index.ts";
import type { ApiResponse, Issue } from "@/api/types";
import { useIssues, useUndismissIssue } from "./use-issues";

const mockUseMutation = vi.mocked(useMutation);
const mockUseQuery = vi.mocked(useQuery);

const ACTIVE_KEY = ["issues", { dismissed: false }];
const DISMISSED_KEY = ["issues", { dismissed: true }];

interface CapturedQueryOptions {
  queryKey: unknown;
  queryFn: () => unknown;
}

interface CapturedMutationOptions {
  mutationFn: (key: string) => unknown;
  onMutate: (key: string) => Promise<{ previous: ApiResponse<Issue[]> | undefined }>;
  onError: (
    err: unknown,
    key: string,
    context: { previous: ApiResponse<Issue[]> | undefined },
  ) => void;
  onSettled: () => void;
}

function lastQueryOptions(): CapturedQueryOptions {
  const calls = mockUseQuery.mock.calls;
  return calls[calls.length - 1][0] as unknown as CapturedQueryOptions;
}

function lastMutationOptions(): CapturedMutationOptions {
  const calls = mockUseMutation.mock.calls;
  return calls[calls.length - 1][0] as unknown as CapturedMutationOptions;
}

function makeIssue(overrides: Partial<Issue> = {}): Issue {
  return {
    severity: "warning",
    type: "audit_error_group:boom",
    butler: "general",
    description: "boom (general)",
    link: null,
    issue_key: "audit_error_group:boom::general",
    dismissed: true,
    ...overrides,
  };
}

describe("useIssues", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses distinct query keys for the active vs dismissed views", () => {
    useIssues(false);
    expect(lastQueryOptions().queryKey).toEqual(ACTIVE_KEY);
    useIssues(true);
    expect(lastQueryOptions().queryKey).toEqual(DISMISSED_KEY);
  });

  it("forwards include_dismissed to getIssues via queryFn", () => {
    useIssues(true);
    lastQueryOptions().queryFn();
    expect(getIssues).toHaveBeenCalledWith(true);

    vi.mocked(getIssues).mockClear();
    useIssues(false);
    lastQueryOptions().queryFn();
    expect(getIssues).toHaveBeenCalledWith(false);
  });
});

describe("useUndismissIssue", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls undismissIssue with the issue key", () => {
    useUndismissIssue();
    lastMutationOptions().mutationFn("audit_error_group:boom::general");
    expect(undismissIssue).toHaveBeenCalledWith("audit_error_group:boom::general");
  });

  it("optimistically removes the issue from the dismissed view cache", async () => {
    const issue = makeIssue();
    const other = makeIssue({ issue_key: "other::general" });
    const previous = { data: [issue, other] } as ApiResponse<Issue[]>;
    mockGetQueryData.mockReturnValue(previous);

    useUndismissIssue();
    const context = await lastMutationOptions().onMutate(issue.issue_key);

    expect(mockCancelQueries).toHaveBeenCalledWith({ queryKey: DISMISSED_KEY });
    expect(mockSetQueryData).toHaveBeenCalledWith(DISMISSED_KEY, {
      ...previous,
      data: [other],
    });
    expect(context).toEqual({ previous });
  });

  it("rolls back the dismissed cache on error", () => {
    const previous = { data: [] } as unknown as ApiResponse<Issue[]>;
    useUndismissIssue();
    lastMutationOptions().onError(new Error("nope"), "k", { previous });
    expect(mockSetQueryData).toHaveBeenCalledWith(DISMISSED_KEY, previous);
  });

  it("invalidates the shared issues prefix on settle (both views refresh)", () => {
    useUndismissIssue();
    lastMutationOptions().onSettled();
    expect(mockInvalidateQueries).toHaveBeenCalledWith({ queryKey: ["issues"] });
  });
});
