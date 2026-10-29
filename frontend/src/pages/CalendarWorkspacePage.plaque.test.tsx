// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";

import { setButlerWorkspaceFixtures } from "@/test/calendar-page/butler-fixtures";
import { installCalendarPageHarness } from "@/test/calendar-page/harness";
import {
  flush,
  setSyncState,
  setWorkspaceMetaState,
  type UseWorkspaceMetaResult,
} from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("CalendarWorkspacePage sync freshness plaque (bu-hmdqz.10)", () => {
  const { renderPage } = installCalendarPageHarness();

  function plaqueEl() {
    return document.querySelector(
      '[data-testid="calendar-freshness-plaque"]',
    );
  }

  it("renders nothing when every enabled source is fresh", async () => {
    setButlerWorkspaceFixtures();
    setWorkspaceMetaState(); // default fixture: two fresh sources
    setSyncState();

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    expect(plaqueEl()).toBeNull();
  });

  it("renders a degraded clause with the worst source's staleness and a Sync now CTA", async () => {
    setButlerWorkspaceFixtures();
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [
            {
              source_id: "source-fresh",
              source_key: "google:primary",
              source_kind: "provider_event",
              lane: "user",
              provider: "google",
              calendar_id: "primary",
              butler_name: "general",
              display_name: "Primary",
              writable: true,
              metadata: {},
              cursor_name: "provider_sync",
              last_synced_at: "2026-03-01T09:59:00Z",
              last_success_at: "2026-03-01T09:59:00Z",
              last_error_at: null,
              last_error: null,
              full_sync_required: false,
              sync_state: "fresh",
              staleness_ms: 60_000,
              error_kind: "none",
              sync_enabled: true,
              sync_applicable: true,
            },
            {
              source_id: "source-stale",
              source_key: "google:work",
              source_kind: "provider_event",
              lane: "user",
              provider: "google",
              calendar_id: "work",
              butler_name: "general",
              display_name: "Work",
              writable: true,
              metadata: {},
              cursor_name: "provider_sync",
              last_synced_at: "2025-11-27T09:00:00Z",
              last_success_at: "2025-11-27T09:00:00Z",
              last_error_at: null,
              last_error: null,
              full_sync_required: false,
              sync_state: "stale",
              staleness_ms: 96 * 24 * 60 * 60 * 1000, // 96 days, matches live incident
              error_kind: "none",
              sync_enabled: true,
              sync_applicable: true,
            },
          ],
          writable_calendars: [],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);
    const syncMutateAsync = vi.fn().mockResolvedValue({
      data: { triggered_count: 2 },
      meta: {},
    });
    setSyncState({ mutateAsync: syncMutateAsync });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    const plaque = plaqueEl();
    expect(plaque).not.toBeNull();
    expect(plaque?.textContent).toContain("Calendar sync");
    expect(plaque?.textContent).toContain("96 days ago");
    expect(plaque?.textContent).toContain("Sync now");

    const ctaButton = Array.from(
      plaque?.querySelectorAll("button") ?? [],
    ).find((button) => button.textContent?.trim() === "Sync now");
    expect(ctaButton).toBeDefined();
    await act(async () => {
      ctaButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });
    expect(syncMutateAsync).toHaveBeenCalledWith({ all: true });
  });

  it("never treats an empty connected-sources list as fresh — renders unknown-stale", async () => {
    setButlerWorkspaceFixtures();
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [],
          writable_calendars: [],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);
    setSyncState();

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    const plaque = plaqueEl();
    expect(plaque).not.toBeNull();
    expect(plaque?.textContent).toContain("Sync status unknown");
  });

  it("never treats a freshness-fetch error as fresh — renders unknown-stale", async () => {
    setButlerWorkspaceFixtures();
    setWorkspaceMetaState({
      data: undefined,
      isError: true,
      error: new Error("network down"),
    } as Partial<UseWorkspaceMetaResult>);
    setSyncState();

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    const plaque = plaqueEl();
    expect(plaque).not.toBeNull();
    expect(plaque?.textContent).toContain("Sync status unavailable");
  });

  it("ignores a disabled source's staleness (operator-off is not a failure)", async () => {
    setButlerWorkspaceFixtures();
    setWorkspaceMetaState({
      data: {
        data: {
sources_available: true,
          capabilities: {
            views: ["user", "butler"],
            filters: { butlers: true, sources: true, timezone: true },
            sync: { global: true, by_source: true },
          },
          connected_sources: [
            {
              source_id: "source-disabled",
              source_key: "google:archived",
              source_kind: "provider_event",
              lane: "user",
              provider: "google",
              calendar_id: "archived",
              butler_name: "general",
              display_name: "Archived",
              writable: true,
              metadata: {},
              cursor_name: "provider_sync",
              last_synced_at: "2025-01-01T00:00:00Z",
              last_success_at: "2025-01-01T00:00:00Z",
              last_error_at: null,
              last_error: null,
              full_sync_required: false,
              sync_state: "stale",
              staleness_ms: 400 * 24 * 60 * 60 * 1000,
              error_kind: "none",
              sync_enabled: false,
              sync_applicable: true,
            },
          ],
          writable_calendars: [],
          lane_definitions: [],
          default_timezone: "UTC",
          primary_calendar_id: null,
        },
        meta: {},
      },
    } as Partial<UseWorkspaceMetaResult>);
    setSyncState();

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
    await act(async () => {
      await flush();
    });

    expect(plaqueEl()).toBeNull();
  });
});
