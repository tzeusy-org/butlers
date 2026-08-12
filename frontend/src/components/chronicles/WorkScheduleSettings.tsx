// ---------------------------------------------------------------------------
// WorkScheduleSettings — owner work-schedule declaration surface (bu-whhll.11)
//
// Lets the owner declare a work schedule ("I work Mon–Fri 09:30–19:30 at
// <label>") straight into chronicler.routines with origin='declared', so the
// occupation-inference adapter recognizes the workday immediately instead of
// waiting weeks for the miner to accrue observed support. Declared rows are
// fully owner-editable (days, window, label) and deletable; mined rows (from
// the weekly miner) can be enabled/disabled here but their window is the
// miner's to refine.
//
// Degraded-mode honesty (CLAUDE.md API conventions): a FAILED routines fetch
// renders a SourceDegradedNote, never a calm "no schedule declared yet". The
// empty state is gated on `!isError`.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { Loader2, Pencil, Plus, Trash2, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { SourceDegradedNote } from "@/components/ui/query-boundary";
import { Time } from "@/components/ui/time";
import {
  useChroniclesRoutines,
  useCreateChroniclesRoutine,
  useDeleteChroniclesRoutine,
  useUpdateChroniclesRoutine,
} from "@/hooks/use-chronicles";
import type { ChroniclerRoutine } from "@/api/types";
import {
  DOW_LABELS,
  daysFromDowMask,
  dowMaskFromDays,
  formatDowMask,
  formatWindow,
  formatWindowTime,
  toApiTime,
  validateScheduleDraft,
  type ScheduleDraft,
} from "./work-schedule-utils";

const EMPTY_DRAFT: ScheduleDraft = {
  dowMask: 0b0011111, // Mon–Fri, the common default.
  windowStart: "09:30",
  windowEnd: "19:30",
  label: "",
};

function draftFromRoutine(r: ChroniclerRoutine): ScheduleDraft {
  return {
    dowMask: r.dow_mask,
    windowStart: formatWindowTime(r.window_start_local),
    windowEnd: formatWindowTime(r.window_end_local),
    label: r.label,
  };
}

// ── Day-of-week toggle row ─────────────────────────────────────────────────

function DayPicker({
  dowMask,
  onChange,
  idPrefix,
}: {
  dowMask: number;
  onChange: (mask: number) => void;
  idPrefix: string;
}) {
  const selected = new Set(daysFromDowMask(dowMask));
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label="Days of the week">
      {DOW_LABELS.map((label, i) => {
        const isOn = selected.has(i);
        return (
          <Button
            key={label}
            type="button"
            variant={isOn ? "default" : "outline"}
            size="sm"
            aria-pressed={isOn}
            data-testid={`${idPrefix}-day-${i}`}
            className="h-7 w-11 px-0 text-xs"
            onClick={() => {
              const next = new Set(selected);
              if (isOn) next.delete(i);
              else next.add(i);
              onChange(dowMaskFromDays(next));
            }}
          >
            {label}
          </Button>
        );
      })}
    </div>
  );
}

// ── Shared draft form (used for both declare and edit) ─────────────────────

