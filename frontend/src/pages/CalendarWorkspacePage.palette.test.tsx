// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";

import { setButlerWorkspaceFixtures } from "@/test/calendar-page/butler-fixtures";
import {
  findDialogByTitle,
  installCalendarPageHarness,
} from "@/test/calendar-page/harness";
import { flush } from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("CalendarWorkspacePage palette verbs + bindings (bu-t64p2)", () => {
  const { renderPage, getSearchText } = installCalendarPageHarness();

  afterEach(() => {
    vi.useRealTimers();
  });

  async function pressKey(key: string): Promise<void> {
    await act(async () => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }),
      );
      await flush();
    });
  }

  it("'c' opens the create-butler-event dialog in butler view", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");
    await pressKey("c");
    expect(findDialogByTitle("Create butler event")).toBeDefined();
  });

  it("'c' does nothing in the read-only user view", async () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await pressKey("c");
    expect(document.querySelector('[data-slot="dialog-content"]')).toBeNull();
  });

  it("'t' jumps the anchor to today", async () => {
    vi.setSystemTime(new Date(2026, 4, 15, 12, 0, 0));
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await pressKey("t");
    expect(getSearchText()).toContain("anchor=2026-05-15");
  });

  it("ArrowLeft and ArrowRight step the active calendar range", async () => {
    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    await pressKey("ArrowRight");
    expect(getSearchText()).toContain("anchor=2026-03-08");

    await pressKey("ArrowLeft");
    expect(getSearchText()).toContain("anchor=2026-03-01");
  });
});
