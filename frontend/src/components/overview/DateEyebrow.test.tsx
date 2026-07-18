// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import { AppTimezoneProvider } from "@/components/ui/timezone-context";
import { DateEyebrow } from "./DateEyebrow";

describe("DateEyebrow", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the current date and time in the configured owner timezone", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-18T16:30:00.000Z"));

    const html = renderToStaticMarkup(
      <AppTimezoneProvider timezone="Asia/Singapore">
        <DateEyebrow />
      </AppTimezoneProvider>,
    );

    expect(html).toContain("Overview · Sun, 19 July 2026 · 00:30");
    expect(html).not.toContain("Overview · Sat, 18 July 2026 · 16:30");
  });
});
