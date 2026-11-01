/**
 * Create/edit dialog for butler-lane events (reminders and scheduled tasks),
 * including recurrence, until-boundary and the live recurrence preview.
 */

import { DraftNotice } from "@/components/ui/draft-notice";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Input } from "@/components/ui/input";
import { formatEventTime } from "@/lib/calendar-grid";
import { CommitButton, PillButton } from "@/pages/calendar/atoms";
import type { ButlerEventEditor } from "@/pages/calendar/use-butler-event-editor";
import {
  FIELD_SELECT_CLASS,
  formatLaneTitle,
  type ButlerEventKind,
  type RecurrenceFrequency,
} from "@/pages/calendar/workspace-model";

export function ButlerEventDialog({
  editor,
  availableButlers,
  defaultTimezone,
}: {
  editor: ButlerEventEditor;
  availableButlers: string[];
  defaultTimezone: string;
}) {
  const {
    butlerMutation,
    butlerEventDialogOpen,
    butlerEventDialogMode,
    butlerEventDraft,
    setButlerEventDraft,
    butlerDraft,
    recurrencePreview,
    recurrencePreviewRequest,
    recurrencePreviewData,
    closeButlerEventDialog,
    handleSaveButlerEvent,
  } = editor;

  return (
    <Dialog
      open={butlerEventDialogOpen}
      onOpenChange={closeButlerEventDialog}
    >
      <DialogContent className="sm:max-w-lg">
        <DraftNotice session={butlerDraft.session} />
        <DialogHeader>
          <DialogTitle>
            {butlerEventDialogMode === "create"
              ? "Create butler event"
              : "Edit butler event"}
          </DialogTitle>
          <DialogDescription>
            Create or update schedule/reminder events in butler lanes,
            including recurring-until boundaries.
          </DialogDescription>
        </DialogHeader>

        {butlerEventDraft ? (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label
                  htmlFor="calendar-butler-name"
                  className="text-sm font-medium"
                >
                  Butler lane
                </label>
                <select
                  id="calendar-butler-name"
                  value={butlerEventDraft.butlerName}
                  disabled={butlerEventDialogMode === "edit"}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            butlerName: event.target.value,
                          }
                        : current,
                    )
                  }
                  className={FIELD_SELECT_CLASS}
                >
                  {availableButlers.length === 0 ? (
                    <option value="">No butlers available</option>
                  ) : null}
                  {availableButlers.map((butlerName) => (
                    <option key={butlerName} value={butlerName}>
                      {formatLaneTitle(butlerName)}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-2">
                <label
                  htmlFor="calendar-event-kind"
                  className="text-sm font-medium"
                >
                  Event type
                </label>
                <select
                  id="calendar-event-kind"
                  value={butlerEventDraft.eventKind}
                  disabled={butlerEventDialogMode === "edit"}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            eventKind: event.target.value as ButlerEventKind,
                          }
                        : current,
                    )
                  }
                  className={FIELD_SELECT_CLASS}
                >
                  <option value="butler_reminder">Reminder</option>
                  <option value="scheduled_task">Scheduled task</option>
                </select>
              </div>
            </div>

            <div className="space-y-2">
              <label
                htmlFor="calendar-event-title"
                className="text-sm font-medium"
              >
                Title
              </label>
              <Input
                id="calendar-event-title"
                value={butlerEventDraft.title}
                onChange={(event) =>
                  setButlerEventDraft((current) =>
                    current
                      ? {
                          ...current,
                          title: event.target.value,
                        }
                      : current,
                  )
                }
                placeholder="e.g. Daily medication"
                disabled={butlerMutation.isPending}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label
                  htmlFor="calendar-start-at"
                  className="text-sm font-medium"
                >
                  Start
                </label>
                <Input
                  id="calendar-start-at"
                  type="datetime-local"
                  value={butlerEventDraft.startAtLocal}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            startAtLocal: event.target.value,
                          }
                        : current,
                    )
                  }
                  disabled={butlerMutation.isPending}
                />
              </div>
              <div className="space-y-2">
                <label
                  htmlFor="calendar-timezone"
                  className="text-sm font-medium"
                >
                  Timezone
                </label>
                <Input
                  id="calendar-timezone"
                  value={butlerEventDraft.timezone}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            timezone: event.target.value,
                          }
                        : current,
                    )
                  }
                  disabled={butlerMutation.isPending}
                />
              </div>
            </div>

            {butlerEventDraft.eventKind === "scheduled_task" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <label
                    htmlFor="calendar-end-at"
                    className="text-sm font-medium"
                  >
                    End
                  </label>
                  <Input
                    id="calendar-end-at"
                    type="datetime-local"
                    value={butlerEventDraft.endAtLocal}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              endAtLocal: event.target.value,
                            }
                          : current,
                      )
                    }
                    disabled={butlerMutation.isPending}
                  />
                </div>
                <div className="space-y-2">
                  <label
                    htmlFor="calendar-cron"
                    className="text-sm font-medium"
                  >
                    Cron (optional)
                  </label>
                  <Input
                    id="calendar-cron"
                    value={butlerEventDraft.cron}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              cron: event.target.value,
                            }
                          : current,
                      )
                    }
                    placeholder="0 9 * * *"
                    disabled={butlerMutation.isPending}
                  />
                </div>
              </div>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <label
                  htmlFor="calendar-frequency"
                  className="text-sm font-medium"
                >
                  Recurrence
                </label>
                <select
                  id="calendar-frequency"
                  value={butlerEventDraft.recurrenceFrequency}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            recurrenceFrequency: event.target
                              .value as RecurrenceFrequency,
                          }
                        : current,
                    )
                  }
                  className={FIELD_SELECT_CLASS}
                  disabled={butlerMutation.isPending}
                >
                  <option value="NONE">None</option>
                  <option value="DAILY">Daily</option>
                  <option value="WEEKLY">Weekly</option>
                  <option value="MONTHLY">Monthly</option>
                  <option value="YEARLY">Yearly</option>
                </select>
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-2 pt-7 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={butlerEventDraft.hasUntilAt}
                    onChange={(event) =>
                      setButlerEventDraft((current) =>
                        current
                          ? {
                              ...current,
                              hasUntilAt: event.target.checked,
                              untilAtLocal: event.target.checked
                                ? current.untilAtLocal || current.startAtLocal
                                : "",
                            }
                          : current,
                      )
                    }
                    disabled={butlerMutation.isPending}
                  />
                  Set until boundary
                </label>
              </div>
            </div>

            {butlerEventDraft.hasUntilAt ? (
              <div className="space-y-2">
                <label
                  htmlFor="calendar-until-at"
                  className="text-sm font-medium"
                >
                  Until
                </label>
                <Input
                  id="calendar-until-at"
                  type="datetime-local"
                  value={butlerEventDraft.untilAtLocal}
                  onChange={(event) =>
                    setButlerEventDraft((current) =>
                      current
                        ? {
                            ...current,
                            untilAtLocal: event.target.value,
                          }
                        : current,
                    )
                  }
                  disabled={butlerMutation.isPending}
                />
              </div>
            ) : null}

            {recurrencePreviewRequest ? (
              <div
                data-testid="recurrence-preview"
                className="space-y-1.5 rounded-[3px] border border-[var(--border)] p-3"
              >
                <Eyebrow>
                  Preview ({Intl.DateTimeFormat().resolvedOptions().timeZone})
                </Eyebrow>
                {recurrencePreview.isError ? (
                  <p className="text-sm text-[var(--mfg)]">
                    Couldn’t preview this recurrence. Check the rule or cron
                    expression.
                  </p>
                ) : recurrencePreviewData ? (
                  recurrencePreviewData.occurrences.length === 0 ? (
                    <p className="text-sm text-[var(--mfg)]">
                      No occurrences in the next 90 days.
                    </p>
                  ) : (
                    <>
                      <ul className="space-y-0.5 text-sm tabular-nums text-fg">
                        {recurrencePreviewData.occurrences.map((iso) => (
                          <li key={iso}>
                            {formatEventTime(
                              iso,
                              defaultTimezone,
                              "EEE, MMM d · h:mm a",
                            )}
                          </li>
                        ))}
                      </ul>
                      {recurrencePreviewData.more_count > 0 ? (
                        <p
                          data-testid="recurrence-preview-more"
                          className="font-serif text-[13px] italic text-[var(--mfg)]"
                        >
                          +{recurrencePreviewData.more_count} more in 90 days
                        </p>
                      ) : null}
                      {recurrencePreviewData.notes.length > 0 ? (
                        <div className="space-y-0.5 pt-1">
                          {recurrencePreviewData.notes.map((note) => (
                            <p
                              key={note}
                              data-testid="recurrence-preview-note"
                              className="text-xs italic text-[var(--mfg)]"
                            >
                              {note}
                            </p>
                          ))}
                        </div>
                      ) : null}
                    </>
                  )
                ) : (
                  <p className="text-sm text-[var(--mfg)]">Calculating…</p>
                )}
              </div>
            ) : null}
          </div>
        ) : null}

        <DialogFooter>
          <PillButton
            onClick={() => closeButlerEventDialog(false)}
            disabled={butlerMutation.isPending}
          >
            Cancel
          </PillButton>
          <CommitButton
            onClick={handleSaveButlerEvent}
            disabled={butlerMutation.isPending || !butlerEventDraft}
          >
            {butlerMutation.isPending
              ? butlerEventDialogMode === "create"
                ? "Creating..."
                : "Saving..."
              : butlerEventDialogMode === "create"
                ? "Create event"
                : "Save changes"}
          </CommitButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
