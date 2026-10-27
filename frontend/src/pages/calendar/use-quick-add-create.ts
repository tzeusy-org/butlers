/**
 * Confirm step of the natural-language quick-add bar: the dashboard's
 * retained provider-event create action. It sends one `create` through
 * `POST /api/calendar/workspace/user-events` with a fresh `request_id`.
 */

import { toast } from "sonner";

import type { CalendarWorkspaceWritableCalendar, QuickAddDraft } from "@/api/types.ts";
import { useMutateCalendarWorkspaceUserEvent } from "@/hooks/use-calendar-workspace";
import {
  buildRequestId,
  calendarMutationErrorMessage,
  isCalendarMutationOk,
  maybeText,
} from "@/pages/calendar/mutation-result";

interface QuickAddCreateArgs {
  submittableCalendars: CalendarWorkspaceWritableCalendar[];
  selectedSourceKey: string;
  defaultTimezone: string;
}

export function useQuickAddCreate({
  submittableCalendars,
  selectedSourceKey,
  defaultTimezone,
}: QuickAddCreateArgs) {
  const userEventMutation = useMutateCalendarWorkspaceUserEvent();

  // The owning butler / calendar: the selected source if it is writable, else
  // the first submittable calendar.
  const targetCalendar =
    (selectedSourceKey !== "all" &&
      submittableCalendars.find((c) => c.source_key === selectedSourceKey)) ||
    submittableCalendars[0];

  async function confirmQuickAddDraft(draft: QuickAddDraft) {
    if (!targetCalendar) {
      toast.error("No writable calendar sources are available for user events.");
      return;
    }
    const title = draft.title.trim();
    if (!title) {
      toast.error("Title is required.");
      return;
    }
    const butlerName = targetCalendar.butler_name;
    if (!butlerName) {
      toast.error("Could not resolve owning butler for this calendar source.");
      return;
    }

    const payload: Record<string, unknown> = {
      title,
      timezone: defaultTimezone,
      all_day: draft.all_day,
    };
    if (draft.start_at) payload.start_at = draft.start_at;
    if (draft.end_at) payload.end_at = draft.end_at;
    if (draft.location) payload.location = draft.location;
    if (draft.description) payload.description = draft.description;
    if (targetCalendar.calendar_id) payload.calendar_id = targetCalendar.calendar_id;

    try {
      const response = await userEventMutation.mutateAsync({
        butler_name: butlerName,
        action: "create",
        request_id: buildRequestId("create"),
        payload,
      });
      const result = response.data.result;
      const status = maybeText(result?.status);
      if (status === "conflict") {
        const overlaps = response.data.conflicts?.length ?? 0;
        toast.error(
          `That time overlaps ${overlaps === 1 ? "another event" : `${overlaps} events`}. Adjust the phrase and try again.`,
        );
        return;
      }
      if (!isCalendarMutationOk(result)) {
        toast.error(
          `Failed to create event: ${calendarMutationErrorMessage(result, "Failed to create calendar event.")}`,
        );
        return;
      }
      toast.success(status ? `Created event (${status}).` : "Created calendar event.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to add calendar event.");
    }
  }

  return {
    confirmQuickAddDraft,
    quickAddButlerName: targetCalendar ? (targetCalendar.butler_name ?? undefined) : undefined,
    canCreateUserEvents: submittableCalendars.length > 0,
  };
}