function ScheduleForm({
  draft,
  setDraft,
  onSubmit,
  onCancel,
  submitLabel,
  isPending,
  isError,
  idPrefix,
}: {
  draft: ScheduleDraft;
  setDraft: (d: ScheduleDraft) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  submitLabel: string;
  isPending: boolean;
  isError: boolean;
  idPrefix: string;
}) {
  const validationError = validateScheduleDraft(draft);

  return (
    <form
      className="space-y-3"
      aria-label={submitLabel}
      onSubmit={(e) => {
        e.preventDefault();
        if (validationError || isPending) return;
        onSubmit();
      }}
    >
      <DayPicker
        dowMask={draft.dowMask}
        onChange={(mask) => setDraft({ ...draft, dowMask: mask })}
        idPrefix={idPrefix}
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label htmlFor={`${idPrefix}-start`} className="text-xs text-muted-foreground">
            Start
          </label>
          <Input
            id={`${idPrefix}-start`}
            type="time"
            value={draft.windowStart}
            className="w-28"
            onChange={(e) => setDraft({ ...draft, windowStart: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor={`${idPrefix}-end`} className="text-xs text-muted-foreground">
            End
          </label>
          <Input
            id={`${idPrefix}-end`}
            type="time"
            value={draft.windowEnd}
            className="w-28"
            onChange={(e) => setDraft({ ...draft, windowEnd: e.target.value })}
          />
        </div>
        <div className="min-w-[10rem] flex-1 space-y-1">
          <label htmlFor={`${idPrefix}-label`} className="text-xs text-muted-foreground">
            Where
          </label>
          <Input
            id={`${idPrefix}-label`}
            value={draft.label}
            placeholder="e.g. Work at Acme"
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={!!validationError || isPending}
          data-testid={`${idPrefix}-submit`}
        >
          {isPending ? (
            <>
              <Loader2 className="size-3.5 animate-spin" />
              Saving…
            </>
          ) : (
            <>
              {submitLabel === "Declare schedule" && <Plus className="size-3.5" />}
              {submitLabel}
            </>
          )}
        </Button>
        {onCancel && (
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            <X className="size-3.5" />
            Cancel
          </Button>
        )}
        {validationError && (
          <span className="text-xs text-muted-foreground" data-testid={`${idPrefix}-hint`}>
            {validationError}
          </span>
        )}
      </div>

      {isError && (
        <p className="text-xs text-destructive" data-testid={`${idPrefix}-error`}>
          Couldn&apos;t save the schedule. Try again.
        </p>
      )}
    </form>
  );
}

// ── One routine row ────────────────────────────────────────────────────────

function RoutineRow({ routine }: { routine: ChroniclerRoutine }) {
  const isDeclared = routine.origin === "declared";
  const missedMineLabel = `${routine.missed_mine_cycles} missed ${
    routine.missed_mine_cycles === 1 ? "mine" : "mines"
  }`;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ScheduleDraft>(() => draftFromRoutine(routine));

  const update = useUpdateChroniclesRoutine();
  const del = useDeleteChroniclesRoutine();

  function handleToggle(enabled: boolean) {
    update.mutate({ routineId: routine.id, body: { enabled } });
  }

  function handleEditSubmit() {
    update.mutate(
      {
        routineId: routine.id,
        body: {
          dow_mask: draft.dowMask,
          window_start_local: toApiTime(draft.windowStart),
          window_end_local: toApiTime(draft.windowEnd),
          label: draft.label.trim(),
        },
      },
      { onSuccess: () => setEditing(false) },
    );
  }

  return (
    <li
      className="rounded-md border p-3 text-sm"
      data-testid={`routine-row-${routine.id}`}
      style={{ borderColor: "var(--border)" }}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{routine.label}</span>
            <Badge variant={isDeclared ? "default" : "outline"} className="text-[10px]">
              {isDeclared ? "declared" : "mined"}
            </Badge>
            {!routine.enabled && (
              <Badge variant="secondary" className="text-[10px]">
                disabled
              </Badge>
            )}
            {!isDeclared && routine.stale && (
              <Badge
                variant="outline"
                className="text-[10px]"
                data-testid={`routine-staleness-${routine.id}`}
              >
                {missedMineLabel}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {formatDowMask(routine.dow_mask)} ·{" "}
            {formatWindow(routine.window_start_local, routine.window_end_local)} ·{" "}
            {routine.timezone}
          </p>
          {!isDeclared && (
            <p
              className="text-xs text-muted-foreground"
              data-testid={`routine-last-confirmed-${routine.id}`}
            >
              Last confirmed:{" "}
              {routine.last_confirmed_at ? (
                <Time value={routine.last_confirmed_at} mode="absolute" precision="minute" />
              ) : (
                "not yet observed"
              )}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Switch
            checked={routine.enabled}
            onCheckedChange={handleToggle}
            disabled={update.isPending}
            aria-label={`${routine.enabled ? "Disable" : "Enable"} ${routine.label}`}
            data-testid={`routine-toggle-${routine.id}`}
          />
          {isDeclared && !editing && (
            <>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2"
                aria-label={`Edit ${routine.label}`}
                data-testid={`routine-edit-${routine.id}`}
                onClick={() => {
                  setDraft(draftFromRoutine(routine));
                  setEditing(true);
                }}
              >
                <Pencil className="size-3.5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-destructive"
                aria-label={`Delete ${routine.label}`}
                data-testid={`routine-delete-${routine.id}`}
                disabled={del.isPending}
                onClick={() => del.mutate(routine.id)}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>

      {editing && (
        <div className="mt-3 border-t pt-3" style={{ borderColor: "var(--border)" }}>
          <ScheduleForm
            draft={draft}
            setDraft={setDraft}
            onSubmit={handleEditSubmit}
            onCancel={() => setEditing(false)}
            submitLabel="Save changes"
            isPending={update.isPending}
            isError={update.isError}
            idPrefix={`edit-${routine.id}`}
          />
        </div>
      )}

      {del.isError && (
        <p className="mt-2 text-xs text-destructive" data-testid={`routine-delete-error-${routine.id}`}>
          Couldn&apos;t delete this schedule. Try again.
        </p>
      )}

      {update.isError && !editing && (
        <p className="mt-2 text-xs text-destructive" data-testid={`routine-update-error-${routine.id}`}>
          Couldn&apos;t update this schedule. Try again.
        </p>
      )}
    </li>
  );
}

// ── Public component ───────────────────────────────────────────────────────

export function WorkScheduleSettings() {
  const routines = useChroniclesRoutines();
  const create = useCreateChroniclesRoutine();
  const [draft, setDraft] = useState<ScheduleDraft>(EMPTY_DRAFT);

  const rows = routines.data?.data ?? [];

  function handleCreate() {
    create.mutate(
      {
        dow_mask: draft.dowMask,
        window_start_local: toApiTime(draft.windowStart),
        window_end_local: toApiTime(draft.windowEnd),
        label: draft.label.trim(),
      },
      { onSuccess: () => setDraft(EMPTY_DRAFT) },
    );
  }

  return (
    <div className="space-y-4" data-testid="work-schedule-settings">
      <p className="text-sm text-muted-foreground">
        Declare when you work so the Chronicler recognizes your workday right away. Your
        declared hours drive inference immediately; the weekly miner refines them as it
        observes real signal.
      </p>

      {/* Existing routines — degraded fetch must not read as "no schedule". */}
      {routines.isError ? (
        <SourceDegradedNote
          label="Work schedule"
          detail="couldn't load your declared routines"
          onRetry={() => void routines.refetch()}
        />
      ) : routines.isLoading ? (
        <div className="space-y-2" data-testid="work-schedule-loading">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground" data-testid="work-schedule-empty">
          No schedule declared yet. Add one below.
        </p>
      ) : (
        <ul className="space-y-2" data-testid="work-schedule-list">
          {rows.map((r) => (
            <RoutineRow key={r.id} routine={r} />
          ))}
        </ul>
      )}

      {/* Declare a new schedule. */}
      <div className="rounded-md border border-dashed p-3" style={{ borderColor: "var(--border)" }}>
        <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Declare a schedule
        </h4>
        <ScheduleForm
          draft={draft}
          setDraft={setDraft}
          onSubmit={handleCreate}
          submitLabel="Declare schedule"
          isPending={create.isPending}
          isError={create.isError}
          idPrefix="declare"
        />
      </div>
    </div>
  );
}
