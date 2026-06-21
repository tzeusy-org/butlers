// @vitest-environment jsdom
/**
 * Tests for the calendar ICS data-portability client (bu-y16a8):
 *  - `calendarIcsExportUrl` — view/range/facet param serialization
 *  - `calendarSubscribeUrl` / `calendarSubscribeWebcalUrl` — feed URL forms
 *  - `importCalendarIcs` — multipart POST, response passthrough, error mapping
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

afterEach(() => {
  vi.clearAllMocks();
});

import {
  ApiError,
  calendarIcsExportUrl,
  calendarSubscribeUrl,
  calendarSubscribeWebcalUrl,
  importCalendarIcs,
} from "./client.ts";

describe("calendarIcsExportUrl", () => {
  it("serializes view, range, and facet filters", () => {
    const url = calendarIcsExportUrl({
      view: "butler",
      start: "2026-02-22T00:00:00.000Z",
      end: "2026-02-23T00:00:00.000Z",
      sources: ["provider:google:primary", "provider:google:work"],
      status: "active",
      source_type: "scheduled_task",
    });
    expect(url).toContain("/calendar/export/ics?");
    expect(url).toContain("view=butler");
    expect(url).toContain("start=2026-02-22T00%3A00%3A00.000Z");
    expect(url).toContain("end=2026-02-23T00%3A00%3A00.000Z");
    expect(url).toContain("sources=provider%3Agoogle%3Aprimary");
    expect(url).toContain("sources=provider%3Agoogle%3Awork");
    expect(url).toContain("status=active");
    expect(url).toContain("source_type=scheduled_task");
  });

  it("omits optional facets when not provided", () => {
    const url = calendarIcsExportUrl({
      view: "user",
      start: "2026-02-22T00:00:00.000Z",
      end: "2026-02-23T00:00:00.000Z",
    });
    expect(url).not.toContain("status=");
    expect(url).not.toContain("source_type=");
    expect(url).not.toContain("sources=");
  });
});

describe("calendar subscribe URLs", () => {
  it("targets the subscribe.ics feed", () => {
    expect(calendarSubscribeUrl()).toContain("/calendar/subscribe.ics");
  });

  it("derives a webcal:// URL from the https feed", () => {
    const webcal = calendarSubscribeWebcalUrl();
    expect(webcal.startsWith("webcal://")).toBe(true);
    expect(webcal).toContain("/calendar/subscribe.ics");
    expect(webcal).not.toMatch(/^https?:\/\//);
  });
});

describe("importCalendarIcs", () => {
  function icsFile(): File {
    return new File(["BEGIN:VCALENDAR\nEND:VCALENDAR"], "events.ics", {
      type: "text/calendar",
    });
  }

  it("POSTs multipart form-data and returns the parsed/imported/skipped counts", async () => {
    const body = {
      data: {
        parsed: 5,
        imported: 3,
        skipped_duplicates: 2,
        imported_events: [],
      },
    };
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => body,
    });

    const result = await importCalendarIcs({
      file: icsFile(),
      butlerName: "general",
      calendarId: "primary",
    });

    expect(result.data.parsed).toBe(5);
    expect(result.data.imported).toBe(3);
    expect(result.data.skipped_duplicates).toBe(2);

    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toContain("/calendar/import/ics");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect(form.get("butler_name")).toBe("general");
    expect(form.get("calendar_id")).toBe("primary");
    expect(form.get("file")).toBeInstanceOf(File);
    // The browser must own the multipart Content-Type/boundary.
    expect(
      (init.headers as Record<string, string>)["Content-Type"],
    ).toBeUndefined();
  });

  it("omits calendar_id when not provided", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        data: { parsed: 0, imported: 0, skipped_duplicates: 0, imported_events: [] },
      }),
    });
    await importCalendarIcs({ file: icsFile(), butlerName: "general" });
    const form = mockFetch.mock.calls[0][1].body as FormData;
    expect(form.has("calendar_id")).toBe(false);
  });

  it("maps a 413 over-limit error to an ApiError with the detail message", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 413,
      statusText: "Request Entity Too Large",
      json: async () => ({ detail: "Import exceeds the 1000-event limit" }),
    });
    await expect(
      importCalendarIcs({ file: icsFile(), butlerName: "general" }),
    ).rejects.toMatchObject({
      status: 413,
      message: "Import exceeds the 1000-event limit",
    });
  });

  it("maps a 400 empty-file error to an ApiError", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: async () => ({ detail: "Uploaded .ics file is empty" }),
    });
    const error = await importCalendarIcs({
      file: icsFile(),
      butlerName: "general",
    }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(400);
    expect(error.message).toBe("Uploaded .ics file is empty");
  });
});
