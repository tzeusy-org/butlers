// @vitest-environment jsdom
/**
 * CalendarProposalsPanel — RTL tests (bu-0l32t).
 *
 * Covers:
 *  - Renders pending proposals with confidence chip + provenance.
 *  - Accept calls the accept mutation and optimistically removes the row.
 *  - Dismiss calls the dismiss mutation and optimistically removes the row.
 *  - A non-terminal error reverts the optimistic removal (row reappears).
 *  - A 409 (lost race) keeps the optimistic removal (resolved server-side).
 *  - Edit-before-accept forwards inline overrides to the accept mutation.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { ApiError } from "@/api/client.ts";
import type { UnifiedCalendarEntry } from "@/api/types.ts";

import { CalendarProposalsPanel } from "./CalendarProposalsPanel.tsx";

const toastMocks = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}));

vi.mock("sonner", () => ({ toast: toastMocks }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function proposalEntry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "11111111-1111-1111-1111-111111111111",
    event_id: null,
    view: "proposals",
    source_type: "proposed_event",
    source_key: "proposals",
    title: "Dentist appointment",
    start_at: "2026-06-22T09:00:00+00:00",
    end_at: "2026-06-22T10:00:00+00:00",
    timezone: "UTC",
    all_day: false,
    calendar_id: null,
    provider_event_id: null,
    butler_name: "general",
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "active",
    sync_state: null,
    editable: false,
    metadata: {
      source_type: "proposed_event",
      confidence: 0.82,
      source_snippet: "see you at the dentist on the 22nd at 9am",
      source_event_id: "evt-abc-123",
      proposal_status: "pending",
    },
    source_butler: "general",
    source_session_id: null,
    ...overrides,
  };
}

function makeMutation(impl?: (vars: unknown) => Promise<unknown>) {
  return {
    mutateAsync: vi.fn(impl ?? (async () => ({ data: { status: "ok" } }))),
  } as unknown as Parameters<typeof CalendarProposalsPanel>[0]["acceptMutation"];
}

function renderPanel(
  props: Partial<Parameters<typeof CalendarProposalsPanel>[0]> = {},
) {
  const acceptMutation = props.acceptMutation ?? makeMutation();
  const dismissMutation = props.dismissMutation ?? makeMutation();
  render(
    <CalendarProposalsPanel
      entries={props.entries ?? [proposalEntry()]}
      isLoading={props.isLoading}
      isError={props.isError}
      error={props.error}
      acceptMutation={acceptMutation}
      dismissMutation={dismissMutation}
    />,
  );
  return { acceptMutation, dismissMutation };
}

describe("CalendarProposalsPanel", () => {
  it("renders a pending proposal with confidence chip and provenance", () => {
    renderPanel();
    expect(screen.getByText("Dentist appointment")).toBeTruthy();
    expect(screen.getByTestId("proposal-confidence").textContent).toBe("82%");
    const provenance = screen.getByTestId("proposal-provenance");
    expect(provenance.textContent).toContain("see you at the dentist");
    expect(screen.getByTestId("proposal-source-event").textContent).toContain("evt-abc-123");
  });

  it("renders the empty state when there are no proposals", () => {
    renderPanel({ entries: [] });
    expect(screen.getByTestId("proposals-empty")).toBeTruthy();
  });

  it("accepts a proposal and optimistically removes the row", async () => {
    const { acceptMutation } = renderPanel();
    fireEvent.click(screen.getByTestId("proposal-accept"));

    expect(acceptMutation.mutateAsync).toHaveBeenCalledWith({
      proposalId: "11111111-1111-1111-1111-111111111111",
      overrides: undefined,
    });
    await waitFor(() => expect(screen.queryByTestId("proposal-row")).toBeNull());
    expect(toastMocks.success).toHaveBeenCalled();
  });

  it("dismisses a proposal and optimistically removes the row", async () => {
    const { dismissMutation } = renderPanel();
    fireEvent.click(screen.getByTestId("proposal-dismiss"));

    expect(dismissMutation.mutateAsync).toHaveBeenCalledWith({
      proposalId: "11111111-1111-1111-1111-111111111111",
    });
    await waitFor(() => expect(screen.queryByTestId("proposal-row")).toBeNull());
  });

  it("reverts the optimistic removal on a non-terminal error", async () => {
    const acceptMutation = makeMutation(async () => {
      throw new Error("network down");
    });
    renderPanel({ acceptMutation });

    fireEvent.click(screen.getByTestId("proposal-accept"));

    // Row reappears after the failed mutation settles.
    await waitFor(() => expect(screen.getByTestId("proposal-row")).toBeTruthy());
    expect(toastMocks.error).toHaveBeenCalled();
  });

  it("keeps the row removed on a 409 lost-race (resolved server-side)", async () => {
    const dismissMutation = makeMutation(async () => {
      throw new ApiError("CONFLICT", "already accepted", 409);
    });
    renderPanel({ dismissMutation });

    fireEvent.click(screen.getByTestId("proposal-dismiss"));

    await waitFor(() => expect(screen.queryByTestId("proposal-row")).toBeNull());
    expect(toastMocks.info).toHaveBeenCalled();
    expect(toastMocks.error).not.toHaveBeenCalled();
  });

  it("shows the full end date for a multi-day proposal span", () => {
    renderPanel({
      entries: [
        proposalEntry({
          start_at: "2026-06-22T22:00:00+00:00",
          end_at: "2026-06-23T06:00:00+00:00",
        }),
      ],
    });
    // The end date (Jun 23) must be visible, not just the end time, so an
    // overnight span is not misread as ending the same day.
    const row = screen.getByTestId("proposal-row");
    expect(row.textContent).toContain("Jun 23");
  });

  it("blocks an edit where the end is not after the start", async () => {
    const { acceptMutation } = renderPanel();

    fireEvent.click(screen.getByTestId("proposal-edit"));
    // Start at 12:00, end at 09:00 on the same day — inverted range.
    fireEvent.change(screen.getByTestId("proposal-edit-start"), {
      target: { value: "2026-06-22T12:00" },
    });
    fireEvent.change(screen.getByTestId("proposal-edit-end"), {
      target: { value: "2026-06-22T09:00" },
    });
    fireEvent.click(screen.getByTestId("proposal-edit-save"));

    expect(toastMocks.error).toHaveBeenCalled();
    expect(acceptMutation.mutateAsync).not.toHaveBeenCalled();
    // The edit form stays open so the user can correct the range.
    expect(screen.getByTestId("proposal-edit-form")).toBeTruthy();
  });

  it("forwards inline edits as overrides to the accept mutation", async () => {
    const { acceptMutation } = renderPanel();

    fireEvent.click(screen.getByTestId("proposal-edit"));
    fireEvent.change(screen.getByTestId("proposal-edit-title"), {
      target: { value: "Dentist — rescheduled" },
    });
    fireEvent.click(screen.getByTestId("proposal-edit-save"));

    expect(acceptMutation.mutateAsync).toHaveBeenCalledTimes(1);
    const call = (acceptMutation.mutateAsync as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(call.proposalId).toBe("11111111-1111-1111-1111-111111111111");
    expect(call.overrides.title).toBe("Dentist — rescheduled");
    await waitFor(() => expect(screen.queryByTestId("proposal-row")).toBeNull());
  });
});
