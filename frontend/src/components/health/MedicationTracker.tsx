// ---------------------------------------------------------------------------
// MedicationTracker — Dispatch rule-list with dose logging [bu-w7b18.3]
//
// Reframed from the old card grid (CardHeader/CardContent + nested DoseLog
// table) into the Dispatch language (design language §4a "Lists"): each
// medication is a hairline-separated Row — status dot / name + dosage /
// adherence statement / actions / expand chevron — never a card.
//
// Adherence is sourced from GET /health/medications/{id}/adherence (the
// server-computed, frequency-expected figure) and stated plainly ("12 of 14
// doses taken"), never rewarded with celebration or a green check. State color
// (amber/red dot) appears only when adherence is genuinely falling.
//
// Dose history moves to a per-row detail/expand affordance. A dashboard
// dose-logging affordance posts to POST /health/medications/{id}/doses (a
// `took_dose` fact). The Active/All filter and the create/edit Dialog + Form
// are preserved.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { toast } from "sonner";

import type { Medication, MedicationAdherence } from "@/api/types";
import { MedicationForm } from "@/components/health/MedicationForm";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Eyebrow } from "@/components/ui/Eyebrow";
import { Mono } from "@/components/ui/Mono";
import { Row } from "@/components/ui/Row";
import { Skeleton } from "@/components/ui/skeleton";
import { StateDot, type AnyDotState } from "@/components/ui/StateDot";
import { Time } from "@/components/ui/time";
import { Voice } from "@/components/ui/Voice";
import { parseScheduleTime } from "@/lib/medication-schedule";
import { cn } from "@/lib/utils";
import {
  useDeleteMedication,
  useLogMedicationDose,
  useMedicationAdherence,
  useMedicationDoses,
  useMedications,
} from "@/hooks/use-health";

// ---------------------------------------------------------------------------
// Row action — a quiet mono text button (no badge / card chrome)
// ---------------------------------------------------------------------------

