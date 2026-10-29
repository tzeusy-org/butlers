/**
 * Butler-lane event editor state: the create/edit dialog draft (with unsent-draft
 * restore), the debounced recurrence dry-run preview, and the save mutation.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { addDays } from "date-fns";
import { toast } from "sonner";

import type {
  CalendarWorkspaceButlerEventPreviewRequest,
  UnifiedCalendarEntry,
} from "@/api/types.ts";
import {
  useMutateCalendarWorkspaceButlerEvent,
  usePreviewCalendarWorkspaceButlerEvent,
} from "@/hooks/use-calendar-workspace";
import { useDebounce } from "@/hooks/use-debounce";
import { useDraft } from "@/hooks/use-draft";
import {
  calendarMutationErrorMessage,
  isCalendarMutationOk,
} from "@/pages/calendar/mutation-result";
import {
  buildRRule,
  createDefaultButlerDraft,
  newButlerRequestId,
  parseLocalDateTimeInput,
  resolveButlerEventTarget,
  type ButlerEventDialogMode,
  type ButlerEventDraft,
  type ButlerEventKind,
  type RecurrenceFrequency,
} from "@/pages/calendar/workspace-model";

export function useButlerEventEditor({
  timezone,
  availableButlers,
}: {
  /** Browser timezone: default for new drafts and for the preview request. */
  timezone: string;
  availableButlers: string[];
}) {
  const butlerMutation = useMutateCalendarWorkspaceButlerEvent();
  const [butlerEventDialogOpen, setButlerEventDialogOpen] = useState(false);
  const [butlerEventDialogMode, setButlerEventDialogMode] =
    useState<ButlerEventDialogMode>("create");
  const [butlerEventDraft, setButlerEventDraft] =
    useState<ButlerEventDraft | null>(null);
  const [editingButlerEntry, setEditingButlerEntry] =
    useState<UnifiedCalendarEntry | null>(null);
  // Live recurrence dry-run preview for the butler-event dialog. Debounced so we
  // don't fire a request on every keystroke; only runs when a recurrence is set.
  const recurrencePreview = usePreviewCalendarWorkspaceButlerEvent();
  const initialButlerForm = useRef<ButlerEventDraft | null>(null);
  // The unsent-draft baseline is the form as it was opened; useDraft takes it as a
  // render-time snapshot, so reading the ref here is intended (moved verbatim).
  /* eslint-disable react-hooks/refs */
  const butlerDraft = useDraft("calendar.butler-event", [butlerEventDraft?.butlerName || "unselected", butlerEventDialogMode, editingButlerEntry?.entry_id ?? "new"], {
    butlerName: butlerEventDraft?.butlerName ?? "", eventKind: butlerEventDraft?.eventKind ?? "scheduled_task", title: butlerEventDraft?.title ?? "", startAtLocal: butlerEventDraft?.startAtLocal ?? "", endAtLocal: butlerEventDraft?.endAtLocal ?? "", timezone: butlerEventDraft?.timezone ?? "",
    recurrenceFrequency: butlerEventDraft?.recurrenceFrequency ?? "NONE", cron: butlerEventDraft?.cron ?? "", hasUntilAt: butlerEventDraft?.hasUntilAt ?? false, untilAtLocal: butlerEventDraft?.untilAtLocal ?? "",
  }, (v) => setButlerEventDraft((current) => current ? { ...v, eventKind: v.eventKind as ButlerEventKind, recurrenceFrequency: v.recurrenceFrequency as RecurrenceFrequency } : current), butlerEventDialogOpen && !!butlerEventDraft?.butlerName, {
    butlerName: initialButlerForm.current?.butlerName ?? "", eventKind: initialButlerForm.current?.eventKind ?? "scheduled_task", title: initialButlerForm.current?.title ?? "", startAtLocal: initialButlerForm.current?.startAtLocal ?? "", endAtLocal: initialButlerForm.current?.endAtLocal ?? "", timezone: initialButlerForm.current?.timezone ?? "",
    recurrenceFrequency: initialButlerForm.current?.recurrenceFrequency ?? "NONE", cron: initialButlerForm.current?.cron ?? "", hasUntilAt: initialButlerForm.current?.hasUntilAt ?? false, untilAtLocal: initialButlerForm.current?.untilAtLocal ?? "",
  });
  /* eslint-enable react-hooks/refs */

  const debouncedButlerDraft = useDebounce(butlerEventDraft, 400);
  const recurrencePreviewRequest =
    useMemo<CalendarWorkspaceButlerEventPreviewRequest | null>(() => {
      if (!debouncedButlerDraft) return null;
      const startAt = parseLocalDateTimeInput(
        debouncedButlerDraft.startAtLocal,
      );
      if (!startAt) return null;
      const untilAt = debouncedButlerDraft.hasUntilAt
        ? parseLocalDateTimeInput(debouncedButlerDraft.untilAtLocal)
        : null;
      const tz = debouncedButlerDraft.timezone.trim() || timezone;
      if (debouncedButlerDraft.recurrenceFrequency !== "NONE") {
        return {
          rrule: buildRRule(debouncedButlerDraft.recurrenceFrequency, untilAt),
          start_at: startAt.toISOString(),
          timezone: tz,
          limit: 6,
        };
      }
      if (
        debouncedButlerDraft.eventKind === "scheduled_task" &&
        debouncedButlerDraft.cron.trim()
      ) {
        return {
          cron: debouncedButlerDraft.cron.trim(),
          start_at: startAt.toISOString(),
          until_at: untilAt ? untilAt.toISOString() : null,
          timezone: tz,
          limit: 6,
        };
      }
      return null;
    }, [debouncedButlerDraft, timezone]);
  const { mutate: runRecurrencePreview, reset: resetRecurrencePreview } =
    recurrencePreview;
  useEffect(() => {
    if (!butlerEventDialogOpen || !recurrencePreviewRequest) {
      resetRecurrencePreview();
      return;
    }
    runRecurrencePreview(recurrencePreviewRequest);
  }, [
    recurrencePreviewRequest,
    butlerEventDialogOpen,
    runRecurrencePreview,
    resetRecurrencePreview,
  ]);
  const recurrencePreviewData = recurrencePreview.data?.data ?? null;
  function openButlerCreateDialog(initialButler?: string) {
    const butlerName = initialButler ?? availableButlers[0] ?? "";
    setEditingButlerEntry(null);
    setButlerEventDialogMode("create");
    const initialForm = createDefaultButlerDraft(timezone, butlerName);
    initialButlerForm.current = initialForm;
    setButlerEventDraft(initialForm);
    setButlerEventDialogOpen(true);
  }

  function closeButlerEventDialog(open: boolean) {
    if (open) { setButlerEventDialogOpen(true); return; }
    butlerDraft.dismiss(() => { setButlerEventDialogOpen(false); setEditingButlerEntry(null); setButlerEventDraft(null); });
  }
  async function handleSaveButlerEvent() {
    if (!butlerEventDraft) {
      return;
    }

    const startAt = parseLocalDateTimeInput(butlerEventDraft.startAtLocal);
    const endAt = parseLocalDateTimeInput(butlerEventDraft.endAtLocal);
    const untilAt = butlerEventDraft.hasUntilAt
      ? parseLocalDateTimeInput(butlerEventDraft.untilAtLocal)
      : null;

    if (!butlerEventDraft.butlerName.trim()) {
      toast.error("Select a butler lane before saving");
      return;
    }
    if (!butlerEventDraft.title.trim()) {
      toast.error("Title is required");
      return;
    }
    if (!startAt) {
      toast.error("Start time is required");
      return;
    }
    if (butlerEventDraft.eventKind === "scheduled_task") {
      if (!endAt) {
        toast.error("End time is required for scheduled events");
        return;
      }
      if (endAt <= startAt) {
        toast.error("End time must be after start time");
        return;
      }
      if (
        butlerEventDraft.recurrenceFrequency === "NONE" &&
        !butlerEventDraft.cron.trim()
      ) {
        toast.error(
          "Scheduled events require either a recurrence frequency or cron expression",
        );
        return;
      }
    }
    if (butlerEventDraft.hasUntilAt && !untilAt) {
      toast.error("Until boundary is invalid");
      return;
    }

    const recurrenceRule = buildRRule(
      butlerEventDraft.recurrenceFrequency,
      untilAt,
    );
    const payload: Record<string, unknown> = {
      title: butlerEventDraft.title.trim(),
      start_at: startAt.toISOString(),
      timezone: butlerEventDraft.timezone.trim() || timezone,
      source_hint: butlerEventDraft.eventKind,
    };

    if (butlerEventDraft.eventKind === "scheduled_task") {
      payload.end_at = (endAt ?? addDays(startAt, 0)).toISOString();
      if (!recurrenceRule && butlerEventDraft.cron.trim()) {
        payload.cron = butlerEventDraft.cron.trim();
      }
    }

    if (recurrenceRule) {
      payload.recurrence_rule = recurrenceRule;
    }
    if (butlerEventDraft.hasUntilAt && untilAt) {
      payload.until_at = untilAt.toISOString();
    }

    const action: "create" | "update" =
      butlerEventDialogMode === "create" ? "create" : "update";
    if (action === "update") {
      if (!editingButlerEntry) {
        toast.error("Missing event context for update");
        return;
      }
      const target = resolveButlerEventTarget(editingButlerEntry);
      if (!target) {
        toast.error("Missing butler event linkage for update");
        return;
      }
      payload.event_id = target.eventId;
      payload.source_hint = target.sourceHint;
    }

    const submission = butlerDraft.capture();
    const preparation = butlerDraft.prepare();
    if (preparation instanceof Promise ? !await preparation : !preparation) return;
    butlerMutation.mutate(
      {
        butler_name: butlerEventDraft.butlerName,
        action,
        request_id: newButlerRequestId(action),
        payload,
      },
      {
        onSuccess: (response) => {
          if (!butlerDraft.current(submission)) {
            if (isCalendarMutationOk(response.data.result)) void butlerDraft.submitted(submission, () => {});
            return;
          }
          const mutationResult = response.data.result;
          if (!isCalendarMutationOk(mutationResult)) {
            const detail = calendarMutationErrorMessage(
              mutationResult,
              "Event mutation failed",
            );
            toast.error(
              action === "create"
                ? `Failed to create butler event: ${detail}`
                : `Failed to update butler event: ${detail}`,
            );
            return;
          }
          toast.success(
            action === "create"
              ? "Butler event created"
              : "Butler event updated",
          );
          void butlerDraft.submitted(submission, () => { setButlerEventDialogOpen(false); setEditingButlerEntry(null); setButlerEventDraft(null); });
        },
        onSettled: butlerDraft.finish,
        onError: (error) => {
          if (!butlerDraft.current(submission)) return;
          toast.error(
            error instanceof Error ? error.message : "Event mutation failed",
          );
        },
      },
    );
  }

  return {
    butlerMutation,
    butlerEventDialogOpen,
    butlerEventDialogMode,
    butlerEventDraft,
    setButlerEventDraft,
    butlerDraft,
    recurrencePreview,
    recurrencePreviewRequest,
    recurrencePreviewData,
    openButlerCreateDialog,
    closeButlerEventDialog,
    handleSaveButlerEvent,
  };
}

export type ButlerEventEditor = ReturnType<typeof useButlerEventEditor>;
