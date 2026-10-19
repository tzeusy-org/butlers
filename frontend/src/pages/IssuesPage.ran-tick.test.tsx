// @vitest-environment jsdom
/**
 * IssuesPage: a ran forced tick reads its typed `dispatched` count in plain language
 * (bu-5s42ls), not the tool's raw JSON. Busy and failure copy are unchanged.
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
  const toastFn = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  });
  return { toast: toastFn };
});

import IssuesPage from "@/pages/IssuesPage";
import type { Issue } from "@/api/types";
import {
  useDismissIssue,
  useIssueOccurrences,
  useIssues,
  useUndismissIssue,
} from "@/hooks/use-issues";
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
    data: {
      data: [],
      meta: { total: 0, offset: 0, limit: 50, has_more: false },
    },
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

describe("IssuesPage ran forced tick", () => {
  it("shows the dispatched count in plain language", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({
      data: {
        success: true,
        busy: false,
        dispatched: 2,
        message: "tick ran, 2 jobs dispatched",
      },
    });

    expect(toast.success).toHaveBeenCalledWith(
      "calendar: tick ran, 2 jobs dispatched",
    );
  });

  it("uses the singular for one job and still reports zero", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({
      data: { success: true, busy: false, dispatched: 1, message: null },
    });
    onSuccess({
      data: { success: true, busy: false, dispatched: 0, message: null },
    });

    expect(toast.success).toHaveBeenNthCalledWith(
      1,
      "calendar: tick ran, 1 job dispatched",
    );
    expect(toast.success).toHaveBeenNthCalledWith(
      2,
      "calendar: tick ran, 0 jobs dispatched",
    );
  });

  it("never shows raw tool JSON when a count is present", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({
      data: {
        success: true,
        busy: false,
        dispatched: 2,
        message: '{"dispatched":2}',
      },
    });

    expect(toast.success).toHaveBeenCalledWith(
      "calendar: tick ran, 2 jobs dispatched",
    );
  });

  it("falls back to the message, then the surface copy, without a count", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({
      data: {
        success: true,
        busy: false,
        dispatched: null,
        message: "ran 2 due schedules",
      },
    });
    onSuccess({ data: { success: true, busy: false, message: null } });

    expect(toast.success).toHaveBeenNthCalledWith(
      1,
      "calendar: ran 2 due schedules",
    );
    expect(toast.success).toHaveBeenNthCalledWith(
      2,
      "calendar: schedule run now",
    );
  });

  it("leaves busy and failure copy unchanged", () => {
    const onSuccess = clickRunNowAndGetOnSuccess();

    onSuccess({
      data: { success: false, busy: true, dispatched: null, message: null },
    });
    onSuccess({ data: { success: false, dispatched: 3, message: "boom" } });

    expect(toast.warning).toHaveBeenCalledWith(
      "calendar: scheduled work is already running",
    );
    expect(toast.error).toHaveBeenCalledWith(
      "calendar: tick did not complete successfully",
    );
    expect(toast.success).not.toHaveBeenCalled();
  });
});