function RowAction({
  children,
  onClick,
  "aria-label": ariaLabel,
  tone = "muted",
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  "aria-label": string;
  tone?: "muted" | "danger";
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={ariaLabel}
      disabled={disabled}
      className={cn(
        "shrink-0 font-mono text-[11px] uppercase tracking-wider underline underline-offset-2 transition-colors cursor-pointer disabled:cursor-default disabled:opacity-50",
        tone === "danger"
          ? "text-muted-foreground hover:text-[var(--red)]"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Adherence — derive a (non-rewarding) status dot + plain statement
// ---------------------------------------------------------------------------

/**
 * Map adherence to a status dot. State color appears ONLY when adherence is
 * genuinely falling — healthy adherence is a neutral (dim) dot, never a green
 * "reward". Inactive medications read as archived.
 */
function adherenceDotState(active: boolean, adherence?: MedicationAdherence): AnyDotState {
  if (!active) return "archived";
  const rate = adherence?.adherence_rate;
  if (adherence == null || adherence.total_doses === 0 || rate == null) return "waiting";
  if (rate < 50) return "error";
  if (rate < 80) return "degraded";
  // Healthy — deliberately neutral, no celebratory green.
  return "waiting";
}

/**
 * Plain adherence statement sourced from the adherence route. Never a naive
 * client-side ratio; never celebratory. Color is applied only when adherence
 * is genuinely falling.
 */
function AdherenceStatement({ medicationId }: { medicationId: string }) {
  const { data, isLoading } = useMedicationAdherence(medicationId);

  if (isLoading) {
    return <Skeleton className="mt-1 h-3 w-32" />;
  }

  if (!data || data.total_doses === 0 || data.adherence_rate == null) {
    return (
      <Mono muted className="mt-0.5 block">
        No doses logged yet
      </Mono>
    );
  }

  const falling = data.adherence_rate < 80;
  return (
    <Mono
      muted={!falling}
      className={cn(
        "mt-0.5 block",
        data.adherence_rate < 50 && "text-[var(--red)]",
        data.adherence_rate >= 50 && data.adherence_rate < 80 && "text-[var(--amber)]",
      )}
    >
      {data.taken_doses} of {data.total_doses} doses taken · {data.adherence_rate.toFixed(0)}%
    </Mono>
  );
}

// ---------------------------------------------------------------------------
// DoseHistory — per-row detail/expand (replaces the nested table card)
// ---------------------------------------------------------------------------

function DoseHistory({ medicationId, name }: { medicationId: string; name: string }) {
  const { data: doses, isLoading } = useMedicationDoses(medicationId);
  const logDose = useLogMedicationDose();

  async function handleLog(skipped: boolean) {
    try {
      await logDose.mutateAsync({ id: medicationId, body: { skipped } });
      toast.success(skipped ? "Dose recorded as skipped." : "Dose logged.");
    } catch {
      toast.error("Failed to record dose.");
    }
  }

  return (
    <div className="border-b border-border bg-foreground/[0.02] px-3 py-3 pl-[30px]">
      <div className="mb-2 flex items-center justify-between gap-3">
        <Eyebrow as="div">Dose history</Eyebrow>
        <RowAction
          aria-label={`Log a skipped dose for ${name}`}
          onClick={() => void handleLog(true)}
          disabled={logDose.isPending}
        >
          Log skipped
        </RowAction>
      </div>

      {isLoading ? (
        <div className="space-y-1.5">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      ) : !doses || doses.length === 0 ? (
        <Voice variant="italic" className="text-sm text-muted-foreground">
          No doses recorded yet.
        </Voice>
      ) : (
        <ul className="flex flex-col">
          {doses.slice(0, 20).map((dose) => (
            <li
              key={dose.id}
              className="grid grid-cols-[auto_auto_1fr] items-baseline gap-3 border-b border-border/40 py-1.5 last:border-0"
            >
              <Mono muted>
                <Time value={dose.taken_at} mode="absolute" precision="minute" compact />
              </Mono>
              <Mono className={cn(dose.skipped ? "text-[var(--amber)]" : "text-muted-foreground")}>
                {dose.skipped ? "Skipped" : "Taken"}
              </Mono>
              <span className="truncate text-xs text-muted-foreground">{dose.notes ?? ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MedicationRow — one Dispatch rule-list row
// ---------------------------------------------------------------------------

function MedicationRow({
  medication,
  onEdit,
}: {
  medication: Medication;
  onEdit: (medication: Medication) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const { data: adherence } = useMedicationAdherence(medication.id);
  const deleteMutation = useDeleteMedication();
  const logDose = useLogMedicationDose();

  const dotState = adherenceDotState(medication.active, adherence);

  async function handleDelete() {
    try {
      await deleteMutation.mutateAsync(medication.id);
      toast.success("Medication deleted.");
      setConfirmingDelete(false);
    } catch {
      toast.error("Failed to delete medication.");
    }
  }

  async function handleLogDose() {
    try {
      await logDose.mutateAsync({ id: medication.id, body: { skipped: false } });
      toast.success("Dose logged.");
    } catch {
      toast.error("Failed to log dose.");
    }
  }

  return (
    <>
      <Row
        mark={<StateDot state={dotState} size={8} />}
        meta={
          <div className="flex items-center gap-3">
            <RowAction
              aria-label={`Log dose for ${medication.name}`}
              onClick={() => void handleLogDose()}
              disabled={logDose.isPending}
            >
              Log dose
            </RowAction>
            <RowAction aria-label={`Edit ${medication.name}`} onClick={() => onEdit(medication)}>
              Edit
            </RowAction>
            <RowAction
              aria-label={`Delete ${medication.name}`}
              tone="danger"
              onClick={() => setConfirmingDelete(true)}
            >
              Delete
            </RowAction>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-label={`${expanded ? "Hide" : "Show"} dose history for ${medication.name}`}
              aria-expanded={expanded}
              className="shrink-0 text-muted-foreground transition-colors hover:text-foreground cursor-pointer"
            >
              <ChevronRight
                className={cn("size-4 transition-transform", expanded && "rotate-90")}
                aria-hidden
              />
            </button>
          </div>
        }
      >
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="truncate font-medium">{medication.name}</span>
            <Mono muted className="shrink-0">
              {medication.dosage} · {medication.frequency}
            </Mono>
            {!medication.active && (
              <Mono muted className="shrink-0 uppercase tracking-wider">
                inactive
              </Mono>
            )}
          </div>
          <AdherenceStatement medicationId={medication.id} />
          {medication.notes && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{medication.notes}</p>
          )}
        </div>
      </Row>

      {expanded && <DoseHistory medicationId={medication.id} name={medication.name} />}

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {medication.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes {medication.name} from your medication list. The record is retained for
              history but will no longer appear here.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void handleDelete();
              }}
              disabled={deleteMutation.isPending}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Next doses — right-rail of upcoming scheduled doses across active meds
// ---------------------------------------------------------------------------

interface NextDose {
  medicationId: string;
  name: string;
  dosage: string;
  /** Minutes-from-now until the next scheduled time (for sorting). */
  minutesAway: number;
  /** "HH:MM" scheduled time. */
  time: string;
  /** True when the soonest occurrence is tomorrow. */
  tomorrow: boolean;
}

function computeNextDoses(medications: Medication[]): NextDose[] {
  const now = new Date();
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const next: NextDose[] = [];

  for (const med of medications) {
    if (!med.active) continue;
    let soonest: { minutesAway: number; minutesOfDay: number; tomorrow: boolean } | null = null;
    for (const entry of med.schedule ?? []) {
      const minutesOfDay = parseScheduleTime(entry);
      if (minutesOfDay == null) continue;
      const tomorrow = minutesOfDay < nowMinutes;
      const minutesAway = tomorrow ? minutesOfDay + 1440 - nowMinutes : minutesOfDay - nowMinutes;
      if (soonest == null || minutesAway < soonest.minutesAway) {
        soonest = { minutesAway, minutesOfDay, tomorrow };
      }
    }
    if (soonest) {
      const hh = String(Math.floor(soonest.minutesOfDay / 60)).padStart(2, "0");
      const mm = String(soonest.minutesOfDay % 60).padStart(2, "0");
      next.push({
        medicationId: med.id,
        name: med.name,
        dosage: med.dosage,
        minutesAway: soonest.minutesAway,
        time: `${hh}:${mm}`,
        tomorrow: soonest.tomorrow,
      });
    }
  }

  return next.sort((a, b) => a.minutesAway - b.minutesAway).slice(0, 8);
}

function NextDoses({ medications }: { medications: Medication[] }) {
  const upcoming = computeNextDoses(medications);

  return (
    <aside className="md:w-56 md:shrink-0">
      <Eyebrow as="div" className="mb-2">
        Next doses
      </Eyebrow>
      {upcoming.length === 0 ? (
        <Voice variant="italic" className="text-sm text-muted-foreground">
          No scheduled doses.
        </Voice>
      ) : (
        <ul className="flex flex-col">
          {upcoming.map((d) => (
            <li
              key={`${d.medicationId}-${d.time}`}
              className="grid grid-cols-[auto_1fr] items-baseline gap-3 border-b border-border/40 py-1.5 last:border-0"
            >
              <Mono muted>
                {d.time}
                {d.tomorrow ? "⁺" : ""}
              </Mono>
              <span className="truncate text-sm">{d.name}</span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// MedicationTracker — toolbar + rule-list + dialog
// ---------------------------------------------------------------------------

export default function MedicationTracker() {
  const [showAll, setShowAll] = useState(false);
  // `null` = closed; `undefined` = add mode; a Medication = edit mode.
  const [formTarget, setFormTarget] = useState<Medication | null | undefined>(null);

  const { data, isLoading } = useMedications({
    active: showAll ? undefined : true,
    limit: 100,
  });

  const medications = data?.data ?? [];
  const dialogOpen = formTarget !== null;
  const editing = formTarget != null;

  return (
    <div className="space-y-4">
      {/* Toolbar: Active/All filter + add affordance */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-4" role="group" aria-label="Filter medications">
          <button
            type="button"
            onClick={() => setShowAll(false)}
            aria-pressed={!showAll}
            className={cn(
              "font-mono text-[11px] uppercase tracking-wider underline-offset-4 transition-colors cursor-pointer",
              !showAll ? "text-foreground underline" : "text-muted-foreground hover:text-foreground",
            )}
          >
            Active
          </button>
          <button
            type="button"
            onClick={() => setShowAll(true)}
            aria-pressed={showAll}
            className={cn(
              "font-mono text-[11px] uppercase tracking-wider underline-offset-4 transition-colors cursor-pointer",
              showAll ? "text-foreground underline" : "text-muted-foreground hover:text-foreground",
            )}
          >
            All
          </button>
        </div>
        <button
          type="button"
          onClick={() => setFormTarget(undefined)}
          className="font-mono text-[11px] uppercase tracking-wider underline underline-offset-4 text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        >
          Add medication
        </button>
      </div>

      <div className="flex flex-col gap-8 md:flex-row md:items-start md:gap-10">
        {/* Rule-list — the primary surface */}
        <div className="min-w-0 flex-1">
          {isLoading ? (
            <div className="flex flex-col">
              {Array.from({ length: 5 }, (_, i) => (
                <div key={i} className="flex items-center gap-3 border-b border-border py-2.5">
                  <Skeleton className="h-2 w-2 rounded-full" />
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="ml-auto h-3 w-24" />
                </div>
              ))}
            </div>
          ) : medications.length === 0 ? (
            <Voice variant="italic" className="text-muted-foreground">
              {showAll
                ? "No medications recorded yet."
                : "No active medications. Switch to All to see inactive ones."}
            </Voice>
          ) : (
            <div className="flex flex-col">
              {medications.map((med) => (
                <MedicationRow key={med.id} medication={med} onEdit={setFormTarget} />
              ))}
            </div>
          )}
        </div>

        {/* Next doses rail */}
        {!isLoading && medications.length > 0 && <NextDoses medications={medications} />}
      </div>

      {/* Add / edit dialog (preserved) */}
      <Dialog open={dialogOpen} onOpenChange={(open) => !open && setFormTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit medication" : "Add medication"}</DialogTitle>
            <DialogDescription>
              {editing
                ? "Update this medication's details."
                : "Add a medication to your record. It appears immediately."}
            </DialogDescription>
          </DialogHeader>
          <MedicationForm
            medication={editing ? formTarget : undefined}
            onDone={() => setFormTarget(null)}
            onCancel={() => setFormTarget(null)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
