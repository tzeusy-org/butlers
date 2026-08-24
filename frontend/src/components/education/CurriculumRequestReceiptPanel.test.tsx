// @vitest-environment jsdom
/**
 * CurriculumRequestReceiptPanel - RTL + axe tests (bu-6jv4m.10).
 *
 * The gap this panel closes: the dashboard announced "the butler will set it
 * up and message you" the moment the API returned 202, while the API had only
 * written a lock and spawned a detached task whose failure was swallowed. A
 * trigger failure left that promise standing with nothing able to falsify it.
 *
 * These tests pin the honesty contract: acceptance is never rendered as
 * completion, completion is claimed only from a terminal receipt, a failure is
 * named with its reason, and an unreadable receipt store is never rendered as
 * "nothing in flight".
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { axe, toHaveNoViolations } from "jest-axe";

import CurriculumRequestReceiptPanel from "./CurriculumRequestReceiptPanel";
import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import type { CurriculumRequestReceipt, CurriculumRequestStatusResponse } from "@/api/index.ts";

expect.extend(toHaveNoViolations);

vi.mock("@/hooks/use-education", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-education")>();
  return { ...actual, useCurriculumRequestReceipt: vi.fn() };
});

import { useCurriculumRequestReceipt } from "@/hooks/use-education";

const mockUseReceipt = vi.mocked(useCurriculumRequestReceipt);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeReceipt(overrides: Partial<CurriculumRequestReceipt> = {}): CurriculumRequestReceipt {
  return {
    request_id: "11111111-1111-1111-1111-111111111111",
    topic: "Linear Algebra",
    goal: null,
    status: "accepted",
    session_id: null,
    mind_map_id: null,
    calibration_ready_at: null,
    calibration_notice_outcome: null,
    calibration_notice_accepted_at: null,
    failure_reason: null,
    requested_at: "2026-08-22T10:00:00+00:00",
    triggered_at: null,
    settled_at: null,
    updated_at: "2026-08-22T10:00:00+00:00",
    ...overrides,
  };
}

const TRACKED_ID = "11111111-1111-1111-1111-111111111111";

type ReceiptQuery = ReturnType<typeof useCurriculumRequestReceipt>;

function stubQuery(
  data: CurriculumRequestStatusResponse | undefined,
  extra: Partial<{ isError: boolean; isLoading: boolean }> = {},
) {
  mockUseReceipt.mockReturnValue({
    data,
    isError: extra.isError ?? false,
    isLoading: extra.isLoading ?? false,
    refetch: vi.fn(),
  } as unknown as ReceiptQuery);
}

function renderPanel(
  props: Partial<React.ComponentProps<typeof CurriculumRequestReceiptPanel>> = {},
) {
  return render(
    <MemoryRouter>
      <AppTimezoneProvider timezone="UTC">
        <CurriculumRequestReceiptPanel requestId={TRACKED_ID} {...props} />
      </AppTimezoneProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Accepted / running: acceptance is not completion
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: accepted and running", () => {
  it("names the request as accepted without claiming setup or contact", () => {
    stubQuery({ receipts_available: true, receipt: makeReceipt() });
    renderPanel();

    const accepted = screen.getByTestId("curriculum-receipt-accepted");
    expect(accepted.textContent).toMatch(/accepted/i);
    expect(accepted.textContent).toMatch(/Linear Algebra/);
    // The 202 is not evidence of a curriculum or of the owner being messaged.
    expect(screen.queryByText(/message you/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /open curriculum/i })).toBeNull();
  });

  it("shows a running request as still in flight, not done", () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({ status: "running", triggered_at: "2026-08-22T10:00:05+00:00" }),
    });
    renderPanel();

    expect(screen.getByTestId("curriculum-receipt-running")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /open curriculum/i })).toBeNull();
  });

  it("puts the status in a live region so a change is announced", () => {
    stubQuery({ receipts_available: true, receipt: makeReceipt() });
    renderPanel();

    const live = screen.getByRole("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
  });
});

// ---------------------------------------------------------------------------
// Completed: claims come only from terminal evidence, with doors
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: completed", () => {
  const completed = makeReceipt({
    status: "completed",
    session_id: "sess-abc",
    mind_map_id: "map-1",
    calibration_ready_at: "2026-08-22T10:02:00+00:00",
    triggered_at: "2026-08-22T10:00:05+00:00",
    settled_at: "2026-08-22T10:02:00+00:00",
  });

  it("opens the correlated curriculum through the curriculum door", async () => {
    const onOpenCurriculum = vi.fn();
    stubQuery({ receipts_available: true, receipt: completed });
    renderPanel({ onOpenCurriculum });

    await userEvent.click(screen.getByRole("button", { name: /open curriculum/i }));
    expect(onOpenCurriculum).toHaveBeenCalledWith("map-1");
  });

  it("links to the session that produced the curriculum", () => {
    stubQuery({ receipts_available: true, receipt: completed });
    renderPanel();

    const link = screen.getByRole("link", { name: /session/i });
    expect(link.getAttribute("href")).toBe("/sessions/sess-abc");
  });

  it("says calibration has started only when the receipt records it", () => {
    stubQuery({ receipts_available: true, receipt: completed });
    const { unmount } = renderPanel();
    expect(screen.getByText(/calibration started/i)).toBeTruthy();
    unmount();

    stubQuery({
      receipts_available: true,
      receipt: { ...completed, calibration_ready_at: null },
    });
    renderPanel();
    expect(screen.queryByText(/calibration started/i)).toBeNull();
    expect(screen.getByText(/calibration has not started/i)).toBeTruthy();
  });

  it("has no axe violations", async () => {
    stubQuery({ receipts_available: true, receipt: completed });
    const { container } = renderPanel();
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

// ---------------------------------------------------------------------------
// The calibration notice: the panel may claim contact only from evidence
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: calibration notice", () => {
  const calibrating = makeReceipt({
    status: "completed",
    session_id: "sess-abc",
    mind_map_id: "map-1",
    // The flow IS diagnosing throughout this block. Every case below must
    // still refuse to say the owner was messaged.
    calibration_ready_at: "2026-08-22T10:02:00+00:00",
    triggered_at: "2026-08-22T10:00:05+00:00",
    settled_at: "2026-08-22T10:02:00+00:00",
  });

  it("does not claim contact when the notification path recorded a failure", () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: "failed" },
    });
    renderPanel();

    expect(screen.getByText(/could not send you a starting message/i)).toBeTruthy();
    expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
    // Calibration and contact are stated separately, and both stay true.
    expect(screen.getByText(/calibration started/i)).toBeTruthy();
  });

  it("does not claim contact when no notify was recorded for the session", () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: "no_record" },
    });
    renderPanel();

    expect(screen.getByText(/no record that the butler messaged you/i)).toBeTruthy();
    expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
  });

  it("does not claim contact when the notification path could not be read", () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: "unproven" },
    });
    renderPanel();

    expect(screen.getByText(/could not be confirmed/i)).toBeTruthy();
    expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
  });

  it("does not claim contact when the receipt says nothing about a notice", () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: null },
    });
    renderPanel();

    expect(screen.getByText(/could not be confirmed/i)).toBeTruthy();
    expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
  });

  it("degrades an unrecognised outcome to unconfirmed, never to an implied yes", () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: "some_new_outcome" },
    });
    renderPanel();

    expect(screen.getByText(/could not be confirmed/i)).toBeTruthy();
    expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
  });

  it("names a held or queued notice as not yet sent", () => {
    const cases = [
      { outcome: "suppressed", copy: /held back the starting message/i },
      { outcome: "deferred", copy: /queued for a quieter moment/i },
      { outcome: "coalesced", copy: /folded into another notification/i },
    ];
    expect(cases.length).toBeGreaterThan(0);
    for (const { outcome, copy } of cases) {
      stubQuery({
        receipts_available: true,
        receipt: { ...calibrating, calibration_notice_outcome: outcome },
      });
      const { unmount } = renderPanel();
      expect(screen.getByText(copy)).toBeTruthy();
      expect(screen.queryByText(/accepted the butler's starting message/i)).toBeNull();
      unmount();
    }
  });

  it("says the channel accepted the message only when the ledger did", () => {
    stubQuery({
      receipts_available: true,
      receipt: {
        ...calibrating,
        calibration_notice_outcome: "delivered",
        calibration_notice_accepted_at: "2026-08-22T10:01:00+00:00",
      },
    });
    renderPanel();

    // "Your messaging channel accepted it" is the claim the evidence supports.
    // "The butler messaged you and you saw it" is not, and is not made.
    expect(screen.getByText(/accepted the butler's starting message/i)).toBeTruthy();
    expect(screen.queryByText(/could not be confirmed/i)).toBeNull();
  });

  it("has no axe violations on the notice line", async () => {
    stubQuery({
      receipts_available: true,
      receipt: { ...calibrating, calibration_notice_outcome: "failed" },
    });
    const { container } = renderPanel();
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

// ---------------------------------------------------------------------------
// Failed: the terminal state the old UI could not express at all
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: failed", () => {
  it("names the failure reason instead of leaving a stale promise standing", () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "trigger_unreachable",
        settled_at: "2026-08-22T10:00:06+00:00",
      }),
    });
    renderPanel();

    expect(screen.getByTestId("curriculum-receipt-failed")).toBeTruthy();
    expect(screen.getByText(/could not be reached/i)).toBeTruthy();
    // No session ever ran, so there is no session door to offer.
    expect(screen.queryByRole("link", { name: /session/i })).toBeNull();
  });

  it("keeps the session door on the failure path when a session ran", () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "session_error",
        session_id: "sess-bad",
        settled_at: "2026-08-22T10:01:00+00:00",
      }),
    });
    renderPanel();

    expect(screen.getByRole("link", { name: /session/i }).getAttribute("href")).toBe(
      "/sessions/sess-bad",
    );
  });

  it("distinguishes a clean session that produced no curriculum", () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "no_curriculum_created",
        session_id: "sess-empty",
        settled_at: "2026-08-22T10:01:00+00:00",
      }),
    });
    renderPanel();

    expect(screen.getByText(/without creating a curriculum/i)).toBeTruthy();
  });

  it("names an abandoned request as timed out", () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "timed_out",
        settled_at: "2026-08-22T10:31:00+00:00",
      }),
    });
    renderPanel();

    expect(screen.getByText(/timed out/i)).toBeTruthy();
  });

  it("offers a retry that reopens the request", async () => {
    const onRetry = vi.fn();
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "session_error",
        settled_at: "2026-08-22T10:01:00+00:00",
      }),
    });
    renderPanel({ onRetry });

    await userEvent.click(screen.getByRole("button", { name: /request again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("has no axe violations", async () => {
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "session_error",
        session_id: "sess-bad",
        settled_at: "2026-08-22T10:01:00+00:00",
      }),
    });
    const { container } = renderPanel({ onRetry: vi.fn() });
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

// ---------------------------------------------------------------------------
// Unavailable vs absent: never render an unreadable store as calm
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: unavailable and absent", () => {
  it("renders an unreadable receipt store as unavailable, not as nothing in flight", () => {
    stubQuery({ receipts_available: false, receipt: null });
    renderPanel();

    expect(screen.getByTestId("curriculum-receipt-unavailable")).toBeTruthy();
    expect(screen.queryByText(/no curriculum request/i)).toBeNull();
  });

  it("treats a failed read the same way", () => {
    stubQuery(undefined, { isError: true });
    renderPanel();

    expect(screen.getByTestId("curriculum-receipt-unavailable")).toBeTruthy();
  });

  it("renders nothing when the store is readable and holds no request", () => {
    stubQuery({ receipts_available: true, receipt: null });
    const { container } = renderPanel();

    expect(container.textContent).toBe("");
  });

  it("renders nothing while the first read is still in flight", () => {
    stubQuery(undefined, { isLoading: true });
    const { container } = renderPanel();

    expect(container.textContent).toBe("");
  });
});

// ---------------------------------------------------------------------------
// Fallback mode: no tracked request_id, so the panel reads the latest request
// ---------------------------------------------------------------------------

describe("CurriculumRequestReceiptPanel: latest-request fallback", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps showing a request that is still in flight after a reload", () => {
    stubQuery({ receipts_available: true, receipt: makeReceipt({ status: "running" }) });
    renderPanel({ requestId: null });

    expect(screen.getByTestId("curriculum-receipt-running")).toBeTruthy();
  });

  it("keeps a just-settled request visible", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-22T10:10:00Z"));
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "completed",
        mind_map_id: "map-1",
        settled_at: "2026-08-22T10:02:00+00:00",
      }),
    });
    renderPanel({ requestId: null });

    expect(screen.getByTestId("curriculum-receipt-completed")).toBeTruthy();
  });

  it("drops a long-settled request rather than parking it on the page forever", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-25T10:00:00Z"));
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "completed",
        mind_map_id: "map-1",
        settled_at: "2026-08-22T10:02:00+00:00",
      }),
    });
    const { container } = renderPanel({ requestId: null });

    expect(container.textContent).toBe("");
  });

  it("still shows a long-settled request that this session submitted", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-25T10:00:00Z"));
    stubQuery({
      receipts_available: true,
      receipt: makeReceipt({
        status: "failed",
        failure_reason: "session_error",
        settled_at: "2026-08-22T10:02:00+00:00",
      }),
    });
    renderPanel();

    expect(screen.getByTestId("curriculum-receipt-failed")).toBeTruthy();
  });
});
