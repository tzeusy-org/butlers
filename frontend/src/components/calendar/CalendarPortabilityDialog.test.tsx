// @vitest-environment jsdom
/**
 * CalendarPortabilityDialog — RTL tests (bu-y16a8).
 *
 * Covers the three data-portability affordances:
 *  - Export: clicking "Download .ics" triggers an anchor download to the
 *    filtered export URL.
 *  - Subscribe: the read-only feed URL is shown and copyable.
 *  - Import: uploading a file calls the import client and surfaces the
 *    parsed/imported/skipped counts; an empty target list shows a guard.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import type {
  CalendarIcsExportParams,
  CalendarWorkspaceWritableCalendar,
} from "@/api/types.ts";

vi.mock("@/api/client.ts", async (importActual) => {
  const actual = await importActual<typeof import("@/api/client.ts")>();
  return {
    ...actual,
    calendarIcsExportUrl: vi.fn(() => "http://localhost/api/calendar/export/ics?view=user"),
    calendarSubscribeUrl: vi.fn(() => "http://localhost/api/calendar/subscribe.ics"),
    calendarSubscribeWebcalUrl: vi.fn(() => "webcal://localhost/api/calendar/subscribe.ics"),
    importCalendarIcs: vi.fn(),
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import {
  calendarIcsExportUrl,
  importCalendarIcs,
} from "@/api/client.ts";

import { CalendarPortabilityDialog } from "./CalendarPortabilityDialog.tsx";

const EXPORT_PARAMS: CalendarIcsExportParams = {
  view: "user",
  start: "2026-02-22T00:00:00.000Z",
  end: "2026-02-23T00:00:00.000Z",
};

const TARGET: CalendarWorkspaceWritableCalendar = {
  source_key: "provider:google:primary",
  provider: "google",
  calendar_id: "primary",
  display_name: "Primary",
  butler_name: "general",
};

function renderDialog(
  targets: CalendarWorkspaceWritableCalendar[] = [TARGET],
) {
  return render(
    <CalendarPortabilityDialog
      open
      onOpenChange={() => {}}
      exportParams={EXPORT_PARAMS}
      rangeLabel="Feb 22 – Feb 28"
      importTargets={targets}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(cleanup);

describe("CalendarPortabilityDialog", () => {
  it("renders the three affordances", () => {
    renderDialog();
    expect(
      screen.getByRole("button", { name: /download calendar as ics/i }),
    ).toBeTruthy();
    expect(
      screen.getByLabelText(/subscribe feed url/i),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /import calendar file/i }),
    ).toBeTruthy();
  });

  it("triggers an anchor download to the filtered export URL", () => {
    renderDialog();
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    fireEvent.click(
      screen.getByRole("button", { name: /download calendar as ics/i }),
    );

    expect(calendarIcsExportUrl).toHaveBeenCalledWith(EXPORT_PARAMS);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    clickSpy.mockRestore();
  });

  it("shows the read-only subscribe URL and a webcal link", () => {
    renderDialog();
    const input = screen.getByLabelText(/subscribe feed url/i) as HTMLInputElement;
    expect(input.value).toBe("http://localhost/api/calendar/subscribe.ics");
    expect(input.readOnly).toBe(true);
    const webcalLink = screen.getByRole("link", {
      name: /open in calendar app/i,
    }) as HTMLAnchorElement;
    expect(webcalLink.getAttribute("href")).toBe(
      "webcal://localhost/api/calendar/subscribe.ics",
    );
  });

  it("copies the subscribe URL to the clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /copy subscribe url/i }));
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "http://localhost/api/calendar/subscribe.ics",
      ),
    );
  });

  it("imports a file and surfaces parsed/imported/skipped counts", async () => {
    (importCalendarIcs as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        parsed: 4,
        imported: 3,
        skipped_duplicates: 1,
        imported_events: [],
      },
    });
    renderDialog();

    const fileInput = screen.getByLabelText(/choose .ics file/i);
    const file = new File(["BEGIN:VCALENDAR\nEND:VCALENDAR"], "events.ics", {
      type: "text/calendar",
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    fireEvent.click(
      screen.getByRole("button", { name: /import calendar file/i }),
    );

    await waitFor(() =>
      expect(importCalendarIcs).toHaveBeenCalledWith({
        file,
        butlerName: "general",
        calendarId: "primary",
      }),
    );

    const result = await screen.findByLabelText(/import result/i);
    expect(result.textContent).toContain("4");
    expect(result.textContent).toContain("3");
    expect(result.textContent).toContain("1");
  });

  it("guards when there is no writable import target", () => {
    renderDialog([]);
    expect(
      screen.getByText(/no writable calendar is available to import into/i),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: /import calendar file/i }),
    ).toBeNull();
  });
});
