// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { toast } from "sonner";

import { useToggleCalendarSource } from "@/hooks/use-calendar-workspace";
import { setButlerWorkspaceFixtures } from "@/test/calendar-page/butler-fixtures";
import {
  findButton,
  installCalendarPageHarness,
  latestWorkspaceParams,
} from "@/test/calendar-page/harness";
import {
  flush,
  setPrimaryCalendarState,
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

describe("CalendarWorkspacePage sources", () => {
  const { renderPage } = installCalendarPageHarness();

  it("shows an error toast when set-primary returns persisted: false", async () => {
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
              source_id: "source-g1",
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
              last_synced_at: "2026-03-01T10:00:00Z",
              last_success_at: "2026-03-01T10:00:00Z",
              last_error_at: null,
              last_error: null,
              full_sync_required: false,
              sync_state: "fresh",
              staleness_ms: 900,
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

    const setPrimaryMutate = vi.fn((_body, options) => {
      options?.onSuccess?.({
        data: {
          old_calendar_id: null,
          new_calendar_id: "work",
          persisted: false,
        },
        meta: {},
      });
    });
    setPrimaryCalendarState({ mutate: setPrimaryMutate });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    await act(async () => {
      const configureButton = document.querySelector(
        'button[aria-label="Configure sources"]',
      ) as HTMLButtonElement;
      configureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const setPrimaryButton = findButton("Set as primary");
    expect(setPrimaryButton).toBeDefined();
    await act(async () => {
      setPrimaryButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(setPrimaryMutate).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("not persisted"),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast when set-primary persists", async () => {
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
              source_id: "source-g1",
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
              last_synced_at: "2026-03-01T10:00:00Z",
              last_success_at: "2026-03-01T10:00:00Z",
              last_error_at: null,
              last_error: null,
              full_sync_required: false,
              sync_state: "fresh",
              staleness_ms: 900,
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

    const setPrimaryMutate = vi.fn((_body, options) => {
      options?.onSuccess?.({
        data: {
          old_calendar_id: null,
          new_calendar_id: "work",
          persisted: true,
        },
        meta: {},
      });
    });
    setPrimaryCalendarState({ mutate: setPrimaryMutate });

    renderPage("/calendar?view=user&range=week&anchor=2026-03-01");

    await act(async () => {
      const configureButton = document.querySelector(
        'button[aria-label="Configure sources"]',
      ) as HTMLButtonElement;
      configureButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const setPrimaryButton = findButton("Set as primary");
    await act(async () => {
      setPrimaryButton?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Primary calendar updated");
    expect(toast.error).not.toHaveBeenCalled();
  });

  describe("sync recovery cockpit", () => {
    function setSourceMeta(errorKind: string, lastError: string | null) {
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
                source_id: "source-g1",
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
                last_synced_at: "2026-03-01T10:00:00Z",
                last_success_at: lastError ? null : "2026-03-01T10:00:00Z",
                last_error_at: lastError ? "2026-03-01T10:05:00Z" : null,
                last_error: lastError,
                full_sync_required: false,
                sync_state: lastError ? "failed" : "fresh",
                staleness_ms: 900,
                error_kind: errorKind,
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
    }

    async function openSourcesDialog() {
      renderPage("/calendar?view=user&range=week&anchor=2026-03-01");
      await act(async () => {
        const configureButton = document.querySelector(
          'button[aria-label="Configure sources"]',
        ) as HTMLButtonElement;
        configureButton.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });
    }

    it("Recover button triggers a full re-sync (full=true)", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      const syncMutateAsync = vi.fn().mockResolvedValue({
        data: {
          scope: "source",
          requested_source_key: "google:work",
          requested_source_id: null,
          full: true,
          targets: [
            {
              butler_name: "general",
              source_key: "google:work",
              calendar_id: "work",
              status: "sync_completed",
              detail: null,
              error: null,
              recovery: true,
            },
          ],
          triggered_count: 1,
        },
        meta: {},
      });
      setSyncState({ mutateAsync: syncMutateAsync });

      await openSourcesDialog();

      const recoverButton = findButton("Recover");
      expect(recoverButton).toBeDefined();
      await act(async () => {
        recoverButton?.dispatchEvent(
          new MouseEvent("click", { bubbles: true }),
        );
        await flush();
      });

      expect(syncMutateAsync).toHaveBeenCalledWith(
        expect.objectContaining({ source_key: "google:work", full: true }),
      );
    });

    it("shows a Reconnect CTA for a token-expired source", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("token_expired", "sync token expired (410 Gone)");
      setSyncState();

      await openSourcesDialog();

      const reconnect = Array.from(document.querySelectorAll("a")).find(
        (anchor) => anchor.textContent?.trim() === "Reconnect",
      );
      expect(reconnect).toBeDefined();
    });

    it("hides the Reconnect CTA for a healthy source", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();

      await openSourcesDialog();

      const reconnect = Array.from(document.querySelectorAll("a")).find(
        (anchor) => anchor.textContent?.trim() === "Reconnect",
      );
      expect(reconnect).toBeUndefined();
    });

    it("toggling a source persists the change via POST /api/calendar/sources", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      const toggleMutate = vi.fn();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: toggleMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      const checkbox = document.querySelector(
        '[aria-label^="Toggle "]',
      ) as HTMLElement | null;
      expect(checkbox).not.toBeNull();
      await act(async () => {
        checkbox?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      expect(toggleMutate).toHaveBeenCalledWith(
        expect.objectContaining({
          butler: "general",
          source_key: "google:work",
          enabled: false,
        }),
        expect.anything(),
      );
    });

    it("Hide control removes a source from the read filter without toggling sync", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      const toggleMutate = vi.fn();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: toggleMutate,
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      // Before hiding: no source filter is applied (all sources visible).
      expect(latestWorkspaceParams()?.sources).toBeUndefined();

      const hideButton = findButton("Hide");
      expect(hideButton).toBeDefined();
      await act(async () => {
        hideButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      // The read filter now excludes the hidden source...
      expect(latestWorkspaceParams()?.sources).toEqual([]);
      // ...without persisting any change to the sync toggle.
      expect(toggleMutate).not.toHaveBeenCalled();
      // The control flips to "Show" so it can be un-hidden.
      expect(findButton("Show")).toBeDefined();
      // The masthead reflects the view-only hidden count.
      expect(document.body.textContent).toContain("1 hidden");
    });

    it("disabling sync does not hide the source from the view", async () => {
      setButlerWorkspaceFixtures();
      setSourceMeta("none", null);
      setSyncState();
      vi.mocked(useToggleCalendarSource).mockReturnValue({
        mutate: vi.fn(),
        isPending: false,
      } as unknown as ReturnType<typeof useToggleCalendarSource>);

      await openSourcesDialog();

      const checkbox = document.querySelector(
        '[aria-label^="Toggle "]',
      ) as HTMLElement | null;
      expect(checkbox).not.toBeNull();
      await act(async () => {
        checkbox?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await flush();
      });

      // Toggling the persisted sync state must not affect the view read filter
      // nor the hidden count — the two concerns are decoupled.
      expect(latestWorkspaceParams()?.sources).toBeUndefined();
      expect(document.body.textContent).not.toContain("1 hidden");
    });
  });
});
