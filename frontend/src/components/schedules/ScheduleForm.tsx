import { useState } from "react";

import type {
  Schedule,
  ScheduleCreate,
  ScheduleDispatchMode,
  ScheduleJobArgs,
} from "@/api/types.ts";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ScheduleFormProps {
  /** When set, the form operates in edit mode and pre-fills values. */
  schedule?: Schedule | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called when the user submits the form. */
  onSubmit: (values: ScheduleCreate) => void;
  /** Whether the mutation is currently in flight. */
  isSubmitting?: boolean;
  /** Optional error message to display. */
  error?: string | null;
}

export type ScheduleFormValues = ScheduleCreate;

// ---------------------------------------------------------------------------
// Inner form (remounted via key to reset state)
// ---------------------------------------------------------------------------

function resolveInitialDispatchMode(schedule?: Schedule | null): ScheduleDispatchMode {
  if (schedule?.dispatch_mode === "job" || schedule?.dispatch_mode === "prompt") {
    return schedule.dispatch_mode;
  }
  return schedule?.job_name ? "job" : "prompt";
}

function parseJobArgs(raw: string): { value?: ScheduleJobArgs; error: string | null } {
  const trimmed = raw.trim();
  if (!trimmed) return { error: null };

  try {
    const parsed = JSON.parse(trimmed);
    if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
      return { error: "Job args must be a JSON object" };
    }
    return { value: parsed as ScheduleJobArgs, error: null };
  } catch {
    return { error: "Job args must be valid JSON" };
  }
}

function ScheduleFormFields({
  schedule,
  onSubmit,
  onCancel,
  isSubmitting,
  error,
}: {
  schedule?: Schedule | null;
  onSubmit: (values: ScheduleFormValues) => void;
  onCancel: () => void;
  isSubmitting: boolean;
  error: string | null;
}) {
  const isEdit = !!schedule;
  const initialDispatchMode = resolveInitialDispatchMode(schedule);

  const [name, setName] = useState(schedule?.name ?? "");
  const [cron, setCron] = useState(schedule?.cron ?? "");
  const [dispatchMode, setDispatchMode] = useState<ScheduleDispatchMode>(initialDispatchMode);
  const [prompt, setPrompt] = useState(schedule?.prompt ?? "");
  const [jobName, setJobName] = useState(schedule?.job_name ?? "");
  const [jobArgsRaw, setJobArgsRaw] = useState(
    schedule?.job_args ? JSON.stringify(schedule.job_args, null, 2) : "",
  );

  const parsedJobArgs = parseJobArgs(jobArgsRaw);
  const isPromptMode = dispatchMode === "prompt";
  const isValid =
    name.trim() !== "" &&
    cron.trim() !== "" &&
    (isPromptMode
      ? prompt.trim() !== ""
      : jobName.trim() !== "" && parsedJobArgs.error === null);
  const displayError = parsedJobArgs.error ?? error;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isValid || isSubmitting) return;

    if (dispatchMode === "prompt") {
      onSubmit({
        name: name.trim(),
        cron: cron.trim(),
        dispatch_mode: "prompt",
        prompt: prompt.trim(),
      });
      return;
    }

    if (parsedJobArgs.error) return;
    onSubmit({
      name: name.trim(),
      cron: cron.trim(),
      dispatch_mode: "job",
      job_name: jobName.trim(),
      ...(parsedJobArgs.value ? { job_args: parsedJobArgs.value } : {}),
    });
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-2">
        <label htmlFor="schedule-name" className="text-sm font-medium">
          Name
        </label>
        <Input
          id="schedule-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. daily-digest"
          disabled={isSubmitting}
        />
      </div>

      <div className="space-y-2">
        <label htmlFor="schedule-cron" className="text-sm font-medium">
          Cron Expression
        </label>
        <Input
          id="schedule-cron"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          placeholder="e.g. 0 9 * * *"
          disabled={isSubmitting}
        />
        <p className="text-xs text-muted-foreground">
          Standard 5-field cron: minute hour day-of-month month day-of-week
        </p>
      </div>

      <div className="space-y-2">
        <label htmlFor="schedule-dispatch-mode" className="text-sm font-medium">
          Mode
        </label>
        <select
          id="schedule-dispatch-mode"
          value={dispatchMode}
          onChange={(e) => setDispatchMode(e.target.value as ScheduleDispatchMode)}
          disabled={isSubmitting}
          className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-10 w-full rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <option value="prompt">Prompt</option>
          <option value="job">Job</option>
        </select>
      </div>

      {isPromptMode ? (
        <div className="space-y-2">
          <label htmlFor="schedule-prompt" className="text-sm font-medium">
            Prompt
          </label>
          <Textarea
            id="schedule-prompt"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="The prompt that will be sent to the butler when this schedule fires..."
            className="min-h-24"
            disabled={isSubmitting}
          />
        </div>
      ) : (
        <>
          <div className="space-y-2">
            <label htmlFor="schedule-job-name" className="text-sm font-medium">
              Job Name
            </label>
            <Input
              id="schedule-job-name"
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="e.g. switchboard.eligibility_sweep"
              disabled={isSubmitting}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="schedule-job-args" className="text-sm font-medium">
              Job Args (JSON)
            </label>
            <Textarea
              id="schedule-job-args"
              value={jobArgsRaw}
              onChange={(e) => setJobArgsRaw(e.target.value)}
              placeholder='e.g. {"policy_tier":"default"}'
              className="min-h-24 font-mono text-xs"
              disabled={isSubmitting}
            />
            <p className="text-xs text-muted-foreground">
              Optional JSON object passed to deterministic job execution.
            </p>
          </div>
        </>
      )}

      {displayError && <p className="text-sm text-destructive">{displayError}</p>}

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
          disabled={isSubmitting}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={!isValid || isSubmitting}>
          {isSubmitting
            ? isEdit
              ? "Updating..."
              : "Creating..."
            : isEdit
              ? "Update Schedule"
              : "Create Schedule"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// ---------------------------------------------------------------------------
// ScheduleForm
// ---------------------------------------------------------------------------

export function ScheduleForm({
  schedule,
  open,
  onOpenChange,
  onSubmit,
  isSubmitting = false,
  error = null,
}: ScheduleFormProps) {
  const isEdit = !!schedule;

  // Use a key derived from the schedule id (or "new") to remount the inner
  // form component whenever the dialog opens with different data, which
  // resets the controlled inputs without useEffect + setState.
  const formKey = open ? (schedule?.id ?? "new") : "closed";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit Schedule" : "Create Schedule"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update the schedule details below."
              : "Define a new scheduled task for this butler."}
          </DialogDescription>
        </DialogHeader>

        <ScheduleFormFields
          key={formKey}
          schedule={schedule}
          onSubmit={onSubmit}
          onCancel={() => onOpenChange(false)}
          isSubmitting={isSubmitting}
          error={error}
        />
      </DialogContent>
    </Dialog>
  );
}

export default ScheduleForm;
