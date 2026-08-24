// @vitest-environment jsdom
/**
 * Retry/escalate outcome feedback on the Notifications page (bu-6t8ix.1).
 *
 * The inline Retry/Escalate verbs (bu-ep4ks.4) call a real backend re-send,
 * but the page previously discarded both of the outcomes that are not a
 * plain success: the 409 the endpoint returns for a row that is no longer
 * `failed` (a stale list still offering "Retry" after another tab actioned
 * it), and a 200 whose new attempt itself came back `failed`. In both cases
 * the operator saw the spinner clear and nothing else, so a verb that had
 * not delivered anything read as one that had.
 *
 * jsdom + createRoot because these assertions need a real click on the row's
 * verb button, not static markup.
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { fireEvent } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  useAcknowledgeAllFailed,
  useEscalateNotification,
  useMarkNotificationRead,
  useNotifications,
  useNotificationStats,
  useRetryNotification,
} from "@/hooks/use-notifications";
import NotificationsPage from "@/pages/NotificationsPage";

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications: vi.fn(),
  useNotificationStats: vi.fn(),
  useMarkNotificationRead: vi.fn(),
  useAcknowledgeAllFailed: vi.fn(),
  useRetryNotification: vi.fn(),
  useEscalateNotification: vi.fn(),
}));

// sonner's real export is a callable function that also carries .success/.error;
// mirror that shape so an unrelated callable use in the tree cannot throw.
vi.mock("sonner", () => {
  const toastFn = Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  });
  return { toast: toastFn, Toaster: () => null };
});

type UseNotificationsResult = ReturnType<typeof useNotifications>;
type UseNotificationStatsResult = ReturnType<typeof useNotificationStats>;

/** The subset of react-query's per-call mutate options this page passes. */
interface CapturedMutateOptions {
  onSuccess?: (data: unknown) => void;
  onError?: (error: unknown) => void;
  onSettled?: () => void;
}

const FAILED_NOTIFICATION = {
  id: "notif-failed-1",
  source_butler: "general",
  channel: "email",
  recipient: "owner@example.invalid",
  message: "Weekly summary report",
  metadata: null,
  status: "failed",
  effective_status: "failed",
  error: "SMTP connection refused",
  session_id: null,
  trace_id: null,
  created_at: "2026-02-19T08:00:00Z",
};

describe("NotificationsPage — retry/escalate outcome feedback (bu-6t8ix.1)", () => {
  let container: HTMLDivElement | undefined;
  let root: Root | undefined;
  let retryOptions: CapturedMutateOptions | undefined;
  let escalateOptions: CapturedMutateOptions | undefined;

  beforeEach(() => {
    vi.resetAllMocks();
    retryOptions = undefined;
    escalateOptions = undefined;

    vi.mocked(useMarkNotificationRead).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useMarkNotificationRead>);
    vi.mocked(useAcknowledgeAllFailed).mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAcknowledgeAllFailed>);
    vi.mocked(useRetryNotification).mockReturnValue({
      mutate: vi.fn((_id: string, options?: CapturedMutateOptions) => {
        retryOptions = options;
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryNotification>);
    vi.mocked(useEscalateNotification).mockReturnValue({
      mutate: vi.fn((_id: string, options?: CapturedMutateOptions) => {
        escalateOptions = options;
      }),
      isPending: false,
    } as unknown as ReturnType<typeof useEscalateNotification>);

    vi.mocked(useNotificationStats).mockReturnValue({
      data: {
        data: { total: 1, sent: 0, failed: 1, by_channel: {}, by_butler: {} },
        meta: {},
      },
      isLoading: false,
      isError: false,
      error: null,
    } as UseNotificationStatsResult);
    vi.mocked(useNotifications).mockReturnValue({
      data: {
        data: [FAILED_NOTIFICATION],
        meta: { total: 1, offset: 0, limit: 20, has_more: false },
      },
      isLoading: false,
      isError: false,
      error: null,
    } as UseNotificationsResult);
  });

  afterEach(() => {
    if (root) {
      act(() => root!.unmount());
    }
    container?.remove();
    container = undefined;
    root = undefined;
  });

  function renderLive() {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    const renderedRoot = root;
    act(() => {
      renderedRoot.render(
        <MemoryRouter initialEntries={["/notifications"]}>
          <NotificationsPage />
        </MemoryRouter>,
      );
    });
  }

  function clickVerb(label: string) {
    const button = Array.from(container!.querySelectorAll("button")).find(
      (candidate) => candidate.textContent?.trim() === label,
    );
    if (!button) throw new Error(`No "${label}" button rendered on the failed row`);
    // fireEvent already wraps the dispatch in act(), so the pending-state
    // re-render has flushed by the time this returns.
    fireEvent.click(button);
  }

  it("reports a delivered retry with the channel it landed on", () => {
    renderLive();
    clickVerb("Retry");

    act(() => {
      retryOptions!.onSuccess!({
        original_notification_id: FAILED_NOTIFICATION.id,
        new_notification_id: "notif-retry-1",
        channel: "email",
        status: "sent",
        error: null,
      });
    });

    expect(toast.success).toHaveBeenCalledWith("Notification re-sent on email");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("reports a retry whose new attempt failed again, carrying the delivery error", () => {
    renderLive();
    clickVerb("Retry");

    act(() => {
      retryOptions!.onSuccess!({
        original_notification_id: FAILED_NOTIFICATION.id,
        new_notification_id: "notif-retry-2",
        channel: "email",
        status: "failed",
        error: "SMTP connection refused",
      });
    });

    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Retry failed again", {
      description: "SMTP connection refused",
    });
  });

  it("surfaces the backend rejection when the row was already actioned", () => {
    renderLive();
    clickVerb("Retry");

    act(() => {
      retryOptions!.onError!(
        new Error(
          "Only failed notifications can be retried; this notification was already actioned.",
        ),
      );
    });

    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Could not retry notification", {
      description:
        "Only failed notifications can be retried; this notification was already actioned.",
    });
  });

  it("clears the row's pending state once the retry settles", () => {
    renderLive();
    clickVerb("Retry");
    expect(container!.textContent).toContain("Retrying…");

    act(() => {
      retryOptions!.onSettled!();
    });

    expect(container!.textContent).not.toContain("Retrying…");
    expect(container!.textContent).toContain("Retry");
  });

  it("reports a delivered escalation with the alternate channel", () => {
    renderLive();
    clickVerb("Escalate");

    act(() => {
      escalateOptions!.onSuccess!({
        original_notification_id: FAILED_NOTIFICATION.id,
        new_notification_id: "notif-escalate-1",
        channel: "telegram",
        status: "sent",
        error: null,
      });
    });

    expect(toast.success).toHaveBeenCalledWith("Notification escalated to telegram");
  });

  it("surfaces a rejected escalation instead of clearing silently", () => {
    renderLive();
    clickVerb("Escalate");

    act(() => {
      escalateOptions!.onError!(
        new Error("No owner telegram contact is configured; cannot escalate."),
      );
    });

    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith("Could not escalate notification", {
      description: "No owner telegram contact is configured; cannot escalate.",
    });
  });
});
