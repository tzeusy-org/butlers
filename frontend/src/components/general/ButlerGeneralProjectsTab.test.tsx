// @vitest-environment jsdom
/**
 * ButlerGeneralProjectsTab — owner projects page (bu-jv1moa.12).
 *
 * The API module is mocked at its boundary (ApiError stays real) so the link
 * resolution hook runs for real: each linked record resolves through its own
 * reader, a failing trip reader degrades only the Trips region, a deleted
 * calendar entry reads as not found, deadlines resolve through their butler's
 * schedule and Drive files through General's Drive reader (bu-jv1moa.44; Drive
 * not connected reads calm, not degraded), and the page exposes no Arm or Run
 * control.  Unlinking calls only the General
 * unlink endpoint, never a linked record's API.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";

vi.mock("@/api/index.ts", async () => {
  const actual = await vi.importActual<typeof import("@/api/index.ts")>("@/api/index.ts");
  return {
    ApiError: actual.ApiError,
    getGeneralProjects: vi.fn(),
    getGeneralProject: vi.fn(),
    createGeneralProject: vi.fn(),
    editGeneralProjectCharter: vi.fn(),
    closeGeneralProject: vi.fn(),
    addGeneralProjectLink: vi.fn(),
    removeGeneralProjectLink: vi.fn(),
    getTravelTripSummary: vi.fn(),
    getCalendarWorkspaceEntry: vi.fn(),
    getSystemConditions: vi.fn(),
    getButlerDeadline: vi.fn(),
    getGeneralDriveFile: vi.fn(),
  };
});

vi.mock("@/components/ui/time", () => ({
  Time: ({ value }: { value: string }) => createElement("time", { dateTime: value }, value),
}));

import * as api from "@/api/index.ts";
import ButlerGeneralProjectsTab from "./ButlerGeneralProjectsTab";

const PROJECT_ID = "aaaaaaaa-0000-4000-8000-000000000001";
const TRIP_OK = "bbbbbbbb-0000-4000-8000-000000000001";
const TRIP_DOWN = "bbbbbbbb-0000-4000-8000-000000000002";
const EVENT_GONE = "cccccccc-0000-4000-8000-000000000001";
const CONDITION = "dddddddd-0000-4000-8000-000000000001";
const DEADLINE = "eeeeeeee-0000-4000-8000-000000000001";

function link(id: string, ref_kind: string, ref_id: string) {
  return { id, ref_kind, ref_id, created_at: "2026-11-01T00:00:00Z" };
}

const SUMMARY = {
  id: PROJECT_ID,
  kind: "project",
  title: "Move to Tiong Bahru",
  state: "draft",
  current_charter_version: 2,
  horizon_end: "2026-12-15T00:00:00Z",
  closed_at: null,
  link_count: 6,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
};

const DETAIL = {
  ...SUMMARY,
  charter: {
    version: 2,
    title: SUMMARY.title,
    horizon_end: SUMMARY.horizon_end,
    // Record text is inert data, never markup.
    objective: "<b>Move</b> by Dec 15",
    done_condition: "Old keys returned",
    constraints: { budget: { amount: "4000", currency: "SGD" }, must_keep: ["cat"] },
    created_by: "owner",
    created_at: "2026-11-01T00:00:00Z",
  },
  links: [
    link("l1", "trip", TRIP_OK),
    link("l2", "trip", TRIP_DOWN),
    link("l3", "calendar_event", EVENT_GONE),
    link("l4", "drive_file", "1AbC_def-9"),
    link("l5", "owner_condition", CONDITION),
    link("l6", "deadline", `finance:${DEADLINE}`),
  ],
};

function renderTab(search = `?tab=projects&project=${PROJECT_ID}`, driveConnected = true) {
  vi.mocked(api.getGeneralProjects).mockResolvedValue({
    data: [SUMMARY],
    meta: { total: 1, offset: 0, limit: 200 },
  } as never);
  vi.mocked(api.getGeneralProject).mockResolvedValue({ data: DETAIL, meta: {} } as never);
  vi.mocked(api.getTravelTripSummary).mockImplementation(async (id: string) => {
    if (id === TRIP_DOWN) throw new api.ApiError("UNAVAILABLE", "Travel is down", 503);
    return {
      trip: {
        id,
        name: "Movers' trip",
        destination: "Tiong Bahru",
        start_date: "2026-12-10",
        end_date: "2026-12-11",
      },
    } as never;
  });
  vi.mocked(api.getCalendarWorkspaceEntry).mockRejectedValue(
    new api.ApiError("NOT_FOUND", "Entry not found", 404),
  );
  vi.mocked(api.getSystemConditions).mockResolvedValue({
    data: {
      conditions: [{ id: CONDITION, source: "bills", summary: "Deposit unpaid", state: "open" }],
      total: 1,
      conditions_available: true,
    },
    meta: {},
  } as never);
  vi.mocked(api.getButlerDeadline).mockResolvedValue({
    data: {
      id: DEADLINE,
      butler: "finance",
      name: "deposit",
      display_title: "Deposit due",
      target_date: "2026-12-01",
      deadline_status: "pending",
      enabled: true,
    },
    meta: {},
  } as never);
  if (driveConnected) {
    vi.mocked(api.getGeneralDriveFile).mockRejectedValue(
      new api.ApiError("DRIVE_UNAVAILABLE", "The Drive reader is unavailable.", 503),
    );
  } else {
    vi.mocked(api.getGeneralDriveFile).mockResolvedValue({
      data: { state: "not_connected", file: null },
      meta: {},
    } as never);
  }
  vi.mocked(api.removeGeneralProjectLink).mockResolvedValue({
    data: { ...DETAIL, links: DETAIL.links.filter((l) => l.id !== "l4") },
    meta: {},
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    createElement(
      QueryClientProvider,
      { client },
      createElement(
        MemoryRouter,
        { initialEntries: [`/butlers/general${search}`] },
        createElement(ButlerGeneralProjectsTab),
      ),
    ),
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ButlerGeneralProjectsTab", () => {
  it("resolves each region through its reader and degrades only the failing one", async () => {
    renderTab();

    const trips = await screen.findByTestId("link-region-trip");
    await within(trips).findByText("Movers' trip");
    expect(within(trips).getByRole("alert").textContent).toContain("Trips: reader unavailable");
    expect(within(trips).getByText("Unavailable: reader failed")).toBeTruthy();

    const events = screen.getByTestId("link-region-calendar_event");
    await within(events).findByText("Not found: deleted or never existed");
    expect(within(events).queryByRole("alert")).toBeNull();
    const deadlines = screen.getByTestId("link-region-deadline");
    await within(deadlines).findByText("Deposit due");
    expect(within(deadlines).getByText("finance · due 2026-12-01 · pending")).toBeTruthy();
    expect(api.getButlerDeadline).toHaveBeenCalledWith("finance", DEADLINE);
    const files = screen.getByTestId("link-region-drive_file");
    await within(files).findByText("Unavailable: reader failed");
    expect(within(files).getByRole("alert").textContent).toContain("Drive files: reader unavailable");
    expect(api.getGeneralDriveFile).toHaveBeenCalledWith("1AbC_def-9");
    await within(screen.getByTestId("link-region-owner_condition")).findByText("Deposit unpaid");

    // The project itself is intact and its record text renders inert.
    expect(screen.getAllByText("Move to Tiong Bahru").length).toBeGreaterThan(0);
    expect(screen.getByText("<b>Move</b> by Dec 15")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /arm|run/i })).toBeNull();

    // Unlinking calls only General's unlink endpoint; no record API is written.
    fireEvent.click(screen.getByRole("button", { name: "Unlink 1AbC_def-9" }));
    await waitFor(() =>
      expect(api.removeGeneralProjectLink).toHaveBeenCalledWith(PROJECT_ID, "l4"),
    );
    expect(api.getTravelTripSummary).toHaveBeenCalledTimes(2);
    expect(api.getTravelTripSummary).toHaveBeenCalledWith(TRIP_OK);
  });
  it("shows Drive not connected calmly, without a degraded-reader alarm", async () => {
    renderTab(undefined, false);

    const files = await screen.findByTestId("link-region-drive_file");
    await within(files).findByText("Drive is not connected for General");
    expect(within(files).queryByRole("alert")).toBeNull();
    expect(within(files).queryByText(/Unavailable/)).toBeNull();
  });
  it("keeps the form and says so when a create deduplicates onto an existing draft", async () => {
    renderTab("?tab=projects");
    vi.mocked(api.createGeneralProject).mockResolvedValue({
      data: DETAIL,
      meta: { outcome: "deduplicated" },
    } as never);

    fireEvent.click(await screen.findByRole("button", { name: /new/i }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "move to tiong bahru" } });
    fireEvent.change(screen.getByLabelText("Horizon (at most 180 days)"), {
      target: { value: "2026-12-15T08:00" },
    });
    fireEvent.change(screen.getByLabelText("Objective"), { target: { value: "Fresh plan" } });
    fireEvent.change(screen.getByLabelText("Done when"), { target: { value: "Moved in" } });
    fireEvent.click(screen.getByRole("button", { name: "Create draft" }));

    const notice = await screen.findByTestId("project-deduplicated");
    expect(notice.textContent).toContain("Nothing was saved from this form");
    // The owner's text is still in the form, and the old draft was not opened.
    expect((screen.getByLabelText("Objective") as HTMLTextAreaElement).value).toBe("Fresh plan");
    expect(screen.queryByTestId("project-detail")).toBeNull();
    expect(api.getGeneralProject).not.toHaveBeenCalled();

    fireEvent.click(within(notice).getByRole("button", { name: "Open existing draft" }));
    expect(await screen.findByTestId("project-detail")).toBeTruthy();
  });
});
