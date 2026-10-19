// @vitest-environment jsdom
/**
 * IssuesPage: a forced tick the API answers 202 `running` (it outlasted the accept window)
 * reads as running, not as triggered or failed (bu-o5jqmh). Real failures stay errors.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/use-issues", () => ({
  useIssues: vi.fn(),
  useIssueOccurrences: vi.fn(),
  useDismissIssue: vi.fn(),
  useUndismissIssue: vi.fn(),
}));
vi.mock("@/hooks/use-butlers", () => ({
  usePingButler: vi.fn(),
  useForceButlerTick: vi.fn(),
}));
vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() });
  return { toast: toastFn };
});

import IssuesPage from "@/pages/IssuesPage";
import type { Issue } from "@/api/types";
import { useDismissIssue, useIssueOccurrences, useIssues, useUndismissIssue } from "@/hooks/use-issues";
import { useForceButlerTick, usePingButler } from "@/hooks/use-butlers";
import { toast } from "sonner";

const mockRunNow = vi.fn();

function makeIssue(): Issue {
  return {
    severity: "warning",
    type: "audit_error_group:oauth-token-expired",
    butler: "calendar",
    description: "OAuth token expired (calendar)",
    link: "/audit-log?result=error&actor=calendar",
    error_message: "OAuth token expired",
    occurrences: 3,
    first_seen_at: "2026-06-14T10:00:00.000Z",
    last_seen_at: "2026-06-14T11:00:00.000Z",
    butlers: ["calendar"],
    issue_key: "audit_error_group:oauth-token-expired::calendar",
    dismissed: false,
  };
}

beforeEach(() => {
  mockRunNow.mockClear();
  vi.mocked(useIssues).mockReturnValue({
    data: { data: [makeIssue()], meta: {} },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIssues>);
  vi.mocked(useIssueOccurrences).mockReturnValue({
    data: { data: [], meta: { total: 0, offset: 0, limit: 50, has_more: false } },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useIssueOccurrences>);
  vi.mocked(useDismissIssue).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useDismissIssue>);
  vi.mocked(useUndismissIssue).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUndismissIssue>);
  vi.mocked(usePingButler).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof usePingButler>);
  vi.mocked(useForceButlerTick).mockReturnValue({
    mutate: mockRunNow,
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useForceButlerTick>);
  vi.mocked(toast.success).mockClear();
  vi.mocked(toast.error).mockClear();
  vi.mocked(toast.warning).mockClear();
  vi.mocked(toast.info).mockClear();
});

afterEach(() => {
  cleanup();
});

function clickRunNowAndGetOnSuccess() {
  render(
    <MemoryRouter initialEntries={["/issues"]}>
      <IssuesPage />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Run schedule now" }));
  expect(mockRunNow).toHaveBeenCalledTimes(1);
  return mockRunNow.mock.calls[0][1].onSuccess;
}

describe("IssuesPage running forced tick", () => {
  it("shows an info toast, not success, when the tick is still running", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({ data: { success: true, running: true, message: null } });

    expect(toast.info).toHaveBeenCalledWith("calendar: tick is running; it may take a few minutes");
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("still shows success when the tick ran", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({ data: { success: true, running: false, message: "ran 2 due schedules" } });

    expect(toast.success).toHaveBeenCalledWith("calendar: ran 2 due schedules");
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("still shows a red error for a failed tick", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({ data: { success: false, message: "boom" } });

    expect(toast.error).toHaveBeenCalledWith("calendar: tick did not complete successfully");
    expect(toast.info).not.toHaveBeenCalled();
  });
});
