// @vitest-environment jsdom
/**
 * CalendarDuplicatesPanel — RTL tests (bu-fol6y).
 *
 * Covers:
 *  - Renders collapsed clusters with the kept survivor + the duplicate copies.
 *  - Honest empty-state (available=true, no clusters) vs unavailable (available=false).
 *  - Keep-separate toggle calls the override mutation with the cluster key.
 *  - Match-strategy control PATCHes dedup-rules with the chosen strategy.
 *  - Noisy-threshold input commits a PATCH on blur.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import type {
  CalendarDuplicateCluster,
  CalendarDuplicatesResponse,
  UnifiedCalendarEntry,
} from "@/api/types.ts";

import { CalendarDuplicatesPanel } from "./CalendarDuplicatesPanel.tsx";

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

function entry(overrides: Partial<UnifiedCalendarEntry> = {}): UnifiedCalendarEntry {
  return {
    entry_id: "11111111-1111-1111-1111-111111111111",
    event_id: null,
    view: "user",
    source_type: "provider_event",
    source_key: "google:primary",
    title: "Team standup",
    start_at: "2026-06-22T09:00:00+00:00",
    end_at: "2026-06-22T09:30:00+00:00",
    timezone: "UTC",
    all_day: false,
    calendar_id: "primary",
    provider_event_id: "evt-1",
    butler_name: "general",
    schedule_id: null,
    reminder_id: null,
    rrule: null,
    cron: null,
    until_at: null,
    status: "active",
    sync_state: null,
    editable: false,
    metadata: {},
    source_butler: "general",
    source_session_id: null,
    ...overrides,
  };
}

function cluster(overrides: Partial<CalendarDuplicateCluster> = {}): CalendarDuplicateCluster {
  return {
    cluster_key: "title\x01team standup\x011750582800000",
    match_pass: "title",
    member_count: 2,
    keep_separate: false,
    kept_entry: entry(),
    duplicate_entries: [
      entry({
        entry_id: "22222222-2222-2222-2222-222222222222",
        source_key: "google:work",
        butler_name: "finance",
        calendar_id: "work@group.calendar.google.com",
      }),
    ],
    ...overrides,
  };
}

function makeResponse(
  overrides: Partial<CalendarDuplicatesResponse> = {},
): CalendarDuplicatesResponse {
  return {
    clusters: [cluster()],
    rules: { match_strategy: "balanced", noisy_threshold: 2 },
    available: true,
    ...overrides,
  };
}

type PanelProps = Parameters<typeof CalendarDuplicatesPanel>[0];

function makeMutation<T extends PanelProps["rulesMutation"] | PanelProps["keepSeparateMutation"]>(
  impl?: (vars: unknown) => Promise<unknown>,
): T {
  return {
    mutateAsync: vi.fn(impl ?? (async () => ({ data: {} }))),
    isPending: false,
  } as unknown as T;
}

function renderPanel(props: Partial<PanelProps> = {}) {
  const rulesMutation = props.rulesMutation ?? makeMutation<PanelProps["rulesMutation"]>();
  const keepSeparateMutation =
    props.keepSeparateMutation ?? makeMutation<PanelProps["keepSeparateMutation"]>();
  render(
    <CalendarDuplicatesPanel
      data={props.data ?? makeResponse()}
      isLoading={props.isLoading}
      isError={props.isError}
      error={props.error}
      rulesMutation={rulesMutation}
      keepSeparateMutation={keepSeparateMutation}
      timezone={props.timezone ?? "UTC"}
    />,
  );
  return { rulesMutation, keepSeparateMutation };
}

describe("CalendarDuplicatesPanel", () => {
  it("renders collapsed clusters with the kept entry and duplicate copies", () => {
    renderPanel();
    expect(screen.getByText("Team standup")).toBeTruthy();
    const rows = screen.getAllByTestId("duplicate-cluster");
    expect(rows).toHaveLength(1);
    const copies = screen.getAllByTestId("duplicate-copy");
    expect(copies).toHaveLength(1);
    expect(copies[0].textContent).toContain("finance");
  });

  it("renders the honest empty-state when there are no clusters", () => {
    renderPanel({ data: makeResponse({ clusters: [] }) });
    expect(screen.getByTestId("duplicates-empty")).toBeTruthy();
    expect(screen.queryByTestId("duplicates-unavailable")).toBeNull();
  });

  it("renders the unavailable state distinctly from empty", () => {
    renderPanel({ data: makeResponse({ clusters: [], available: false }) });
    expect(screen.getByTestId("duplicates-unavailable")).toBeTruthy();
    expect(screen.queryByTestId("duplicates-empty")).toBeNull();
  });

  it("toggles keep-separate via the override mutation", async () => {
    const { keepSeparateMutation } = renderPanel();
    fireEvent.click(screen.getByTestId("duplicate-keep-separate"));
    expect(keepSeparateMutation.mutateAsync).toHaveBeenCalledWith({
      cluster_key: "title\x01team standup\x011750582800000",
      keep_separate: true,
      match_pass: "title",
      label: "Team standup",
    });
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalled());
  });

  it("unpins a kept-separate cluster (keep_separate: false)", async () => {
    const { keepSeparateMutation } = renderPanel({
      data: makeResponse({ clusters: [cluster({ keep_separate: true })] }),
    });
    const toggle = screen.getByTestId("duplicate-keep-separate");
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(toggle);
    expect(keepSeparateMutation.mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ keep_separate: false }),
    );
  });

  it("changes match strategy via the dedup-rules PATCH", async () => {
    const { rulesMutation } = renderPanel();
    fireEvent.click(screen.getByTestId("dedup-strategy-aggressive"));
    expect(rulesMutation.mutateAsync).toHaveBeenCalledWith({ match_strategy: "aggressive" });
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalled());
  });

  it("does not PATCH when the active strategy is re-selected", () => {
    const { rulesMutation } = renderPanel();
    fireEvent.click(screen.getByTestId("dedup-strategy-balanced"));
    expect(rulesMutation.mutateAsync).not.toHaveBeenCalled();
  });

  it("commits the noisy-threshold on blur", async () => {
    const { rulesMutation } = renderPanel();
    const input = screen.getByTestId("dedup-threshold-input");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.blur(input);
    expect(rulesMutation.mutateAsync).toHaveBeenCalledWith({ noisy_threshold: 3 });
    await waitFor(() => expect(toastMocks.success).toHaveBeenCalled());
  });

  it("rejects an invalid noisy-threshold and resets the draft", () => {
    const { rulesMutation } = renderPanel();
    const input = screen.getByTestId("dedup-threshold-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "1" } });
    fireEvent.blur(input);
    expect(rulesMutation.mutateAsync).not.toHaveBeenCalled();
    expect(toastMocks.error).toHaveBeenCalled();
    expect(input.value).toBe("2");
  });

  it("renders loading and error states", () => {
    const { rerender } = render(
      <CalendarDuplicatesPanel
        isLoading
        rulesMutation={makeMutation<PanelProps["rulesMutation"]>()}
        keepSeparateMutation={makeMutation<PanelProps["keepSeparateMutation"]>()}
        timezone="UTC"
      />,
    );
    expect(screen.getByTestId("duplicates-panel").textContent).toContain("Reviewing duplicates");
    rerender(
      <CalendarDuplicatesPanel
        isError
        error={new Error("boom")}
        rulesMutation={makeMutation<PanelProps["rulesMutation"]>()}
        keepSeparateMutation={makeMutation<PanelProps["keepSeparateMutation"]>()}
        timezone="UTC"
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("boom");
  });
});
