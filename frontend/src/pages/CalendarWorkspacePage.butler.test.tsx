// @vitest-environment jsdom

import { describe, expect, it, vi } from "vitest";
import { act } from "react";
import { toast } from "sonner";

import { setButlerWorkspaceFixtures } from "@/test/calendar-page/butler-fixtures";
import {
  findButton,
  findDialogByTitle,
  installCalendarPageHarness,
} from "@/test/calendar-page/harness";
import {
  flush,
  mutateButlerEvent,
  previewMutate,
  setInputValue,
} from "@/test/calendar-page/state";

vi.mock("@/hooks/use-meeting-debriefs", async () =>
  (await import("@/test/calendar-page/mocks")).meetingDebriefsMock(),
);
vi.mock("@/hooks/use-calendar-workspace", async () =>
  (await import("@/test/calendar-page/mocks")).calendarWorkspaceHookMock(),
);
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@/hooks/use-debounce", () => ({
  useDebounce: <T,>(value: T) => value,
}));

describe("CalendarWorkspacePage butler lanes", () => {
  const { container, renderPage } = installCalendarPageHarness();

  it("renders butler lanes grouped with lane metadata", () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    expect(container.textContent).toContain("General lane");
    expect(container.textContent).toContain("Health lane");
    expect(container.textContent).toContain("Daily prep");
    expect(container.textContent).toContain("Hydration check");
  });

  it("creates butler event through workspace mutation endpoint", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const createButton = findButton("Create butler event");
    expect(createButton).toBeDefined();
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create butler event");
    const titleInput = dialog?.querySelector(
      "#calendar-event-title",
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Stretch break");
      await flush();
    });

    const saveButton = Array.from(
      dialog?.querySelectorAll("button") ?? [],
    ).find(
      (button) => button.textContent?.trim() === "Create event",
    ) as HTMLButtonElement;
    await act(async () => {
      saveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [payload] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(payload).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "create",
        request_id: expect.stringMatching(/^calendar-create-/),
        payload: expect.objectContaining({
          title: "Stretch break",
          source_hint: "butler_reminder",
        }),
      }),
    );
  });

  it("renders a live recurrence preview strip when a recurrence is set", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const createButton = findButton("Create butler event");
    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const dialog = findDialogByTitle("Create butler event");
    expect(dialog).toBeDefined();

    // No recurrence yet -> no preview strip.
    expect(
      dialog?.querySelector('[data-testid="recurrence-preview"]'),
    ).toBeNull();

    const frequencySelect = dialog?.querySelector(
      "#calendar-frequency",
    ) as HTMLSelectElement;
    expect(frequencySelect).toBeDefined();
    await act(async () => {
      frequencySelect.value = "WEEKLY";
      frequencySelect.dispatchEvent(new Event("change", { bubbles: true }));
      await flush();
    });

    // Preview fired with an RRULE carrying the chosen frequency.
    expect(previewMutate).toHaveBeenCalled();
    const [previewBody] = previewMutate.mock.calls.at(-1) ?? [];
    expect(previewBody).toEqual(
      expect.objectContaining({
        rrule: expect.stringContaining("FREQ=WEEKLY"),
        limit: 6,
      }),
    );

    // Strip renders projected dates, the +N sentinel, and the lossy note.
    const strip = dialog?.querySelector('[data-testid="recurrence-preview"]');
    expect(strip).not.toBeNull();
    expect(
      strip?.querySelector('[data-testid="recurrence-preview-more"]')
        ?.textContent,
    ).toContain("+7 more in 90 days");
    expect(
      strip?.querySelector('[data-testid="recurrence-preview-note"]')
        ?.textContent,
    ).toContain("INTERVAL=2");
  });

  it("updates butler event title via detail panel inline edit", async () => {
    setButlerWorkspaceFixtures();
    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    // Butler lane "Edit" button opens the detail panel
    const editButton = findButton("Edit");
    expect(editButton).toBeDefined();
    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const panel = container.querySelector('[data-testid="entry-detail-panel"]');
    expect(panel).toBeDefined();

    const titleInput = panel?.querySelector(
      '[data-testid="detail-title-input"]',
    ) as HTMLInputElement;
    expect(titleInput).toBeDefined();

    await act(async () => {
      setInputValue(titleInput, "Updated daily prep");
      await flush();
    });
    await act(async () => {
      titleInput.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [payload] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(payload).toEqual(
      expect.objectContaining({
        butler_name: "general",
        action: "update",
        request_id: expect.stringMatching(/^detail-update-/),
        payload: expect.objectContaining({
          event_id: "sched-1",
          source_hint: "scheduled_task",
          title: "Updated daily prep",
        }),
      }),
    );
  });

  it("shows an error toast (not success) when a butler delete soft-fails", async () => {
    setButlerWorkspaceFixtures();
    // Invoke onSuccess with a soft-failed envelope, as react-query would on HTTP 200.
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "delete",
          tool_name: "calendar_delete_butler_event",
          request_id: "req-del",
          result: { status: "not_found", error: "event no longer exists" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const deleteButton = findButton("Delete");
    expect(deleteButton).toBeDefined();
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith(
      expect.stringContaining("event no longer exists"),
    );
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("shows a success toast when a butler delete genuinely succeeds", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "delete",
          tool_name: "calendar_delete_butler_event",
          request_id: "req-del",
          result: { status: "deleted" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=week&anchor=2026-03-01");

    const deleteButton = findButton("Delete");
    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(toast.success).toHaveBeenCalledWith("Event deleted");
    expect(toast.error).not.toHaveBeenCalled();
  });

  it("snoozes a butler event to a preset time via the snooze menu", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "snooze",
          tool_name: "calendar_update_butler_event",
          request_id: "req-snooze",
          result: { status: "updated" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    const snoozeButton = document.querySelector(
      '[data-testid="butler-snooze-button"]',
    ) as HTMLButtonElement;
    expect(snoozeButton).toBeTruthy();
    await act(async () => {
      snoozeButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    const preset = document.querySelector(
      '[data-testid="butler-snooze-preset-1 hour"]',
    ) as HTMLButtonElement;
    expect(preset).toBeTruthy();
    await act(async () => {
      preset.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [body] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(body.action).toBe("snooze");
    expect(typeof body.payload.due_at).toBe("string");
    expect(body.payload.event_id).toBeTruthy();
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("Snoozed to"),
    );
  });

  it("dismisses a due reminder from the grid", async () => {
    setButlerWorkspaceFixtures();
    mutateButlerEvent.mockImplementation((_payload, options) => {
      options?.onSuccess?.({
        data: {
          action: "dismiss",
          tool_name: "reminder_dismiss",
          request_id: "req-dismiss",
          result: { status: "dismissed" },
          projection_version: null,
          staleness_ms: null,
          projection_freshness: null,
        },
        meta: {},
      });
    });

    renderPage("/calendar?view=butler&range=list&anchor=2026-03-01");

    const dismissButton = document.querySelector(
      '[data-testid="butler-dismiss-button"]',
    ) as HTMLButtonElement;
    expect(dismissButton).toBeTruthy();
    await act(async () => {
      dismissButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await flush();
    });

    expect(mutateButlerEvent).toHaveBeenCalled();
    const [body] = mutateButlerEvent.mock.calls.at(-1) ?? [];
    expect(body.action).toBe("dismiss");
    // reminder_dismiss targets the reminder id; no extra payload keys.
    expect(body.payload).toEqual({ event_id: "rem-1" });
    expect(toast.success).toHaveBeenCalledWith("Reminder dismissed");
  });
});
