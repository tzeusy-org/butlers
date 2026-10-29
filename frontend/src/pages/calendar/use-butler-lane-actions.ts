/**
 * Butler-lane row actions (pause/resume, delete, snooze, dismiss) over the
 * shared butler-event mutation, each reporting its own outcome by toast.
 */

import { toast } from "sonner";

import type { UnifiedCalendarEntry } from "@/api/types.ts";
import type { useMutateCalendarWorkspaceButlerEvent } from "@/hooks/use-calendar-workspace";
import { formatEventTime } from "@/lib/calendar-grid";
import {
  calendarMutationErrorMessage,
  isCalendarMutationOk,
} from "@/pages/calendar/mutation-result";
import {
  isPausedEntry,
  newButlerRequestId,
  resolveButlerEventTarget,
} from "@/pages/calendar/workspace-model";

export function useButlerLaneActions(
  butlerMutation: ReturnType<typeof useMutateCalendarWorkspaceButlerEvent>,
  defaultTimezone: string,
) {
  function handleButlerToggle(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for toggle");
      return;
    }

    const enabled = isPausedEntry(entry);
    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "toggle",
        request_id: newButlerRequestId("toggle"),
        payload: {
          event_id: target.eventId,
          enabled,
          source_hint: target.sourceHint,
        },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Toggle failed",
            );
            toast.error(`${enabled ? "Resume" : "Pause"} failed: ${detail}`);
            return;
          }
          toast.success(enabled ? "Event resumed" : "Event paused");
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Toggle failed");
        },
      },
    );
  }

  function handleButlerDelete(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for delete");
      return;
    }

    if (!globalThis.confirm(`Delete "${entry.title}"?`)) {
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "delete",
        request_id: newButlerRequestId("delete"),
        payload: {
          event_id: target.eventId,
          source_hint: target.sourceHint,
        },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Delete failed",
            );
            toast.error(`Delete failed: ${detail}`);
            return;
          }
          toast.success("Event deleted");
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Delete failed");
        },
      },
    );
  }

  function handleButlerSnooze(entry: UnifiedCalendarEntry, iso: string) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for snooze");
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "snooze",
        request_id: newButlerRequestId("snooze"),
        payload: {
          event_id: target.eventId,
          source_hint: target.sourceHint,
          due_at: iso,
        },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Snooze failed",
            );
            toast.error(`Snooze failed: ${detail}`);
            return;
          }
          toast.success(
            `Snoozed to ${formatEventTime(iso, defaultTimezone, "MMM d, HH:mm")}`,
          );
        },
        onError: (error) => {
          toast.error(error instanceof Error ? error.message : "Snooze failed");
        },
      },
    );
  }

  function handleButlerDismiss(entry: UnifiedCalendarEntry) {
    const target = resolveButlerEventTarget(entry);
    if (!target || !entry.butler_name) {
      toast.error("Missing butler event linkage for dismiss");
      return;
    }

    butlerMutation.mutate(
      {
        butler_name: entry.butler_name,
        action: "dismiss",
        request_id: newButlerRequestId("dismiss"),
        payload: { event_id: target.eventId },
      },
      {
        onSuccess: (response) => {
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Dismiss failed",
            );
            toast.error(`Dismiss failed: ${detail}`);
            return;
          }
          toast.success("Reminder dismissed");
        },
        onError: (error) => {
          toast.error(
            error instanceof Error ? error.message : "Dismiss failed",
          );
        },
      },
    );
  }

  return { handleButlerToggle, handleButlerDelete, handleButlerSnooze, handleButlerDismiss };
}
